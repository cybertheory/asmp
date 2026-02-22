"""SWP server: FastAPI app factory and workflow runner with FSM, guards, and NDJSON stream."""
from __future__ import annotations

import json
import uuid
import asyncio
from typing import Any, Callable, Optional
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel

from .models import StateFrame, NextState, ActiveSkill, TransitionDef
from .visualize import visualize_fsm


def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


class SWPWorkflow:
    """Defines a workflow FSM and produces State Frames."""

    def __init__(
        self,
        workflow_id: str,
        initial_state: str,
        transitions: list[TransitionDef],
        base_url: str = "http://localhost:8000",
        skill_base_url: Optional[str] = None,
    ):
        self.workflow_id = workflow_id
        self.initial_state = initial_state
        self.transitions = transitions
        self.base_url = base_url.rstrip("/")
        self.skill_base_url = skill_base_url or base_url
        self._state_hints: dict[str, str] = {}
        self._state_skills: dict[str, ActiveSkill] = {}
        self._state_status: dict[str, str] = {}

    def hint(self, state: str, text: str) -> "SWPWorkflow":
        self._state_hints[state] = text
        return self

    def skill(self, state: str, name: str, path: str, context_summary: Optional[str] = None) -> "SWPWorkflow":
        url = f"{self.skill_base_url.rstrip('/')}/skills/{path}"
        self._state_skills[state] = ActiveSkill(name=name, url=url, context_summary=context_summary)
        return self

    def status_default(self, state: str, status: str) -> "SWPWorkflow":
        self._state_status[state] = status
        return self

    def _next_states(self, from_state: str, run_id: str) -> list[NextState]:
        out = []
        for t in self.transitions:
            if t.from_state != from_state:
                continue
            href = f"{self.base_url}/runs/{run_id}/transitions/{t.action}"
            out.append(
                NextState(
                    action=t.action,
                    method="POST",
                    href=href,
                    expects=t.expects,
                    is_critical=t.is_critical,
                )
            )
        return out

    def build_frame(
        self,
        run_id: str,
        state: str,
        status: Optional[str] = None,
        data: Optional[dict] = None,
        milestones: Optional[list[str]] = None,
        stream_path: Optional[str] = None,
    ) -> StateFrame:
        status = status or self._state_status.get(state, "active")
        stream_url = f"{self.base_url}/runs/{run_id}/stream" if stream_path is None else f"{self.base_url}{stream_path}"
        return StateFrame(
            run_id=run_id,
            workflow_id=self.workflow_id,
            resource_url=self.base_url,
            state=state,
            status=status,
            hint=self._state_hints.get(state, "Proceed."),
            active_skill=self._state_skills.get(state),
            next_states=self._next_states(state, run_id),
            data=data or {},
            milestones=milestones,
            stream_url=stream_url,
        )

    def get_transition(self, from_state: str, action: str) -> Optional[TransitionDef]:
        for t in self.transitions:
            if t.from_state == from_state and t.action == action:
                return t
        return None


