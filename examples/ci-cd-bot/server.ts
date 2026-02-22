/**
 * CI-CD Bot - SWP example (TypeScript).
 * States: INITIAL -> LINT -> RUN_LINT -> REVIEW_RESULTS -> MERGE_OK | REQUEST_CHANGES
 */
import { Hono } from "hono";
import { serve } from "@hono/node-server";
import { createApp, SWPWorkflow } from "../../sdks/typescript/src/index.js";
import type { TransitionDef } from "../../sdks/typescript/src/models.js";
import { readFileSync } from "fs";
import { join } from "path";

const transitions: TransitionDef[] = [
  { from_state: "INITIAL", action: "start", to_state: "LINT" },
  { from_state: "LINT", action: "run_lint", to_state: "RUN_LINT" },
  { from_state: "RUN_LINT", action: "lint_done", to_state: "REVIEW_RESULTS", expects: { passed: "boolean", issues: "number" } },
  { from_state: "REVIEW_RESULTS", action: "merge_ok", to_state: "MERGE_OK" },
  { from_state: "REVIEW_RESULTS", action: "request_changes", to_state: "REQUEST_CHANGES", expects: { reason: "string" } },
];

const workflow = new SWPWorkflow("ci-cd-bot-v1", "INITIAL", transitions, "http://localhost:3000")
  .hint("INITIAL", "Start the CI-CD workflow. Use the 'start' action.")
  .hint("LINT", "Trigger the linter. Use the 'run_lint' action.")
  .hint("RUN_LINT", "Lint is running. Wait for stream or poll, then call 'lint_done' with passed (boolean) and issues (number).")
  .hint("REVIEW_RESULTS", "Review results. Use 'merge_ok' to approve or 'request_changes' with reason.")
  .hint("MERGE_OK", "Merge approved.")
  .hint("REQUEST_CHANGES", "Changes requested.")
  .skill("LINT", "lint-review-skill", "lint-review-skill/SKILL.md")
  .statusDefault("RUN_LINT", "processing");

const store: Record<string, { state: string; data: Record<string, unknown>; milestones: string[] }> = {};
const app = createApp(workflow, store);

// Serve skills from repo skills dir
const SKILLS_DIR = join(process.cwd(), "skills");
app.get("/skills/*", async (c) => {
  const path = c.req.path.replace(/^\/skills\//, "");
  const full = join(SKILLS_DIR, path);
  try {
    const content = readFileSync(full, "utf-8");
    return c.text(content, 200, {
      "Content-Type": "text/markdown",
    });
  } catch {
    return c.json({ error: "Not found" }, 404);
  }
});

const port = 3000;
console.log(`SWP CI-CD server at http://localhost:${port}`);
serve({ fetch: app.fetch, port });
