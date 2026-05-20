"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { CopyButton } from "@/components/CopyButton";
import { AppSelect } from "@/components/AppSelect";
import { AppCheckbox } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { Icon, type IconName } from "@/components/ui/Icon";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import {
  clearPendingApproval,
  clearQueueAll,
  clickApprovalImageUrl,
  fetchAreaRegionProbe,
  fetchClickApproval,
  fetchNotifications,
  overlayTestImageUrl,
  resetCurrentScreen,
  setApprovalEnabled,
  submitDecision,
} from "@/lib/api";
import type {
  AreaRegionProbeResult,
  ClickApprovalView,
  NotificationEvent,
  ScenarioProgress,
} from "@/lib/types";
import { debugRunHref, editDslHref, overlayTestHref } from "@/lib/debug-links";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
const NOTIFICATIONS_MAX_AGE_S = 30;
const TOAST_VISIBLE_MS = 6000;
const TICK_MS = 100;
const DOCUMENT_TITLE_BASE = "Click approvals · WOS Autopilot";

type Toast = NotificationEvent & { createdAt: number; expiresAt: number };

type Decision = "approve" | "reject" | "skip";
// Track which control is currently in flight so we can disable only it
// (operators routinely change their mind between approve/reject before the
// previous request returns, and the old "global busy flag" pattern made
// that impossible).
type BusyAction = Decision | "toggle" | "clear-pending" | "clear-queue" | "reset-screen" | null;

const SCREENSHOT_SOURCE_OPTIONS = [
  { value: "capture", label: "Captured (request)" },
  { value: "live", label: "Live rolling" },
];

/** "Just now" / "12s" / "2m 04s" — used for the pending-since indicator and
 *  also for toast freshness. Kept dependency-free so we don't pull a date lib. */
