import { describe, expect, it } from "vitest";
import type { ScenarioDocument, ScenarioStep } from "./dsl";
import {
  docToFlow,
  duplicateStepAt,
  getChildSteps,
  getStepAt,
  insertStepAt,
  isContainerStep,
  listAt,
  moveStepAt,
  parsePathKey,
  pathKey,
  removeStepAt,
  START_NODE_ID,
  stepNodeId,
  updateStepAt,
  withChildSteps,
  wrapStepAt,
} from "./flow";

function sampleDoc(): ScenarioDocument {
  return {
    name: "test_scenario",
    node: "main_city",
    enabled: true,
    steps: [
      { click: "workers" },
      {
        while_match: "popup_close",
        max: 3,
        steps: [{ click: "popup_close" }, { wait: "500ms" }],
      },
      {
        loop: {
          max: 2,
          steps: [{ cond: "currentNode == main_city", steps: [{ wait: "1s" }] }],
        },
      },
      { wait: "2s" },
    ],
  };
}

describe("path helpers", () => {
  it("round-trips path keys", () => {
    expect(parsePathKey(pathKey([1, 2, 0]))).toEqual([1, 2, 0]);
    expect(parsePathKey("")).toEqual([]);
  });
});

describe("child steps access", () => {
  it("returns null for leaf steps", () => {
    expect(getChildSteps({ click: "x" })).toBeNull();
    expect(getChildSteps({ wait: "1s" })).toBeNull();
    expect(isContainerStep({ click: "x" })).toBe(false);
  });

  it("reads while_match/while_scroll/cond/group children from step.steps", () => {
    expect(getChildSteps({ while_match: "r", steps: [{ wait: "1s" }] })).toHaveLength(1);
    expect(getChildSteps({ cond: "g", steps: [] })).toEqual([]);
    expect(
      getChildSteps({ while_scroll: "r", direction: "up", steps: [{ exec: "f" }] }),
    ).toHaveLength(1);
    // Bare group (YAML-anchor helper): only `steps`, no action key.
    expect(getChildSteps({ steps: [{ wait: "1s" }] })).toHaveLength(1);
  });

  it("treats engine-only kinds as leaves with summaries (wait_screen, swipe, tap)", () => {
    expect(getChildSteps({ wait_screen: { any: ["main_city"], max: 8 } })).toBeNull();
    expect(getChildSteps({ tap: "region" })).toBeNull();
    const { nodes } = docToFlow({
      name: "x",
      steps: [{ wait_screen: { any: ["main_city", "main_menu"] } }],
    });
    const n = nodes.find((x) => x.id === stepNodeId([0]))!;
    expect(n.type).toBe("dslStep");
    expect(n.data).toMatchObject({ kind: "wait_screen", summary: "main_city | main_menu" });
  });

  it("reads loop/repeat children from the spec object", () => {
    expect(getChildSteps({ loop: { max: 2, steps: [{ wait: "1s" }] } })).toHaveLength(1);
    expect(getChildSteps({ loop: { max: 2 } })).toEqual([]);
    expect(getChildSteps({ repeat: 3 })).toEqual([]);
  });

  it("withChildSteps writes to the right place", () => {
    const wm = withChildSteps({ while_match: "r", max: 5 }, [{ wait: "1s" }]);
    expect(wm.steps).toHaveLength(1);

    const lp = withChildSteps({ loop: { max: 2 } }, [{ wait: "1s" }]);
    expect((lp.loop as Record<string, unknown>).max).toBe(2);
    expect((lp.loop as Record<string, unknown>).steps).toHaveLength(1);

    // Bare numeric loop spec is normalized into an object, keeping max.
    const bare = withChildSteps({ loop: 4 }, [{ wait: "1s" }]);
    expect((bare.loop as Record<string, unknown>).max).toBe(4);
  });
});

