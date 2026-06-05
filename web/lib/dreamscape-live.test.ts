import { describe, expect, it } from "vitest";

import {
  deriveLiveStatus,
  dreamscapeLetterCount,
  levelNameRead,
  parseDreamscapeSolveState,
  screenRefOptions,
  isActionableDreamscapeWord,
  wordBadges,
  wordBadgesWithSolveState,
  wordRunStates,
} from "./dreamscape-live";
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

describe("dreamscape word filter", () => {
  it("counts letters and ignores short OCR garbage", () => {
    expect(dreamscapeLetterCount("H2O")).toBe(2);
    expect(dreamscapeLetterCount(" Axe ")).toBe(3);
    expect(isActionableDreamscapeWord("Se")).toBe(false);
    expect(isActionableDreamscapeWord("Axe")).toBe(true);
  });
});

describe("dreamscape solve state", () => {
  it("parses live solver state and marks clicked/found word badges", () => {
    const state = parseDreamscapeSolveState(
      JSON.stringify({
        status: "running",
        scene: "practice-level",
        level_name: "Practice Level",
        regions: ["dreamscape_memory.1", "dreamscape_memory.2"],
        clicked_regions: ["dreamscape_memory.1", "dreamscape_memory.2"],
        settled_regions: ["dreamscape_memory.1"],
        pending_click_regions: ["dreamscape_memory.2"],
        updated_at: 123.5,
        region_words: {
          "dreamscape_memory.1": "Book",
          "dreamscape_memory.2": "Smoke",
        },
        slot_states: {
          "dreamscape_memory.1": {
            status: "settled",
            fsm_status: "found",
            word: "Book",
            key: "book",
          },
          "dreamscape_memory.2": {
            status: "mapped",
            fsm_status: "determined",
            word: "Smoke",
            key: "smoke",
          },
        },
        events: [
          {
            at: 123.6,
            kind: "mapped",
            message: "Mapped Smoke -> smoke",
            iteration: 2,
            region: "dreamscape_memory.2",
            word: "Smoke",
            key: "smoke",
            x: 374,
            y: 384,
            ok: true,
          },
        ],
      }),
    );
    expect(state?.updatedAt).toBe(123.5);
    expect(state?.slotStates["dreamscape_memory.2"]).toEqual({
      status: "mapped",
      fsmStatus: "determined",
      word: "Smoke",
      key: "smoke",
    });
    expect(state?.events[0]).toMatchObject({
      kind: "mapped",
      message: "Mapped Smoke -> smoke",
      iteration: 2,
      word: "Smoke",
      x: 374,
      y: 384,
      ok: true,
    });
    const badges = wordBadgesWithSolveState(
      wordBadges(
        [
          {
            region: "dreamscape_memory.2",
            text: "Smoke",
            confidence: 0.9,
            threshold: 0.8,
            low_confidence: false,
            status: "ok",
            duration_ms: 8,
          },
        ],
        ["dreamscape_memory.1", "dreamscape_memory.2"],
      ),
      state,
    );

    expect(badges.map((b) => b.text)).toEqual(["Book", "Smoke"]);
    expect(badges[0].dimmed).toBe(false);
    expect(wordRunStates(badges, state)).toEqual(["found", "determined"]);
  });

  it("drops a stale clicked status once the slot rotates to a new word", () => {
    const state = parseDreamscapeSolveState(
      JSON.stringify({
        status: "running",
        regions: ["dreamscape_memory.1"],
        clicked_regions: ["dreamscape_memory.1"],
        region_words: { "dreamscape_memory.1": "Book" },
        slot_states: {
          "dreamscape_memory.1": {
            status: "clicked",
            fsm_status: "clicked",
            word: "Book",
            key: "book",
          },
        },
      }),
    );
    // Live OCR now reads a different word in the same slot — the old "clicked"
    // verdict must not carry over to it.
    const rotated = wordBadges(
      [
        {
          region: "dreamscape_memory.1",
          text: "Lantern",
          confidence: 0.9,
          threshold: 0.8,
          low_confidence: false,
          status: "ok",
          duration_ms: 8,
        },
      ],
      ["dreamscape_memory.1"],
    );
    expect(wordRunStates(rotated, state)).toEqual(["unknown"]);

    // Same word still shown → status is preserved.
    const same = wordBadges(
      [
        {
          region: "dreamscape_memory.1",
          text: "Book",
          confidence: 0.9,
          threshold: 0.8,
          low_confidence: false,
          status: "ok",
          duration_ms: 8,
        },
      ],
      ["dreamscape_memory.1"],
    );
    expect(wordRunStates(same, state)).toEqual(["clicked"]);
  });

  it("keeps a found status even when the struck-through pill re-OCRs differently", () => {
    const state = parseDreamscapeSolveState(
      JSON.stringify({
        status: "running",
        regions: ["dreamscape_memory.1"],
        settled_regions: ["dreamscape_memory.1"],
        slot_states: {
          "dreamscape_memory.1": {
            status: "settled",
            fsm_status: "found",
            word: "Book",
            key: "book",
          },
        },
      }),
    );
    const noisy = wordBadges(
      [
        {
          region: "dreamscape_memory.1",
          text: "B00k",
          confidence: 0.4,
          threshold: 0.8,
          low_confidence: true,
          status: "ok",
          duration_ms: 8,
        },
      ],
      ["dreamscape_memory.1"],
    );
    expect(wordRunStates(noisy, state)).toEqual(["found"]);
  });

  it("ignores malformed solve state", () => {
    expect(parseDreamscapeSolveState("not json")).toBeNull();
  });
});

describe("levelNameRead", () => {
  function titleRow(text: string, lowConfidence = false): RegionOcrRow {
    return {
      region: "dreamscape_memory.level.name",
      text,
      confidence: lowConfidence ? 0.27 : 0.83,
      threshold: 0.8,
      low_confidence: lowConfidence,
      status: "ok",
      duration_ms: 18,
    };
  }

  it("cleans decorative OCR punctuation from the displayed title", () => {
    expect(levelNameRead([titleRow("Practice)Level · 27%")])?.text).toBe(
      "Practice Level",
    );
    expect(levelNameRead([titleRow(".Practice Level")])?.text).toBe(
      "Practice Level",
    );
  });

  it("keeps confidence dimming based on the backend row", () => {
    const read = levelNameRead([titleRow("Practice)Level", true)]);

    expect(read).toMatchObject({
      text: "Practice Level",
      dimmed: true,
    });
  });
});

describe("screen list options", () => {
  const refs = [
    refMeta({ rel: "a.png", screen_id: "dreamscape_memory", name: "a" }),
    refMeta({ rel: "b.png", screen_id: "", name: "b" }),
  ];

  it("builds an option per ref with a screen-id-prefixed label", () => {
    const opts = screenRefOptions(refs);
    expect(opts.map((s) => s.rel)).toEqual(["a.png", "b.png"]);
    expect(opts[0].label).toBe("dreamscape_memory · a");
    expect(opts[1].label).toBe("b");
  });

  it("returns an empty list for null/undefined refs", () => {
    expect(screenRefOptions(null)).toEqual([]);
    expect(screenRefOptions(undefined)).toEqual([]);
  });
});
