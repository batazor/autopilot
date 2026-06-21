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
export const DREAMSCAPE_MIN_WORD_LETTERS = 3;

export function dreamscapeLetterCount(raw: string): number {
  return Array.from(raw).filter((ch) => /\p{L}/u.test(ch)).length;
}

export function isActionableDreamscapeWord(raw: string): boolean {
  return dreamscapeLetterCount(raw) >= DREAMSCAPE_MIN_WORD_LETTERS;
}

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

export type DreamscapeSolveState = {
  status: string;
  scene: string;
  levelName: string;
  updatedAt: number | null;
  regions: string[];
  clickedRegions: string[];
  settledRegions: string[];
  pendingClickRegions: string[];
  regionWords: Record<string, string>;
  slotStates: Record<string, DreamscapeSlotState>;
  events: DreamscapeSolveEvent[];
};

export type DreamscapeSlotState = {
  status: string;
  fsmStatus: DreamscapeWordRunState;
  word: string;
  key: string;
};

export type DreamscapeSolveEvent = {
  at: number | null;
  kind: string;
  message: string;
  iteration: number | null;
  region: string;
  word: string;
  key: string;
  x: number | null;
  y: number | null;
  ok: boolean | null;
  reason: string;
};

export type DreamscapeWordRunState =
  | "unknown"
  | "determined"
  | "clicked"
  | "help_requested"
  | "detecting_on_map"
  | "found"
  | "rejected"
  | null;

function stringArray(raw: unknown): string[] {
  return Array.isArray(raw)
    ? raw.map((v) => String(v || "").trim()).filter(Boolean)
    : [];
}

function stringRecord(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw)
      .map(([k, v]) => [String(k).trim(), String(v || "").trim()] as const)
      .filter(([k, v]) => k && v),
  );
}

function slotStateRecord(raw: unknown): Record<string, DreamscapeSlotState> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw)
      .map(([region, value]) => {
        const row =
          value && typeof value === "object" && !Array.isArray(value)
            ? (value as Record<string, unknown>)
            : {};
        return [
          String(region).trim(),
          {
            status: String(row.status || "").trim(),
            fsmStatus: normalizeSlotFsmStatus(row.fsm_status || row.fsmStatus),
            word: String(row.word || "").trim(),
            key: String(row.key || row.raw_key || "").trim(),
          },
        ] as const;
      })
      .filter(([region, state]) => region && state.status),
  );
}

function normalizeSlotFsmStatus(raw: unknown): DreamscapeWordRunState {
  const status = String(raw || "").trim();
  if (
    status === "unknown" ||
    status === "determined" ||
    status === "clicked" ||
    status === "help_requested" ||
    status === "detecting_on_map" ||
    status === "found" ||
    status === "rejected"
  ) {
    return status;
  }
  return null;
}

function publicStatusFromInternal(status: string): DreamscapeWordRunState {
  if (status === "settled") return "found";
  if (status === "mapped") return "determined";
  if (status === "clicked" || status === "retry_exhausted") return "clicked";
  if (status === "help_requested") return "help_requested";
  if (status === "help_detecting" || status === "detecting_on_map") {
    return "detecting_on_map";
  }
  if (status === "tap_rejected") return "rejected";
  if (status === "unknown" || status === "unmapped") return "unknown";
  return null;
}

function finiteNumber(raw: unknown): number | null {
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function solveEvents(raw: unknown): DreamscapeSolveEvent[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((value) => {
      const row =
        value && typeof value === "object" && !Array.isArray(value)
          ? (value as Record<string, unknown>)
          : {};
      const kind = String(row.kind || "").trim();
      const message = String(row.message || "").trim();
      if (!kind && !message) return null;
      return {
        at: finiteNumber(row.at),
        kind,
        message,
        iteration: finiteNumber(row.iteration),
        region: String(row.region || "").trim(),
        word: String(row.word || "").trim(),
        key: String(row.key || "").trim(),
        x: finiteNumber(row.x),
        y: finiteNumber(row.y),
        ok: typeof row.ok === "boolean" ? row.ok : null,
        reason: String(row.reason || "").trim(),
      } satisfies DreamscapeSolveEvent;
    })
    .filter((event): event is DreamscapeSolveEvent => event !== null);
}

export function parseDreamscapeSolveState(
  raw: string | null | undefined,
): DreamscapeSolveState | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      status: String(parsed.status || ""),
      scene: String(parsed.scene || ""),
      levelName: String(parsed.level_name || ""),
      updatedAt:
        typeof parsed.updated_at === "number" && Number.isFinite(parsed.updated_at)
          ? parsed.updated_at
          : null,
      regions: stringArray(parsed.regions),
      clickedRegions: stringArray(parsed.clicked_regions),
      settledRegions: stringArray(parsed.settled_regions),
      pendingClickRegions: stringArray(parsed.pending_click_regions),
      regionWords: stringRecord(parsed.region_words),
      slotStates: slotStateRecord(parsed.slot_states),
      events: solveEvents(parsed.events),
    };
  } catch {
    return null;
  }
}

