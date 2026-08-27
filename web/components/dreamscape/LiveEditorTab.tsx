"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import { useApiStatus } from "@/components/ApiStatusProvider";
import {
  ApiError,
  activateDreamscapeScene,
  captureLabelingScreenshot,
  clickApprovalImageUrl,
  fetchInstanceDetail,
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  fetchDreamscapeSolverStatus,
  fetchOcrLang,
  fetchRegionOcr,
  fetchScreenDetect,
  setOcrLang,
  startDreamscapeSolver,
  stopDreamscapeSolver,
} from "@/lib/api";
import {
  DREAMSCAPE_ALL_ITEM_FOUND_SCREEN,
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_TIME_UP_SCREEN,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  isActionableDreamscapeWord,
  parseDreamscapeSolveState,
  statusFromDetectedScreen,
  wordBadges,
  wordBadgesWithSolveState,
  wordRunStates,
} from "@/lib/dreamscape-live";
import {
  addDreamscapeNewCapture,
  hasDreamscapeNewCapture,
} from "@/lib/dreamscape-new-captures";
import type {
  DreamscapeSolveEvent,
  DreamscapeWordRunState,
  LiveStatus,
  WordBadge,
} from "@/lib/dreamscape-live";
import type { DreamscapeSceneDetail } from "@/lib/types";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import { DetectedWordsBadges } from "./DetectedWordsBadges";
import { Button } from "./Button";

const POLL_MS = 1500;

// Season buckets (kept in sync with config/dreamscape_db.py): 0 = practice,
// 100 = co-op Multiplayer, otherwise the numbered content season.
const PRACTICE_SEASON = 0;
const MULTIPLAYER_SEASON = 100;

function seasonTag(season: number): string {
  if (season === PRACTICE_SEASON) return "Practice";
  if (season === MULTIPLAYER_SEASON) return "MP";
  return `S${season}`;
}

function seasonRank(season: number): number {
  if (season === PRACTICE_SEASON) return Number.MAX_SAFE_INTEGER;
  if (season === MULTIPLAYER_SEASON) return Number.MAX_SAFE_INTEGER - 1;
  return season;
}

/** Mirror the solver's key normalization (config exec `_normalize_word`):
 * lower-case and collapse inner whitespace so OCR text matches scene item keys. */
function normalizeWord(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    let detail = err.body;
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* keep raw body */
    }
    return detail ? `${err.status} — ${detail}` : err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

