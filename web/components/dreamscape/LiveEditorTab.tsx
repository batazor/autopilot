"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import {
  ApiError,
  captureLabelingScreenshot,
  clickApprovalImageUrl,
  createQueueTask,
  fetchBotStatus,
  fetchInstanceDetail,
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  fetchRegionOcr,
  fetchScreenDetect,
  resetCurrentScreen,
  startLocalBot,
  stopLocalBot,
} from "@/lib/api";
import {
  DREAMSCAPE_ALL_ITEM_FOUND_SCREEN,
  DREAMSCAPE_LEVEL_NAME_REGION,
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_TIME_UP_SCREEN,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  isActionableDreamscapeWord,
  levelNameRead,
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
  LevelNameRead,
  LiveStatus,
  WordBadge,
} from "@/lib/dreamscape-live";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import { DetectedWordsBadges } from "./DetectedWordsBadges";
import { Button } from "./Button";

const POLL_MS = 1500;

/** Mirror the solver's key normalization (config exec `_normalize_word`):
 * lower-case and collapse inner whitespace so OCR text matches scene item keys. */
function normalizeWord(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

function normalizeLevelName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\b\d+(?:\.\d+)?\s*%.*$/i, " ")
    .replace(/(?<=[a-z])[\|/\\]+(?=[a-z])/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

/** Drop a trailing season tag ("Garden (S3)" → "Garden") so a recognised level
 * name matches a scene title regardless of its season suffix. */
function stripSeasonTag(title: string): string {
  return title.replace(/\s*\(s\d+\)\s*$/i, "");
}

function sceneMatchesLevel(
  scene: { slug: string; title: string },
  levelKey: string,
): boolean {
  return (
    normalizeLevelName(stripSeasonTag(scene.title)) === levelKey ||
    normalizeLevelName(scene.slug) === levelKey
  );
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
  /** Title region whose text is the recognised level name (shared by both modes). */
  levelNameRegion?: string;
  /** Scenario key enqueued by "Start solving" (the mode's fast solve loop). */
  scenarioKey?: string;
};

export function LiveEditorTab({
  wordRegions = DREAMSCAPE_WORD_REGIONS,
  wordsRef = DREAMSCAPE_WORDS_REF,
  levelNameRegion = DREAMSCAPE_LEVEL_NAME_REGION,
  scenarioKey,
}: LiveEditorTabProps = {}) {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const router = useRouter();
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

  // ── Live polling (status + detected words) ──
  const screenQuery = useQuery({
    queryKey: ["dreamscape-screen", instanceId],
    queryFn: () => fetchScreenDetect(instanceId),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });
  // Read ONLY the level-name title each tick. Word slots are intentionally not
  // OCR'd here: until the title resolves to a known scene we don't touch the
  // word buttons (see wordOcrQuery), so a "scene not in DB" frame never burns
  // OCR on words.
  const levelOcrQuery = useQuery({
    queryKey: ["dreamscape-level-ocr", instanceId],
    queryFn: () => fetchRegionOcr(instanceId, [levelNameRegion]),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });
  const instanceDetailQuery = useQuery({
    queryKey: ["dreamscape-instance-detail", instanceId],
    queryFn: () => fetchInstanceDetail(instanceId),
    enabled: Boolean(instanceId),
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
  const levelName = useMemo(
    () => levelNameRead(levelOcrQuery.data?.rows, levelNameRegion),
    [levelOcrQuery.data, levelNameRegion],
  );

  // ── Scene match: title → scene in the solver DB ──
  // Match the OCR'd level name to a scene; word slots are read only once this
  // resolves — the gate: no scene caught → no word OCR.
  const scenesQuery = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const matchedSlug = useMemo(() => {
    const lvl = normalizeLevelName(levelName?.text ?? "");
    if (!lvl) return null;
    const scenes = scenesQuery.data?.scenes ?? [];
    const hit = scenes.find((s) => sceneMatchesLevel(s, lvl));
    if (hit) return hit.slug;
    const active = scenesQuery.data?.active;
    return active && normalizeLevelName(active) === lvl ? active : null;
  }, [levelName, scenesQuery.data]);
  const sceneMatched = Boolean(matchedSlug);

  // ── Word slots: OCR them only once the scene is caught ──
  // The instant the title matches a scene we begin reading word buttons (the
  // same frame if the match landed on it); before that they are never polled.
  const wordOcrQuery = useQuery({
    queryKey: ["dreamscape-word-ocr", instanceId, wordsRef],
    queryFn: () => fetchRegionOcr(instanceId, [...wordRegions]),
    enabled: Boolean(instanceId) && sceneMatched,
    refetchInterval: POLL_MS,
  });
  // Empty when no scene is matched so stale word badges don't linger after the
  // gate closes (React Query keeps the last data while a query is disabled).
  const rawBadges = useMemo(
    () => (sceneMatched ? wordBadges(wordOcrQuery.data?.rows, wordRegions) : []),
    [sceneMatched, wordOcrQuery.data, wordRegions],
  );
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

  // ── Bot/instance status ──
  const botQuery = useQuery({
    queryKey: ["bot-status"],
    queryFn: fetchBotStatus,
    refetchInterval: 4000,
  });
  const botRunning = Boolean(botQuery.data?.running);
  const solverButtonLabel = "Play";
  const solverPendingLabel = "Starting...";

  // Live device frame, 1:1 with the approvals page: the worker's rolling
  // preview PNG, refreshed the instant the instance revision advances (SSE
  // below) by bumping a cache-busting tick.
  const [imageTick, setImageTick] = useState(0);
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

  // Grab the current device frame as a fresh labeling capture and jump to the
  // full labeling editor on it, so an operator can label anything new on screen
  // ("доразметить") without leaving the live view to set it up by hand.
  const captureMutation = useMutation({
    mutationFn: () => captureLabelingScreenshot(instanceId, DREAMSCAPE_SCOPE),
    onSuccess: ({ ref }) => {
      const q = new URLSearchParams({ module: DREAMSCAPE_SCOPE, ref });
      router.push(`/labeling?${q.toString()}`);
    },
    onError: (err: unknown) => setMessage(`Screenshot failed: ${String(err)}`),
  });

  // ── Bot control: start the worker + enqueue this mode's fast solve loop ──
  const liveFramePlaceholder = !instanceId
    ? "Select an instance"
    : !botRunning
      ? "Bot stopped — rolling preview is not being published."
      : instanceDetailQuery.isLoading
        ? "Checking rolling preview…"
        : !instanceDetail?.preview_available
          ? "No rolling preview PNG from worker yet."
          : previewIsFromStaleRun
            ? "Waiting for a fresh frame from the new Dreamscape run..."
            : cardImageUrl === failedImageUrl
            ? "Rolling preview image failed to load."
            : "Waiting for a live frame…";

  const startMutation = useMutation({
    // Start the local worker (idempotent if already up), then enqueue the
    // solver so it begins reading + tapping the level right away.
    mutationFn: async () => {
      const scenario = (scenarioKey || "").trim();
      const selectedInstance = instanceId.trim();
      if (!scenario) throw new Error("No solver scenario is configured for this mode.");
      if (!selectedInstance) throw new Error("Select an instance before starting Dreamscape.");
      const startedAt = Date.now() / 1000;
      setRunStartedAtSec(startedAt);
      enteredGameplayRef.current = false;
      setConfettiVisible(false);
      setFailedImageUrl(null);
      setImageTick((t) => t + 1);
      setMessage("Preparing a fresh Dreamscape run...");
      await resetCurrentScreen(selectedInstance);
      void screenQuery.refetch();
      void levelOcrQuery.refetch();
      void wordOcrQuery.refetch();
      setMessage(botRunning ? "Bot is already running." : "Starting bot worker...");
      if (!botRunning) await startLocalBot();
      setMessage("Starting Dreamscape solver...");
      const queued = await createQueueTask({
        scenario_key: scenario,
        instance_id: selectedInstance,
        scheduled_at: Date.now() / 1000,
        priority: 90_000,
        replace_existing: true,
      });
      return queued;
    },
    onSuccess: (queued) => {
      setAutoCaptureArmed(true);
      void botQuery.refetch();
      void instanceDetailQuery.refetch();
      void screenQuery.refetch();
      void levelOcrQuery.refetch();
      void wordOcrQuery.refetch();
      setMessage(`Dreamscape solver started (${queued.task_id}).`);
    },
    onError: (err: unknown) => setMessage(`Start failed: ${formatApiError(err)}`),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopLocalBot(),
    onSuccess: () => {
      setAutoCaptureArmed(false);
      void botQuery.refetch();
      setRunStartedAtSec(null);
      setMessage("Bot stopped.");
    },
    onError: (err: unknown) => setMessage(`Stop failed: ${String(err)}`),
  });

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));

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
    if (!autoCaptureArmed || !botRunning || !instanceId || autoCaptureBusy.current) return;
    if (scenesQuery.isLoading || sceneQuery.isLoading) return;

    const levelText = (levelName?.text ?? "").trim();
    const unknownScene =
      Boolean(levelText) &&
      !levelName?.dimmed &&
      !matchedSlug &&
      !scenesQuery.isError;
    const hasNewWords = Boolean(matchedSlug) && unknownWords.length > 0;
    if (!unknownScene && !hasNewWords) return;

    const reason = unknownScene ? "unknown_scene" : "new_word";
    const levelKey = normalizeLevelName(levelText);
    const key = [
      instanceId,
      mode,
      reason,
      matchedSlug || levelKey,
      unknownWords.join("|"),
    ].join(":");
    if (autoCaptureKeys.current.has(key)) return;
    const alreadyQueued = hasDreamscapeNewCapture((capture) => {
      if (capture.reason !== reason || capture.mode !== mode) return false;
      if (reason === "unknown_scene") {
        return normalizeLevelName(capture.levelName) === levelKey;
      }
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
          reason,
          createdAt: Date.now(),
          instanceId,
          mode,
          levelName: levelText,
          sceneSlug: matchedSlug,
          sceneTitle: sceneQuery.data?.title ?? null,
          words: unknownScene ? [] : unknownWords,
        });
        setMessage(
          reason === "unknown_scene"
            ? "Unknown Dreamscape scene captured — open New to assign it."
            : `New word captured: ${unknownWords.join(", ")} — open New to place it.`,
        );
      })
      .catch((err: unknown) => {
        setMessage(`Auto-capture failed: ${String(err)}`);
      })
      .finally(() => {
        autoCaptureBusy.current = false;
      });
  }, [
    autoCaptureArmed,
    botRunning,
    instanceId,
    levelName,
    matchedSlug,
    mode,
    sceneQuery.data,
    sceneQuery.isLoading,
    scenesQuery.isError,
    scenesQuery.isLoading,
    unknownWords,
  ]);

  return (
    <div className="mt-4 space-y-4">
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
        {!botRunning ? (
          <Button
            variant="primary"
            disabled={startMutation.isPending || !instanceId || !scenarioKey}
            onClick={() => startMutation.mutate()}
            title={
              !instanceId
                ? "Select an instance before starting Dreamscape"
                : !scenarioKey
                  ? "No solver scenario is configured for this mode"
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
            variant="secondary"
            disabled={stopMutation.isPending}
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
          <h2 className="mb-3 text-base font-semibold">Current screen</h2>
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
        <WordSearchPanel
          badges={badges}
          levelName={levelName}
          status={status}
          sceneTitle={sceneQuery.data?.title ?? null}
          matchedSlug={matchedSlug}
          scenesLoading={scenesQuery.isLoading}
          scenesError={scenesQuery.isError}
          wordKnown={wordKnown}
          wordRunState={wordRunState}
          loading={levelOcrQuery.isFetching || wordOcrQuery.isFetching}
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

/** Right-hand panel mirroring TestTab: OCR title, matched scene, and word badges. */
function WordSearchPanel({
  badges,
  levelName,
  status,
  sceneTitle,
  matchedSlug,
  scenesLoading,
  scenesError,
  wordKnown,
  wordRunState,
  loading,
  instanceSelected,
}: {
  badges: WordBadge[];
  levelName: LevelNameRead | null;
  status: LiveStatus;
  sceneTitle: string | null;
  matchedSlug: string | null;
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
          <p className="meta mb-3">
            Title (OCR):{" "}
            <span
              className={
                levelName && !levelName.dimmed
                  ? "text-wos-text"
                  : "text-wos-text-muted"
              }
            >
              {levelName?.text ||
                (levelName?.status === "empty" ? "— not recognised —" : "—")}
            </span>
            {levelName?.confidence != null
              ? ` · ${Math.round(levelName.confidence * 100)}%`
              : ""}
            {sceneTitle ? (
              <>
                {" "}
                · Scene: <span className="text-emerald-300">{sceneTitle}</span>
              </>
            ) : scenesLoading ? (
              " · loading scenes…"
            ) : matchedSlug ? (
              " · loading scene…"
            ) : scenesError ? (
              " · scene list failed"
            ) : levelName?.text ? (
              " · scene not in DB"
            ) : null}
          </p>
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
