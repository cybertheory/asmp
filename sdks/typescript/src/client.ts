import type { StateFrame } from "./models.js";
import { StateFrameSchema } from "./models.js";

export class SWPClient {
  private baseUrl: string;
  private timeout: number;
  private _runId: string | null = null;

  constructor(baseUrl: string, timeout = 30_000) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.timeout = timeout;
  }

  private parseFrame(data: unknown): StateFrame {
    return StateFrameSchema.parse(data);
  }

  async startRun(data?: Record<string, unknown>): Promise<StateFrame> {
    const res = await fetch(`${this.baseUrl}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: data ?? {} }),
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    const frame = this.parseFrame(await res.json());
    this._runId = frame.run_id;
    return frame;
  }

  async getFrame(runId?: string): Promise<StateFrame> {
    const rid = runId ?? this._runId;
    if (!rid) throw new Error("No run_id; call startRun first or pass runId");
    const res = await fetch(`${this.baseUrl}/runs/${rid}`, {
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return this.parseFrame(await res.json());
  }

  async transition(
    action: string,
    body?: Record<string, unknown>,
    runId?: string
  ): Promise<StateFrame> {
    const rid = runId ?? this._runId;
    if (!rid) throw new Error("No run_id");
    const frame = await this.getFrame(rid);
    const ns = frame.next_states.find((x) => x.action === action);
    if (!ns) {
      throw new Error(
        `Action '${action}' not in next_states: ${frame.next_states.map((x) => x.action).join(", ")}`
      );
    }
    const url = ns.href.startsWith("http") ? ns.href : `${this.baseUrl}${ns.href}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return this.parseFrame(await res.json());
  }

  async *stream(runId?: string): AsyncGenerator<Record<string, unknown>> {
    const rid = runId ?? this._runId;
    if (!rid) throw new Error("No run_id");
    const frame = await this.getFrame(rid);
    const streamUrl = frame.stream_url ?? `${this.baseUrl}/runs/${rid}/stream`;
    const res = await fetch(streamUrl, {
      headers: { Accept: "application/x-ndjson" },
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    const reader = res.body?.getReader();
    if (!reader) throw new Error("No body");
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        yield JSON.parse(line) as Record<string, unknown>;
      }
    }
    if (buf.trim()) yield JSON.parse(buf) as Record<string, unknown>;
  }

  get runId(): string | null {
    return this._runId;
  }
}
