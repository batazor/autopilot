import type {
  LabelingReferenceMeta,
  OverlayTestResult,
  RegionOcrRow,
} from "@/lib/types";

/** Repo-relative reference PNG for the Dreamscape solo practice-level screen. */
export const DREAMSCAPE_WORDS_REF =
  "games/wos/events/dreamscape_memory/references/practice_level.png";

/** Repo-relative reference PNG for the Dreamscape multiplayer screen (6 words). */
export const DREAMSCAPE_MULTIPLAYER_WORDS_REF =
  "games/wos/events/dreamscape_memory/references/dreamscape_memory_.multiplayer.png";

/** Labeling scope key for the dreamscape_memory module (see inferScopeFromRef). */
export const DREAMSCAPE_SCOPE = "wos:events/dreamscape_memory";

/** Scenario keys (filename stems) the live view enqueues to run the solver.
 * Both wrap their OCR+solve in a ~300ms loop so the bot keeps up with the
 * dynamic recall-road animation (see the scenario YAMLs). */
export const DREAMSCAPE_SOLO_SCENARIO = "dreamscape_memory";
export const DREAMSCAPE_MULTIPLAYER_SCENARIO = "dreamscape_memory_multiplayer";

/** The screen-title region holding the level/room name (e.g. "Aquarium").
 * The solver OCRs this to auto-pick the scene; we read it live too so the
 * operator can see which level we recognised. Shared by solo and multiplayer. */
export const DREAMSCAPE_LEVEL_NAME_REGION = "dreamscape_memory.level.name";

/** The three word-button regions OCR'd at the bottom of a solo recall-road level. */
export const DREAMSCAPE_WORD_REGIONS = [
  "dreamscape_memory.1",
  "dreamscape_memory.2",
  "dreamscape_memory.3",
] as const;

/** The six word-button regions OCR'd in multiplayer mode. */
export const DREAMSCAPE_MULTIPLAYER_WORD_REGIONS = [
  "dreamscape_memory_.multiplayer.1",
  "dreamscape_memory_.multiplayer.2",
  "dreamscape_memory_.multiplayer.3",
  "dreamscape_memory_.multiplayer.4",
  "dreamscape_memory_.multiplayer.5",
  "dreamscape_memory_.multiplayer.6",
] as const;

/** Names of the solver-managed system regions, not edited by the operator:
 * word-search zones (3 solo, 6 multiplayer) and the screen title marker. They
 * belong in area.yaml but stay out of the region editor, where only item points
 * are placed. */
const SYSTEM_REGION_NAMES: ReadonlySet<string> = new Set<string>([
  ...DREAMSCAPE_WORD_REGIONS,
  ...DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
]);

/** True for solver-managed system regions (word-search zones + screen title). */
export function isSystemRegion(name: string): boolean {
  return SYSTEM_REGION_NAMES.has(name) || name.endsWith(".title");
}

export const DREAMSCAPE_SCREEN_PREFIX = "dreamscape_memory";
export const DREAMSCAPE_TIME_UP_SCREEN = "dreamscape_memory.time_up";
export const DREAMSCAPE_ALL_ITEM_FOUND_SCREEN = "dreamscape_memory.all_item_found";

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

/** The recognised level/room name read live from the title region. */
export type LevelNameRead = {
  region: string;
  text: string;
  status: RegionOcrRow["status"];
  confidence: number | null;
  /** No text, low confidence, or an error/no-frame status — render dimmed. */
  dimmed: boolean;
};

/** Pull the level-name read out of the OCR rows (the title region we poll
 * alongside the word zones). Returns null when no row was requested. */
export function levelNameRead(
  rows: RegionOcrRow[] | null | undefined,
  region: string = DREAMSCAPE_LEVEL_NAME_REGION,
): LevelNameRead | null {
  const row = (rows ?? []).find((r) => r.region === region);
  if (!row) return null;
  const text = (row.text || "").trim();
  return {
    region,
    text,
    status: row.status,
    confidence: row.confidence ?? null,
    dimmed: !text || row.status !== "ok" || Boolean(row.low_confidence),
  };
}

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
};

/** Build the screen-selector options from labeled references. */
export function screenRefOptions(
  refs: LabelingReferenceMeta[] | null | undefined,
): ScreenRef[] {
  return (refs ?? []).map((r) => {
    const sid = r.screen_id.trim();
    const label = sid ? `${sid} · ${r.name}` : r.name;
    return { rel: r.rel, label };
  });
}
