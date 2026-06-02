import { describe, expect, it } from "vitest";

import { deriveLiveStatus, screenRefOptions, wordBadges } from "./dreamscape-live";
import type {
  LabelingReferenceMeta,
  OverlayTestResult,
  RegionOcrRow,
} from "./types";

function refMeta(partial: Partial<LabelingReferenceMeta>): LabelingReferenceMeta {
  return {
    rel: "games/wos/events/dreamscape_memory/references/x.png",
    name: "x.png",
    rel_under: "x.png",
    title: "x",
    screen_id: "",
    region_count: 0,
    active_version: null,
    unassigned: false,
    ...partial,
  };
}

function overlay(partial: Partial<OverlayTestResult>): OverlayTestResult {
  return {
    instance_id: "bs1",
    current_screen: "",
    detected_screen: "",
    active_player: "",
    preview: { available: true, rel: "", mtime: null, width: 720, height: 1280 },
    rules: [],
    overlays: [],
    total_rules: 0,
    matched_count: 0,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    analysis: { screen_source: "none" } as any,
    ...partial,
  };
}

describe("deriveLiveStatus", () => {
  it("flags screen detected + area covered for a dreamscape screen", () => {
    const s = deriveLiveStatus(overlay({ detected_screen: "dreamscape_memory" }));
    expect(s.screenDetected).toBe(true);
    expect(s.areaCovered).toBe(true);
    expect(s.detectedScreen).toBe("dreamscape_memory");
  });

  it("detected but not covered when screen is some other module", () => {
    const s = deriveLiveStatus(overlay({ detected_screen: "main_city" }));
    expect(s.screenDetected).toBe(true);
    expect(s.areaCovered).toBe(false);
  });

  it("nothing detected when empty", () => {
    const s = deriveLiveStatus(overlay({}));
    expect(s.screenDetected).toBe(false);
    expect(s.areaCovered).toBe(false);
  });

  it("handles null result", () => {
    const s = deriveLiveStatus(null);
    expect(s.screenDetected).toBe(false);
    expect(s.areaCovered).toBe(false);
  });
});

describe("wordBadges", () => {
  const rows: RegionOcrRow[] = [
    {
      region: "dreamscape_memory.1",
      text: "Book",
      confidence: 0.95,
      threshold: 0.8,
      low_confidence: false,
      status: "ok",
      duration_ms: 12.3,
    },
    {
      region: "dreamscape_memory.2",
      text: "Wolf",
      confidence: 0.5,
      threshold: 0.8,
      low_confidence: true,
      status: "ok",
      duration_ms: 9,
    },
  ];

  it("returns one badge per region in order, even when a row is missing", () => {
    const badges = wordBadges(rows);
    expect(badges.map((b) => b.index)).toEqual([1, 2, 3]);
    expect(badges[0]).toMatchObject({ text: "Book", dimmed: false, durationMs: 12.3 });
    // low confidence -> dimmed even though text present
    expect(badges[1]).toMatchObject({ text: "Wolf", dimmed: true });
    // missing third region -> dimmed no_frame placeholder
    expect(badges[2]).toMatchObject({
      text: "",
      dimmed: true,
      status: "no_frame",
      durationMs: null,
    });
  });

  it("dims everything when rows are empty/null", () => {
    expect(wordBadges(null).every((b) => b.dimmed)).toBe(true);
  });
});

describe("screen list archive filter", () => {
  const refs = [
    refMeta({ rel: "a.png", screen_id: "dreamscape_memory" }),
    refMeta({ rel: "b.png", screen_id: "" }),
  ];

  it("hides operator-archived refs unless showArchived is on", () => {
    const archivedRels = new Set(["b.png"]);
    expect(
      screenRefOptions(refs, { showArchived: false, archivedRels }).map((s) => s.rel),
    ).toEqual(["a.png"]);
    const all = screenRefOptions(refs, { showArchived: true, archivedRels });
    expect(all.map((s) => s.rel)).toEqual(["a.png", "b.png"]);
    expect(all.find((s) => s.rel === "b.png")?.archived).toBe(true);
  });

  it("shows everything when nothing is archived", () => {
    const opts = screenRefOptions(refs, {
      showArchived: false,
      archivedRels: new Set(),
    });
    expect(opts.map((s) => s.rel)).toEqual(["a.png", "b.png"]);
  });
});
