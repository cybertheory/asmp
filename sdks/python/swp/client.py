"""SWP client: fetch frames, trigger transitions, and consume NDJSON stream."""
from __future__ import annotations

import json
import httpx
from typing import Any, Iterator, Optional

from .models import StateFrame


class SWPClient:
    """HTTP client for SWP. Uses run_id and resource_url from the first frame."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._run_id: Optional[str] = None
        self._resource_url: Optional[str] = None

    def _parse_frame(self, data: dict) -> StateFrame:
        return StateFrame.model_validate(data)

    def start_run(self, data: Optional[dict] = None) -> StateFrame:
        """POST /runs and return initial State Frame."""
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            r = client.post("/runs", json={"data": data or {}})
            r.raise_for_status()
            frame = self._parse_frame(r.json())
            self._run_id = frame.run_id
            self._resource_url = frame.resource_url or self.base_url
            return frame

    def get_frame(self, run_id: Optional[str] = None) -> StateFrame:
        """GET current State Frame for run_id."""
        rid = run_id or self._run_id
        if not rid:
            raise ValueError("No run_id; call start_run first or pass run_id")
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            r = client.get(f"/runs/{rid}")
            r.raise_for_status()
            return self._parse_frame(r.json())

    def transition(self, action: str, body: Optional[dict] = None, run_id: Optional[str] = None) -> StateFrame:
        """POST to the href for the given action. Body must satisfy expects."""
        rid = run_id or self._run_id
        if not rid:
            raise ValueError("No run_id")
        frame = self.get_frame(rid)
        ns = frame.get_transition_by_action(action)
        if not ns:
            raise ValueError(f"Action '{action}' not in next_states: {[x.action for x in frame.next_states]}")
        url = ns.href if ns.href.startswith("http") else f"{self.base_url}{ns.href}"
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            r = client.post(url, json=body or {})
            r.raise_for_status()
            return self._parse_frame(r.json())

    def stream(self, run_id: Optional[str] = None) -> Iterator[dict]:
        """GET stream_url and yield NDJSON objects (State Frames or progress)."""
        rid = run_id or self._run_id
        if not rid:
            raise ValueError("No run_id")
        frame = self.get_frame(rid)
        stream_url = frame.stream_url or f"{self.base_url}/runs/{rid}/stream"
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            r = client.get(
                stream_url,
                headers={"Accept": "application/x-ndjson"},
            )
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                yield json.loads(line)

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id