function solveStateCoversRegion(state: DreamscapeSolveState, region: string): boolean {
  return state.regions.length === 0 || state.regions.includes(region);
}

/** Mirror the solver's key normalization (exec `_normalize_word`): lower-case,
 * trim, collapse inner whitespace so a live OCR read compares to a slot key. */
function normalizeWordKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

/** Public run-states bound to the word *currently* shown in a slot. When the
 * live OCR word rotates to something new, such a status is stale and must not
 * carry over to the fresh word. "found" is excluded on purpose: a found pill is
 * struck through and re-OCRs poorly, so a text mismatch there is expected, not
 * a rotation. */
const WORD_BOUND_RUN_STATES: ReadonlySet<DreamscapeWordRunState> = new Set<
  DreamscapeWordRunState
>(["determined", "clicked", "help_requested", "detecting_on_map", "rejected"]);

/** True when the slot's stored status still describes the word now in the badge.
 * Empty live text can't contradict the slot (the pill may be greyed/found). */
function slotWordMatchesBadge(slot: DreamscapeSlotState, badge: WordBadge): boolean {
  const current = normalizeWordKey(badge.text);
  if (!current) return true;
  return (
    current === normalizeWordKey(slot.word) ||
    current === normalizeWordKey(slot.key)
  );
}

export function wordBadgesWithSolveState(
  badges: WordBadge[],
  state: DreamscapeSolveState | null,
): WordBadge[] {
  if (!state) return badges;
  return badges.map((badge) => {
    if (!solveStateCoversRegion(state, badge.region)) return badge;
    const text = (
      state.regionWords[badge.region] ||
      state.slotStates[badge.region]?.word ||
      ""
    ).trim();
    if (!text || badge.text.trim()) return badge;
    return { ...badge, text, dimmed: false };
  });
}

export function wordRunStates(
  badges: WordBadge[],
  state: DreamscapeSolveState | null,
): DreamscapeWordRunState[] {
  if (!state) return badges.map(() => null);
  const clicked = new Set(state.clickedRegions);
  const settled = new Set(state.settledRegions);
  return badges.map((badge) => {
    if (!solveStateCoversRegion(state, badge.region)) return null;
    const slot = state.slotStates[badge.region];
    const publicStatus =
      slot?.fsmStatus ?? publicStatusFromInternal(slot?.status || "");
    if (publicStatus) {
      // Drop a stale per-region status once the slot has rotated to a new word:
      // the live OCR badge already shows the fresh word, so an old "clicked"/
      // "determined" verdict no longer applies to it.
      if (
        slot &&
        WORD_BOUND_RUN_STATES.has(publicStatus) &&
        !slotWordMatchesBadge(slot, badge)
      ) {
        return "unknown";
      }
      return publicStatus;
    }
    if (settled.has(badge.region)) return "found";
    if (clicked.has(badge.region)) return "clicked";
    return "unknown";
  });
}

/** The recognised level/room name read live from the title region. */
export type LevelNameRead = {
  region: string;
  text: string;
  status: RegionOcrRow["status"];
  confidence: number | null;
  /** No text, low confidence, or an error/no-frame status — render dimmed. */
  dimmed: boolean;
};

function cleanLevelNameDisplayText(raw: string): string {
  return raw
    .replace(/\b\d+(?:\.\d+)?\s*%.*$/i, " ")
    .replace(/(?<=[A-Za-z0-9])[^A-Za-z0-9]+(?=[A-Za-z0-9])/g, " ")
    .replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Pull the level-name read out of the OCR rows (the title region we poll
 * alongside the word zones). Returns null when no row was requested. */
export function levelNameRead(
  rows: RegionOcrRow[] | null | undefined,
  region: string = DREAMSCAPE_LEVEL_NAME_REGION,
): LevelNameRead | null {
  const row = (rows ?? []).find((r) => r.region === region);
  if (!row) return null;
  const text = cleanLevelNameDisplayText((row.text || "").trim());
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
