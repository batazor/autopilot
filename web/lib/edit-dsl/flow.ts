/** Scenario document ↔ React Flow projection.
 *
 * The DSL is an ordered, nested list of steps — not a free-form graph — so the
 * canvas is a pure projection: nodes/edges are derived from the document on
 * every change, and all mutations go through path-based helpers that return a
 * new document. Node positions are never persisted.
 */

import { MarkerType, type Edge, type Node } from "@xyflow/react";
import {
  detectStepType,
  stepSummary,
  type ScenarioDocument,
  type ScenarioStep,
} from "./dsl";

export type StepPath = number[];

export const START_NODE_ID = "start";

export const FLOW_NODE_W = 260;
export const FLOW_NODE_H = 76;
export const FLOW_START_H = 86;
const GAP = 28;
const PAD = 14;
const HEADER_H = 48;
const EMPTY_H = 44;

/** Step kinds whose primary value is a region name (previewable). */
const REGION_KINDS = new Set(["click", "long_click", "match", "ocr"]);

/** Subset of the editor meta used for canvas-side validation. Structurally
 *  compatible with `EditorMeta` (components/edit-dsl/StepCard). */
export type FlowMeta = {
  regions?: string[];
  region_red_dot?: string[];
  exec_names?: string[];
  scenario_keys?: string[];
  fsm_nodes?: string[];
};

type MetaSets = {
  regions: Set<string> | null;
  redDot: Set<string> | null;
  execs: Set<string> | null;
  scenarios: Set<string> | null;
  nodes: Set<string> | null;
};

function metaSets(meta?: FlowMeta): MetaSets {
  const toSet = (v?: string[]) => (Array.isArray(v) && v.length ? new Set(v) : null);
  return {
    regions: toSet(meta?.regions),
    redDot: toSet(meta?.region_red_dot),
    execs: toSet(meta?.exec_names),
    scenarios: toSet(meta?.scenario_keys),
    nodes: toSet(meta?.fsm_nodes),
  };
}

/** Template scenarios (`*.{var}.yaml`) reference regions/keys via `${var}`
 *  placeholders that only resolve at load time — never flag those. */
function isTemplated(value: string): boolean {
  return value.includes("${");
}

export function pushScenarioName(step: ScenarioStep): string {
  const ps = step.push_scenario;
  if (ps && typeof ps === "object" && !Array.isArray(ps)) {
    return String((ps as Record<string, unknown>).name ?? "").trim();
  }
  return String(ps ?? "").trim();
}

/** Static problems with a step that the canvas should surface (unknown
 *  region/scenario/exec, red-dot filter on a region without a badge). */
function stepIssues(step: ScenarioStep, kind: string, sets: MetaSets): string[] {
  const issues: string[] = [];
  if (REGION_KINDS.has(kind) || kind === "while_match" || kind === "while_scroll") {
    const region = String(step[kind] ?? "").trim();
    if (!region) {
      issues.push("region not set");
    } else if (sets.regions && !sets.regions.has(region) && !isTemplated(region)) {
      issues.push(`unknown region "${region}"`);
    } else if (
      step.isRedDot !== undefined &&
      sets.redDot &&
      !sets.redDot.has(region)
    ) {
      issues.push(`isRedDot filter, but "${region}" has no has_red_dot in area`);
    }
  }
  if (kind === "push_scenario") {
    const name = pushScenarioName(step);
    if (!name) issues.push("scenario key not set");
    else if (sets.scenarios && !sets.scenarios.has(name) && !isTemplated(name)) {
      issues.push(`unknown scenario "${name}"`);
    }
  }
  if (kind === "exec") {
    const fn = String(step.exec ?? "").trim();
    if (!fn) issues.push("exec function not set");
    else if (sets.execs && !sets.execs.has(fn) && !isTemplated(fn)) {
      issues.push(`unknown exec "${fn}"`);
    }
  }
  if (kind === "?") {
    issues.push(`unknown step type (keys: ${Object.keys(step).join(", ")})`);
  }
  return issues;
}

export function pathKey(path: StepPath): string {
  return path.join("/");
}

export function parsePathKey(key: string): StepPath {
  if (!key) return [];
  return key.split("/").map((p) => parseInt(p, 10));
}

export function stepNodeId(path: StepPath): string {
  return `s:${pathKey(path)}`;
}