describe("document mutators", () => {
  it("getStepAt / listAt navigate nesting", () => {
    const doc = sampleDoc();
    expect(getStepAt(doc, [0])).toEqual({ click: "workers" });
    expect(getStepAt(doc, [1, 1])).toEqual({ wait: "500ms" });
    expect(getStepAt(doc, [2, 0, 0])).toEqual({ wait: "1s" });
    expect(getStepAt(doc, [9])).toBeNull();
    expect(listAt(doc, [1])).toHaveLength(2);
    expect(listAt(doc, [2, 0])).toHaveLength(1);
  });

  it("updateStepAt replaces a nested step without touching the original", () => {
    const doc = sampleDoc();
    const next = updateStepAt(doc, [1, 0], { click: "other" });
    expect(getStepAt(next, [1, 0])).toEqual({ click: "other" });
    expect(getStepAt(doc, [1, 0])).toEqual({ click: "popup_close" });
  });

  it("insertStepAt inserts at index, clamped", () => {
    const doc = sampleDoc();
    const next = insertStepAt(doc, [], 1, { wait: "9s" });
    expect(getStepAt(next, [1])).toEqual({ wait: "9s" });
    expect(listAt(next, [])).toHaveLength(5);

    const nested = insertStepAt(doc, [2, 0], 99, { wait: "9s" });
    expect(getStepAt(nested, [2, 0, 1])).toEqual({ wait: "9s" });
  });

  it("removeStepAt removes a nested step", () => {
    const doc = sampleDoc();
    const next = removeStepAt(doc, [1, 0]);
    expect(listAt(next, [1])).toEqual([{ wait: "500ms" }]);
    expect(listAt(doc, [1])).toHaveLength(2);
  });

  it("duplicateStepAt deep-copies the step after itself", () => {
    const doc = sampleDoc();
    const next = duplicateStepAt(doc, [1]);
    expect(listAt(next, [])).toHaveLength(5);
    expect(getStepAt(next, [2])).toEqual(getStepAt(next, [1]));
    expect(getStepAt(next, [2])).not.toBe(getStepAt(next, [1]));
  });

  it("wrapStepAt replaces the step with a container holding it", () => {
    const doc = sampleDoc();
    const next = wrapStepAt(doc, [0], "loop");
    expect(getStepAt(next, [0])).toEqual({
      loop: { max: 3, steps: [{ click: "workers" }] },
    });
    expect(listAt(next, [])).toHaveLength(4);

    const nested = wrapStepAt(doc, [1, 1], "cond");
    expect(getStepAt(nested, [1, 1])).toEqual({
      cond: "",
      steps: [{ wait: "500ms" }],
    });
    // Original untouched; out-of-range is a no-op.
    expect(getStepAt(doc, [0])).toEqual({ click: "workers" });
    expect(wrapStepAt(doc, [9], "loop")).toBe(doc);
  });

  it("moveStepAt swaps siblings and is a no-op at edges", () => {
    const doc = sampleDoc();
    const next = moveStepAt(doc, [0], 1);
    expect(getStepAt(next, [1])).toEqual({ click: "workers" });
    expect(moveStepAt(doc, [0], -1)).toBe(doc);
    expect(moveStepAt(doc, [3], 1)).toBe(doc);
  });
});

