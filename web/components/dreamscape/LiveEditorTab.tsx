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
  levelNameRead,
  statusFromDetectedScreen,
  wordBadges,
} from "@/lib/dreamscape-live";
import {
  addDreamscapeNewCapture,
  hasDreamscapeNewCapture,
} from "@/lib/dreamscape-new-captures";
import type {
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
  const autoCaptureKeys = useRef<Set<string>>(new Set());
  const autoCaptureBusy = useRef(false);

  // ── Live polling (status + detected words) ──
  const screenQuery = useQuery({
    queryKey: ["dreamscape-screen", instanceId],
    queryFn: () => fetchScreenDetect(instanceId),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });
  const ocrQuery = useQuery({
    queryKey: ["dreamscape-ocr", instanceId, wordsRef],
    queryFn: () =>
      fetchRegionOcr(instanceId, [levelNameRegion, ...wordRegions]),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });

  const status = useMemo(
    () => statusFromDetectedScreen(screenQuery.data?.detected_screen),
    [screenQuery.data],
  );
  const terminalScreen = status.detectedScreen;
  const badges = useMemo(
    () => wordBadges(ocrQuery.data?.rows, wordRegions),
    [ocrQuery.data, wordRegions],
  );
  const levelName = useMemo(
    () => levelNameRead(ocrQuery.data?.rows, levelNameRegion),
    [ocrQuery.data, levelNameRegion],
  );

  // ── Scene/word coverage (green = the bot has it, red-orange = it doesn't) ──
  // Match the OCR'd level name to a scene in the solver DB; if found, pull its
  // item names so each detected word can be flagged as mapped (solvable) or not.
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
        .filter((b, i) => wordKnown[i] === false && !b.dimmed && b.text.trim())
        .map((b) => b.text.trim()),
    [badges, wordKnown],
  );
  const mode = wordRegions === DREAMSCAPE_MULTIPLAYER_WORD_REGIONS ? "multiplayer" : "solo";

  // Live device frame, 1:1 with the approvals page: the worker's rolling
  // preview PNG, refreshed the instant the instance revision advances (SSE
  // below) by bumping a cache-busting tick.
  const [imageTick, setImageTick] = useState(0);
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);
  const cardImageUrl = instanceId
    ? `${clickApprovalImageUrl(instanceId, "live")}&tick=${imageTick}`
    : null;
  const showImage = Boolean(cardImageUrl) && cardImageUrl !== failedImageUrl;

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
  const botQuery = useQuery({
    queryKey: ["bot-status"],
    queryFn: fetchBotStatus,
    refetchInterval: 4000,
  });
  const botRunning = Boolean(botQuery.data?.running);
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
  const liveFramePlaceholder = !instanceId
    ? "Select an instance"
    : !botRunning
      ? "Bot stopped — rolling preview is not being published."
      : instanceDetailQuery.isLoading
        ? "Checking rolling preview…"
        : !instanceDetail?.preview_available
          ? "No rolling preview PNG from worker yet."
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
      setMessage(botRunning ? "Queueing Dreamscape solver..." : "Starting bot worker...");
      if (!botRunning) await startLocalBot();
      setMessage("Queueing Dreamscape solver...");
      const queued = await createQueueTask({
        scenario_key: scenario,
        instance_id: selectedInstance,
        scheduled_at: Date.now() / 1000,
        priority: 90_000,
      });
      return queued;
    },
    onSuccess: (queued) => {
      setAutoCaptureArmed(true);
      botQuery.refetch();
      setMessage(`Dreamscape solver queued (${queued.task_id}).`);
    },
    onError: (err: unknown) => setMessage(`Start failed: ${formatApiError(err)}`),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopLocalBot(),
    onSuccess: () => {
      setAutoCaptureArmed(false);
      botQuery.refetch();
      setMessage("Bot stopped.");
    },
    onError: (err: unknown) => setMessage(`Stop failed: ${String(err)}`),
  });

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));

  useEffect(() => {
    const returnedToStartAfterSolving =
      terminalScreen === "dreamscape_memory" && autoCaptureArmed;
    if (terminalScreen === DREAMSCAPE_ALL_ITEM_FOUND_SCREEN || returnedToStartAfterSolving) {
      setAutoCaptureArmed(false);
      setConfettiVisible(true);
      setMessage("All items found — Dreamscape solved.");
      const timer = window.setTimeout(() => setConfettiVisible(false), 4500);
      return () => window.clearTimeout(timer);
    }
    if (terminalScreen === DREAMSCAPE_TIME_UP_SCREEN) {
      setAutoCaptureArmed(false);
      setConfettiVisible(false);
      setMessage("Time up — Dreamscape run lost.");
    }
    return undefined;
  }, [autoCaptureArmed, terminalScreen]);

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
        {botRunning ? (
          <span
            title="Bot is running the game loop"
            className="inline-flex items-center justify-center rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm"
          >
            Gaming
          </span>
        ) : (
          <Button
            variant="primary"
            disabled={startMutation.isPending}
            onClick={() => startMutation.mutate()}
            title={
              !instanceId
                ? "Select an instance before starting Dreamscape"
                : !scenarioKey
                  ? "No solver scenario is configured for this mode"
                  : "Start the bot and queue this mode's Dreamscape solver"
            }
          >
            {startMutation.isPending ? "Starting…" : "Play"}
          </Button>
        )}
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
          loading={ocrQuery.isFetching}
          instanceSelected={Boolean(instanceId)}
        />
      </div>
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
          <DetectedWordsBadges badges={badges} wordKnown={wordKnown} />
        </>
      ) : null}

      {!instanceSelected ? (
        <p className="meta">Select an instance to read the level&apos;s words.</p>
      ) : null}
    </section>
  );
}
