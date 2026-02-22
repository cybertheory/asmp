# Stateful Workflow Protocol (SWP)

## 1. Overview

SWP is a lean, agent-native protocol over HTTP. It replaces the "static tool menu" of MCP with **dynamic process navigation**: the server exposes a **State Frame** (current state, hint, and valid next actions) so agents receive only the context they need for the current step.

- **Transport**: Standard HTTP/HTTPS. No custom protocol; works with curl and any HTTP client.
- **Format**: JSON. State Frames conform to `STATE_FRAME.json` schema.
- **Async**: Streamable HTTP (NDJSON) on a single endpoint for push-based state updates.
- **Resumption**: `Mcp-Session-Id` and `Last-Event-ID` (or equivalent) for re-attaching after disconnect.

---

## 2. Protocol Primitives

### 2.1 State Frame

Every successful response from an SWP server is a **State Frame**: a JSON object that is the single source of truth for the agent. It includes:

| Field | Purpose |
|-------|--------|
| `run_id` | Unique execution instance; used for all subsequent requests and stream attachment. |
| `workflow_id` | Identifies the workflow blueprint. |
| `state` | Current FSM node. |
| `status` | `active` \| `processing` \| `awaiting_input` \| `completed` \| `failed`. |
| `hint` | Natural language guidance for the LLM (system-prompt bridge). |
| `active_skill` | Optional; link to SKILL.md to load only when in this state. |
| `next_states` | Array of valid transitions (action, method, href, expects). |
| `stream_url` | Where to listen for NDJSON state updates. |

The server MUST reject any transition not listed in `next_states` for the current state (e.g. 403 Forbidden with a corrective `hint` in the body).

### 2.2 Finite State Machine (FSM)

- **States**: Named nodes (e.g. `UPLOAD`, `AWAITING_AUDIT`, `COMPLETED`).
- **Transitions**: Directed edges (event/action → next state). Only transitions present in the current frame’s `next_states` are allowed.
- **Guards**: Server-side conditions. If a guard fails, the transition is not offered or is rejected; the frame may include a reason (e.g. `can_approve: false`, `reason: "Insufficient funds"`).

---

## 3. Protocol Operations

### 3.1 Discovery (Entry)

- **Request**: `GET /` or `GET /workflows` (server-defined).
- **Response**: 200 OK with a State Frame that either:
  - Lists available workflows and a way to start one (e.g. `next_states` with `start_workflow`), or
  - Represents the initial state of a default workflow.

This allows an agent to discover and start workflows without prior documentation.

### 3.2 Start Run

- **Request**: `POST /` or `POST /runs` with optional body (workflow_id, initial data).
- **Response**: 201 Created, `Location: <resource_url>/runs/<run_id>`, body = initial State Frame.

The client should use `run_id` and `resource_url` for all subsequent requests and for attaching to `stream_url`.

### 3.3 Get Current Frame

- **Request**: `GET /runs/{run_id}`.
- **Response**: 200 OK with current State Frame.

Used for polling or after reconnection to refresh state.

### 3.4 Transition (Trigger Next State)

- **Request**: `POST <href>` where `href` is one of the current frame’s `next_states[].href`. Body must satisfy the corresponding `expects` schema.
- **Response**:
  - **200 OK**: Transition applied; body = new State Frame.
  - **202 Accepted**: Transition accepted; long-running work started. Body = State Frame with `status: processing`. Connection MAY be kept open and switched to NDJSON stream (Unified Endpoint).
  - **400 Bad Request**: Invalid body (e.g. missing required fields).
  - **403 Forbidden**: Action not in `next_states` or guard failed. Body should include a `hint` explaining why.
  - **404 Not Found**: Unknown run_id or transition.

### 3.5 Streamable HTTP (Async Updates)

- **Request**: `GET <stream_url>` with headers:
  - `Accept: application/x-ndjson` (or equivalent).
  - `Mcp-Session-Id: <run_id>` (or `X-Run-Id`) for session identity.
  - `Last-Event-ID: <last_seen_id>` (optional) for resumption after disconnect.
- **Response**: 200 OK, `Content-Type: application/x-ndjson`, body = stream of newline-delimited JSON objects, each a State Frame or a progress envelope (e.g. `{"progress": "45%", "hint": "Still parsing..."}`).

**Unified Endpoint (optional):** A server may respond to a transition `POST` with 202 and immediately send NDJSON on the same connection (chunked transfer), so the client does not need a separate GET to `stream_url` for that run.

---

## 4. Resumption (Proactive Recovery)

- **Mcp-Session-Id**: Sent by the client on stream and/or API requests; server associates the request with the run (e.g. `run_id`).
- **Last-Event-Id**: Sent when reconnecting to the stream; server may replay or skip to the latest frame so the agent can resume without context loss.

The server should persist State Frames (e.g. in Redis or Postgres) keyed by `run_id` so that:
- `GET /runs/{run_id}` always returns the latest frame.
- Reconnecting to `stream_url` with the same session id allows the client to continue receiving updates.

---

## 5. Agent Skills Integration

- When a State Frame includes `active_skill`, the client should:
  1. Fetch the skill from `active_skill.url` (e.g. SKILL.md).
  2. Inject its content (and optionally `active_skill.context_summary`) into the LLM system message or message history.
- Skills are **progressive**: only the skill for the current state is loaded, keeping context minimal and token-efficient.

---

## 6. Conventions

- **Token efficiency**: Frames should be minimal. Avoid redundant keys; use short names where the schema allows.
- **Hints**: Always provide a clear, actionable `hint` so the agent can reason without guessing.
- **Guards**: Enforce business rules on the server; return clear reasons in the body when a transition is rejected.

---

## 7. Versioning

- The State Frame schema may include a `state_machine.version` or similar field.
- Servers can use `workflow_id` to version workflows (e.g. `document-approval-v2`).
