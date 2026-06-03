import type {
  LabelingReferenceMeta,
  OverlayTestResult,
  RegionOcrRow,
} from "@/lib/types";

/** Repo-relative reference PNG for the Dreamscape practice-level screen. */
export const DREAMSCAPE_WORDS_REF =
  "games/wos/events/dreamscape_memory/references/practice_level.png";

/** Labeling scope key for the dreamscape_memory module (see inferScopeFromRef). */
export const DREAMSCAPE_SCOPE = "wos:events/dreamscape_memory";

/** The three word-button regions OCR'd at the bottom of a recall-road level. */
export const DREAMSCAPE_WORD_REGIONS = [
  "dreamscape_memory.1",
  "dreamscape_memory.2",
  "dreamscape_memory.3",
] as const;

export const DREAMSCAPE_SCREEN_PREFIX = "dreamscape_memory";

export type LiveStatus = {
  /** Screen detection identified a screen on the current frame. */
  screenDetected: boolean;
  /** The detected screen, if any. */
  detectedScreen: string;
  /** The current frame is covered by a Dreamscape area definition. */
  areaCovered: boolean;
};

/** Derive the two status pills from a polled overlay-test result.
 *
 * The detector only returns a non-empty ``detected_screen`` when a labeled
 * ``screen_region`` matched, so a non-empty value already means "covered by a
 * known area." ``areaCovered`` additionally requires the screen to belong to
 * the Dreamscape module.
 */
export function deriveLiveStatus(result: OverlayTestResult | null | undefined): LiveStatus {
  const detected = (result?.detected_screen || "").trim();
  const screenDetected =
    detected.length > 0 || result?.analysis?.screen_source === "detected";
  return {
    screenDetected,
    detectedScreen: detected,
    areaCovered: detected.startsWith(DREAMSCAPE_SCREEN_PREFIX),
  };
}

/** Build a status from a bare detected-screen string (e.g. the upload-test result). */
export function statusFromDetectedScreen(detected: string | null | undefined): LiveStatus {
  const d = (detected || "").trim();
  return {
    screenDetected: d.length > 0,
    detectedScreen: d,
    areaCovered: d.startsWith(DREAMSCAPE_SCREEN_PREFIX),
  };
}

export type WordBadge = {
  region: string;
  /** 1-based position shown on the badge. */
  index: number;
  text: string;
  /** Render dimmed: no text, low confidence, or an error/no-frame status. */
  dimmed: boolean;
  status: RegionOcrRow["status"];
  confidence: number | null;
  /** Per-region OCR time in ms (null when not OCR'd). */
  durationMs: number | null;
};

/** Build the ordered word badges for the three regions, even when a row is missing. */
export function wordBadges(
  rows: RegionOcrRow[] | null | undefined,
  regions: readonly string[] = DREAMSCAPE_WORD_REGIONS,
): WordBadge[] {
  const byRegion = new Map((rows ?? []).map((r) => [r.region, r]));
  return regions.map((region, i) => {
    const row = byRegion.get(region);
    const text = (row?.text || "").trim();
    const status = row?.status ?? "no_frame";
    const dimmed = !text || status !== "ok" || Boolean(row?.low_confidence);
    return {
      region,
      index: i + 1,
      text,
      dimmed,
      status,
      confidence: row?.confidence ?? null,
      durationMs: row?.duration_ms ?? null,
    };
  });
}

/** A screen the Live editor can open (one labeled reference). */
export type ScreenRef = {
  rel: string;
  label: string;
  archived: boolean;
};

/** localStorage key for the operator's archived-screen set (browser only). */
export const DREAMSCAPE_ARCHIVED_KEY = "dreamscape:archived-screens";

/** Build the screen-selector options, hiding archived refs unless requested.
 *
 * There is no archive flag in area.yaml — "archived" is an operator-controlled,
 * browser-local set (no repo/data change). Refs in ``archivedRels`` are hidden
 * unless ``showArchived`` is on.
 */
export function screenRefOptions(
  refs: LabelingReferenceMeta[] | null | undefined,
  opts: { showArchived: boolean; archivedRels: ReadonlySet<string> },
): ScreenRef[] {
  return (refs ?? [])
    .map((r) => {
      const sid = r.screen_id.trim();
      const label = sid ? `${sid} · ${r.name}` : r.name;
      return { rel: r.rel, label, archived: opts.archivedRels.has(r.rel) };
    })
    .filter((s) => opts.showArchived || !s.archived);
}
