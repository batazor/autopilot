/** Map a scenario run's `steps_trace` (queue history metadata) onto canvas
 *  step paths, so the flow editor can color nodes by their last-run outcome.
 *
 *  Trace row `i` format (see src/tasks/dsl_persist_mixin.py):
 *  - `"N"` — top-level step N (terminal status for the whole step).
 *  - `"N.K.M"` — child M of container N; K is the loop-iteration index for
 *    `while_match`/`while_scroll`/`loop`/`repeat`, or a branch label (e.g.
 *    `else`) for branching steps. Deeper nesting repeats the pattern.
 *  - `"N.K"` with status `"iter"` — iteration marker, not a real step.
 */

import type { ScenarioDocument, ScenarioStep } from "./dsl";
import { getChildSteps, pathKey, type StepPath } from "./flow";

export type TraceBucket = "ok" | "failed" | "skipped";

export type NodeTraceStatus = {
  bucket: TraceBucket;
  /** Raw engine status (`ok`, `failed`, `stopped`, `skipped`, …). */
  status: string;
  reason: string;
  matchScore: number | null;
  ocrValue: string;
  durationMs: number | null;
};

export type TraceRow = Record<string, unknown>;

const OK_STATUSES = new Set(["ok", "early_exit"]);
const FAILED_STATUSES = new Set(["failed", "stopped", "error"]);

function bucketFor(status: string): TraceBucket | null {
  if (OK_STATUSES.has(status)) return "ok";
  if (FAILED_STATUSES.has(status)) return "failed";
  if (status.startsWith("skipped") || status === "preempted") return "skipped";
  return null;
}

/** Resolve a trace `i` index to a canvas step path, or null when the row
 *  doesn't correspond to a canvas node (iteration markers, engine-only branch
 *  forms like `match`+`else`, stale traces after the doc was edited). */
export function traceIndexToPath(
  doc: ScenarioDocument,
  traceIndex: string,
): StepPath | null {
  const parts = String(traceIndex).split(".");
  let steps: ScenarioStep[] = Array.isArray(doc.steps) ? doc.steps : [];
  const path: StepPath = [];
  let k = 0;
  while (k < parts.length) {
    const idx = Number(parts[k]);
    if (!Number.isInteger(idx) || idx < 0 || idx >= steps.length) return null;
    path.push(idx);
    const step = steps[idx];
    k += 1;
    if (k >= parts.length) return path;
    const children = getChildSteps(step);
    if (children === null) return null;
    // Containers insert one separator component (iteration index or branch
    // label) before the child index; tolerate a separator-less `N.M` form
    // when only one component remains.
    if (k + 1 < parts.length) k += 1;
    steps = children;
  }
  return path;
}

/** Fold trace rows into a per-node status map (later rows win, so loops end
 *  up showing their final iteration's outcome). Keys are canvas path keys. */
export function traceToNodeStatuses(
  doc: ScenarioDocument,
  rows: TraceRow[],
): Map<string, NodeTraceStatus> {
  const out = new Map<string, NodeTraceStatus>();
  for (const row of rows) {
    const status = String(row.status ?? "").trim();
    if (!status || status === "iter") continue;
    const bucket = bucketFor(status);
    if (!bucket) continue;
    const path = traceIndexToPath(doc, String(row.i ?? ""));
    if (!path) continue;
    const score = Number(row.match_score);
    const duration = Number(row.duration_ms);
    out.set(pathKey(path), {
      bucket,
      status,
      reason: String(row.reason ?? "").trim(),
      matchScore: Number.isFinite(score) ? score : null,
      ocrValue: String(row.ocr_value ?? "").trim(),
      durationMs: Number.isFinite(duration) ? duration : null,
    });
  }
  return out;
}

export function traceStatusTip(s: NodeTraceStatus): string {
  const parts = [s.status];
  if (s.reason) parts.push(s.reason);
  if (s.matchScore !== null) parts.push(`score ${s.matchScore.toFixed(3)}`);
  if (s.ocrValue) parts.push(`ocr "${s.ocrValue}"`);
  if (s.durationMs !== null) parts.push(`${s.durationMs} ms`);
  return parts.join(" · ");
}