/** Inner steps of a container step, or null for leaf steps.
 *  `loop`/`repeat` nest under their spec object; `while_match`/`cond` nest
 *  under the step itself. */
export function getChildSteps(step: ScenarioStep): ScenarioStep[] | null {
  const kind = detectStepType(step);
  if (kind === "loop" || kind === "repeat") {
    const spec = step[kind];
    if (spec && typeof spec === "object" && !Array.isArray(spec)) {
      const s = (spec as Record<string, unknown>).steps;
      return Array.isArray(s) ? (s as ScenarioStep[]) : [];
    }
    return [];
  }
  if (kind === "while_match" || kind === "while_scroll" || kind === "cond" || kind === "group") {
    return Array.isArray(step.steps) ? (step.steps as ScenarioStep[]) : [];
  }
  return null;
}

export function isContainerStep(step: ScenarioStep): boolean {
  return getChildSteps(step) !== null;
}

/** Returns a copy of `step` with its inner steps replaced. */
export function withChildSteps(
  step: ScenarioStep,
  children: ScenarioStep[],
): ScenarioStep {
  const kind = detectStepType(step);
  if (kind === "loop" || kind === "repeat") {
    const spec = step[kind];
    const base =
      spec && typeof spec === "object" && !Array.isArray(spec)
        ? (spec as Record<string, unknown>)
        : { max: typeof spec === "number" ? spec : 1 };
    return { ...step, [kind]: { ...base, steps: children } };
  }
  return { ...step, steps: children };
}

// ---------------------------------------------------------------------------
// Path-based document mutators (pure — always return a new document).
// ---------------------------------------------------------------------------

function rebuildList(
  steps: ScenarioStep[],
  parentPath: StepPath,
  fn: (steps: ScenarioStep[]) => ScenarioStep[],
): ScenarioStep[] {
  if (!parentPath.length) return fn(steps);
  const [i, ...rest] = parentPath;
  const cur = steps[i];
  if (!cur) return steps;
  const children = getChildSteps(cur) ?? [];
  const rebuilt = rebuildList(children, rest, fn);
  if (rebuilt === children) return steps;
  const out = [...steps];
  out[i] = withChildSteps(cur, rebuilt);
  return out;
}

/** Apply `fn` to the steps list of the container at `parentPath` ([] = root). */
export function updateStepsAt(
  doc: ScenarioDocument,
  parentPath: StepPath,
  fn: (steps: ScenarioStep[]) => ScenarioStep[],
): ScenarioDocument {
  const root = Array.isArray(doc.steps) ? doc.steps : [];
  const next = rebuildList(root, parentPath, fn);
  return next === root ? doc : { ...doc, steps: next };
}

/** Steps list of the container at `parentPath` ([] = root list). */
export function listAt(doc: ScenarioDocument, parentPath: StepPath): ScenarioStep[] {
  let cur = Array.isArray(doc.steps) ? doc.steps : [];
  for (const i of parentPath) {
    const s = cur[i];
    if (!s) return [];
    cur = getChildSteps(s) ?? [];
  }
  return cur;
}

export function getStepAt(doc: ScenarioDocument, path: StepPath): ScenarioStep | null {
  if (!path.length) return null;
  return listAt(doc, path.slice(0, -1))[path[path.length - 1]] ?? null;
}

export function updateStepAt(
  doc: ScenarioDocument,
  path: StepPath,
  step: ScenarioStep,
): ScenarioDocument {
  return updateStepsAt(doc, path.slice(0, -1), (list) => {
    const i = path[path.length - 1];
    if (i < 0 || i >= list.length) return list;
    const out = [...list];
    out[i] = step;
    return out;
  });
}

export function insertStepAt(
  doc: ScenarioDocument,
  parentPath: StepPath,
  index: number,
  step: ScenarioStep,
): ScenarioDocument {
  return updateStepsAt(doc, parentPath, (list) => {
    const i = Math.max(0, Math.min(index, list.length));
    const out = [...list];
    out.splice(i, 0, step);
    return out;
  });
}

export function removeStepAt(doc: ScenarioDocument, path: StepPath): ScenarioDocument {
  return updateStepsAt(doc, path.slice(0, -1), (list) =>
    list.filter((_, i) => i !== path[path.length - 1]),
  );
}

