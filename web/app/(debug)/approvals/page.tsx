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
  fetchInstanceTestModule,
  fetchModules,
  fetchNotifications,
  h264StreamUrl,
  overlayTestImageUrl,
  resetCurrentScreen,
  setApprovalEnabled,
  setInstanceTestModule,
  submitDecision,
} from "@/lib/api";
import type {
  AreaRegionProbeResult,
  ClickApprovalView,
  NotificationEvent,
  ScenarioProgress,
} from "@/lib/types";
import type { ModuleRow } from "@/lib/config-pages";
import { editDslHref, overlayTestHref } from "@/lib/debug-links";
import { isWebCodecsSupported } from "@/lib/h264VideoStream";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
const NOTIFICATIONS_MAX_AGE_S = 30;
const TOAST_VISIBLE_MS = 6000;
const TICK_MS = 100;
const DOCUMENT_TITLE_BASE = "Click approvals · Autopilot";

type Toast = NotificationEvent & { createdAt: number; expiresAt: number };

type Decision = "approve" | "reject" | "skip";
// Track which control is currently in flight so we can disable only it
// (operators routinely change their mind between approve/reject before the
// previous request returns, and the old "global busy flag" pattern made
// that impossible).
type BusyAction = Decision | "toggle" | "clear-pending" | "clear-queue" | "reset-screen" | "test-module" | null;

type ImageSource = "capture" | "live" | "stream";

const SCREENSHOT_SOURCE_OPTIONS = [
  { value: "capture", label: "Captured (request)" },
  { value: "live", label: "Live rolling" },
  { value: "stream", label: "Live video (WebCodecs)" },
];

const TEST_MODULE_ALL = "";

function defaultImageSourceForView(view: ClickApprovalView): ImageSource {
  const backend = (
    view.screenshot_backend_effective ||
    view.screenshot_backend ||
    ""
  ).toLowerCase();
  if (backend === "scrcpy" && isWebCodecsSupported()) {
    return "stream";
  }
  return "live";
}

