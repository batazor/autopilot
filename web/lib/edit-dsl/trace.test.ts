import { describe, expect, it } from "vitest";
import type { ScenarioDocument } from "./dsl";
import { traceIndexToPath, traceToNodeStatuses } from "./trace";

function doc(): ScenarioDocument {
  return {
    name: "t",
    steps: [
      { click: "a" },
      {
        while_match: "popup",
        max: 3,
        steps: [{ click: "popup" }, { wait: "500ms" }],
      },
      {
        loop: {
          max: 2,
          steps: [{ cond: "x == 1", steps: [{ wait: "1s" }] }],
        },
      },
      { wait: "2s" },
    ],
  };
}

describe("traceIndexToPath", () => {
  it("maps top-level indices", () => {
    expect(traceIndexToPath(doc(), "0")).toEqual([0]);
    expect(traceIndexToPath(doc(), "3")).toEqual([3]);
  });

  it("maps loop-iteration children (N.iter.M)", () => {
    expect(traceIndexToPath(doc(), "1.0.0")).toEqual([1, 0]);
    expect(traceIndexToPath(doc(), "1.2.1")).toEqual([1, 1]);
    // Nested: loop 2 → iter 1 → cond 0 → (separator-less or iter) → wait 0.
    expect(traceIndexToPath(doc(), "2.1.0.0")).toEqual([2, 0, 0]);
  });

  it("tolerates a separator-less child component", () => {
    expect(traceIndexToPath(doc(), "1.0")).toEqual([1, 0]);
  });

  it("returns null for unmappable rows", () => {
    expect(traceIndexToPath(doc(), "9")).toBeNull();
    expect(traceIndexToPath(doc(), "0.0.0")).toBeNull(); // leaf has no children
    expect(traceIndexToPath(doc(), "1.0.7")).toBeNull(); // child out of range
    expect(traceIndexToPath(doc(), "else")).toBeNull();
  });
});

describe("traceToNodeStatuses", () => {
  it("buckets statuses and keeps the last row per node", () => {
    const map = traceToNodeStatuses(doc(), [
      { i: "0", status: "ok", duration_ms: 120 },
      { i: "1.0", status: "iter", summary: "iter 0" },
      { i: "1.0.0", status: "ok", match_score: 0.91 },
      { i: "1.1.0", status: "failed", reason: "match_region_not_found" },
      { i: "1", status: "ok" },
      { i: "3", status: "skipped", reason: "cond_false" },
      { i: "9.9", status: "ok" },
    ]);
    expect(map.get("0")).toMatchObject({ bucket: "ok", durationMs: 120 });
    // Later row for the same node wins (final iteration outcome).
    expect(map.get("1/0")).toMatchObject({
      bucket: "failed",
      reason: "match_region_not_found",
    });
    expect(map.get("1")).toMatchObject({ bucket: "ok" });
    expect(map.get("3")).toMatchObject({ bucket: "skipped", reason: "cond_false" });
    expect(map.has("1/0/0")).toBe(false);
    expect(map.size).toBe(4);
  });

  it("ignores iter markers and unknown statuses", () => {
    const map = traceToNodeStatuses(doc(), [
      { i: "1.0", status: "iter" },
      { i: "0", status: "weird_status" },
    ]);
    expect(map.size).toBe(0);
  });
});
