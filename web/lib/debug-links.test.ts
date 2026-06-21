import { describe, expect, it } from "vitest";

import { editDslHref, regionFromQueueHistory } from "./debug-links";
import type { QueueHistoryRow } from "./types";

function row(overrides: Partial<QueueHistoryRow>): QueueHistoryRow {
  return {
    task_id: "t1",
    scenario: "scn",
    scenario_key: "scn",
    player_id: "p1",
    instance_id: "i1",
    priority: 0,
    started_at: 0,
    finished_at: 0,
    duration_s: 0,
    success: false,
    region: "",
    reason: "",
    steps: "",
    trace_id: "",
    tempo_trace_url: "",
    steps_trace: null,
    ...overrides,
  };
}

describe("regionFromQueueHistory", () => {
  it("returns the direct region when set", () => {
    expect(regionFromQueueHistory(row({ region: "claim_button" }))).toBe(
      "claim_button",
    );
  });

  it("treats '—' and whitespace as empty", () => {
    expect(regionFromQueueHistory(row({ region: "—" }))).toBe("");
    expect(regionFromQueueHistory(row({ region: "   " }))).toBe("");
  });

  it("returns '' when no region and no trace", () => {
    expect(regionFromQueueHistory(row({ steps_trace: null }))).toBe("");
    expect(regionFromQueueHistory(row({ steps_trace: [] }))).toBe("");
  });

  it("returns the last failing step's region from the trace", () => {
    const r = regionFromQueueHistory(
      row({
        region: "",
        steps_trace: [
          { status: "ok", region: "first_ok" },
          { status: "failed", region: "early_fail" },
          { status: "ok", region: "later_ok" },
          { status: "error", region: "last_fail" },
          { status: "ok", region: "tail_ok" },
        ],
      }),
    );
    expect(r).toBe("last_fail");
  });

  it("skips failing steps with empty region and keeps walking back", () => {
    const r = regionFromQueueHistory(
      row({
        steps_trace: [
          { status: "failed", region: "earlier_fail" },
          { status: "error", region: "" },
        ],
      }),
    );
    expect(r).toBe("earlier_fail");
  });

  it("falls back to last region when every step is OK/skipped/empty", () => {
    const r = regionFromQueueHistory(
      row({
        steps_trace: [
          { status: "ok", region: "first" },
          { status: "skipped", region: "middle" },
          { status: "success", region: "last_ok" },
        ],
      }),
    );
    expect(r).toBe("last_ok");
  });

  it("ignores empty-status steps in the failure pass but uses them on fallback", () => {
    const r = regionFromQueueHistory(
      row({
        steps_trace: [
          { region: "no_status_first" },
          { region: "no_status_last" },
        ],
      }),
    );
    expect(r).toBe("no_status_last");
  });
});

describe("editDslHref", () => {
  it("emits scope + module + scenario params", () => {
    expect(editDslHref({ module: "core/popup", scenario: "dismiss.yaml" })).toBe(
      "/edit-dsl?scope=core%2Fpopup&module=core%2Fpopup&scenario=dismiss.yaml",
    );
  });

  it("adds new=1 when newScenario is true", () => {
    const href = editDslHref({ module: "heroes", newScenario: true });
    expect(href).toContain("module=heroes");
    expect(href).toContain("new=1");
  });

  it("omits new param when newScenario is false/undefined", () => {
    expect(editDslHref({ module: "heroes" })).not.toContain("new=");
    expect(editDslHref({ module: "heroes", newScenario: false })).not.toContain(
      "new=",
    );
  });

  it("returns bare /edit-dsl when no opts", () => {
    expect(editDslHref({})).toBe("/edit-dsl");
  });
});
