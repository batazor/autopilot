/**
 * Heuristic timeline estimator for DSL scenario YAML.
 *
 * The estimator scans the raw YAML text line-by-line and assigns a duration to
 * each top-level step ("- <key>: ..."). It is intentionally approximate —
 * meant to give the editor a feel for how long a scenario will take to run.
 */

export type TimelineEstimate = {
  totalMs: number;
  /** Cumulative milliseconds from start at the end of each step (line → ms). */
  perLine: Map<number, number>;
};

/** Heuristic durations for non-wait steps, in milliseconds. */
const STEP_HEURISTIC_MS: Record<string, number> = {
  click: 200,
  long_click: 800,
  match: 300,
  while_match: 500,
  ocr: 500,
  exec: 300,
  swipe_direction: 600,
  push_scenario: 100,
  loop: 0,
  repeat: 0,
  cond: 0,
  break: 0,
};

export function parseDurationMs(value: string): number {
  const m = (value || "").trim().match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|h)?$/i);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  const unit = (m[2] || "s").toLowerCase();
  switch (unit) {
    case "ms":
      return n;
    case "s":
      return n * 1000;
    case "m":
      return n * 60_000;
    case "h":
      return n * 3_600_000;
    default:
      return 0;
  }
}

export function estimateTimeline(yamlText: string): TimelineEstimate {
  const perLine = new Map<number, number>();
  let total = 0;
  const lines = yamlText.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(\s*)-\s+(\w+)\s*:\s*(.*)$/);
    if (!m) continue;
    const key = m[2];
    const rest = m[3].trim().replace(/#.*$/, "").trim();
    let add = 0;
    if (key === "wait") {
      add = parseDurationMs(rest);
    } else if (key in STEP_HEURISTIC_MS) {
      add = STEP_HEURISTIC_MS[key];
    }
    total += add;
    perLine.set(i + 1, total);
  }
  return { totalMs: total, perLine };
}

/** "14 sec", "1 min 30 sec", "2 h 5 min". */
export function formatDuration(ms: number): string {
  if (ms <= 0) return "0 sec";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} sec`;
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  if (min < 60) return s ? `${min} min ${s} sec` : `${min} min`;
  const hr = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${hr} h ${m} min` : `${hr} h`;
}

/** "0:07", "1:23", "10:00". */
export function formatTimestamp(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  return `${min}:${String(s).padStart(2, "0")}`;
}