export function duplicateStepAt(doc: ScenarioDocument, path: StepPath): ScenarioDocument {
  return updateStepsAt(doc, path.slice(0, -1), (list) => {
    const i = path[path.length - 1];
    if (!list[i]) return list;
    const copy = JSON.parse(JSON.stringify(list[i])) as ScenarioStep;
    const out = [...list];
    out.splice(i + 1, 0, copy);
    return out;
  });
}

export const WRAP_KINDS = ["while_match", "while_scroll", "loop", "cond"] as const;
export type WrapKind = (typeof WRAP_KINDS)[number];

/** Replace the step at `path` with a new container holding it as the only
 *  inner step — the "wrap in loop/cond/while_*" refactor. */
export function wrapStepAt(
  doc: ScenarioDocument,
  path: StepPath,
  kind: WrapKind,
): ScenarioDocument {
  return updateStepsAt(doc, path.slice(0, -1), (list) => {
    const i = path[path.length - 1];
    const cur = list[i];
    if (!cur) return list;
    let wrapper: ScenarioStep;
    switch (kind) {
      case "loop":
        wrapper = { loop: { max: 3, steps: [cur] } };
        break;
      case "while_match":
        wrapper = { while_match: "", max: 5, steps: [cur] };
        break;
      case "while_scroll":
        wrapper = { while_scroll: "", direction: "up", delta: 400, max: 6, steps: [cur] };
        break;
      default:
        wrapper = { cond: "", steps: [cur] };
    }
    const out = [...list];
    out[i] = wrapper;
    return out;
  });
}

/** Swap a step with its sibling (`delta` = -1 / +1). Out of range → no-op. */
export function moveStepAt(
  doc: ScenarioDocument,
  path: StepPath,
  delta: number,
): ScenarioDocument {
  return updateStepsAt(doc, path.slice(0, -1), (list) => {
    const i = path[path.length - 1];
    const j = i + delta;
    if (i < 0 || i >= list.length || j < 0 || j >= list.length) return list;
    const out = [...list];
    [out[i], out[j]] = [out[j], out[i]];
    return out;
  });
}

// ---------------------------------------------------------------------------
// Document → React Flow nodes/edges.
// ---------------------------------------------------------------------------

export type DslStartNodeData = {
  name: string;
  node: string;
  cron: string;
  enabled: boolean;
  deviceLevel: boolean;
  stepCount: number;
  issues: string[];
  [key: string]: unknown;
};

export type DslStepNodeData = {
  kind: string;
  summary: string;
  cond: string;
  region: string | null;
  redDot: boolean | null;
  index: number;
  pathKey: string;
  issues: string[];
  [key: string]: unknown;
};

export type DslContainerNodeData = {
  kind: string;
  title: string;
  detail: string;
  cond: string;
  region: string | null;
  childCount: number;
  index: number;
  pathKey: string;
  issues: string[];
  [key: string]: unknown;
};

function stepNodeData(
  step: ScenarioStep,
  kind: string,
  path: StepPath,
  sets: MetaSets,
): DslStepNodeData {
  const region = REGION_KINDS.has(kind) ? String(step[kind] ?? "").trim() : "";
  return {
    kind,
    summary: stepSummary(step),
    cond: String(step.cond ?? "").trim(),
    region: region || null,
    redDot: typeof step.isRedDot === "boolean" ? step.isRedDot : null,
    index: path[path.length - 1] + 1,
    pathKey: pathKey(path),
    issues: stepIssues(step, kind, sets),
  };
}

function containerNodeData(
  step: ScenarioStep,
  kind: string,
  path: StepPath,
  childCount: number,
  sets: MetaSets,
): DslContainerNodeData {
  let title = "";
  let detail = "";
  let region = "";
  if (kind === "while_match") {
    region = String(step.while_match ?? "").trim();
    title = region;
    detail = `max ${Number(step.max ?? 5)}`;
  } else if (kind === "while_scroll") {
    region = String(step.while_scroll ?? "").trim();
    title = region;
    const dir = String(step.direction ?? "").trim();
    detail = `${dir ? `${dir} · ` : ""}max ${Number(step.max ?? 6)}`;
  } else if (kind === "group") {
    title = "(inline group)";
  } else if (kind === "loop" || kind === "repeat") {
    const spec = step[kind];
    const max =
      spec && typeof spec === "object" && !Array.isArray(spec)
        ? (spec as Record<string, unknown>).max
        : spec;
    detail = `max ${Number(max ?? 1)}`;
  } else if (kind === "cond") {
    title = String(step.cond ?? "").trim() || "(no guard)";
  }
  return {
    kind,
    title,
    detail,
    cond: kind === "cond" ? "" : String(step.cond ?? "").trim(),
    region: region || null,
    childCount,
    index: path[path.length - 1] + 1,
    pathKey: pathKey(path),
    issues: stepIssues(step, kind, sets),
  };
}

