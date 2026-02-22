import { Hono } from "hono";
import { stream } from "hono/streaming";
import type { StateFrame, NextState, ActiveSkill, TransitionDef } from "./models.js";
import { visualizeFsm } from "./visualize.js";

function ndjsonLine(obj: object): string {
  return JSON.stringify(obj) + "\n";
}

export class SWPWorkflow {
  workflow_id: string;
  initial_state: string;
  transitions: TransitionDef[];
  base_url: string;
  skill_base_url: string;
  _state_hints: Record<string, string> = {};
  _state_skills: Record<string, ActiveSkill> = {};
  _state_status: Record<string, string> = {};

  constructor(
    workflow_id: string,
    initial_state: string,
    transitions: TransitionDef[],
    base_url = "http://localhost:3000",
    skill_base_url?: string
  ) {
    this.workflow_id = workflow_id;
    this.initial_state = initial_state;
    this.transitions = transitions;
    this.base_url = base_url.replace(/\/$/, "");
    this.skill_base_url = skill_base_url ?? base_url;
  }

  hint(state: string, text: string): this {
    this._state_hints[state] = text;
    return this;
  }

  skill(state: string, name: string, path: string, context_summary?: string): this {
    const url = `${this.skill_base_url.replace(/\/$/, "")}/skills/${path}`;
    this._state_skills[state] = { name, url, context_summary };
    return this;
  }

  statusDefault(state: string, status: string): this {
    this._state_status[state] = status;
    return this;
  }

  private nextStates(from_state: string, run_id: string): NextState[] {
    return this.transitions
      .filter((t) => t.from_state === from_state)
      .map((t) => ({
        action: t.action,
        method: "POST" as const,
        href: `${this.base_url}/runs/${run_id}/transitions/${t.action}`,
        expects: t.expects,
        is_critical: t.is_critical ?? false,
      }));
  }

  buildFrame(
    run_id: string,
    state: string,
    opts: {
      status?: string;
      data?: Record<string, unknown>;
      milestones?: string[];
      stream_path?: string;
    } = {}
  ): StateFrame {
    const status = opts.status ?? this._state_status[state] ?? "active";
    const stream_url = opts.stream_path
      ? `${this.base_url}${opts.stream_path}`
      : `${this.base_url}/runs/${run_id}/stream`;
    return {
      run_id,
      workflow_id: this.workflow_id,
      resource_url: this.base_url,
      state,
      status: status as StateFrame["status"],
      hint: this._state_hints[state] ?? "Proceed.",
      active_skill: this._state_skills[state],
      next_states: this.nextStates(state, run_id),
      data: opts.data ?? {},
      milestones: opts.milestones,
      stream_url,
    };
  }

  getTransition(from_state: string, action: string): TransitionDef | null {
    return this.transitions.find((t) => t.from_state === from_state && t.action === action) ?? null;
  }
}

type Store = Record<string, { state: string; data: Record<string, unknown>; milestones: string[] }>;

export function createApp(
  workflow: SWPWorkflow,
  store: Store = {},
  streamCallback?: (run_id: string, frame: StateFrame) => void
): Hono {
  const app = new Hono();

  function getRun(run_id: string): { state: string; data: Record<string, unknown>; milestones: string[] } {
    const r = store[run_id];
    if (!r) throw new Error("Run not found");
    return r;
  }

  app.get("/", (c) => {
    const run_id = crypto.randomUUID();
    store[run_id] = { state: workflow.initial_state, data: {}, milestones: [] };
    const frame = workflow.buildFrame(run_id, workflow.initial_state, { data: {}, milestones: [] });
    const next_states = [
      {
        ...frame.next_states[0],
        href: `${workflow.base_url}/runs/${run_id}/transitions/start`,
      },
    ];
    return c.json({ ...frame, next_states });
  });

  app.post("/runs", async (c) => {
    const body = (await c.req.json().catch(() => ({}))) as { data?: Record<string, unknown> };
    const run_id = crypto.randomUUID();
    store[run_id] = {
      state: workflow.initial_state,
      data: body?.data ?? {},
      milestones: [],
    };
    const frame = workflow.buildFrame(run_id, workflow.initial_state, {
      data: store[run_id].data,
      milestones: [],
    });
    return c.json(frame, 201, {
      Location: `${workflow.base_url}/runs/${run_id}`,
    });
  });

  app.get("/runs/:run_id", (c) => {
    const run_id = c.req.param("run_id");
    const r = getRun(run_id);
    const frame = workflow.buildFrame(run_id, r.state, {
      data: r.data,
      milestones: r.milestones,
    });
    return c.json(frame);
  });

  app.post("/runs/:run_id/transitions/:action", async (c) => {
    const run_id = c.req.param("run_id");
    const action = c.req.param("action");
    const r = getRun(run_id);
    const current = r.state;
    const trans = workflow.getTransition(current, action);
    if (!trans) {
      return c.json(
        { hint: `Invalid transition: '${action}' not in next_states for state '${current}'.` },
        403
      );
    }
    const body = (await c.req.json().catch(() => ({}))) as Record<string, unknown>;
    const expects = trans.expects ?? {};
    for (const [key] of Object.entries(expects)) {
      if (!(key in body)) {
        return c.json({ hint: `Missing required field: ${key}.` }, 400);
      }
    }
    r.state = trans.to_state;
    if (Object.keys(body).length > 0) {
      r.data = { ...r.data, ...body };
    }
    const newFrame = workflow.buildFrame(run_id, r.state, {
      data: r.data,
      milestones: r.milestones,
    });
    const accept = c.req.header("accept") ?? "";
    if (accept.includes("application/x-ndjson") && newFrame.status === "processing") {
      const runRef = r;
      return stream(c, async (s) => {
        await s.write(ndjsonLine(newFrame));
        streamCallback?.(run_id, newFrame);
        await new Promise((resolve) => setTimeout(resolve, 500));
        runRef.milestones = [...(runRef.milestones ?? []), trans.to_state];
        const updated = workflow.buildFrame(run_id, runRef.state, {
          status: "active",
          data: runRef.data,
          milestones: runRef.milestones,
        });
        await s.write(ndjsonLine(updated));
        await s.close();
      }, {
        headers: {
          "Content-Type": "application/x-ndjson",
          "Transfer-Encoding": "chunked",
        },
        status: 202,
      });
    }
    return c.json(newFrame);
  });

  app.get("/runs/:run_id/stream", async (c) => {
    const run_id = c.req.param("run_id");
    getRun(run_id);
    return stream(c, async (s) => {
      for (let i = 0; i <= 3; i++) {
        const r = getRun(run_id);
        const frame = workflow.buildFrame(run_id, r.state, {
          data: r.data,
          milestones: r.milestones,
        });
        await s.write(ndjsonLine({ id: String(i), ...frame }));
        await new Promise((r) => setTimeout(r, 300));
      }
      await s.close();
    }, {
      headers: {
        "Content-Type": "application/x-ndjson",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  });

  app.get("/visualize", (c) => {
    const run_id = c.req.query("run_id");
    let current: string | null = null;
    if (run_id) {
      try {
        const r = getRun(run_id);
        current = r.state;
      } catch {
        // ignore
      }
    }
    const mermaid = visualizeFsm(
      workflow.workflow_id,
      workflow.initial_state,
      workflow.transitions,
      current
    );
    const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SWP FSM - ${workflow.workflow_id}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script></head>
<body><pre class="mermaid">${mermaid}</pre>
<script>mermaid.initialize({ startOnLoad: true });</script></body></html>`;
    return c.html(html);
  });

  return app;
}