function formatElapsed(ms: number): string {
  if (ms < 1000) return "just now";
  const totalS = Math.floor(ms / 1000);
  if (totalS < 60) return `${totalS}s`;
  const m = Math.floor(totalS / 60);
  const s = totalS % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export default function ApprovalsPage() {
  const searchParams = useSearchParams();
  const { instanceId, instancesError } = useFleet();
  const [view, setView] = useState<ClickApprovalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [imageSource, setImageSource] =
    useState<"capture" | "live">("capture");
  const [imageTick, setImageTick] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showPayload, setShowPayload] = useState(false);
  const [showReset, setShowReset] = useState(false);
  const [showProbe, setShowProbe] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [regionProbe, setRegionProbe] = useState<AreaRegionProbeResult | null>(null);
  const [probeRegion, setProbeRegion] = useState("");
  const [probeThreshold, setProbeThreshold] = useState(0.9);
  const [probeTick, setProbeTick] = useState(0);
  const [probeError, setProbeError] = useState<string | null>(null);
  // Tracks when the current pending request first appeared so we can render
  // a "Waiting Xs" timer. We use the raw payload's trace_id (or a stable
  // synthetic key) as the change signal — `has_pending` flipping alone isn't
  // enough because a new request can arrive in the same tick the previous one
  // was resolved.
  const [pendingSinceMs, setPendingSinceMs] = useState<number | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());
  // Inline confirm state for destructive actions — replaces window.confirm()
  // which was the only thing in this page that broke the dark-themed look.
  const [confirmAction, setConfirmAction] = useState<
    "clear-queue" | "clear-pending" | null
  >(null);
  // Notifications are non-destructive in Redis (the Streamlit page kept its
  // dedup set in ``st.session_state``). Track the IDs we've already toasted
  // so re-polling the list doesn't re-fire the same event in a loop.
  const seenNotificationsRef = useRef<Set<string>>(new Set());
  // Remember which pending-request key was last seen so we can bump the
  // image cache key only when the underlying request actually changes
  // (otherwise we'd thrash the browser's decoded-image cache every second).
  const lastPendingKeyRef = useRef<string>("");

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      const data = await fetchClickApproval(instanceId, imageSource);
      setView(data);
      // Stable image key: the trace id is unique per pending request and we
      // fall back to a coarse "(no request)" sentinel when nothing's pending
      // so the canvas doesn't keep refetching the placeholder.
      const nextKey = data.has_pending ? data.trace_id || "(pending)" : "(idle)";
      if (nextKey !== lastPendingKeyRef.current) {
        lastPendingKeyRef.current = nextKey;
        setImageTick((t) => t + 1);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId, imageSource]);

  const pollNotifications = useCallback(async () => {
    if (!instanceId) return;
    try {
      const items = await fetchNotifications(
        instanceId,
        seenNotificationsRef.current,
        NOTIFICATIONS_MAX_AGE_S,
      );
      if (!items.length) return;
      const now = Date.now();
      const next: Toast[] = [];
      for (const ev of items) {
        if (!ev.id) continue;
        seenNotificationsRef.current.add(ev.id);
        next.push({ ...ev, createdAt: now, expiresAt: now + TOAST_VISIBLE_MS });
      }
      if (next.length) {
        setToasts((prev) => {
          const merged = [...prev, ...next];
          return merged.slice(-10);
        });
      }
    } catch (e) {
      if (process.env.NODE_ENV !== "production") {
        console.warn("notifications poll failed", e);
      }
    }
  }, [instanceId]);

  const refreshProbe = useCallback(async () => {
    if (!instanceId) return;
    try {
      const data = await fetchAreaRegionProbe(instanceId, {
        region: probeRegion || undefined,
        threshold: probeThreshold,
      });
      setRegionProbe(data);
      if (!probeRegion && data.selected_region) {
        setProbeRegion(data.selected_region);
      }
      setProbeTick((t) => t + 1);
      setProbeError(null);
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId, probeRegion, probeThreshold]);

  const approvalStreamTopics = useMemo(
    () =>
      showProbe
        ? ["approval", "notifications", "instance"]
        : ["approval", "notifications"],
    [showProbe],
  );

  useDashboardEventStream({
    topics: approvalStreamTopics,
    instanceId: instanceId || undefined,
    enabled: autoRefresh && !!instanceId,
    onEvent: (topic) => {
      if (topic === "approval") void refresh();
      if (topic === "notifications") void pollNotifications();
      if (topic === "instance" && showProbe) void refreshProbe();
    },
    onFallbackPoll: async () => {
      await refresh();
      await pollNotifications();
      if (showProbe) await refreshProbe();
    },
  });

  // Reset toast dedup memory when switching instances — events are scoped
  // per instance and a fresh tab on a different instance shouldn't inherit
  // a previously-toasted ID set (small but real risk of dropping a relevant
  // event with a colliding UUID after a worker restart).
  useEffect(() => {
    seenNotificationsRef.current = new Set();
    setToasts([]);
    lastPendingKeyRef.current = "";
    setPendingSinceMs(null);
  }, [instanceId]);

  useEffect(() => {
    const region = searchParams.get("region");
    const probe = searchParams.get("probe");
    if (region?.trim()) setProbeRegion(region.trim());
    if (probe === "1") setShowProbe(true);
  }, [searchParams]);

  // 10Hz tick while toasts or a pending timer are active: drives smooth
  // progress bars and expires toasts without a separate 1s sweep.
  useEffect(() => {
    if (toasts.length === 0 && pendingSinceMs == null) return;
    const tick = () => {
      const t = Date.now();
      setNow(t);
      setToasts((prev) => {
        if (prev.length === 0) return prev;
        return prev.filter((toast) => toast.expiresAt > t);
      });
    };
    tick();
    const id = window.setInterval(tick, TICK_MS);
    return () => window.clearInterval(id);
  }, [toasts.length, pendingSinceMs]);

  const extendToast = useCallback((id: string, extraMs: number) => {
    if (extraMs <= 0) return;
    setToasts((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, expiresAt: t.expiresAt + extraMs } : t,
      ),
    );
  }, []);

  // Track when the current pending request first appeared. We anchor on
  // `lastPendingKeyRef` (set in `refresh`) so a new pending request resets
  // the timer even if has_pending was already true the previous tick.
  useEffect(() => {
    if (!view?.has_pending) {
      if (pendingSinceMs != null) setPendingSinceMs(null);
      return;
    }
    if (pendingSinceMs == null) {
      setPendingSinceMs(Date.now());
    }
  }, [view?.has_pending, view?.trace_id, pendingSinceMs]);

  // Browser tab title is the cheapest "you have work" indicator that survives
  // when the operator alt-tabs to another tool. Restore on unmount.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const original = document.title;
    if (view?.has_pending) {
      const elapsed = pendingSinceMs ? formatElapsed(Math.max(0, now - pendingSinceMs)) : "";
      document.title = elapsed ? `● ${elapsed} · ${DOCUMENT_TITLE_BASE}` : `● ${DOCUMENT_TITLE_BASE}`;
    } else {
      document.title = DOCUMENT_TITLE_BASE;
    }
    return () => {
      document.title = original;
    };
  }, [view?.has_pending, pendingSinceMs, now]);

  const dismissToast = (id: string) =>
    setToasts((prev) => prev.filter((t) => t.id !== id));

  const onDecision = useCallback(
    async (decision: Decision) => {
      if (!instanceId || busyAction !== null) return;
      setBusyAction(decision);
      try {
        await submitDecision(instanceId, decision);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyAction(null);
      }
    },
    [instanceId, busyAction, refresh],
  );

  const onToggleEnabled = async (enabled: boolean) => {
    if (!instanceId || busyAction !== null) return;
    setBusyAction("toggle");
    try {
      await setApprovalEnabled(instanceId, enabled);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAction(null);
    }
  };

  const onClearPending = async () => {
    if (!instanceId || busyAction !== null) return;
    setBusyAction("clear-pending");
    try {
      await clearPendingApproval(instanceId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  };

  const onResetScreen = async () => {
    if (!instanceId || busyAction !== null) return;
    setBusyAction("reset-screen");
    try {
      await resetCurrentScreen(instanceId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAction(null);
    }
  };

  const onClearQueue = async () => {
    if (busyAction !== null) return;
    setBusyAction("clear-queue");
    try {
      const removed = await clearQueueAll();
      setToasts((prev) => [
        ...prev,
        {
          id: `local:queue-clear:${Date.now()}`,
          ts: Date.now() / 1000,
          kind: "ui.local",
          message: `Queue cleared (${removed} key${removed === 1 ? "" : "s"} removed)`,
          level: "success",
          createdAt: Date.now(),
          expiresAt: Date.now() + TOAST_VISIBLE_MS,
        },
      ]);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAction(null);
      setConfirmAction(null);
    }
  };

  useEffect(() => {
    if (showProbe && autoRefresh && instanceId) {
      void refreshProbe();
    }
  }, [showProbe, autoRefresh, instanceId, refreshProbe]);

  useEffect(() => {
    setProbeRegion("");
    setRegionProbe(null);
    setProbeError(null);
  }, [instanceId]);

  // Keyboard shortcuts: only active when (a) a request is pending, (b) we're
  // not focused inside an input/textarea/select, and (c) no modifier keys are
  // held (so Cmd+A still selects all in the payload textarea).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (
          tag === "INPUT" ||
          tag === "TEXTAREA" ||
          tag === "SELECT" ||
          target.isContentEditable
        ) {
          return;
        }
      }
      if (!view?.has_pending || busyAction !== null) return;
      const k = e.key.toLowerCase();
      if (k === "a" || k === "y") {
        e.preventDefault();
        void onDecision("approve");
      } else if (k === "r" || k === "n") {
        e.preventDefault();
        void onDecision("reject");
      } else if (k === "s") {
        e.preventDefault();
        void onDecision("skip");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view?.has_pending, busyAction, onDecision]);

  const imageUrl =
    view?.preview.available && instanceId
      ? `${clickApprovalImageUrl(instanceId, imageSource)}&tick=${imageTick}`
      : null;
  const probeImageUrl =
    regionProbe?.preview.available && instanceId
      ? `${overlayTestImageUrl(instanceId)}&probeTick=${probeTick}`
      : null;

  const payloadJson = useMemo(() => {
    if (!view?.pending) return "";
    try {
      return JSON.stringify(view.pending, null, 2);
    } catch {
      return "";
    }
  }, [view?.pending]);

  // Memo the canvas overlays so a tick-only refresh (same array contents,
  // new array reference) doesn't force ApprovalCanvas to rebuild its frame.
  const overlaysStable = useMemo(
    () => view?.overlays ?? [],
    // Re-derive only when the underlying overlays change. JSON.stringify is
    // cheap here — the overlay list is at most a handful of shapes per frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(view?.overlays ?? [])],
  );

  const scenarioLabel = view?.scenario_label || view?.scenario_key || "";
  const regionLabel = view?.region_label || "";
  const traceId = view?.trace_id || "";
  const navigation = view?.navigation || null;
  const taskContext = view?.task_context || null;
  const actionType = view?.action_type || "";
  const setNodeTarget = view?.set_node_target || "";
  const currentScreen = view?.current_screen || "";
  const activePlayer = view?.active_player || "";
  const inGameId = view?.active_player_in_game_id || "";
  const playerAccountKey =
    activePlayer && inGameId && activePlayer !== inGameId ? activePlayer : "";
  const hasPending = !!view?.has_pending;
  const waitingFor =
    hasPending && pendingSinceMs != null
      ? formatElapsed(Math.max(0, now - pendingSinceMs))
      : null;
  const scenarioProgress = view?.scenario_progress ?? null;
  const approvalEnabled = !!view?.approval_enabled;
  const heartbeatActive = !!view?.heartbeat_active;
  const anyBusy = busyAction !== null;

  return (
    <>
      <FleetPageHeader title="Click approvals">
        Approve or reject sensitive worker actions (ADB taps / swipes / screen
        node updates) in real time. The page also writes the per-instance
        heartbeat so the worker only blocks when this page is open.
      </FleetPageHeader>

      {/* One compact status strip replaces the old status row + "Current
          context" panel. Single source of truth for the operator: am I
          waiting, who/where am I, and are the safety rails on. */}
      <div className="approvals-status-strip">
        <span
          className={`status-pill status-pill--lg ${hasPending ? "status-pending pulse" : "status-idle"}`}
          title={hasPending ? "A worker is waiting on your decision" : "No worker action waiting"}
        >
          <span className="status-pill__dot" aria-hidden />
          {hasPending ? `Pending${waitingFor ? ` · ${waitingFor}` : ""}` : "Idle"}
        </span>

        <span className="status-fact">
          <span className="status-fact__label">Instance</span>
          <code>{instanceId || "—"}</code>
        </span>
        <span className="status-fact">
          <span className="status-fact__label">Node</span>
          <code>{currentScreen || "—"}</code>
        </span>
        <span className="status-fact">
          <span className="status-fact__label">Player</span>
          <code>{inGameId || activePlayer || "—"}</code>
          {playerAccountKey ? (
            <span className="status-fact__sub" title="Redis active_player account key">
              {playerAccountKey}
            </span>
          ) : null}
        </span>

        <span className="status-fact status-fact--rail">
          <span
            className={`heartbeat-dot ${heartbeatActive ? "heartbeat-dot--on" : "heartbeat-dot--off"}`}
            title={heartbeatActive ? "Heartbeat active (ttl ≈ 5s)" : "Heartbeat off — worker will not block"}
            aria-hidden
          />
          <span className="status-fact__label">Heartbeat</span>
          <strong className={heartbeatActive ? "text-emerald-300" : "text-wos-text-muted"}>
            {heartbeatActive ? "live" : "off"}
          </strong>
        </span>
      </div>

      {scenarioProgress ? (
        <ScenarioProgressBar progress={scenarioProgress} />
      ) : null}

      {error || instancesError ? (
        <div className="error-banner">{error ?? instancesError}</div>
      ) : null}

      <div className="toolbar approvals-toolbar">
        {/* Promoted to a real toggle so the most important global safety
            switch isn't a 16px checkbox shoved between two dropdowns. */}
        <button
          type="button"
          role="switch"
          aria-checked={approvalEnabled}
          className={`approval-switch ${approvalEnabled ? "is-on" : "is-off"}`}
          disabled={busyAction === "toggle" || !instanceId}
          onClick={() => onToggleEnabled(!approvalEnabled)}
          title="OFF lets the worker tap/swipe without asking"
        >
          <span className="approval-switch__track">
            <span className="approval-switch__thumb" />
          </span>
          <span className="approval-switch__text">
            <span className="approval-switch__label">Approval mode</span>
            <span className="approval-switch__value">
              {approvalEnabled ? "ON · worker will ask" : "OFF · worker auto-acts"}
            </span>
          </span>
        </button>

        <AppSelect
          label="Screenshot"
          options={SCREENSHOT_SOURCE_OPTIONS}
          value={imageSource}
          onChange={(next) => setImageSource(next as "capture" | "live")}
          minWidth={210}
          isSearchable={false}
        />
        <AppCheckbox
          inline
          className="checkbox-label"
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={refresh}
          disabled={anyBusy}
        >
          Refresh now
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setShowReset((v) => !v)}
          aria-expanded={showReset}
        >
          {showReset ? "Hide reset" : "Reset…"}
        </button>
      </div>

      {showReset ? (
        <section className="panel approvals-reset-panel">
          <h2>Reset</h2>
          <p className="meta">
            Operator escape hatches. These only affect Redis — the worker
            process keeps running.
          </p>
          <div className="approvals-reset-actions">
            <DangerButton
              label="Clear queue (all instances)"
              confirmLabel="Confirm clear queue"
              tooltip="Deletes wos:queue:* (keeps :running). Same as the Streamlit Clear queue button."
              confirming={confirmAction === "clear-queue"}
              busy={busyAction === "clear-queue"}
              disabled={anyBusy && busyAction !== "clear-queue"}
              onArm={() => setConfirmAction("clear-queue")}
              onCancel={() => setConfirmAction(null)}
              onConfirm={onClearQueue}
            />
            <DangerButton
              label="Clear pending approval"
              confirmLabel="Confirm clear pending"
              tooltip="Cancels the in-flight approval for this instance (treated as reject)"
              confirming={confirmAction === "clear-pending"}
              busy={busyAction === "clear-pending"}
              disabled={!instanceId || (anyBusy && busyAction !== "clear-pending")}
              onArm={() => setConfirmAction("clear-pending")}
              onCancel={() => setConfirmAction(null)}
              onConfirm={onClearPending}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={anyBusy || !instanceId}
              onClick={onResetScreen}
              title="Sets current_screen to empty so the detector re-classifies from scratch"
            >
              {busyAction === "reset-screen" ? "Resetting…" : "Reset node to none (unknown)"}
            </button>
          </div>
        </section>
      ) : null}

      <div className="approvals-grid">
        <section className="panel">
          <h2>Screenshot</h2>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={view?.preview.width ?? 0}
            height={view?.preview.height ?? 0}
            overlays={overlaysStable}
          />
          {view?.tap_x != null && view?.tap_y != null ? (
            <p className="meta">
              Tap target: ({view.tap_x}, {view.tap_y})
            </p>
          ) : null}
        </section>

        <section className={`panel ${hasPending ? "panel--accent-pending" : ""}`}>
          <h2 className="panel-title-row">
            Approvals
            {hasPending ? (
              <span className="kbd-hint" aria-label="Keyboard shortcuts">
                <kbd>A</kbd>pprove · <kbd>S</kbd>kip · <kbd>R</kbd>eject
              </span>
            ) : null}
          </h2>
          {hasPending ? (
            <PendingApprovalCard
              view={view!}
              instanceId={instanceId}
              scenarioLabel={scenarioLabel}
              regionLabel={regionLabel}
              traceId={traceId}
              tempoTraceUrl={view?.tempo_trace_url || ""}
              labelingHref={view?.labeling_href || ""}
              navigation={navigation}
              taskContext={taskContext}
              actionType={actionType}
              setNodeTarget={setNodeTarget}
              payloadJson={payloadJson}
              showPayload={showPayload}
              onTogglePayload={() => setShowPayload((v) => !v)}
              busyAction={busyAction}
              onDecision={onDecision}
            />
          ) : (
            <IdleApprovalsCard
              busy={anyBusy}
              resetting={busyAction === "reset-screen"}
              onResetScreen={onResetScreen}
              instanceId={instanceId}
            />
          )}
        </section>
      </div>

      <section className="panel approvals-probe-panel panel--spaced">
        <button
          type="button"
          className="approvals-probe-toggle"
          onClick={() => setShowProbe((v) => !v)}
          aria-expanded={showProbe}
        >
          <span className={`approvals-probe-toggle__chevron ${showProbe ? "is-open" : ""}`} aria-hidden>
            ▸
          </span>
          <span>Region probe</span>
          <span className="meta approvals-probe-toggle__meta">
            {showProbe ? "Live · SSE" : "Click to run an area.json region check"}
          </span>
        </button>
        {showProbe ? (
          <RegionProbePanel
            probe={regionProbe}
            imageUrl={probeImageUrl}
            selectedRegion={probeRegion}
            threshold={probeThreshold}
            error={probeError}
            onRegionChange={setProbeRegion}
            onThresholdChange={setProbeThreshold}
            onRefresh={refreshProbe}
          />
        ) : null}
      </section>

      {toasts.length ? (
        <div className="approvals-toast-stack" role="status" aria-live="polite">
          {toasts.map((t) => (
            <ApprovalToast
              key={t.id}
              toast={t}
              now={now}
              onDismiss={() => dismissToast(t.id)}
              onExtend={(extraMs) => extendToast(t.id, extraMs)}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

function scenarioProgressLabel(progress: ScenarioProgress): string {
  const key = progress.scenario_label || progress.scenario_key;
  if (key && progress.step_total > 0) {
    let text = `${key} · Step ${progress.step_current + 1}/${progress.step_total}`;
    if (progress.is_running && progress.step_iter > 0) {
      text += ` · iter ${progress.step_iter}`;
    }
    if (!progress.is_running) text += " · idle";
    if (progress.nav_target) text += ` · navigating → ${progress.nav_target}`;
    return text;
  }
  if (key) return `${key} · running`;
  return "no active scenario";
}

function ScenarioProgressBar({ progress }: { progress: ScenarioProgress }) {
  const completedSteps =
    progress.step_total > 0
      ? progress.is_running
        ? progress.step_current + 1
        : progress.step_current
      : 0;
  const ratio =
    progress.step_total > 0
      ? Math.min(100, (completedSteps / progress.step_total) * 100)
      : 0;
  const label = scenarioProgressLabel(progress);
  const currentIdx = progress.is_running ? progress.step_current : -1;

  return (
    <div className="approvals-scenario-progress">
      <div
        className="approvals-scenario-progress__track"
        role="progressbar"
        aria-label="Scenario step progress"
        aria-valuemin={0}
        aria-valuemax={Math.max(progress.step_total, 1)}
        aria-valuenow={completedSteps}
      >
        <div
          className="approvals-scenario-progress__bar"
          style={{ width: `${ratio}%` }}
        />
      </div>
      <span className="approvals-scenario-progress__label meta">{label}</span>
      {progress.step_summaries.length > 0 ? (
        <p className="approvals-scenario-progress__steps meta">
          {progress.step_summaries.map((summary, i) => (
            <span key={`${summary}-${i}`}>
              {i > 0 ? " · " : null}
              {i === currentIdx ? <strong>{summary}</strong> : summary}
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

function ApprovalToast({
  toast,
  now,
  onDismiss,
  onExtend,
}: {
  toast: Toast;
  now: number;
  onDismiss: () => void;
  onExtend: (extraMs: number) => void;
}) {
  const pauseStartRef = useRef<number | null>(null);
  const remaining = Math.max(0, toast.expiresAt - now);
  const progress = Math.min(100, (remaining / TOAST_VISIBLE_MS) * 100);

  const handlePointerEnter = () => {
    pauseStartRef.current = Date.now();
  };
  const handlePointerLeave = () => {
    if (pauseStartRef.current == null) return;
    onExtend(Date.now() - pauseStartRef.current);
    pauseStartRef.current = null;
  };

  return (
    <div
      className={`approvals-toast approvals-toast--${toast.level}`}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
    >
      <div className="approvals-toast__body">
        <span className="approvals-toast__icon" aria-hidden>
          <Icon name={toastLevelIcon(toast.level)} size="sm" />
        </span>
        <span className="approvals-toast__msg">{toast.message}</span>
        <button
          type="button"
          className="approvals-toast__close"
          onClick={onDismiss}
          aria-label="Dismiss notification"
        >
          <Icon name="close" size="sm" />
        </button>
      </div>
      <div
        className="approvals-toast__progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
        aria-label="Notification auto-dismiss"
      >
        <div
          className="approvals-toast__progress-bar"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function toastLevelIcon(level: NotificationEvent["level"]): IconName {
  switch (level) {
    case "success":
      return "check";
    case "warning":
      return "warning";
    case "error":
      return "alert";
    default:
      return "info";
  }
}

/** Two-step "arm, then confirm" button. Avoids the jarring native
 *  window.confirm() while still requiring a deliberate second click. */
function DangerButton({
  label,
  confirmLabel,
  tooltip,
  confirming,
  busy,
  disabled,
  onArm,
  onCancel,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  tooltip?: string;
  confirming: boolean;
  busy: boolean;
  disabled: boolean;
  onArm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (busy) {
    return (
      <button type="button" className="btn-danger" disabled>
        {confirmLabel.replace(/^Confirm /, "")}…
      </button>
    );
  }
  if (confirming) {
    return (
      <span className="danger-confirm">
        <button type="button" className="btn-danger" onClick={onConfirm}>
          {confirmLabel}
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      className="btn-secondary"
      disabled={disabled}
      onClick={onArm}
      title={tooltip}
    >
      {label}
    </button>
  );
}

function RegionProbePanel({
  probe,
  imageUrl,
  selectedRegion,
  threshold,
  error,
  onRegionChange,
  onThresholdChange,
  onRefresh,
}: {
  probe: AreaRegionProbeResult | null;
  imageUrl: string | null;
  selectedRegion: string;
  threshold: number;
  error: string | null;
  onRegionChange: (region: string) => void;
  onThresholdChange: (threshold: number) => void;
  onRefresh: () => void;
}) {
  const result = probe?.result ?? null;
  const matched = !!result?.matched;
  const score = asNumber(result?.score);
  const thresholdSeen = asNumber(result?.threshold) ?? threshold;
  const searchRegion = String(result?.search_region || selectedRegion || "—");
  const resolvedRegion = String(result?.resolved_region || result?.region || selectedRegion || "—");
  const reason = [result?.reason, result?.detail].filter(Boolean).join(" · ");

  // Stabilise overlays for ApprovalCanvas so the probe canvas doesn't redraw
  // on every probe tick that returns the same shapes.
  const overlaysStable = useMemo(
    () => probe?.overlays ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(probe?.overlays ?? [])],
  );

  return (
    <div className="approvals-probe-body">
      <p className="meta">
        Live check for an `area.json` region. Orange shows where the matcher searched,
        green/gray shows the best template match and its confidence.
      </p>

      <div className="toolbar">
        <AppSelect
          label="Region"
          options={(probe?.regions ?? []).map((r) => ({ value: r, label: r }))}
          value={selectedRegion || probe?.selected_region || ""}
          onChange={onRegionChange}
          placeholder="Select region…"
          minWidth={260}
          maxWidth={360}
        />
        <label>
          Threshold
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => onThresholdChange(clamp01(Number(e.target.value)))}
          />
        </label>
        <button type="button" className="btn-secondary" onClick={onRefresh}>
          Probe now
        </button>
        {probe ? (
          <span className="meta">
            screen: <code>{probe.current_screen || "—"}</code>
            {probe.active_player ? (
              <>
                {" "}
                · player: <code>{probe.active_player}</code>
              </>
            ) : null}
          </span>
        ) : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="approvals-grid">
        <div>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={probe?.preview.width ?? 0}
            height={probe?.preview.height ?? 0}
            overlays={overlaysStable}
          />
          <p className="meta probe-legend">
            <span className="probe-legend__search">■ search area</span>{" "}
            <span className="probe-legend__match">■ matched</span>{" "}
            <span className="probe-legend__miss">■ best below threshold</span>{" "}
            <span className="probe-legend__tap">+ tap point</span>
          </p>
        </div>

        <div>
          {!probe ? (
            <p className="meta">Loading region list…</p>
          ) : !result ? (
            <p className="meta">Select a region to run the probe.</p>
          ) : (
            <>
              <div className="metrics-row metrics-row--4">
                <MetricCard
                  label="Matched"
                  value={matched ? "yes" : "no"}
                  tone={matched ? "ok" : "danger"}
                />
                <MetricCard label="Score" value={score != null ? score.toFixed(4) : "—"} />
                <MetricCard label="Threshold" value={thresholdSeen.toFixed(3)} />
                <MetricCard label="Action" value={String(result.action || "findIcon")} />
              </div>
              <p className="meta">
                Region: <code>{resolvedRegion}</code>
                {result.resolved_version ? (
                  <>
                    {" "}
                    · version: <code>{String(result.resolved_version)}</code>
                  </>
                ) : null}
                {" "}
                · search: <code>{searchRegion}</code>
              </p>
              <p className="meta">
                Template:{" "}
                <code>
                  {asNumber(result.template_w) ?? "—"}×{asNumber(result.template_h) ?? "—"}
                </code>
                {Array.isArray(result.top_left) ? (
                  <>
                    {" "}
                    · top-left: <code>{result.top_left.join(", ")}</code>
                  </>
                ) : null}
                {result.match_source ? (
                  <>
                    {" "}
                    · source: <code>{String(result.match_source)}</code>
                  </>
                ) : null}
              </p>
              {result.template_bright_ratio != null || result.patch_bright_ratio != null ? (
                <p className="meta">
                  Bright detail ratio · template{" "}
                  <code>{fmtMaybe(result.template_bright_ratio)}</code> · live{" "}
                  <code>{fmtMaybe(result.patch_bright_ratio)}</code>
                </p>
              ) : null}
              {result.mean_saturation != null ? (
                <p className="meta">
                  Mean saturation: <code>{fmtMaybe(result.mean_saturation)}</code>
                </p>
              ) : null}
              {reason ? <p className="approvals-callout approvals-callout--warn">{reason}</p> : null}
              <RegionProbeCropCompare crops={probe.crops} region={resolvedRegion} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RegionProbeCropCompare({
  crops,
  region,
}: {
  crops: AreaRegionProbeResult["crops"];
  region: string;
}) {
  if (!crops) return null;

  const live = crops.live;
  const template = crops.template;
  const titleRegion = crops.region || region || "—";

  return (
    <section className="approvals-probe-crops panel" aria-label="Live crop vs template">
      <h3 className="approvals-probe-crops__title">
        <code>{titleRegion}</code>
        {crops.resolved_region && crops.resolved_region !== titleRegion ? (
          <>
            {" "}
            → <code>{crops.resolved_region}</code>
          </>
        ) : null}
        {" "}
        — live crop vs template
      </h3>
      {crops.reference_rel ? (
        <p className="meta approvals-probe-crops__ref">
          Template from <code>{crops.reference_rel}</code>
          {template?.label && template.label !== "Template crop" ? (
            <>
              {" "}
              · <code>references/crop/{template.label}</code>
            </>
          ) : null}
        </p>
      ) : null}
      <div className="approvals-probe-crops__grid">
        <ProbeCropTile side={live} fallbackCaption="Live (rolling PNG)" />
        <ProbeCropTile side={template} fallbackCaption="Template crop" />
      </div>
    </section>
  );
}

function ProbeCropTile({
  side,
  fallbackCaption,
}: {
  side?: { available?: boolean; width?: number; height?: number; label?: string; data_url?: string };
  fallbackCaption: string;
}) {
  const caption = side?.label || fallbackCaption;
  const w = side?.width ?? 0;
  const h = side?.height ?? 0;
  const sizeLabel = w > 0 && h > 0 ? `${w}×${h} px` : null;

  return (
    <figure className="approvals-probe-crops__tile">
      <figcaption className="meta">{caption}</figcaption>
      {side?.available && side.data_url ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={side.data_url} alt={caption} className="approvals-probe-crops__img" />
          {sizeLabel ? <p className="meta approvals-probe-crops__size">{sizeLabel}</p> : null}
        </>
      ) : (
        <p className="meta approvals-probe-crops__empty">—</p>
      )}
    </figure>
  );
}

function MetricCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "danger";
}) {
  const color =
    tone === "ok" ? "text-emerald-300" : tone === "danger" ? "text-red-300" : "";
  return (
    <div className="metric-card">
      <span className="label">{label}</span>
      <span className={`value ${color}`}>{value}</span>
    </div>
  );
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function fmtMaybe(value: unknown): string {
  const n = asNumber(value);
  return n == null ? "—" : n.toFixed(4);
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function IdleApprovalsCard({
  busy,
  resetting,
  onResetScreen,
  instanceId,
}: {
  busy: boolean;
  resetting: boolean;
  onResetScreen: () => void;
  instanceId: string;
}) {
  return (
    <div className="idle-card">
      <div className="idle-card__icon" aria-hidden>✓</div>
      <p className="idle-card__title">All clear</p>
      <p className="meta">
        No pending click requests for this instance. The worker will queue a
        decision here as soon as it needs one.
      </p>
      <button
        type="button"
        className="btn-secondary mt-3"
        disabled={busy || !instanceId}
        onClick={onResetScreen}
        title="Clears current_screen in Redis (useful when the worker is stuck on the wrong node)"
      >
        {resetting ? "Resetting…" : "Reset node to none (unknown)"}
      </button>
    </div>
  );
}

function PendingApprovalCard({
  view,
  instanceId,
  scenarioLabel,
  regionLabel,
  traceId,
  tempoTraceUrl,
  labelingHref,
  navigation,
  taskContext,
  actionType,
  setNodeTarget,
  payloadJson,
  showPayload,
  onTogglePayload,
  busyAction,
  onDecision,
}: {
  view: ClickApprovalView;
  instanceId: string;
  scenarioLabel: string;
  regionLabel: string;
  traceId: string;
  tempoTraceUrl: string;
  labelingHref: string;
  navigation: ClickApprovalView["navigation"];
  taskContext: ClickApprovalView["task_context"];
  actionType: string;
  setNodeTarget: string;
  payloadJson: string;
  showPayload: boolean;
  onTogglePayload: () => void;
  busyAction: BusyAction;
  onDecision: (d: Decision) => void;
}) {
  const playerId = view.active_player_in_game_id || view.active_player || "";
  const actionLabel = view.action_label || actionType || "action";
  const isBusy = (d: Decision) => busyAction === d;
  // Disable a button only when *another* decision is in flight. Operators
  // commonly want to bail to "reject" the moment they realise approve was the
  // wrong call; the old global busy flag locked them out for ~1s.
  const isDisabled = (d: Decision) => busyAction !== null && busyAction !== d;

  return (
    <>
      {/* Decision row at the TOP of the card so it's always visible
          without scrolling past the scenario blurb. */}
      <div className="actions actions--prominent" role="group" aria-label="Decision">
        <button
          type="button"
          className="btn-approve"
          disabled={isDisabled("approve")}
          onClick={() => onDecision("approve")}
          aria-keyshortcuts="A Y"
        >
          {isBusy("approve") ? "Approving…" : "Approve"}
          <span className="btn-kbd" aria-hidden>A</span>
        </button>
        <button
          type="button"
          className="btn-skip"
          disabled={isDisabled("skip")}
          onClick={() => onDecision("skip")}
          title="Treat as no-op success (don't tap, but don't abort the scenario)"
          aria-keyshortcuts="S"
        >
          {isBusy("skip") ? "Skipping…" : "Skip"}
          <span className="btn-kbd" aria-hidden>S</span>
        </button>
        <button
          type="button"
          className="btn-reject"
          disabled={isDisabled("reject")}
          onClick={() => onDecision("reject")}
          aria-keyshortcuts="R N"
        >
          {isBusy("reject") ? "Rejecting…" : "Reject"}
          <span className="btn-kbd" aria-hidden>R</span>
        </button>
      </div>

      {scenarioLabel ? (
        <div className="scenario-card">
          <strong>{scenarioLabel}</strong>
          {view.scenario_key && view.scenario_key !== scenarioLabel ? (
            <span className="meta">
              <code>{view.scenario_key}</code>
            </span>
          ) : null}
          {view.scenario_key ? (
            <nav className="queue-task-actions approvals-scenario-links" aria-label="Scenario">
              <Link
                href={debugRunHref({
                  instanceId,
                  playerId: playerId || undefined,
                  scenario: view.scenario_key,
                })}
                className="queue-task-actions__link"
              >
                DSL runner
              </Link>
              <Link href={editDslHref({ scenario: view.scenario_key })} className="queue-task-actions__link">
                Edit scenario
              </Link>
            </nav>
          ) : null}
        </div>
      ) : null}

      {navigation ? (
        <NavigationRoute info={navigation} />
      ) : null}

      {actionType === "set_node" && setNodeTarget ? (
        <p className="approvals-callout approvals-callout--info">
          Will set <strong>current_screen</strong> to <code>{setNodeTarget}</code>.
        </p>
      ) : null}

      {actionType === "diagnostic" ? (
        <>
          <p className="approvals-callout approvals-callout--info">
            {view.diagnostic_kind === "while_match_no_iterations" ? (
              <>
                <code>while_match</code> matched zero times. Approve retries later; reject stops.
              </>
            ) : (
              <>
                Diagnostic check
                {view.diagnostic_kind ? (
                  <>
                    {" "}
                    · <code>{view.diagnostic_kind}</code>
                  </>
                ) : null}
                . Approve retries, reject aborts.
              </>
            )}
          </p>
          {regionLabel && actionType === "diagnostic" ? (
            <p className="meta">
              Region under inspection: <code>{regionLabel}</code>
            </p>
          ) : null}
          {view.diagnostic_attempts ? (
            <p className="meta">
              Initial probes <code>{view.diagnostic_attempts}</code>
              {view.diagnostic_interval ? (
                <>
                  {" "}
                  · interval <code>{view.diagnostic_interval}s</code>
                </>
              ) : null}
            </p>
          ) : null}
        </>
      ) : null}

      {regionLabel && actionType !== "diagnostic" ? (
        <p className="meta">
          Target region: <code>{regionLabel}</code>
        </p>
      ) : null}

      {regionLabel && labelingHref ? (
        <p className="approvals-region-links">
          <Link href={labelingHref} className="queue-task-actions__link">
            Open Labeling for <code>{regionLabel}</code>
          </Link>
          {instanceId ? (
            <Link
              href={overlayTestHref(instanceId, { region: regionLabel })}
              className="queue-task-actions__link"
            >
              Overlay test
            </Link>
          ) : null}
        </p>
      ) : null}

      {taskContext ? <TaskContextCaption ctx={taskContext} /> : null}

      {traceId ? (
        <div className="approvals-trace">
          <span className="meta">Trace ID (Grafana / Tempo trace search)</span>
          <code className="approvals-trace__id">{traceId}</code>
          <CopyButton
            text={traceId}
            label="Copy"
            title="Copy trace ID (paste into Grafana / Tempo)"
            className="approvals-trace__copy"
          />
          {tempoTraceUrl ? (
            <a
              href={tempoTraceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="queue-task-actions__link"
            >
              Open in Tempo
            </a>
          ) : null}
        </div>
      ) : null}

      <details
        className="approvals-payload"
        open={showPayload}
        onToggle={(e) => {
          // Mirror UI state so other interactions can read whether the
          // operator has expanded the raw payload.
          const t = e.currentTarget as HTMLDetailsElement;
          if (t.open !== showPayload) onTogglePayload();
        }}
      >
        <summary>Payload · {actionLabel}</summary>
        <pre className="code-block">{payloadJson || "—"}</pre>
      </details>
    </>
  );
}

function NavigationRoute({
  info,
}: {
  info: NonNullable<ClickApprovalView["navigation"]>;
}) {
  const { path, hop_index: hopIndex, from, to } = info;
  // When the worker provided a full BFS route + 1-based hop index we render
  // every node with the current edge highlighted — matches the Streamlit
  // "Navigation · `a` → **`b → c`** → `d`" formatting.
  if (path.length >= 2 && hopIndex >= 1 && hopIndex < path.length) {
    return (
      <p className="approvals-callout approvals-callout--warn">
        Navigation ·{" "}
        {path.map((node, i) => {
          const isCurrentEdge = i === hopIndex - 1 || i === hopIndex;
          const sep = i < path.length - 1 ? " → " : "";
          return (
            <span key={`${node}-${i}`}>
              {isCurrentEdge ? (
                <strong>
                  <code>{node}</code>
                </strong>
              ) : (
                <code>{node}</code>
              )}
              {sep}
            </span>
          );
        })}
      </p>
    );
  }
  if (from || to) {
    return (
      <p className="approvals-callout approvals-callout--warn">
        Navigation · <code>{from || "?"}</code> → <code>{to || "?"}</code>
      </p>
    );
  }
  return null;
}

function TaskContextCaption({
  ctx,
}: {
  ctx: NonNullable<ClickApprovalView["task_context"]>;
}) {
  const { threshold, score, text, confidence } = ctx;
  // Two flavours: overlay-by-text (OCR-driven) and overlay-by-template
  // (score-driven). Match the Streamlit caption exactly so logs and screenshots
  // can be compared 1:1.
  if (text) {
    return (
      <p className="meta">
        Overlay(text) · text <code>{text}</code>
        {confidence ? (
          <>
            {" "}
            · conf <code>{confidence}</code>
          </>
        ) : null}
      </p>
    );
  }
  if (threshold || score) {
    const parts: string[] = [];
    if (threshold) parts.push(`threshold ${threshold}`);
    if (score) parts.push(`match score ${score}`);
    return (
      <p className="meta">
        Overlay ·{" "}
        {parts.map((p, i) => (
          <span key={p}>
            <code>{p}</code>
            {i < parts.length - 1 ? " · " : ""}
          </span>
        ))}
      </p>
    );
  }
  return null;
}
