/** Module DSL editor helpers (mirrors Streamlit edit_scenarios view). */

/** Mirrors `DSL_ACTION_KEYS` in src/dsl/dsl_schema.py (canonical list). */
export const DSL_ACTION_KEYS = [
  "click",
  "long_click",
  "match",
  "while_match",
  "while_scroll",
  "ocr",
  "swipe_direction",
  "swipe",
  "tap",
  "push_scenario",
  "exec",
  "wait",
  "wait_screen",
  "ttl",
  "repeat",
  "loop",
  "break",
  "system_back",
] as const;

export const STEP_TYPES_FOR_NEW = [
  "click",
  "match",
  "while_match",
  "while_scroll",
  "wait",
  "ocr",
  "exec",
  "push_scenario",
  "swipe_direction",
  "loop",
  "cond",
  "long_click",
] as const;

export const LOOP_PARENT_KINDS = new Set([
  "loop",
  "repeat",
  "while_match",
  "while_scroll",
]);

export const SWIPE_DIRECTIONS = ["up", "down", "left", "right"] as const;

export type ScenarioDocument = Record<string, unknown> & {
  name?: string;
  node?: string;
  cond?: string;
  icon?: string;
  enabled?: boolean;
  device_level?: boolean;
  priority?: number;
  cron?: string;
  steps?: ScenarioStep[];
};

export type ScenarioStep = Record<string, unknown>;

export function detectStepType(step: ScenarioStep): string {
  for (const k of DSL_ACTION_KEYS) {
    if (k in step && step[k] != null) return k;
  }
  if ("cond" in step && "steps" in step) return "cond";
  // Bare group: only `steps`, no action key — the runtime inlines the inner
  // steps (used for YAML anchors pointing at step lists).
  if (Array.isArray(step.steps)) return "group";
  return "?";
}

export function newStep(stepType: string): ScenarioStep {
  switch (stepType) {
    case "wait":
      return { wait: "1s" };
    case "while_scroll":
      return { while_scroll: "", direction: "up", delta: 400, max: 6, steps: [] };
    case "click":
      return { click: "" };
    case "long_click":
      return { long_click: "", wait: "800ms" };
    case "match":
      return { match: "" };
    case "while_match":
      return { while_match: "", max: 5, steps: [] };
    case "ocr":
      return { ocr: "" };
    case "exec":
      return { exec: "" };
    case "push_scenario":
      return { push_scenario: "" };
    case "swipe_direction":
      return { swipe_direction: { direction: "up", delta: 400, duration_ms: 600 } };
    case "loop":
      return { loop: { max: 3, steps: [] } };
    case "cond":
      return { cond: "", steps: [] };
    case "break":
      return { break: "loop" };
    default:
      return { [stepType]: "" };
  }
}

export function stepSummary(step: ScenarioStep): string {
  const stype = detectStepType(step);
  switch (stype) {
    case "click":
      return String(step.click ?? "");
    case "long_click":
      return String(step.long_click ?? "");
    case "match":
      return String(step.match ?? "");
    case "while_match":
      return String(step.while_match ?? "");
    case "while_scroll": {
      const dir = String(step.direction ?? "").trim();
      return `${String(step.while_scroll ?? "")}${dir ? ` (${dir})` : ""}`;
    }
    case "wait_screen": {
      const spec = step.wait_screen;
      if (spec && typeof spec === "object" && !Array.isArray(spec)) {
        const anyOf = (spec as Record<string, unknown>).any;
        if (Array.isArray(anyOf)) return anyOf.map(String).join(" | ");
      }
      return String(spec ?? "");
    }
    case "swipe":
    case "tap": {
      const spec = step[stype];
      if (spec && typeof spec === "object" && !Array.isArray(spec)) {
        const s = spec as Record<string, unknown>;
        return Object.entries(s)
          .map(([k, v]) => `${k}=${String(v)}`)
          .join(" ");
      }
      return String(spec ?? "");
    }
    case "group": {
      const inner = Array.isArray(step.steps) ? step.steps : [];
      return `steps=${inner.length}`;
    }
    case "ocr":
      return String(step.ocr ?? "");
    case "exec":
      return String(step.exec ?? "");
    case "push_scenario": {
      const ps = step.push_scenario;
      if (ps && typeof ps === "object" && !Array.isArray(ps)) {
        return String((ps as Record<string, unknown>).name ?? "");
      }
      return String(ps ?? "");
    }
    case "wait":
      return String(step.wait ?? "");
    case "break":
      return String(step.break ?? "");
    case "swipe_direction": {
      const spec = step.swipe_direction;
      if (spec && typeof spec === "object" && !Array.isArray(spec)) {
        return String((spec as Record<string, unknown>).direction ?? "");
      }
      return String(spec ?? "");
    }
    case "loop":
    case "repeat": {
      const spec = step[stype];
      if (spec && typeof spec === "object" && !Array.isArray(spec)) {
        const s = spec as Record<string, unknown>;
        const inner = Array.isArray(s.steps) ? s.steps.length : 0;
        return `max=${s.max} inner=${inner}`;
      }
      return String(spec ?? "");
    }
    case "cond": {
      const inner = Array.isArray(step.steps) ? step.steps : [];
      return String(step.cond ?? "").trim() || `steps=${inner.length}`;
    }
    default:
      return "";
  }
}

export function ensureStepsList(doc: ScenarioDocument): ScenarioStep[] {
  if (!Array.isArray(doc.steps)) doc.steps = [];
  return doc.steps;
}

export function cloneDocument(doc: ScenarioDocument): ScenarioDocument {
  return JSON.parse(JSON.stringify(doc)) as ScenarioDocument;
}