function ScreenStatusPill({
  detected,
  screen,
}: {
  detected: boolean;
  screen?: string;
}) {
  return (
    <span
      title={
        screen
          ? `Detected: ${screen}`
          : "Screen detection found no labeled screen on this frame"
      }
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        detected
          ? "bg-emerald-500/15 text-emerald-400"
          : "bg-rose-500/15 text-rose-400"
      }`}
    >
      <span aria-hidden>{detected ? "●" : "○"}</span>
      {detected ? "Detected" : "No screen"}
    </span>
  );
}

/** The live status view is shared between solo (3 words) and multiplayer (6
 * words); the word-region set and reference screen are the only differences. */
export type LiveEditorTabProps = {
  /** OCR word-button regions to poll/show as badges (defaults to solo's 3). */
  wordRegions?: readonly string[];
  /** Reference screen this mode keys its OCR poll on (defaults to solo's). */
  wordsRef?: string;
  /** Scenario key enqueued by "Play" — the mode's operator-picked-round solve
   * loop (`scene_source: active`): the solver locks onto the picked scene and
   * spends every tick on word OCR + taps. */
  scenarioKey?: string;
  /** When false, unknown words never auto-capture/redirect to /labeling —
   * for the bare one-page runner (/solve), which must stay on its page. */
  autoCapture?: boolean;
};

export function LiveEditorTab({
  wordRegions = DREAMSCAPE_WORD_REGIONS,
  wordsRef = DREAMSCAPE_WORDS_REF,
  scenarioKey,
  autoCapture = true,
}: LiveEditorTabProps = {}) {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [message, setMessage] = useState<string | null>(null);
  const [autoCaptureArmed, setAutoCaptureArmed] = useState(false);
  const [confettiVisible, setConfettiVisible] = useState(false);
  const [runStartedAtSec, setRunStartedAtSec] = useState<number | null>(null);
  const autoCaptureKeys = useRef<Set<string>>(new Set());
  const autoCaptureBusy = useRef(false);
  // Sticky for the current armed run: flips true once the solver shows real
  // progress (a tap/found item). Gates the "returned to start = win" heuristic
  // so it cannot fire while the run is still sitting on the start screen.
  const enteredGameplayRef = useRef(false);

  // ── Isolated solver status: one process, no worker/queue/Redis ──
  const botQuery = useQuery({
    queryKey: ["dreamscape-solver-status"],
    queryFn: fetchDreamscapeSolverStatus,
    refetchInterval: 3000,
  });
  const botRunning = Boolean(botQuery.data?.running);

  // ── Live polling (status + detected words) ──
  // Poll ONLY while the solver runs: it is the sole frame/detection producer,
  // so with it stopped there is nothing to refresh.
  const screenQuery = useQuery({
    queryKey: ["dreamscape-screen", instanceId],
    queryFn: () => fetchScreenDetect(instanceId),
    enabled: Boolean(instanceId) && botRunning,
    refetchInterval: POLL_MS,
  });
  const instanceDetailQuery = useQuery({
    queryKey: ["dreamscape-instance-detail", instanceId],
    queryFn: () => fetchInstanceDetail(instanceId),
    enabled: Boolean(instanceId) && botRunning,
    refetchInterval: POLL_MS,
  });
  const instanceDetail =
    instanceDetailQuery.data && "preview_available" in instanceDetailQuery.data
      ? instanceDetailQuery.data
      : null;

  const detectedPreviewMtime =
    screenQuery.data?.preview?.mtime == null
      ? null
      : Number(screenQuery.data.preview.mtime);
  const detectedScreenIsFromStaleRun =
    runStartedAtSec != null &&
    detectedPreviewMtime != null &&
    detectedPreviewMtime <= runStartedAtSec;
  const effectiveDetectedScreen = detectedScreenIsFromStaleRun
    ? ""
    : screenQuery.data?.detected_screen;
  const status = useMemo(
    () => statusFromDetectedScreen(effectiveDetectedScreen),
    [effectiveDetectedScreen],
  );
  const terminalScreen = status.detectedScreen;

  // ── Word slots: OCR every tick ──
  // The on-screen title is unreliable, so the scene is identified from the *set
  // of words shown*. We therefore always read the word buttons (no title gate) —
  // they are both what we display and the key the scene detector matches on.
  const wordOcrQuery = useQuery({
    queryKey: ["dreamscape-word-ocr", instanceId, wordsRef],
    queryFn: () => fetchRegionOcr(instanceId, [...wordRegions]),
    enabled: Boolean(instanceId) && botRunning,
    refetchInterval: POLL_MS,
  });
  const rawBadges = useMemo(
    () => wordBadges(wordOcrQuery.data?.rows, wordRegions),
    [wordOcrQuery.data, wordRegions],
  );
  // Actionable words on screen (drop blanks / OCR noise) — the detector key.
  const detectedWords = useMemo(
    () =>
      rawBadges
        .map((b) => b.text.trim())
        .filter((t) => isActionableDreamscapeWord(t)),
    [rawBadges],
  );
  const scenesQuery = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });

  // ── OCR language: an explicit operator setting, never auto-detected ──
  const ocrLangQuery = useQuery({
    queryKey: ["ocr-lang"],
    queryFn: fetchOcrLang,
  });
  const ocrLangMutation = useMutation({
    mutationFn: (lang: string) => setOcrLang(lang),
    onSuccess: (res) => {
      void ocrLangQuery.refetch();
      setMessage(
        `OCR language set to ${res.lang}.` +
          (botRunning ? " Restart the bot to apply it to the solver." : ""),
      );
    },
    onError: (err: unknown) => setMessage(`OCR language change failed: ${formatApiError(err)}`),
  });
  const ocrLangOptions = (ocrLangQuery.data?.available ?? []).map((code) => ({
    value: code,
    label: code,
  }));

  // The scene is ALWAYS the operator's pick — no word-based auto-detection.
  // Deep-linked via ?scene=slug (History API); with no link the pick starts
  // from the DB's active scene, so a reload keeps the last choice.
  const [overrideSlug, setOverrideSlug] = useState<string | null>(
    () => params.get("scene")?.trim() || null,
  );
  useEffect(() => {
    const fromUrl = params.get("scene")?.trim();
    if (fromUrl) setOverrideSlug(fromUrl);
  }, [params]);
  useEffect(() => {
    if (!overrideSlug && scenesQuery.data?.active) {
      setOverrideSlug(scenesQuery.data.active);
    }
  }, [overrideSlug, scenesQuery.data]);
  const matchedSlug = overrideSlug;
  const solveStateRaw =
    instanceDetail?.state?.["dreamscape_memory.solve_state"] ?? null;
  const parsedSolveState = useMemo(
    () => parseDreamscapeSolveState(solveStateRaw),
    [solveStateRaw],
  );
  const solveState = useMemo(() => {
    if (!parsedSolveState) return null;
    if (
      runStartedAtSec != null &&
      parsedSolveState.updatedAt != null &&
      parsedSolveState.updatedAt <= runStartedAtSec
    ) {
      return null;
    }
    return parsedSolveState;
  }, [parsedSolveState, runStartedAtSec]);
  const badges = useMemo(
    () => wordBadgesWithSolveState(rawBadges, solveState),
    [rawBadges, solveState],
  );
  const wordRunState = useMemo<DreamscapeWordRunState[]>(
    () => wordRunStates(badges, solveState),
    [badges, solveState],
  );
  const sceneQuery = useQuery({
    queryKey: ["dreamscape-scene", matchedSlug],
    queryFn: () => fetchDreamscapeScene(matchedSlug as string),
    enabled: !!matchedSlug,
  });
  const knownNames = useMemo(
    () =>
      new Set((sceneQuery.data?.points ?? []).map((p) => normalizeWord(p.name))),
    [sceneQuery.data],
  );
  // Per-word coverage aligned to `badges`: true = mapped, false = read but
  // unmapped, null = nothing to judge yet (no text, or no scene matched).
  const wordKnown = useMemo<(boolean | null)[]>(
    () =>
      badges.map((b) => {
        const w = normalizeWord(b.text);
        if (!w || !matchedSlug) return null;
        return knownNames.has(w);
      }),
    [badges, knownNames, matchedSlug],
  );
  const unknownWords = useMemo(
    () =>
      badges
        .filter(
          (b, i) =>
            wordKnown[i] === false &&
            !b.dimmed &&
            isActionableDreamscapeWord(b.text),
        )
        .map((b) => b.text.trim()),
    [badges, wordKnown],
  );
  const mode = wordRegions === DREAMSCAPE_MULTIPLAYER_WORD_REGIONS ? "multiplayer" : "solo";
  const sceneTitle = sceneQuery.data?.title ?? null;

  // ── Manual override selector: Season first, then the scene within it ──
  // Scenes follow the in-game order (`sort_order`, stamped from the upstream
  // catalog), NOT the alphabet; unordered legacy scenes fall back to title.
  const allScenes = scenesQuery.data?.scenes;
  const seasonOptions = useMemo(() => {
    const seasons = [...new Set((allScenes ?? []).map((s) => s.season))];
    // Newest content first (S5, S4, …), then Multiplayer, then Practice.
    seasons.sort((a, b) => {
      const special = (n: number) =>
        n === PRACTICE_SEASON ? 2 : n === MULTIPLAYER_SEASON ? 1 : 0;
      return special(a) - special(b) || b - a;
    });
    return seasons.map((n) => ({
      value: String(n),
      label:
        n === PRACTICE_SEASON
          ? "Practice"
          : n === MULTIPLAYER_SEASON
            ? "Multiplayer"
            : `Season ${n}`,
    }));
  }, [allScenes]);
  const overrideSeason = useMemo(() => {
    if (!overrideSlug) return null;
    const scene = (allScenes ?? []).find((s) => s.slug === overrideSlug);
    return scene ? String(scene.season) : null;
  }, [allScenes, overrideSlug]);
  const [seasonChoice, setSeasonChoice] = useState<string | null>(null);
  const seasonValue =
    seasonChoice ?? overrideSeason ?? seasonOptions[0]?.value ?? "";
  const sceneOptions = useMemo(
    () =>
      (allScenes ?? [])
        .filter((s) => String(s.season) === seasonValue)
        .sort(
          (a, b) =>
            (a.sort_order || Number.MAX_SAFE_INTEGER) -
              (b.sort_order || Number.MAX_SAFE_INTEGER) ||
            a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
        )
        .map((s) => ({ value: s.slug, label: s.title })),
    [allScenes, seasonValue],
  );
  const activateMutation = useMutation({
    mutationFn: (slug: string) => activateDreamscapeScene(slug),
  });
  // Pin the scene (wins over auto-detection), deep-link it via ?scene=, and make
  // it active so the solver taps it too. Empty slug clears the override.
  const selectScene = (slug: string) => {
    const next = slug.trim() || null;
    setOverrideSlug(next);
    if (typeof window !== "undefined") {
      const q = new URLSearchParams(window.location.search);
      if (next) q.set("scene", next);
      else q.delete("scene");
      const qs = q.toString();
      window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
    }
    if (next) activateMutation.mutate(next);
  };


  const solverButtonLabel = "Play";
  const solverPendingLabel = "Starting...";

  // Live device frame, 1:1 with the approvals page: the worker's rolling
  // preview PNG, refreshed the instant the instance revision advances (SSE
  // below) by bumping a cache-busting tick.
  const [imageTick, setImageTick] = useState(0);
  // Debug popup: the live frame with every scene point + solver plans drawn on
  // top — «что бот находит и куда собирается кликать».
  const [debugOpen, setDebugOpen] = useState(false);
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);
  const previewMtime =
    instanceDetail?.preview_mtime == null
      ? null
      : Number(instanceDetail.preview_mtime);
  const previewIsFromStaleRun =
    runStartedAtSec != null &&
    previewMtime != null &&
    previewMtime <= runStartedAtSec;
  const cardImageUrl = instanceId
    ? `${clickApprovalImageUrl(instanceId, "live")}&tick=${imageTick}`
    : null;
  const showImage =
    Boolean(cardImageUrl) && cardImageUrl !== failedImageUrl && !previewIsFromStaleRun;

  // Keep the frame continuously current like the approvals screen: the SSE
  // stream watches the rolling preview mtime, and a short client fallback covers
  // degraded/closed streams.
  useDashboardEventStream({
    topics: ["instance"],
    instanceId: instanceId || undefined,
    enabled: Boolean(instanceId),
    fallbackPollMs: 1000,
    onEvent: (topic) => {
      if (topic === "instance") setImageTick((t) => t + 1);
    },
    onFallbackPoll: () => setImageTick((t) => t + 1),
  });
  // Isolated mode has no worker publishing instance revisions, so the SSE
  // stream stays silent (open but eventless) and the fallback never fires —
  // the frame froze. The solver rewrites the preview file every second; bump
  // the cache-busting tick on a plain timer so the <img> actually re-fetches.
  useEffect(() => {
    if (!instanceId || !botRunning) return undefined;
    const timer = window.setInterval(() => setImageTick((t) => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [instanceId, botRunning]);

  // Grab the current device frame as a fresh labeling capture and jump to the
  // full labeling editor on it, so an operator can label anything new on screen
  // ("доразметить") without leaving the live view to set it up by hand.
  const captureMutation = useMutation({
    mutationFn: () => captureLabelingScreenshot(instanceId, DREAMSCAPE_SCOPE),
    onSuccess: ({ ref }) => {
      const q = new URLSearchParams({ module: DREAMSCAPE_SCOPE, ref });
      // New tab: the operator keeps playing on this page and can snap more
      // screenshots as the round progresses.
      window.open(`/labeling?${q.toString()}`, "_blank", "noopener");
      setMessage(`Screenshot captured — opened in a new tab (${ref}).`);
    },
    onError: (err: unknown) => setMessage(`Screenshot failed: ${String(err)}`),
  });

  // ── Bot control: start the worker + enqueue this mode's fast solve loop ──
  const liveFramePlaceholder = !instanceId
    ? "Select an instance"
    : cardImageUrl === failedImageUrl
      ? "No frame yet — press Play; the solver publishes the preview itself."
      : "Waiting for a live frame…";

  const startMutation = useMutation({
    // Start the local worker (idempotent if already up), then enqueue the
    // solver so it begins reading + tapping the level right away.
    mutationFn: async (action: "start" | "restart" = "start") => {
      const selectedInstance = instanceId.trim();
      if (!selectedInstance) throw new Error("Select an instance before starting Dreamscape.");
      if (!overrideSlug) throw new Error("Pick a scene before starting Dreamscape.");
      // Re-assert the pick right before the run — the pick may come from a
      // deep link (?scene=) while the DB's active scene points elsewhere.
      await activateDreamscapeScene(overrideSlug);
      const startedAt = Date.now() / 1000;
      setRunStartedAtSec(startedAt);
      enteredGameplayRef.current = false;
      setConfettiVisible(false);
      setFailedImageUrl(null);
      setImageTick((t) => t + 1);
      setMessage(action === "restart" ? "Restarting solver..." : "Starting solver...");
      // Isolated mode: one standalone process that detects words and taps.
      // No worker, no queue, no Redis — start IS restart (the backend kills a
      // previous solver first).
      return startDreamscapeSolver({
        instance_id: selectedInstance,
        scene: overrideSlug,
        mode: wordRegions === DREAMSCAPE_MULTIPLAYER_WORD_REGIONS ? "multiplayer" : "solo",
      });
    },
    onSuccess: (res, action) => {
      setAutoCaptureArmed(true);
      void botQuery.refetch();
      void wordOcrQuery.refetch();
      setMessage(
        `Solver ${action === "restart" ? "restarted" : "started"} on ${res.scene} (pid ${res.pid}).`,
      );
    },
    onError: (err: unknown, action) =>
      setMessage(
        `${action === "restart" ? "Restart" : "Start"} failed: ${formatApiError(err)}`,
      ),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopDreamscapeSolver(),
    onSuccess: () => {
      setAutoCaptureArmed(false);
      void botQuery.refetch();
      setRunStartedAtSec(null);
      setMessage("Bot stopped.");
    },
    onError: (err: unknown) => setMessage(`Stop failed: ${String(err)}`),
  });

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));

  // Stack health, loudly: the bot cannot run without the API and Redis, and a
  // dead redis-server used to surface only as generic 500s on Play.
  const { connectivity } = useApiStatus();

  useEffect(() => {
    if (!autoCaptureArmed) return undefined;
    // The run must show real solve progress before a return to the start screen
    // can count as a win. Without this, the effect fires the instant the solver
    // is armed — still on the dreamscape_memory start screen — and falsely
    // reports "All items found". The all_item_found screen is an explicit win
    // and needs no such guard.
    if (
      solveState != null &&
      (solveState.settledRegions.length > 0 || solveState.clickedRegions.length > 0)
    ) {
      enteredGameplayRef.current = true;
    }
    const returnedToStartAfterSolving =
      terminalScreen === "dreamscape_memory" && enteredGameplayRef.current;
    if (terminalScreen === DREAMSCAPE_ALL_ITEM_FOUND_SCREEN || returnedToStartAfterSolving) {
      setAutoCaptureArmed(false);
      setRunStartedAtSec(null);
      setConfettiVisible(true);
      setMessage("All items found — Dreamscape solved.");
      const timer = window.setTimeout(() => setConfettiVisible(false), 4500);
      return () => window.clearTimeout(timer);
    }
    if (terminalScreen === DREAMSCAPE_TIME_UP_SCREEN) {
      setAutoCaptureArmed(false);
      setRunStartedAtSec(null);
      setConfettiVisible(false);
      setMessage("Time up — Dreamscape run lost.");
    }
    return undefined;
  }, [autoCaptureArmed, terminalScreen, solveState]);

  useEffect(() => {
    if (!autoCapture) return;
    if (!autoCaptureArmed || !botRunning || !instanceId || autoCaptureBusy.current) return;
    if (scenesQuery.isLoading || sceneQuery.isLoading) return;

    // Scene is now picked by word-detection / the operator, so the only auto
    // capture left is "new word in a known scene" — words the matched scene
    // doesn't yet map. (Unknown-scene capture went away with the title detector.)
    const hasNewWords = Boolean(matchedSlug) && unknownWords.length > 0;
    if (!hasNewWords) return;

    const key = [instanceId, mode, "new_word", matchedSlug, unknownWords.join("|")].join(":");
    if (autoCaptureKeys.current.has(key)) return;
    const alreadyQueued = hasDreamscapeNewCapture((capture) => {
      if (capture.reason !== "new_word" || capture.mode !== mode) return false;
      if (capture.sceneSlug !== matchedSlug) return false;
      const queuedWords = new Set(capture.words.map(normalizeWord));
      return unknownWords.some((word) => queuedWords.has(normalizeWord(word)));
    });
    if (alreadyQueued) {
      autoCaptureKeys.current.add(key);
      return;
    }

    autoCaptureKeys.current.add(key);
    autoCaptureBusy.current = true;
    captureLabelingScreenshot(instanceId, DREAMSCAPE_SCOPE)
      .then(({ ref }) => {
        addDreamscapeNewCapture({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          ref,
          reason: "new_word",
          createdAt: Date.now(),
          instanceId,
          mode,
          levelName: sceneTitle ?? "",
          sceneSlug: matchedSlug,
          sceneTitle,
          words: unknownWords,
        });
        setMessage(
          `New word captured: ${unknownWords.join(", ")} — open New to place it.`,
        );
      })
      .catch((err: unknown) => {
        setMessage(`Auto-capture failed: ${String(err)}`);
      })
      .finally(() => {
        autoCaptureBusy.current = false;
      });
  }, [
    autoCapture,
    autoCaptureArmed,
    botRunning,
    instanceId,
    matchedSlug,
    mode,
    sceneTitle,
    sceneQuery.isLoading,
    scenesQuery.isLoading,
    unknownWords,
  ]);

  return (
    <div className="mt-4 space-y-4">
      {connectivity === "api_offline" || connectivity === "redis_unreachable" ? (
        <div
          role="alert"
          className="rounded-md border border-red-500/50 bg-red-500/10 px-4 py-3 text-sm text-red-300"
        >
          {connectivity === "api_offline"
            ? "API is offline — the dashboard cannot reach the backend (uv run api)."
            : "Redis is unreachable — the bot cannot run. Start it (docker compose up -d redis or redis-server) and press Play again."}
        </div>
      ) : null}
      <div className="flex flex-wrap items-end gap-3">
        <AppListbox
          label="Instance"
          options={instanceOptions}
          value={instanceId}
          onChange={setInstanceId}
          loading={instancesLoading}
          placeholder="Select a device"
          inline
        />
        <AppListbox
          label="OCR"
          options={ocrLangOptions}
          value={ocrLangQuery.data?.lang ?? ""}
          onChange={(lang) => ocrLangMutation.mutate(lang)}
          loading={ocrLangQuery.isLoading || ocrLangMutation.isPending}
          placeholder="lang"
          minWidth={90}
          inline
        />
        {!botRunning ? (
          <Button
            variant="primary"
            disabled={
              startMutation.isPending || !instanceId || !scenarioKey || !overrideSlug
            }
            onClick={() => startMutation.mutate("start")}
            title={
              !instanceId
                ? "Select an instance before starting Dreamscape"
                : !scenarioKey
                  ? "No solver scenario is configured for this mode"
                  : !overrideSlug
                    ? "Pick a scene before starting Dreamscape"
                    : "Start the bot and Dreamscape solver"
            }
          >
            {startMutation.isPending ? solverPendingLabel : solverButtonLabel}
          </Button>
        ) : null}
        {botRunning ? (
          <span
            title="Bot is running the game loop"
            className="inline-flex items-center justify-center rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm"
          >
            Gaming
          </span>
        ) : null}
        {botRunning ? (
          <Button
            variant="primary"
            disabled={
              startMutation.isPending ||
              stopMutation.isPending ||
              !instanceId ||
              !scenarioKey
            }
            onClick={() => startMutation.mutate("restart")}
            title="Reset current screen and solver state, replace the pending solver task, and start Dreamscape again"
          >
            {startMutation.isPending ? "Restarting…" : "Restart"}
          </Button>
        ) : null}
        {botRunning ? (
          <Button
            variant="secondary"
            disabled={stopMutation.isPending || startMutation.isPending}
            onClick={() => stopMutation.mutate()}
            title="Stop the bot worker"
          >
            {stopMutation.isPending ? "Stopping…" : "Stop bot"}
          </Button>
        ) : null}
        <Button
          variant="secondary"
          disabled={!instanceId || captureMutation.isPending}
          onClick={() => captureMutation.mutate()}
          title="Capture the current device screen and open it in the labeling editor to mark anything new"
        >
          {captureMutation.isPending ? "Capturing…" : "Make screenshot"}
        </Button>
        <span
          className={`inline-flex items-center gap-1.5 text-xs ${
            botRunning ? "text-emerald-400" : "text-wos-text-muted"
          }`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              botRunning ? "bg-emerald-400" : "bg-wos-text-muted/50"
            }`}
          />
          {botRunning ? "bot running" : "bot stopped"}
        </span>
      </div>

      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <section className="panel">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">Current screen</h2>
            <button
              type="button"
              className="rounded-md border border-wos-border px-2 py-1 text-xs text-wos-text-muted hover:text-wos-text"
              onClick={() => setDebugOpen(true)}
              title="Show the live frame with every scene point and the solver's planned taps"
            >
              Debug view
            </button>
          </div>
          <div className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep">
            {showImage && cardImageUrl ? (
              <img
                src={cardImageUrl}
                alt="live device frame"
                className="h-full w-full object-contain"
                onError={() => setFailedImageUrl(cardImageUrl)}
              />
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-sm text-wos-text-muted">
                {liveFramePlaceholder}
              </div>
            )}
            {confettiVisible ? <WinConfetti /> : null}
          </div>
        </section>
        {debugOpen ? (
          <DreamscapeDebugOverlay
            imageUrl={showImage ? cardImageUrl : null}
            scene={sceneQuery.data ?? null}
            badges={badges}
            wordRunState={wordRunState}
            onClose={() => setDebugOpen(false)}
          />
        ) : null}
        <WordSearchPanel
          badges={badges}
          status={status}
          sceneTitle={sceneTitle}
          matchedSlug={matchedSlug}
          overrideSlug={overrideSlug}
          sceneOptions={sceneOptions}
          seasonOptions={seasonOptions}
          seasonValue={seasonValue}
          onSelectSeason={setSeasonChoice}
          onSelectScene={selectScene}
          detectedCount={detectedWords.length}
          scenesLoading={scenesQuery.isLoading}
          scenesError={scenesQuery.isError}
          wordKnown={wordKnown}
          wordRunState={wordRunState}
          loading={wordOcrQuery.isFetching}
          instanceSelected={Boolean(instanceId)}
        />
      </div>
      <SolveLogPanel events={solveState?.events ?? []} />
    </div>
  );
}

