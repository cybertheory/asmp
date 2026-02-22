"""Tests for SWP Python SDK: FSM, State Frame, client-server exchange, visualizer."""
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdks" / "python"))

from swp.models import StateFrame, NextState, TransitionDef
from swp.visualize import visualize_fsm
from swp.server import SWPWorkflow, create_app
from swp.client import SWPClient
from fastapi.testclient import TestClient


# --- Unit: FSM transition logic ---
def test_transition_def():
    t = TransitionDef(from_state="A", action="go", to_state="B", expects={"x": "string"})
    assert t.from_state == "A"
    assert t.action == "go"
    assert t.to_state == "B"
    assert t.expects == {"x": "string"}


def test_workflow_build_frame():
    transitions = [
        TransitionDef(from_state="INIT", action="start", to_state="NEXT"),
    ]
    w = SWPWorkflow("wf1", "INIT", transitions, base_url="http://localhost:8000")
    w.hint("INIT", "Start here.")
    frame = w.build_frame("run-123", "INIT")
    assert frame.run_id == "run-123"
    assert frame.state == "INIT"
    assert frame.hint == "Start here."
    assert len(frame.next_states) == 1
    assert frame.next_states[0].action == "start"
    assert "/runs/run-123/transitions/start" in frame.next_states[0].href


def test_workflow_get_transition():
    transitions = [
        TransitionDef(from_state="A", action="x", to_state="B"),
        TransitionDef(from_state="A", action="y", to_state="C"),
    ]
    w = SWPWorkflow("wf1", "A", transitions)
    assert w.get_transition("A", "x").to_state == "B"
    assert w.get_transition("A", "y").to_state == "C"
    assert w.get_transition("A", "z") is None


def test_state_frame_get_transition_by_action():
    frame = StateFrame(
        run_id="r1",
        workflow_id="w1",
        state="S",
        status="active",
        hint="Go",
        next_states=[
            NextState(action="a", method="POST", href="/a"),
            NextState(action="b", method="POST", href="/b"),
        ],
    )
    ns = frame.get_transition_by_action("b")
    assert ns is not None
    assert ns.action == "b"
    assert frame.get_transition_by_action("c") is None


# --- Unit: Visualizer ---
def test_visualize_fsm():
    transitions = [
        TransitionDef(from_state="A", action="x", to_state="B"),
        TransitionDef(from_state="B", action="y", to_state="C"),
    ]
    mermaid = visualize_fsm("wf1", "A", transitions, current_state="B")
    assert "flowchart LR" in mermaid
    assert "--> A" in mermaid
    assert "A -->|x| B" in mermaid
    assert "B -->|y| C" in mermaid
    assert "class B current" in mermaid


# --- Integration: Client-Server ---
@pytest.fixture
def app_and_client():
    transitions = [
        TransitionDef(from_state="INIT", action="start", to_state="DONE"),
    ]
    w = SWPWorkflow("test-wf", "INIT", transitions).hint("INIT", "Start").hint("DONE", "Done")
    store = {}
    app = create_app(w, store=store)
    client = TestClient(app)
    return app, client, store, w


def test_start_run(app_and_client):
    _, client, store, _ = app_and_client
    r = client.post("/runs", json={"data": {"foo": "bar"}})
    assert r.status_code == 201
    data = r.json()
    assert "run_id" in data
    assert data["state"] == "INIT"
    assert data["workflow_id"] == "test-wf"
    run_id = data["run_id"]
    # Server persists run in store; verify by GET
    r2 = client.get(f"/runs/{run_id}")
    assert r2.status_code == 200
    assert r2.json()["run_id"] == run_id


def test_get_frame(app_and_client):
    _, client, store, _ = app_and_client
    r = client.post("/runs", json={})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    r2 = client.get(f"/runs/{run_id}")
    assert r2.status_code == 200
    assert r2.json()["run_id"] == run_id
    assert r2.json()["state"] == "INIT"


def test_transition_success(app_and_client):
    _, client, store, _ = app_and_client
    r = client.post("/runs", json={})
    run_id = r.json()["run_id"]
    r2 = client.post(f"/runs/{run_id}/transitions/start", json={})
    assert r2.status_code == 200
    assert r2.json()["state"] == "DONE"
    # Verify state via GET
    r3 = client.get(f"/runs/{run_id}")
    assert r3.json()["state"] == "DONE"


def test_transition_invalid_action(app_and_client):
    _, client, _, _ = app_and_client
    r = client.post("/runs", json={})
    run_id = r.json()["run_id"]
    r2 = client.post(f"/runs/{run_id}/transitions/nonexistent", json={})
    assert r2.status_code == 403


def test_transition_missing_expects(app_and_client):
    transitions = [
        TransitionDef(from_state="INIT", action="submit", to_state="DONE", expects={"name": "string"}),
    ]
    w = SWPWorkflow("test-wf", "INIT", transitions).hint("INIT", "Start").hint("DONE", "Done")
    app = create_app(w, store={})
    client = TestClient(app)
    r = client.post("/runs", json={})
    run_id = r.json()["run_id"]
    r2 = client.post(f"/runs/{run_id}/transitions/submit", json={})
    assert r2.status_code == 400
    r3 = client.post(f"/runs/{run_id}/transitions/submit", json={"name": "alice"})
    assert r3.status_code == 200


def test_visualize_endpoint(app_and_client):
    _, client, _, w = app_and_client
    r = client.get("/visualize")
    assert r.status_code == 200
    assert "mermaid" in r.text
    assert w.workflow_id in r.text


# --- SWPClient: parse frame ---
def test_swp_client_parse_frame():
    """SWPClient can parse a State Frame from JSON."""
    frame_json = {
        "run_id": "run-x",
        "workflow_id": "w",
        "state": "S",
        "status": "active",
        "hint": "Go",
        "next_states": [{"action": "a", "method": "POST", "href": "http://localhost/runs/run-x/transitions/a"}],
    }
    client = SWPClient("http://localhost:8000")
    frame = client._parse_frame(frame_json)
    assert frame.run_id == "run-x"
    assert frame.state == "S"
    assert frame.get_transition_by_action("a") is not None
