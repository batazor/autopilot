"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import {
  ApiError,
  clearPendingApproval,
  clearQueueAll,
  clickApprovalImageUrl,
  fetchAreaRegionProbe,
  fetchClickApproval,
  fetchInstanceTestModule,
  fetchModules,
  fetchNotifications,
  overlayTestImageUrl,
  resetActivePlayer,
  resetCurrentScreen,
  setApprovalEnabled,
  setInstanceTestModule,
  submitDecision,
} from "@/lib/api";
import type { AreaRegionProbeResult, ClickApprovalView } from "@/lib/types";
import type { ModuleRow } from "@/lib/config-pages";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import {
  type BusyAction,
  type Decision,
  type ImageSource,
  type Toast,
  DOCUMENT_TITLE_BASE,
  NOTIFICATIONS_MAX_AGE_S,
  TEST_MODULE_ALL,
  TICK_MS,
  TOAST_VISIBLE_MS,
} from "@/lib/approvals/types";

/** All click-approvals page state, polling, SSE wiring, and action handlers. */
export function useApprovalsState() {
  const searchParams = useSearchParams();
  const { instanceId, instancesError } = useFleet();
  const [view, setView] = useState<ClickApprovalView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  // Rolling PNG by default; the operator can switch to the captured request
  // image via the dropdown.
  const [imageSource, setImageSource] = useState<ImageSource>("live");
  const [imageTick, setImageTick] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [showPayload, setShowPayload] = useState(false);
  const [showReset, setShowReset] = useState(false);
  const [showProbe, setShowProbe] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [regionProbe, setRegionProbe] = useState<AreaRegionProbeResult | null>(null);
  const [probeRegion, setProbeRegion] = useState("");
  const [probeThreshold, setProbeThreshold] = useState<number | null>(null);
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
  const decisionInFlightRef = useRef("");
  // Remember which pending-request key was last seen so we can bump the
  // image cache key only when the underlying request actually changes
  // (otherwise we'd thrash the browser's decoded-image cache every second).
  const lastPendingKeyRef = useRef<string>("");
  const lastPreviewMtimeRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (!instanceId) return;
    try {
      const data = await fetchClickApproval(instanceId, imageSource);
      setView(data);
      const nextKey = data.has_pending ? data.trace_id || "(pending)" : "(idle)";
      const pendingChanged = nextKey !== lastPendingKeyRef.current;
      if (pendingChanged) {
        lastPendingKeyRef.current = nextKey;
      }
      // Live rolling: worker overwrites the PNG on disk; bust cache when mtime moves.
      // Capture: only refetch when the pending approval identity changes.
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
        threshold: probeThreshold ?? undefined,
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
    debounceMs: 0,
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
    setImageSource("live");
    setView(null);
    setError(null);
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
      prev.map((t) => (t.id === id ? { ...t, expiresAt: t.expiresAt + extraMs } : t)),
    );
  }, []);

  // Browser tab title is the cheapest "you have work" indicator that survives
  // when the operator alt-tabs to another tool. Restore on unmount.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const original = document.title;
    // Only flag "you have work" when a worker is actually alive to act on the
    // decision — a stale pending request from a stopped bot is not actionable.
    document.title =
      view?.has_pending && view?.worker_alive
        ? `● ${DOCUMENT_TITLE_BASE}`
        : DOCUMENT_TITLE_BASE;
    return () => {
      document.title = original;
    };
  }, [view?.has_pending, view?.worker_alive]);

  const dismissToast = (id: string) =>
    setToasts((prev) => prev.filter((t) => t.id !== id));

  const onDecision = useCallback(
    async (decision: Decision) => {
      if (!instanceId || busyAction !== null || decisionInFlightRef.current) return;
      const requestId =
        typeof view?.pending?.request_id === "string" ? view.pending.request_id : "";
      decisionInFlightRef.current = `${instanceId}:${requestId || "(unknown)"}`;
      setBusyAction(decision);
      try {
        const ok = await submitDecision(instanceId, decision, requestId);
        if (!ok) {
          await refresh();
          return;
        }
        setView((prev) => {
          if (!prev?.has_pending) return prev;
          const currentRequestId =
            typeof prev.pending?.request_id === "string" ? prev.pending.request_id : "";
          if (requestId && currentRequestId && currentRequestId !== requestId) {
            return prev;
          }
          return {
            ...prev,
            has_pending: false,
            pending: null,
            overlays: [],
            tap_x: null,
            tap_y: null,
            action_type: "",
            action_label: "",
            region_label: "",
            trace_id: "",
            tempo_trace_url: "",
            labeling_href: "",
            navigation: null,
            task_context: null,
            set_node_target: "",
          };
        });
        setError(null);
        void refresh();
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          try {
            await refresh();
            setError(null);
          } catch (refreshError) {
            setError(
              refreshError instanceof Error ? refreshError.message : String(refreshError),
            );
          }
          return;
        }
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        decisionInFlightRef.current = "";
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

  const onResetPlayer = async () => {
    if (!instanceId || busyAction !== null) return;
    setBusyAction("reset-player");
    try {
      await resetActivePlayer(instanceId);
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
      // Don't let approve/reject/skip shortcuts fire against a stale request
      // when the bot is stopped — the decision would have no worker to consume it.
      if (!view?.has_pending || !view?.worker_alive || busyAction !== null) return;
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
  }, [view?.has_pending, view?.worker_alive, busyAction, onDecision]);

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
  const workerAlive = !!view?.worker_alive;
  // A pending request only counts as actionable while a worker is alive to
  // consume the decision. When the bot is stopped we surface the leftover
  // request as an explanatory idle state instead of prompting the operator.
  const hasPending = !!view?.has_pending && workerAlive;
  const stalePending = !!view?.has_pending && !workerAlive;
  const instanceState = view?.instance_state ?? {};
  const staleTaskLabel =
    (instanceState.current_scenario || instanceState.current_task_type || "").trim();
  const staleWorkerTask =
    !workerAlive &&
    !view?.has_pending &&
    Boolean(
      (instanceState.state || "").trim().toLowerCase() === "busy" ||
        (instanceState.current_task_id || "").trim() ||
        staleTaskLabel,
    );
  const scenarioProgress = view?.scenario_progress ?? null;
  const approvalEnabled = !!view?.approval_enabled;
  const heartbeatActive = !!view?.heartbeat_active;
  const anyBusy = busyAction !== null;

  return {
    // fleet
    instanceId,
    instancesError,
    // raw state
    view,
    error,
    busyAction,
    imageSource,
    setImageSource,
    autoRefresh,
    setAutoRefresh,
    showPayload,
    setShowPayload,
    showReset,
    setShowReset,
    showProbe,
    setShowProbe,
    toasts,
    now,
    regionProbe,
    probeRegion,
    setProbeRegion,
    probeThreshold,
    setProbeThreshold,
    probeError,
    modules,
    testModule,
    confirmAction,
    setConfirmAction,
    // derived
    imageUrl,
    probeImageUrl,
    payloadJson,
    overlaysStable,
    scenarioLabel,
    regionLabel,
    traceId,
    navigation,
    taskContext,
    actionType,
    setNodeTarget,
    currentScreen,
    activePlayer,
    inGameId,
    playerAccountKey,
    hasPending,
    stalePending,
    staleTaskLabel,
    staleWorkerTask,
    scenarioProgress,
    approvalEnabled,
    heartbeatActive,
    anyBusy,
    // actions
    refresh,
    refreshProbe,
    onChangeTestModule,
    extendToast,
    dismissToast,
    onDecision,
    onToggleEnabled,
    onClearPending,
    onResetScreen,
    onResetPlayer,
    onClearQueue,
  };
}

export type ApprovalsState = ReturnType<typeof useApprovalsState>;