function WinConfetti() {
  const pieces = Array.from({ length: 32 }, (_, i) => i);
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {pieces.map((i) => {
        const left = (i * 23) % 100;
        const delay = (i % 8) * 0.12;
        const duration = 1.7 + (i % 5) * 0.18;
        const hue =
          i % 4 === 0
            ? "bg-emerald-300"
            : i % 4 === 1
              ? "bg-sky-300"
              : i % 4 === 2
                ? "bg-amber-300"
                : "bg-rose-300";
        return (
          <span
            key={i}
            className={`absolute -top-4 h-2.5 w-1.5 rounded-sm ${hue}`}
            style={{
              left: `${left}%`,
              animation: `dreamscape-confetti ${duration}s ${delay}s ease-out forwards`,
              transform: `rotate(${(i * 37) % 180}deg)`,
            }}
          />
        );
      })}
      <style jsx>{`
        @keyframes dreamscape-confetti {
          0% {
            opacity: 0;
            translate: 0 -10%;
          }
          10% {
            opacity: 1;
          }
          100% {
            opacity: 0;
            translate: 0 1150%;
            rotate: 540deg;
          }
        }
      `}</style>
    </div>
  );
}

function eventTone(kind: string): string {
  if (kind.includes("error") || kind.includes("rejected")) {
    return "border-rose-400/50 bg-rose-500/10 text-rose-200";
  }
  if (kind.includes("helper") || kind === "learned") {
    return "border-amber-300/50 bg-amber-500/10 text-amber-100";
  }
  if (kind === "click" || kind === "retry") {
    return "border-sky-300/50 bg-sky-500/10 text-sky-100";
  }
  if (kind === "mapped" || kind === "settled") {
    return "border-emerald-300/50 bg-emerald-500/10 text-emerald-100";
  }
  if (kind === "unmapped" || kind === "retry_exhausted") {
    return "border-orange-300/50 bg-orange-500/10 text-orange-100";
  }
  return "border-wos-border-subtle bg-wos-panel-raised text-wos-text-muted";
}