def create_app(
    workflow: SWPWorkflow,
    store: Optional[dict[str, Any]] = None,
    stream_callback: Optional[Callable[[str, StateFrame], None]] = None,
) -> FastAPI:
    """Create FastAPI app with SWP routes. store keys: run_id -> { state, data, milestones }."""
    app = FastAPI(title="SWP Server", version="0.1.0")
    store = store or {}
    stream_callback = stream_callback or (lambda run_id, frame: None)

    def get_run(run_id: str) -> dict:
        if run_id not in store:
            raise HTTPException(status_code=404, detail="Run not found")
        return store[run_id]

    @app.get("/")
    async def discover():
        """Discovery: return a frame that allows starting a workflow."""
        run_id = str(uuid.uuid4())
        store[run_id] = {
            "state": workflow.initial_state,
            "data": {},
            "milestones": [],
        }
        frame = workflow.build_frame(run_id, workflow.initial_state, data={}, milestones=[])
        frame_dict = frame.model_dump(by_alias=True, exclude_none=True)
        frame_dict["next_states"] = [
            {
                **ns.model_dump(),
                "href": f"{workflow.base_url}/runs/{run_id}/transitions/start",
            }
        ]
        return frame_dict

    @app.post("/runs")
    async def start_run(body: dict = {}):
        """Start a new run."""
        run_id = str(uuid.uuid4())
        store[run_id] = {
            "state": workflow.initial_state,
            "data": body.get("data", {}),
            "milestones": [],
        }
        frame = workflow.build_frame(run_id, workflow.initial_state, data=store[run_id]["data"], milestones=[])
        return JSONResponse(
            status_code=201,
            content=frame.model_dump(by_alias=True, exclude_none=True),
            headers={"Location": f"{workflow.base_url}/runs/{run_id}"},
        )

    @app.get("/runs/{run_id}")
    async def get_frame(run_id: str):
        """Get current State Frame."""
        r = get_run(run_id)
        frame = workflow.build_frame(
            run_id,
            r["state"],
            data=r.get("data"),
            milestones=r.get("milestones"),
        )
        return frame.model_dump(by_alias=True, exclude_none=True)

    @app.post("/runs/{run_id}/transitions/{action}")
    async def transition(
        run_id: str,
        action: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        """Execute a transition. Returns 202 + NDJSON stream for async when status is processing."""
        r = get_run(run_id)
        current = r["state"]
        trans = workflow.get_transition(current, action)
        if not trans:
            raise HTTPException(
                status_code=403,
                detail={
                    "hint": f"Invalid transition: '{action}' not in next_states for state '{current}'.",
                },
            )
        body = await request.json() if await request.body() else {}
        # Validate expects
        expects = trans.expects or {}
        for key, typ in expects.items():
            if key not in body:
                raise HTTPException(
                    status_code=400,
                    detail={"hint": f"Missing required field: {key} (expected {typ})."},
                )
        # Update state
        r["state"] = trans.to_state
        if body:
            r.setdefault("data", {}).update(body)
        new_frame = workflow.build_frame(
            run_id,
            r["state"],
            data=r.get("data"),
            milestones=r.get("milestones"),
        )
        frame_dict = new_frame.model_dump(by_alias=True, exclude_none=True)

        # If server wants to stream (e.g. processing), optionally return 202 + stream
        accept = request.headers.get("accept", "")
        if "application/x-ndjson" in accept and new_frame.status == "processing":
            async def stream():
                yield _ndjson_line(frame_dict)
                stream_callback(run_id, new_frame)
                # Simulate async work then push another frame
                await asyncio.sleep(0.5)
                r["state"] = trans.to_state
                r["milestones"] = r.get("milestones", []) + [trans.to_state]
                updated = workflow.build_frame(
                    run_id,
                    r["state"],
                    status="active",
                    data=r.get("data"),
                    milestones=r.get("milestones"),
                )
                yield _ndjson_line(updated.model_dump(by_alias=True, exclude_none=True))
            return StreamingResponse(
                stream(),
                media_type="application/x-ndjson",
                status_code=202,
            )
        return frame_dict

    @app.get("/runs/{run_id}/stream")
    async def stream_updates(run_id: str, request: Request):
        """Stream State Frame updates as NDJSON."""
        get_run(run_id)
        last_id = request.headers.get("last-event-id") or request.headers.get("x-last-event-id", "")

        async def event_stream():
            # Send initial frame so client has state
            r = get_run(run_id)
            frame = workflow.build_frame(
                run_id,
                r["state"],
                data=r.get("data"),
                milestones=r.get("milestones"),
            )
            yield _ndjson_line({"id": "0", **frame.model_dump(by_alias=True, exclude_none=True)})
            # In production, subscribe to a queue (Redis, etc.) for this run_id
            for i in range(3):
                await asyncio.sleep(0.3)
                r = get_run(run_id)
                frame = workflow.build_frame(
                    run_id,
                    r["state"],
                    data=r.get("data"),
                    milestones=r.get("milestones"),
                )
                yield _ndjson_line({"id": str(i + 1), **frame.model_dump(by_alias=True, exclude_none=True)})

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/visualize")
    async def visualize(run_id: Optional[str] = None):
        """Render Mermaid.js FSM diagram; optionally highlight current state for run_id."""
        current = None
        if run_id:
            try:
                r = get_run(run_id)
                current = r.get("state")
            except HTTPException:
                pass
        mermaid = visualize_fsm(
            workflow.workflow_id,
            workflow.initial_state,
            workflow.transitions,
            current_state=current,
        )
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SWP FSM - {workflow.workflow_id}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script></head>
<body><pre class="mermaid">{mermaid}</pre>
<script>mermaid.initialize({{ startOnLoad: true }});</script></body></html>"""
        return HTMLResponse(html)

    return app