export default function ApprovalsPage() {
  const searchParams = useSearchParams();
  const { instanceId, instancesError } = useFleet();
  const [view, setView] = useState<ClickApprovalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  // Start on the rolling PNG until the first view payload tells us the
  // effective screenshot backend; then choose the matching default source.
  // Manual selections (via the dropdown) stick until the instance changes.
  const [imageSource, setImageSource] = useState<ImageSource>("live");
  // Tracks whether the user has manually picked a source from the dropdown.
  // Once they do, backend-driven defaults stop overriding the select.
  const userPickedSourceRef = useRef(false);
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
  const [now, setNow] = useState<number>(() => Date.now());
  const [modules, setModules] = useState<ModuleRow[]>([]);
  const [testModule, setTestModule] = useState<string>(TEST_MODULE_ALL);
  // Inline confirm state for destructive actions — replaces window.confirm()
  // which was the only thing in this page that broke the dark-themed look.
  const [confirmAction, setConfirmAction] = useState<
    "clear-queue" | "clear-pending" | null
  >(null);
  // Notifications are non-destructive in Redis (the Streamlit page kept its
  // dedup set in ``st.session_state``). Track the IDs we've already toasted
  // so re-polling the list doesn't re-fire the same event in a loop.
  const seenNotificationsRef = useRef<Set<string>>(new Set());
  // Concurrent ``pollNotifications`` invocations (SSE dispatch + immediate
  // fallback poll on connect) would both snapshot the same empty ``seen`` set
  // and race to display the same toast twice. Drop overlapping calls — the
  // in-flight request will still surface anything new.
  const notificationsInFlightRef = useRef(false);
  // Remember which pending-request key was last seen so we can bump the
  // image cache key only when the underlying request actually changes
  // (otherwise we'd thrash the browser's decoded-image cache every second).
  const lastPendingKeyRef = useRef<string>("");
  const lastPreviewMtimeRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      // Stream mode reuses the "live" metadata (region boxes, tap target,
      // preview dimensions) — the image bytes themselves are pulled via
      // WebSocket, but the backend overlay data is the same.
      const metadataSource: "capture" | "live" =
        imageSource === "stream" ? "live" : imageSource;
      const data = await fetchClickApproval(instanceId, metadataSource);
      setView(data);
      if (!userPickedSourceRef.current) {
        const nextSource = defaultImageSourceForView(data);
        if (nextSource !== imageSource) {
          setImageSource(nextSource);
        }
      }
      const nextKey = data.has_pending ? data.trace_id || "(pending)" : "(idle)";
      const pendingChanged = nextKey !== lastPendingKeyRef.current;
      if (pendingChanged) {
        lastPendingKeyRef.current = nextKey;
      }
      // Live rolling: worker overwrites the PNG on disk; bust cache when mtime moves.
      // Capture: only refetch when the pending approval identity changes.
      // Stream: no <img> cache bust needed (canvas redraws on each VideoFrame).
      let imageStale = pendingChanged;
      if (imageSource === "live") {
        const mtime = data.preview?.mtime ?? null;
        if (mtime != null && mtime !== lastPreviewMtimeRef.current) {
          lastPreviewMtimeRef.current = mtime;
          imageStale = true;
        }
      } else {
        lastPreviewMtimeRef.current = null;
      }
      if (imageStale) {
        setImageTick((t) => t + 1);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [instanceId, imageSource]);

  const pollNotifications = useCallback(async () => {
    if (!instanceId) return;
    if (notificationsInFlightRef.current) return;
    notificationsInFlightRef.current = true;
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
        if (seenNotificationsRef.current.has(ev.id)) continue;
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
    } finally {
      notificationsInFlightRef.current = false;
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

  const approvalStreamTopics = useMemo(() => {
    const topics = ["approval", "notifications"] as string[];
    // Rolling preview mtime is on the ``instance`` revision, not ``approval``.
    if (imageSource === "live" || showProbe) topics.push("instance");
    if (showProbe) topics.push("area");
    return topics;
  }, [showProbe, imageSource]);

  useDashboardEventStream({
    topics: approvalStreamTopics,
    instanceId: instanceId || undefined,
    enabled: autoRefresh && !!instanceId,
    onEvent: (topic) => {
      if (topic === "approval") void refresh();
      if (topic === "notifications") void pollNotifications();
      if (topic === "instance") {
        if (imageSource === "live") void refresh();
        if (showProbe) void refreshProbe();
      }
      if (topic === "area" && showProbe) void refreshProbe();
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
    notificationsInFlightRef.current = false;
    userPickedSourceRef.current = false;
    setImageSource("live");
    setToasts([]);
    lastPendingKeyRef.current = "";
    lastPreviewMtimeRef.current = null;
  }, [instanceId]);

  useEffect(() => {
    lastPreviewMtimeRef.current = null;
    if (instanceId) void refresh();
  }, [imageSource, instanceId, refresh]);

  // Module list for the "Test module" filter. Loaded once per session — the
  // catalog rarely changes between page loads and the /modules page already
  // handles add/remove flows.
  useEffect(() => {
    fetchModules("all")
      .then((rows) => setModules(rows.filter((m) => (m.id || "").trim())))
      .catch(() => setModules([]));
  }, []);

  // Sync currently-selected test_module from Redis whenever the instance
  // changes (operator may have set it from a previous tab / session).
  useEffect(() => {
    if (!instanceId) {
      setTestModule(TEST_MODULE_ALL);
      return;
    }
    fetchInstanceTestModule(instanceId)
      .then((m) => setTestModule(m || TEST_MODULE_ALL))
      .catch(() => setTestModule(TEST_MODULE_ALL));
  }, [instanceId]);

  const onChangeTestModule = useCallback(
    async (next: string) => {
      if (!instanceId) return;
      const target = next === TEST_MODULE_ALL ? "" : next;
      setBusyAction("test-module");
      try {
        const applied = await setInstanceTestModule(instanceId, target);
        setTestModule(applied || TEST_MODULE_ALL);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyAction(null);
      }
    },
    [instanceId],
  );

  useEffect(() => {
    const region = searchParams.get("region");
    const probe = searchParams.get("probe");
    if (region?.trim()) setProbeRegion(region.trim());
    if (probe === "1") setShowProbe(true);
  }, [searchParams]);

  // 10Hz tick while toasts are visible: smooth progress bars + auto-dismiss.
  useEffect(() => {
    if (toasts.length === 0) return;
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
  }, [toasts.length]);

  const extendToast = useCallback((id: string, extraMs: number) => {
    if (extraMs <= 0) return;
    setToasts((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, expiresAt: t.expiresAt + extraMs } : t,
      ),
    );
  }, []);

  // Browser tab title is the cheapest "you have work" indicator that survives
  // when the operator alt-tabs to another tool. Restore on unmount.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const original = document.title;
    document.title = view?.has_pending
      ? `● ${DOCUMENT_TITLE_BASE}`
      : DOCUMENT_TITLE_BASE;
    return () => {
      document.title = original;
    };
  }, [view?.has_pending]);

  const dismissToast = (id: string) =>
    setToasts((prev) => prev.filter((t) => t.id !== id));

  const onDecision = useCallback(
    async (decision: Decision) => {
      if (!instanceId || busyAction !== null) return;
      setBusyAction(decision);
      try {
        const requestId =
          typeof view?.pending?.request_id === "string" ? view.pending.request_id : "";
        await submitDecision(instanceId, decision, requestId);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyAction(null);
      }
    },
    [instanceId, busyAction, refresh, view?.pending],
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

  // Stream mode pulls bytes from the WebSocket directly; the URL passed to
  // the canvas is the ws:// endpoint, not the click-approval image URL.
  const streamUrl =
    imageSource === "stream" && instanceId
      ? h264StreamUrl(instanceId)
      : null;
  const imageUrl =
    imageSource !== "stream" && view?.preview.available && instanceId
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
          {hasPending ? "Pending" : "Idle"}
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

      {testModule ? (
        <div
          className="approvals-callout approvals-callout--warn"
          role="status"
          aria-live="polite"
        >
          Test mode: only <code>{testModule}</code> scenarios and analyzers run.
          Other queued tasks stay parked until you switch back to{" "}
          <strong>All modules</strong>.
        </div>
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
          onChange={(next) => {
            // Manual selection — sticks across refreshes; the auto-upgrade
            // path below won't override the operator's choice.
            userPickedSourceRef.current = true;
            setImageSource(next as ImageSource);
          }}
          minWidth={210}
          isSearchable={false}
        />
        <AppSelect
          label="Module"
          options={[
            { value: TEST_MODULE_ALL, label: "All modules" },
            ...modules.map((m) => ({
              value: m.id,
              label: `${m.title || m.id} · ${m.id}`,
            })),
          ]}
          value={testModule}
          onChange={(next) => void onChangeTestModule(next)}
          minWidth={260}
          isSearchable
          disabled={!instanceId || busyAction === "test-module"}
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
            streamUrl={streamUrl}
            width={view?.preview.width ?? 0}
            height={view?.preview.height ?? 0}
            overlays={overlaysStable}
            onStreamClosed={(reason) => {
              // No silent fallback — surface the close reason verbatim. The
              // operator stays in stream mode and decides whether to switch
              // via the dropdown.
              setError(`Live video stream closed: ${reason}`);
            }}
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
  if (progress.progress_label?.trim()) return progress.progress_label.trim();
  const key = progress.scenario_label || progress.scenario_key;
  if (progress.is_navigating && progress.nav_target) {
    return `${key} · Navigating → ${progress.nav_target}`;
  }
  if (key && progress.step_total > 0) {
    let text = `${key} · Step ${progress.step_current + 1}/${progress.step_total}`;
    if (progress.is_running && progress.step_iter > 0) {
      text += ` · iter ${progress.step_iter}`;
    }
    if (!progress.is_running) text += " · idle";
    return text;
  }
  if (key) return `${key} · running`;
  return "no active scenario";
}

function ScenarioProgressBar({ progress }: { progress: ScenarioProgress }) {
  const completedSteps =
    progress.completed_steps ??
    (progress.step_total > 0
      ? progress.is_navigating
        ? progress.step_current
        : progress.is_running
          ? progress.step_current + 1
          : progress.step_current
      : 0);
  const ratio =
    progress.progress_ratio != null
      ? Math.min(100, progress.progress_ratio * 100)
      : progress.step_total > 0
        ? Math.min(100, (completedSteps / progress.step_total) * 100)
        : 0;
  const label = scenarioProgressLabel(progress);
  const currentIdx =
    progress.highlight_step_index ??
    (progress.is_running && !progress.is_navigating ? progress.step_current : -1);

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
        Live check for a merged area region (core `area.json` + module `area.yaml`).
        Orange shows where the matcher searched,
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
        <summary className="approvals-payload__summary">
          <span>Payload · {actionLabel}</span>
          <span
            className="approvals-payload__summary-actions"
            onPointerDown={(e) => e.preventDefault()}
            onClick={(e) => e.stopPropagation()}
          >
            <CopyButton
              text={payloadJson}
              label="Copy"
              title="Copy payload JSON"
              className="approvals-payload__copy"
            />
          </span>
        </summary>
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