function formatEventTime(at: number | null): string {
  if (at == null) return "";
  const date = new Date(at * 1000);
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function eventDetails(event: DreamscapeSolveEvent): string[] {
  const details: string[] = [];
  if (event.word) details.push(event.word);
  if (event.region) details.push(event.region);
  if (event.key && event.key !== event.word.toLowerCase()) details.push(event.key);
  if (event.x != null && event.y != null) details.push(`${event.x},${event.y}`);
  if (event.reason) details.push(event.reason);
  if (event.ok === false) details.push("rejected");
  return details;
}

function SolveLogPanel({ events }: { events: DreamscapeSolveEvent[] }) {
  const visible = events.slice(-60).reverse();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const json = JSON.stringify(events, null, 2);
    try {
      await navigator.clipboard.writeText(json);
    } catch {
      // Fallback for non-secure contexts / older browsers
      const ta = document.createElement("textarea");
      ta.value = json;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } finally {
        document.body.removeChild(ta);
      }
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section className="panel">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Solver log</h2>
        <div className="flex items-center gap-2">
          <span className="meta">{events.length ? `${events.length} events` : "idle"}</span>
          <button
            type="button"
            onClick={handleCopy}
            disabled={!events.length}
            className="rounded border border-wos-border-subtle bg-wos-panel-raised px-2 py-1 text-xs font-medium text-wos-text-muted transition hover:text-wos-text disabled:cursor-not-allowed disabled:opacity-40"
            title="Copy solver actions as JSON"
          >
            {copied ? "Copied ✓" : "Copy JSON"}
          </button>
        </div>
      </div>
      {visible.length ? (
        <div className="max-h-72 overflow-y-auto rounded border border-wos-border-subtle bg-wos-bg-deep/40">
          <table className="w-full min-w-[680px] text-left text-xs">
            <thead className="sticky top-0 bg-wos-panel-raised text-wos-text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Iter</th>
                <th className="px-3 py-2 font-medium">Event</th>
                <th className="px-3 py-2 font-medium">Message</th>
                <th className="px-3 py-2 font-medium">Data</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((event, index) => {
                const details = eventDetails(event);
                return (
                  <tr
                    key={`${event.at ?? "na"}-${event.kind}-${index}`}
                    className="border-t border-wos-border-subtle/70"
                  >
                    <td className="whitespace-nowrap px-3 py-2 text-wos-text-muted">
                      {formatEventTime(event.at) || "—"}
                    </td>
                    <td className="px-3 py-2 tabular-nums text-wos-text-muted">
                      {event.iteration ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 font-medium ${eventTone(
                          event.kind,
                        )}`}
                      >
                        {event.kind || "event"}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-wos-text">{event.message || "—"}</td>
                    <td className="px-3 py-2 text-wos-text-muted">
                      {details.length ? details.join(" · ") : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="meta">Start the solver to see OCR, mapping, click, and helper history.</p>
      )}
    </section>
  );
}

/** Right-hand panel: scene detected from the on-screen words, a manual override
 * selector, and the word badges. */
function WordSearchPanel({
  badges,
  status,
  sceneTitle,
  matchedSlug,
  overrideSlug,
  sceneOptions,
  seasonOptions,
  seasonValue,
  onSelectSeason,
  onSelectScene,
  detectedCount,
  scenesLoading,
  scenesError,
  wordKnown,
  wordRunState,
  loading,
  instanceSelected,
}: {
  badges: WordBadge[];
  status: LiveStatus;
  sceneTitle: string | null;
  matchedSlug: string | null;
  /** Operator-picked scene (the solver plays exactly this one). */
  overrideSlug: string | null;
  sceneOptions: { value: string; label: string }[];
  seasonOptions: { value: string; label: string }[];
  seasonValue: string;
  onSelectSeason: (season: string) => void;
  onSelectScene: (slug: string) => void;
  /** Count of actionable words feeding detection. */
  detectedCount: number;
  scenesLoading: boolean;
  scenesError: boolean;
  /** Per-badge coverage aligned to `badges` (mapped / unmapped / unknown). */
  wordKnown: (boolean | null)[];
  /** Per-badge live solver state aligned to `badges`. */
  wordRunState: DreamscapeWordRunState[];
  loading: boolean;
  instanceSelected: boolean;
}) {
  return (
    <section className="panel">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">
          Detected words{" "}
          <span className="text-sm font-normal text-wos-text-muted">
            ({badges.length})
          </span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {loading ? <span className="meta">refreshing…</span> : null}
          <ScreenStatusPill
            detected={status.screenDetected || Boolean(matchedSlug)}
            screen={status.detectedScreen || sceneTitle || matchedSlug || undefined}
          />
        </div>
      </div>

      {instanceSelected ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <AppListbox
              label="Season"
              options={seasonOptions}
              value={seasonValue}
              onChange={onSelectSeason}
              loading={scenesLoading}
              minWidth={130}
              inline
            />
            <AppListbox
              label="Scene"
              options={sceneOptions}
              value={matchedSlug ?? ""}
              onChange={onSelectScene}
              loading={scenesLoading}
              placeholder={
                detectedCount === 0 ? "Waiting for words…" : "Pick a scene"
              }
              minWidth={200}
              inline
            />
            <span className="meta">
              {overrideSlug
                ? "the solver plays this scene"
                : scenesError
                  ? "scene list failed"
                  : "pick a scene to play"}
            </span>
          </div>
          <DetectedWordsBadges
            badges={badges}
            wordKnown={wordKnown}
            wordRunState={wordRunState}
          />
        </>
      ) : null}

      {!instanceSelected ? (
        <p className="meta">Select an instance to read the level&apos;s words.</p>
      ) : null}
    </section>
  );
}


/** Full-screen debug popup: the live frame with every scene point drawn on it.
 * Green = a word currently on the pills maps to this point (the solver's next
 * taps); red = already clicked this run; grey = the rest of the scene map. */
function DreamscapeDebugOverlay({
  imageUrl,
  scene,
  badges,
  wordRunState,
  onClose,
}: {
  imageUrl: string | null;
  scene: DreamscapeSceneDetail | null;
  badges: WordBadge[];
  wordRunState: DreamscapeWordRunState[];
  onClose: () => void;
}) {
  const rect = scene?.scene_rect ?? { left: 0, top: 0, width: 100, height: 100 };
  const toFrame = (p: { xPct: number; yPct: number }) => ({
    x: rect.left + (p.xPct / 100) * rect.width,
    y: rect.top + (p.yPct / 100) * rect.height,
  });
  const onScreen = new Set(
    badges.map((b) => normalizeWord(b.text)).filter(Boolean),
  );
  const clicked = new Set(
    badges
      .map((b, i) => ({ b, s: wordRunState[i] }))
      .filter((x) => x.s === "clicked" || x.s === "found")
      .map((x) => normalizeWord(x.b.text)),
  );
  const pointNames = (p: { name: string; aliases?: string[] }) => [
    normalizeWord(p.name),
    ...(p.aliases ?? []).map(normalizeWord),
  ];
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      onClick={onClose}
    >
      <div
        className="max-h-full overflow-auto rounded-lg bg-wos-panel p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center justify-between gap-6">
          <div className="text-sm font-semibold">
            {scene ? `${scene.title} (${scene.slug})` : "no scene picked"}
          </div>
          <button
            type="button"
            className="rounded border border-wos-border px-2 py-0.5 text-xs"
            onClick={onClose}
          >
            close
          </button>
        </div>
        <div className="relative w-[360px]">
          {imageUrl ? (
            <img src={imageUrl} alt="frame" className="w-full rounded" />
          ) : (
            <div className="flex aspect-[9/16] items-center justify-center text-sm text-wos-text-muted">
              no live frame
            </div>
          )}
          {(scene?.points ?? []).map((p) => {
            const f = toFrame(p);
            const names = pointNames(p);
            const isClicked = names.some((n) => clicked.has(n));
            const isPlanned = !isClicked && names.some((n) => onScreen.has(n));
            const color = isClicked ? "#ef4444" : isPlanned ? "#22c55e" : "#94a3b8";
            return (
              <div
                key={p.n}
                title={`${p.name}${p.aliases?.length ? ` (${p.aliases.join(", ")})` : ""}`}
                style={{
                  position: "absolute",
                  left: `${f.x}%`,
                  top: `${f.y}%`,
                  transform: "translate(-50%, -50%)",
                }}
              >
                <div
                  style={{
                    width: isPlanned || isClicked ? 18 : 8,
                    height: isPlanned || isClicked ? 18 : 8,
                    borderRadius: "50%",
                    border: `2px solid ${color}`,
                    background: isPlanned ? "rgba(34,197,94,.25)" : "transparent",
                  }}
                />
                {isPlanned || isClicked ? (
                  <div
                    style={{
                      position: "absolute",
                      left: 20,
                      top: -2,
                      whiteSpace: "nowrap",
                      font: "11px monospace",
                      color,
                      textShadow: "0 0 3px #000",
                    }}
                  >
                    {p.name}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="mt-2 text-xs text-wos-text-muted">
          <span style={{ color: "#22c55e" }}>●</span> next taps ·{" "}
          <span style={{ color: "#ef4444" }}>●</span> clicked ·{" "}
          <span style={{ color: "#94a3b8" }}>●</span> scene map
        </div>
      </div>
    </div>
  );
}
