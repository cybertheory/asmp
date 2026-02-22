import type { TransitionDef } from "./models.js";

export function visualizeFsm(
  workflowId: string,
  initialState: string,
  transitions: TransitionDef[],
  currentState: string | null = null
): string {
  const lines = [
    "flowchart LR",
    `    start([Start]) --> ${initialState}`,
  ];
  const seen = new Set<string>([initialState]);
  for (const t of transitions) {
    seen.add(t.from_state);
    seen.add(t.to_state);
    const label = t.is_critical ? `${t.action} *` : t.action;
    lines.push(`    ${t.from_state} -->|${label}| ${t.to_state}`);
  }
  for (const s of seen) {
    if (["completed", "failed", "COMPLETED", "FAILED"].includes(s)) {
      lines.push(`    ${s}([${s}])`);
    }
  }
  if (currentState) {
    lines.push("    classDef current fill:#90EE90,stroke:#333");
    lines.push(`    class ${currentState} current`);
  }
  return lines.join("\n");
}