describe("docToFlow", () => {
  it("emits start node, one node per step, and sibling chain edges", () => {
    const { nodes, edges } = docToFlow(sampleDoc());
    const ids = nodes.map((n) => n.id);
    expect(ids[0]).toBe(START_NODE_ID);
    // 1 start + 4 root + 2 in while_match + 1 cond + 1 in cond = 9
    expect(nodes).toHaveLength(9);
    expect(ids).toContain(stepNodeId([2, 0, 0]));

    const pairs = edges.map((e) => `${e.source}->${e.target}`);
    expect(pairs).toContain(`${START_NODE_ID}->${stepNodeId([0])}`);
    expect(pairs).toContain(`${stepNodeId([0])}->${stepNodeId([1])}`);
    expect(pairs).toContain(`${stepNodeId([1, 0])}->${stepNodeId([1, 1])}`);
    // No edge from container into its first child (containment is visual).
    expect(pairs).not.toContain(`${stepNodeId([1])}->${stepNodeId([1, 0])}`);
    // start edge + 3 root chains + 1 while_match chain = 5
    expect(edges).toHaveLength(5);
  });

  it("orders parents before children and sets parentId/extent", () => {
    const { nodes } = docToFlow(sampleDoc());
    const ids = nodes.map((n) => n.id);
    const byId = new Map(nodes.map((n) => [n.id, n]));

    const child = byId.get(stepNodeId([1, 0]))!;
    expect(child.parentId).toBe(stepNodeId([1]));
    expect(child.extent).toBe("parent");
    expect(ids.indexOf(stepNodeId([1]))).toBeLessThan(ids.indexOf(stepNodeId([1, 0])));
    expect(ids.indexOf(stepNodeId([2, 0]))).toBeLessThan(
      ids.indexOf(stepNodeId([2, 0, 0])),
    );

    const root = byId.get(stepNodeId([0]))!;
    expect(root.parentId).toBeUndefined();
  });

  it("uses container/step node types and sizes containers around children", () => {
    const { nodes } = docToFlow(sampleDoc());
    const byId = new Map(nodes.map((n) => [n.id, n]));
    expect(byId.get(stepNodeId([0]))!.type).toBe("dslStep");
    expect(byId.get(stepNodeId([1]))!.type).toBe("dslContainer");
    expect(byId.get(stepNodeId([2]))!.type).toBe("dslContainer");
    expect(byId.get(stepNodeId([2, 0]))!.type).toBe("dslContainer");

    const outer = byId.get(stepNodeId([2]))!.style as { width: number; height: number };
    const inner = byId.get(stepNodeId([2, 0]))!.style as { width: number; height: number };
    expect(outer.width).toBeGreaterThan(inner.width);
    expect(outer.height).toBeGreaterThan(inner.height);
  });

  it("handles empty docs and empty containers", () => {
    const empty = docToFlow({ name: "x" });
    expect(empty.nodes).toHaveLength(1);
    expect(empty.edges).toHaveLength(0);

    const doc: ScenarioDocument = {
      name: "x",
      steps: [{ loop: { max: 1, steps: [] } } as ScenarioStep],
    };
    const { nodes } = docToFlow(doc);
    const container = nodes.find((n) => n.type === "dslContainer")!;
    expect((container.data as { childCount: number }).childCount).toBe(0);
    expect((container.style as { height: number }).height).toBeGreaterThan(0);
  });

  it("flags unknown regions/scenarios/execs and red-dot misuse when meta is given", () => {
    const meta = {
      regions: ["workers", "popup_close"],
      region_red_dot: ["workers"],
      exec_names: ["claim_all"],
      scenario_keys: ["other_scenario"],
      fsm_nodes: ["main_city"],
    };
    const doc: ScenarioDocument = {
      name: "x",
      node: "nope_city",
      steps: [
        { click: "missing_region" },
        { click: "" },
        { match: "popup_close", isRedDot: true },
        { match: "workers", isRedDot: true },
        { push_scenario: "ghost" },
        { exec: "ghost_fn" },
        { while_match: "missing_too", steps: [] },
      ],
    };
    const { nodes } = docToFlow(doc, meta);
    const issuesOf = (id: string) =>
      (nodes.find((n) => n.id === id)!.data as { issues: string[] }).issues;
    expect(issuesOf(START_NODE_ID)).toEqual(['unknown node "nope_city"']);
    expect(issuesOf(stepNodeId([0]))).toEqual(['unknown region "missing_region"']);
    expect(issuesOf(stepNodeId([1]))).toEqual(["region not set"]);
    expect(issuesOf(stepNodeId([2]))[0]).toMatch(/has no has_red_dot/);
    expect(issuesOf(stepNodeId([3]))).toEqual([]);
    expect(issuesOf(stepNodeId([4]))).toEqual(['unknown scenario "ghost"']);
    expect(issuesOf(stepNodeId([5]))).toEqual(['unknown exec "ghost_fn"']);
    expect(issuesOf(stepNodeId([6]))).toEqual(['unknown region "missing_too"']);
  });

  it("never flags ${var} template placeholders", () => {
    const meta = {
      regions: ["workers"],
      scenario_keys: ["a"],
      exec_names: ["b"],
      fsm_nodes: ["main_city"],
    };
    const doc: ScenarioDocument = {
      name: "x",
      node: "page.${tab}",
      steps: [
        { while_match: "page.backpack.${tab}", steps: [{ click: "${pointer}" }] },
        { push_scenario: "claim.${day}" },
        { exec: "scan_${panel}" },
      ],
    };
    const { nodes } = docToFlow(doc, meta);
    for (const n of nodes) {
      expect((n.data as { issues: string[] }).issues).toEqual([]);
    }
  });

  it("flags unknown step types", () => {
    const { nodes } = docToFlow(
      { name: "x", steps: [{ frobnicate: "yes" }] },
      { regions: ["r"] },
    );
    const n = nodes.find((x) => x.id === stepNodeId([0]))!;
    expect((n.data as { issues: string[] }).issues[0]).toMatch(/unknown step type/);
  });

  it("reports no issues without meta", () => {
    const { nodes } = docToFlow({ name: "x", steps: [{ click: "anything" }] });
    for (const n of nodes) {
      expect((n.data as { issues: string[] }).issues).toEqual([]);
    }
  });

  it("exposes region/badge metadata on step nodes", () => {
    const doc: ScenarioDocument = {
      name: "x",
      steps: [
        { click: "workers", cond: "currentNode == main_city", isRedDot: true },
        { exec: "fn" },
      ],
    };
    const { nodes } = docToFlow(doc);
    const click = nodes.find((n) => n.id === stepNodeId([0]))!;
    expect(click.data).toMatchObject({
      kind: "click",
      region: "workers",
      cond: "currentNode == main_city",
      redDot: true,
      index: 1,
    });
    const exec = nodes.find((n) => n.id === stepNodeId([1]))!;
    expect(exec.data).toMatchObject({ kind: "exec", region: null, redDot: null });
  });
});