function chainEdge(source: string, target: string): Edge {
  return {
    id: `e:${source}->${target}`,
    source,
    target,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
  };
}

type FlowAcc = { nodes: Node[]; edges: Edge[]; sets: MetaSets };

/** Stack `steps` vertically starting at (x0, y0) in the parent's coordinate
 *  space; containers recurse with their children positioned relative to the
 *  container node. Returns the bounding size of the stacked list. */
function layoutList(
  steps: ScenarioStep[],
  parentPath: StepPath,
  parentId: string | undefined,
  x0: number,
  y0: number,
  acc: FlowAcc,
): { width: number; height: number } {
  let y = y0;
  let maxW = 0;
  steps.forEach((step, i) => {
    const path = [...parentPath, i];
    const id = stepNodeId(path);
    const kind = detectStepType(step);
    const children = getChildSteps(step);
    if (i > 0) acc.edges.push(chainEdge(stepNodeId([...parentPath, i - 1]), id));
    const common = {
      id,
      draggable: false,
      ...(parentId ? { parentId, extent: "parent" as const } : {}),
    };
    if (children !== null) {
      // React Flow requires parents before children in the array — reserve the
      // slot, lay out the subtree, then fill in the container node.
      const slot = acc.nodes.length;
      acc.nodes.push(undefined as unknown as Node);
      const inner = layoutList(children, path, id, PAD, HEADER_H, acc);
      const w = Math.max(FLOW_NODE_W, inner.width + PAD * 2);
      const h = HEADER_H + (children.length ? inner.height : EMPTY_H) + PAD;
      acc.nodes[slot] = {
        ...common,
        type: "dslContainer",
        position: { x: x0, y },
        data: containerNodeData(step, kind, path, children.length, acc.sets),
        style: { width: w, height: h },
      };
      maxW = Math.max(maxW, w);
      y += h + GAP;
    } else {
      acc.nodes.push({
        ...common,
        type: "dslStep",
        position: { x: x0, y },
        data: stepNodeData(step, kind, path, acc.sets),
        style: { width: FLOW_NODE_W, height: FLOW_NODE_H },
      });
      maxW = Math.max(maxW, FLOW_NODE_W);
      y += FLOW_NODE_H + GAP;
    }
  });
  return { width: maxW, height: steps.length ? y - y0 - GAP : 0 };
}

export function docToFlow(
  doc: ScenarioDocument,
  meta?: FlowMeta,
): { nodes: Node[]; edges: Edge[] } {
  const steps = Array.isArray(doc.steps) ? doc.steps : [];
  const sets = metaSets(meta);
  const acc: FlowAcc = { nodes: [], edges: [], sets };
  const node = String(doc.node ?? "").trim();
  const startIssues: string[] = [];
  if (node && sets.nodes && !sets.nodes.has(node) && !isTemplated(node)) {
    startIssues.push(`unknown node "${node}"`);
  }
  acc.nodes.push({
    id: START_NODE_ID,
    type: "dslStart",
    position: { x: 0, y: 0 },
    draggable: false,
    data: {
      name: String(doc.name ?? "").trim(),
      node,
      cron: String(doc.cron ?? "").trim(),
      enabled: Boolean(doc.enabled),
      deviceLevel: Boolean(doc.device_level),
      stepCount: steps.length,
      issues: startIssues,
    } satisfies DslStartNodeData,
    style: { width: FLOW_NODE_W, height: FLOW_START_H },
  });
  layoutList(steps, [], undefined, 0, FLOW_START_H + GAP, acc);
  if (steps.length) acc.edges.push(chainEdge(START_NODE_ID, stepNodeId([0])));
  return acc;
}
