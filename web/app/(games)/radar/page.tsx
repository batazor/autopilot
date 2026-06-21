"use client";

import dynamic from "next/dynamic";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppConfirmDialog, AppListbox, AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { Button } from "@/components/ui/Button";
import { PageLoading } from "@/components/ui/Spinner";
import ScanProgressDiamond, {
  cellKey,
  type CellMark,
} from "@/components/radar/ScanProgressDiamond";
import { ApiError } from "@/lib/api";
import {
  RADAR_EVENTS_URL,
  assembleCityMap,
  buildRadarTiles,
  calibrateRadarCorner,
  deleteAllRadarRuns,
  deleteRadarRun,
  fetchRadarActive,
  fetchRadarInstances,
  fetchRadarManifest,
  fetchRadarRuns,
  fetchRadarTilesMeta,
  markRadarCorners,
  radarPreviewUrl,
  startRadarScan,
  stopRadarScan,
  RADAR_CORNER_ORDER,
  RADAR_TARGETS,
  RADAR_TARGET_LABELS,
  type RadarCorner,
  type RadarEvent,
  type RadarGridCell,
  type RadarManifest,
  type RadarTarget,
} from "@/lib/radar-api";

const RadarMapViewer = dynamic(() => import("@/components/radar/RadarMapViewer"), {
  ssr: false,
  loading: () => <PageLoading />,
});

const errMsg = (e: unknown) =>
  e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e);

// ---------------------------------------------------------------------------
// Live scan state (fed exclusively by SSE — no polling)
// ---------------------------------------------------------------------------

type LiveState = {
  phase: "idle" | "queued" | "scanning" | "failed";
  runId: string | null;
  /** Which map tab owns the live scan — null when idle. */
  target: RadarTarget | null;
  done: number;
  total: number;
  grid: RadarGridCell[] | null;
  cells: Record<string, CellMark>;
  error: string | null;
  /** Receipt timestamps of recent frame_done events, for the ETA. */
  frameAt: number[];
  /** Bumps on every map_updated — cache-busts the live preview image. */
  mapVersion: number;
};

const LIVE_IDLE: LiveState = {
  phase: "idle",
  runId: null,
  target: null,
  done: 0,
  total: 0,
  grid: null,
  cells: {},
  error: null,
  frameAt: [],
  mapVersion: 0,
};

type LiveAction =
  | { type: "event"; event: RadarEvent; at: number }
  | { type: "queued"; runId: string; target: RadarTarget; total: number; grid: RadarGridCell[] }
  | { type: "hydrate"; grid: RadarGridCell[]; cells: Record<string, CellMark> };

function liveReducer(state: LiveState, action: LiveAction): LiveState {
  if (action.type === "queued") {
    return {
      ...LIVE_IDLE,
      phase: "scanning",
      runId: action.runId,
      target: action.target,
      total: action.total,
      grid: action.grid,
    };
  }
  if (action.type === "hydrate") {
    // Mid-scan page (re)load: grid + already-done cells come from the manifest.
    if (state.grid !== null) return state;
    return { ...state, grid: action.grid, cells: { ...action.cells, ...state.cells } };
  }
  const ev = action.event;
  switch (ev.type) {
    case "scan_active": {
      // Connection bootstrap: rebase on the server's authoritative state.
      if (!ev.active) {
        return state.phase === "scanning" || state.phase === "queued" ? LIVE_IDLE : state;
      }
      const sameRun = state.runId === ev.active.run_id;
      const cells = sameRun ? state.cells : {};
      const grid = (sameRun ? state.grid : null) ?? ev.active.grid ?? null;
      return {
        ...state,
        phase: ev.active.status === "queued" ? "queued" : "scanning",
        runId: ev.active.run_id,
        target: ev.active.target ?? null,
        done: ev.active.done,
        total: ev.active.total,
        grid,
        cells: grid ? markDonePrefix(grid, ev.active.done, cells) : cells,
        error: null,
        // Mid-scan (re)connect: frames already done imply a stitched preview.
        mapVersion: sameRun ? Math.max(state.mapVersion, ev.active.done) : ev.active.done,
      };
    }
    case "scan_started":
      return {
        ...LIVE_IDLE,
        phase: "scanning",
        runId: ev.run_id,
        target: ev.target ?? null,
        total: ev.total_frames,
        grid: ev.grid,
      };
    case "frame_done": {
      if (state.runId !== null && ev.run_id !== state.runId) return state;
      return {
        ...state,
        phase: "scanning",
        runId: ev.run_id,
        target: ev.target ?? state.target,
        done: ev.done,
        total: ev.total,
        cells: { ...state.cells, [cellKey(ev)]: ev.unstable ? "unstable" : "done" },
        frameAt: [...state.frameAt.slice(-19), action.at],
      };
    }
    case "scan_finished":
      return LIVE_IDLE;
    case "scan_failed":
      return { ...state, phase: "failed", error: ev.error };
    case "map_updated":
      if (state.runId !== null && ev.run_id !== state.runId) return state;
      return { ...state, mapVersion: Math.max(state.mapVersion + 1, ev.frames) };
    case "tiles_ready":
      return state;
    default:
      return state;
  }
}

function manifestCells(manifest: RadarManifest): {
  grid: RadarGridCell[];
  cells: Record<string, CellMark>;
} {
  const frames = Object.values(manifest.frames ?? {});
  const grid =
    manifest.grid?.points ?? frames.map((f) => ({ ix: f.ix, iy: f.iy }));
  const cells: Record<string, CellMark> = {};
  for (const f of frames) cells[cellKey(f)] = f.unstable ? "unstable" : "done";
  return { grid, cells };
}

function markDonePrefix(
  grid: RadarGridCell[],
  done: number,
  cells: Record<string, CellMark>,
): Record<string, CellMark> {
  if (!Number.isFinite(done) || done <= 0) return cells;
  const next = { ...cells };
  for (const cell of grid.slice(0, Math.min(done, grid.length))) {
    const key = cellKey(cell);
    if (!(key in next)) next[key] = "done";
  }
  return next;
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function formatStartedAt(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function progressPercent(done: number, total: number): number {
  if (!Number.isFinite(done) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, (done / total) * 100));
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RadarPage() {
  const queryClient = useQueryClient();
  const { showSuccess, showInfo } = useFeedback();
  const [selectedTarget, setSelectedTarget] = useState<RadarTarget>("global_map");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null);
  const [deleteConfirmRunId, setDeleteConfirmRunId] = useState<string | null>(null);
  const [clearAllConfirm, setClearAllConfirm] = useState(false);
  const [live, dispatch] = useReducer(liveReducer, LIVE_IDLE);
  // Corner-marking mode: operator clicks the 4 kingdom vertices to pin the grid.
  const [marking, setMarking] = useState(false);
  const [markStep, setMarkStep] = useState(0);
  const [cornerClicks, setCornerClicks] = useState<Partial<Record<RadarCorner, [number, number]>>>(
    {},
  );

  const scanActiveNow = live.phase === "queued" || live.phase === "scanning";

  const runs = useQuery({
    queryKey: ["radar", "runs", selectedTarget],
    queryFn: () => fetchRadarRuns(selectedTarget),
  });
  const activeScan = useQuery({
    queryKey: ["radar", "active"],
    queryFn: fetchRadarActive,
    refetchOnWindowFocus: true,
    // Safety net: SSE is the live channel, but if a proxy buffers or drops
    // it, this poll keeps progress + the live map moving during a scan.
    refetchInterval: scanActiveNow ? 2000 : false,
  });
  const runId = selectedRunId ?? runs.data?.[0]?.run_id ?? null;

  const instances = useQuery({
    queryKey: ["radar", "instances"],
    queryFn: fetchRadarInstances,
  });
  // Default scan target = first configured instance (matches the backend).
  const effectiveInstanceId =
    selectedInstanceId ?? instances.data?.[0]?.instance_id ?? "";

  const tiles = useQuery({
    queryKey: ["radar", "tiles", runId],
    queryFn: () => fetchRadarTilesMeta(runId as string),
    enabled: runId !== null,
  });
  const manifest = useQuery({
    queryKey: ["radar", "manifest", runId],
    queryFn: () => fetchRadarManifest(runId as string),
    enabled: runId !== null,
  });

  const refreshAll = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["radar"] }),
    [queryClient],
  );
  const refreshActive = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["radar", "active"] }),
    [queryClient],
  );

  const wasActiveRef = useRef(false);
  useEffect(() => {
    if (!activeScan.data) return;
    const isActive = activeScan.data.active !== null;
    // Poll-only path (SSE dead): when the scan ends, the active key clears —
    // refresh runs/tiles the same way the scan_finished event would.
    if (wasActiveRef.current && !isActive) void refreshAll();
    wasActiveRef.current = isActive;
    dispatch({
      type: "event",
      event: { type: "scan_active", active: activeScan.data.active },
      at: Date.now(),
    });
  }, [activeScan.data, refreshAll]);

  // SSE is the live channel. The active JSON snapshot is only a resync hook for
  // mount/focus/reconnect, so progress does not depend on timer polling.
  useEffect(() => {
    const es = new EventSource(RADAR_EVENTS_URL);
    let opens = 0;
    es.onopen = () => {
      opens += 1;
      refreshActive();
      if (opens > 1) refreshAll();
    };
    es.onerror = () => {
      refreshActive();
    };
    es.onmessage = (msg) => {
      let ev: RadarEvent;
      try {
        ev = JSON.parse(msg.data as string) as RadarEvent;
      } catch {
        return;
      }
      dispatch({ type: "event", event: ev, at: Date.now() });
      if (ev.type === "scan_finished") {
        refreshAll();
        if (ev.target) setSelectedTarget(ev.target);
        setSelectedRunId(ev.run_id);
        showSuccess(`Scan ${ev.run_id} finished in ${formatDuration(ev.duration_s)}`);
      } else if (ev.type === "tiles_ready") {
        refreshAll();
        showInfo(`Tiles ready for ${ev.run_id}`);
      }
    };
    return () => es.close();
  }, [refreshActive, refreshAll, showSuccess, showInfo]);

  // Page opened mid-scan: the bootstrap only carries counters, so pull the
  // grid layout + already-captured cells from the active run's manifest.
  useEffect(() => {
    if (live.phase !== "scanning" || live.grid !== null || !live.runId) return;
    let cancelled = false;
    fetchRadarManifest(live.runId)
      .then((m) => {
        if (!cancelled) dispatch({ type: "hydrate", ...manifestCells(m) });
      })
      .catch(() => {
        /* manifest may not exist yet right after scan start */
      });
    return () => {
      cancelled = true;
    };
  }, [live.phase, live.grid, live.runId]);

  const scan = useMutation({
    mutationFn: (resume: boolean) =>
      startRadarScan(effectiveInstanceId, selectedTarget, resume),
    onSuccess: (res) => {
      dispatch({
        type: "queued",
        runId: res.run_id,
        target: res.target,
        total: res.total_frames,
        grid: res.grid,
      });
      refreshActive();
      showInfo(
        `Scan ${res.run_id} (${RADAR_TARGET_LABELS[res.target]}) ${
          res.resumed ? "resumed" : "started"
        } on ${res.instance_id}`,
      );
    },
  });
  const stopScan = useMutation({
    mutationFn: stopRadarScan,
    onSuccess: (res) => {
      refreshActive();
      showInfo(`Stopping scan ${res.run_id} — frames so far are kept`);
    },
  });
  const tilesBuild = useMutation({
    mutationFn: () => buildRadarTiles(runId as string),
    onSuccess: () => showInfo("Tile build started — the map appears when it finishes"),
  });
  const assembleMap = useMutation({
    mutationFn: assembleCityMap,
    onSuccess: (res) =>
      showSuccess(
        `Base map assembled — ${res.buildings} buildings from ${res.chunks} chunk(s)` +
          (res.dropped ? `, ${res.dropped} dropped (no overlap)` : ""),
      ),
  });
  const calibrateCorner = useMutation({
    mutationFn: () => calibrateRadarCorner(effectiveInstanceId, selectedTarget),
    onSuccess: (res) => {
      const [cx, cy] = res.corner_ref.cross_px;
      showSuccess(
        `${RADAR_TARGET_LABELS[res.target]} corner reference recorded — ` +
          `crossing at (${Math.round(cx)}, ${Math.round(cy)})`,
      );
    },
  });
  const markCorners = useMutation({
    mutationFn: (corners: Record<RadarCorner, [number, number]>) =>
      markRadarCorners(runId as string, corners),
    onSuccess: () => {
      setMarking(false);
      setMarkStep(0);
      setCornerClicks({});
      showInfo("Pinning the grid to the marked corners — the map updates when it finishes");
    },
  });

  // Each map click records the current vertex; the 4th submits all four.
  const handleMapClick = useCallback(
    (canvasPx: [number, number]) => {
      if (!marking) return;
      const corner = RADAR_CORNER_ORDER[markStep];
      const next = { ...cornerClicks, [corner]: canvasPx };
      setCornerClicks(next);
      if (markStep < RADAR_CORNER_ORDER.length - 1) {
        setMarkStep(markStep + 1);
      } else {
        markCorners.mutate(next as Record<RadarCorner, [number, number]>);
      }
    },
    [marking, markStep, cornerClicks, markCorners],
  );
  const cornerMarkers = useMemo<[number, number][]>(
    () => (marking ? Object.values(cornerClicks) : []),
    [marking, cornerClicks],
  );
  const deleteRun = useMutation({
    mutationFn: deleteRadarRun,
    onSuccess: (res) => {
      setDeleteConfirmRunId(null);
      if (selectedRunId === res.run_id) setSelectedRunId(null);
      queryClient.removeQueries({ queryKey: ["radar", "manifest", res.run_id] });
      queryClient.removeQueries({ queryKey: ["radar", "tiles", res.run_id] });
      refreshAll();
      showInfo(`Run ${res.run_id} deleted`);
    },
  });
  const clearAllRuns = useMutation({
    mutationFn: deleteAllRadarRuns,
    onSuccess: (res) => {
      setClearAllConfirm(false);
      if (selectedRunId !== null && res.deleted.includes(selectedRunId)) setSelectedRunId(null);
      for (const id of res.deleted) {
        queryClient.removeQueries({ queryKey: ["radar", "manifest", id] });
        queryClient.removeQueries({ queryKey: ["radar", "tiles", id] });
      }
      refreshAll();
      showInfo(
        res.skipped.length > 0
          ? `${res.deleted.length} run(s) deleted, ${res.skipped.length} kept (active scan)`
          : `${res.deleted.length} run(s) deleted`,
      );
    },
  });

  // Tab-scoped: the live progress/map/metrics belong to whichever tab owns the
  // running scan. Other tabs render their own runs as if idle. ``scanActiveNow``
  // stays global so a scan on one target still blocks starting another.
  const scanActive = scanActiveNow && live.target === selectedTarget;
  const selectedRunHasMap =
    (runs.data ?? []).find((r) => r.run_id === runId)?.has_map ?? false;

  // The viewer subtree depends only on (runId, tiles meta) — progress events
  // re-render the page but never re-mount or re-render the tile layer.
  const viewer = useMemo(() => {
    if (runId === null) return null;
    if (tiles.data == null) return null;
    return (
      <RadarMapViewer
        runId={runId}
        meta={tiles.data}
        onMapClick={marking ? handleMapClick : undefined}
        cornerMarkers={marking ? cornerMarkers : undefined}
      />
    );
  }, [runId, tiles.data, marking, handleMapClick, cornerMarkers]);

  // Idle metrics come from the selected run's manifest; live ones from SSE.
  const manifestStats = useMemo(() => {
    const m = manifest.data;
    if (!m) return null;
    const frames = Object.values(m.frames ?? {});
    const ts = frames.map((f) => f.ts).filter(Boolean);
    return {
      done: frames.length,
      total: m.grid?.count ?? frames.length,
      unstable: frames.filter((f) => f.unstable).length,
      duration: ts.length > 1 ? Math.max(...ts) - Math.min(...ts) : 0,
    };
  }, [manifest.data]);

  const liveUnstable = useMemo(
    () => Object.values(live.cells).filter((m) => m === "unstable").length,
    [live.cells],
  );

  const eta = useMemo(() => {
    if (live.phase !== "scanning" || live.frameAt.length < 2 || live.total === 0) return null;
    const at = live.frameAt;
    const avgMs = (at[at.length - 1] - at[0]) / (at.length - 1);
    return ((live.total - live.done) * avgMs) / 1000;
  }, [live.phase, live.frameAt, live.done, live.total]);

  const progressGrid = scanActive
    ? live.grid
    : manifest.data
      ? manifestCells(manifest.data).grid
      : null;
  const progressCells = scanActive
    ? live.cells
    : manifest.data
      ? manifestCells(manifest.data).cells
      : {};
  const linearProgress = scanActive
    ? { done: live.done, total: live.total }
    : manifestStats
      ? { done: manifestStats.done, total: manifestStats.total }
      : { done: 0, total: 0 };
  const linearProgressPct = progressPercent(linearProgress.done, linearProgress.total);
  const linearProgressLabel =
    linearProgress.total > 0
      ? `${Math.round(linearProgressPct)}%`
      : scanActive
        ? "starting"
        : "—";
  const linearProgressWidth =
    scanActive && linearProgress.total === 0 ? 100 : linearProgressPct;

  const statusPill =
    live.phase === "scanning" ? (
      <span className="status-pill pill-busy">
        scanning {live.done}/{live.total}
      </span>
    ) : live.phase === "queued" ? (
      <span className="status-pill pill-busy">queued</span>
    ) : live.phase === "failed" ? (
      <span className="status-pill pill-danger" title={live.error ?? undefined}>
        failed
      </span>
    ) : (
      <span className="status-pill pill-offline">idle</span>
    );

  const runOptions = (runs.data ?? []).map((r) => ({
    value: r.run_id,
    label: `${r.run_id} (${r.frames_done}/${r.frames_total})`,
  }));
  const instanceOptions = (instances.data ?? []).map((i) => ({
    value: i.instance_id,
    label: i.serial ? `${i.instance_id} (${i.serial})` : i.instance_id,
  }));

  const queryError = runs.isError
    ? errMsg(runs.error)
    : manifest.isError
      ? errMsg(manifest.error)
      : tiles.isError
        ? errMsg(tiles.error)
        : null;

  const busyOnOtherTarget = scanActiveNow && live.target !== selectedTarget;
  const otherTargetLabel =
    busyOnOtherTarget && live.target ? RADAR_TARGET_LABELS[live.target] : "";

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Map target tabs — each is an independent scan/map. */}
      <AppTabs
        variant="section"
        renderPanels={false}
        selectedKey={selectedTarget}
        onChange={(key) => {
          setSelectedTarget(key as RadarTarget);
          // Each tab defaults to its own newest run.
          setSelectedRunId(null);
        }}
        tabs={RADAR_TARGETS.map((t) => ({
          key: t,
          label:
            scanActiveNow && live.target === t
              ? `${RADAR_TARGET_LABELS[t]} (scanning)`
              : RADAR_TARGET_LABELS[t],
          title:
            scanActiveNow && live.target === t
              ? `Scanning now — ${live.done}/${live.total} frames`
              : `${RADAR_TARGET_LABELS[t]} map`,
        }))}
      />

      {/* Header row */}
      <div className="panel flex flex-wrap items-center gap-3 p-3">
        <h1 className="text-lg font-semibold">Radar</h1>
        <AppListbox
          aria-label="Run"
          options={runOptions}
          value={runId ?? ""}
          onChange={setSelectedRunId}
          placeholder={runs.isLoading ? "Loading runs…" : "No runs yet"}
          loading={runs.isLoading}
          disabled={runOptions.length === 0}
          minWidth={260}
          inline
        />
        {scanActive ? (
          <Button
            variant="danger"
            pending={stopScan.isPending}
            title="Stop the running scan — frames captured so far are kept and stitched"
            onClick={() => stopScan.mutate()}
          >
            Stop scan
          </Button>
        ) : (
          <>
            {instanceOptions.length > 1 ? (
              <AppListbox
                aria-label="Scan target instance"
                options={instanceOptions}
                value={effectiveInstanceId}
                onChange={setSelectedInstanceId}
                placeholder={instances.isLoading ? "Loading…" : "Instance"}
                loading={instances.isLoading}
                minWidth={200}
                inline
              />
            ) : null}
            <Button
              variant="primary"
              pending={scan.isPending && scan.variables !== true}
              disabled={busyOnOtherTarget}
              title={
                busyOnOtherTarget
                  ? `A scan is running on ${otherTargetLabel} — only one scan at a time`
                  : `Start a ${RADAR_TARGET_LABELS[selectedTarget]} scan on the selected instance`
              }
              onClick={() => scan.mutate(false)}
            >
              Start scan
            </Button>
            <Button
              variant="secondary"
              pending={scan.isPending && scan.variables === true}
              disabled={busyOnOtherTarget}
              title={
                busyOnOtherTarget
                  ? `A scan is running on ${otherTargetLabel} — only one scan at a time`
                  : `Continue the newest unfinished ${RADAR_TARGET_LABELS[selectedTarget]} scan — re-anchors and captures only the missing cells`
              }
              onClick={() => scan.mutate(true)}
            >
              Resume
            </Button>
          </>
        )}
        {!scanActive ? (
          <Button
            pending={calibrateCorner.isPending}
            disabled={busyOnOtherTarget}
            title={
              busyOnOtherTarget
                ? `A scan is running on ${otherTargetLabel} — stop it before calibrating`
                : "Record the corner reference from the CURRENT screen — pan the camera so the bottom-corner X (dashed yellow lines crossing) is clearly visible first"
            }
            onClick={() => calibrateCorner.mutate()}
          >
            Calibrate corner
          </Button>
        ) : null}
        {selectedTarget !== "global_map" && !scanActive ? (
          <Button
            pending={assembleMap.isPending}
            disabled={busyOnOtherTarget}
            title="Fuse all scanned base chunks into one navigation map (run after scanning the base in overlapping pieces)"
            onClick={() => assembleMap.mutate()}
          >
            Assemble base map
          </Button>
        ) : null}
        {selectedTarget === "global_map" && !scanActive && selectedRunHasMap ? (
          marking ? (
            <Button
              variant="secondary"
              onClick={() => {
                setMarking(false);
                setMarkStep(0);
                setCornerClicks({});
              }}
            >
              Cancel marking
            </Button>
          ) : (
            <Button
              pending={markCorners.isPending}
              disabled={busyOnOtherTarget}
              title="Click the 4 kingdom vertices on the map to pin the coordinate grid exactly to the square game lattice (removes stitch drift)"
              onClick={() => {
                setCornerClicks({});
                setMarkStep(0);
                setMarking(true);
              }}
            >
              Mark corners
            </Button>
          )
        ) : null}
        {statusPill}
        <div className="ml-auto" />
      </div>

      <div className="panel !p-3">
        <div className="mb-2 flex items-center justify-between gap-3 text-sm">
          <span className="font-medium text-wos-text-muted">Scan progress</span>
          <span className="tabular-nums text-wos-text-secondary">
            {linearProgress.total > 0
              ? `${linearProgress.done}/${linearProgress.total} · ${linearProgressLabel}`
              : linearProgressLabel}
          </span>
        </div>
        <div
          className="h-2.5 overflow-hidden rounded-full bg-wos-panel-raised"
          role="progressbar"
          aria-label="Radar scan progress"
          aria-valuemin={0}
          aria-valuemax={Math.max(linearProgress.total, 1)}
          aria-valuenow={Math.min(linearProgress.done, linearProgress.total || 0)}
        >
          <div
            className={`h-full rounded-full bg-sky-400 transition-[width] duration-300 ${
              scanActive && linearProgress.total === 0 ? "animate-pulse" : ""
            }`}
            style={{ width: `${linearProgressWidth}%` }}
          />
        </div>
      </div>

      {scan.isError ? <ErrorBanner message={errMsg(scan.error)} /> : null}
      {calibrateCorner.isError ? <ErrorBanner message={errMsg(calibrateCorner.error)} /> : null}
      {assembleMap.isError ? <ErrorBanner message={errMsg(assembleMap.error)} /> : null}
      {live.phase === "failed" && live.error ? (
        <ErrorBanner message={`Scan failed: ${live.error}`} />
      ) : null}
      {deleteRun.isError ? <ErrorBanner message={errMsg(deleteRun.error)} /> : null}
      {clearAllRuns.isError ? <ErrorBanner message={errMsg(clearAllRuns.error)} /> : null}
      {queryError ? (
        <ErrorBanner
          message={queryError}
          onRetry={() => void refreshAll()}
          retrying={runs.isFetching}
        />
      ) : null}

      {marking ? (
        <div className="panel flex flex-wrap items-center gap-3 p-3 text-sm">
          <span className="status-pill pill-busy">Mark corners</span>
          <span>
            Click the{" "}
            <strong className="uppercase text-sky-400">{RADAR_CORNER_ORDER[markStep]}</strong> vertex
            of the kingdom diamond — step {markStep + 1} of {RADAR_CORNER_ORDER.length}.
          </span>
          <span className="tabular-nums text-wos-text-muted">
            {RADAR_CORNER_ORDER.map((_, i) => (i < markStep ? "●" : "○")).join(" ")}
          </span>
          <span className="text-wos-text-muted">Zoom in for precision.</span>
        </div>
      ) : null}

      {/* Map viewer */}
      <div className="panel p-3">
        {scanActive && live.runId ? (
          // Live mode: the map grows in front of you — every captured frame is
          // re-stitched on the fly and pushed here via the map_updated event.
          live.mapVersion > 0 ? (
            <div className="relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={radarPreviewUrl(live.runId, live.mapVersion)}
                alt={`live stitched map (${live.done}/${live.total} frames)`}
                className="block h-auto w-full rounded"
              />
              <span className="status-pill pill-busy absolute right-2 top-2">
                live · {live.done}/{live.total}
              </span>
            </div>
          ) : (
            <div className="flex h-64 flex-col items-center justify-center gap-2 text-wos-text-muted">
              <p>Capturing the first frame…</p>
              <p className="text-sm">The map appears here and grows as frames land.</p>
            </div>
          )
        ) : runId === null ? (
          <div className="flex h-64 flex-col items-center justify-center gap-2 text-wos-text-muted">
            <p>No scan runs yet.</p>
            <p className="text-sm">Press “Start scan” to capture the kingdom map.</p>
          </div>
        ) : tiles.isLoading ? (
          <PageLoading />
        ) : viewer ?? (
            <div className="flex flex-col items-center justify-center gap-3 py-4 text-wos-text-muted">
              {selectedRunHasMap ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={radarPreviewUrl(runId, "final")}
                  alt="stitched map preview"
                  className="block h-auto w-full rounded"
                />
              ) : null}
              <p>This run has no map tiles yet.</p>
              <Button
                variant="secondary"
                pending={tilesBuild.isPending}
                onClick={() => tilesBuild.mutate()}
              >
                Build tiles
              </Button>
              {tilesBuild.isError ? (
                <p className="text-sm text-red-400">{errMsg(tilesBuild.error)}</p>
              ) : null}
            </div>
          )}
      </div>

      {/* Metric cards */}
      <div className="metrics-row">
        <div className="metric-card">
          <div className="label">Frames</div>
          <div className="value">
            {scanActive
              ? `${live.done}/${live.total}`
              : manifestStats
                ? `${manifestStats.done}/${manifestStats.total}`
                : "—"}
          </div>
        </div>
        <div className="metric-card">
          <div className="label">Duration</div>
          <div className="value">
            {scanActive
              ? eta !== null
                ? `ETA ${formatDuration(eta)}`
                : "…"
              : formatDuration(manifestStats?.duration ?? 0)}
          </div>
        </div>
        <div
          className={`metric-card${(scanActive ? liveUnstable : (manifestStats?.unstable ?? 0)) > 0 ? " metric-card--warn" : ""}`}
        >
          <div className="label">Unstable frames</div>
          <div className="value">
            {scanActive ? liveUnstable : (manifestStats?.unstable ?? "—")}
          </div>
        </div>
      </div>

      {/* Bottom row: progress + history */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel p-3">
          <h2 className="mb-2 text-sm font-semibold text-wos-text-muted">Scan progress</h2>
          {progressGrid && progressGrid.length > 0 ? (
            <>
              <ScanProgressDiamond
                grid={progressGrid}
                cells={progressCells}
                scanning={live.phase === "scanning"}
              />
              <p className="mt-2 text-center text-sm text-wos-text-muted">
                {scanActive
                  ? `${live.done}/${live.total}${eta !== null ? ` · ETA ${formatDuration(eta)}` : ""}`
                  : manifestStats
                    ? `${manifestStats.done}/${manifestStats.total}`
                    : ""}
              </p>
            </>
          ) : (
            <p className="text-sm text-wos-text-muted">
              {scanActive ? "Waiting for scan grid…" : "Select a run to see its grid."}
            </p>
          )}
        </div>

        <div className="panel p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-wos-text-muted">Run history</h2>
            {(runs.data ?? []).length > 0 ? (
              <button
                type="button"
                className="btn-secondary"
                disabled={clearAllRuns.isPending}
                title="Delete every recorded run (an active scan is kept)"
                onClick={() => setClearAllConfirm(true)}
              >
                {clearAllRuns.isPending ? "Clearing…" : "Clear all"}
              </button>
            ) : null}
          </div>
          {(runs.data ?? []).length === 0 ? (
            <p className="text-sm text-wos-text-muted">No runs recorded yet.</p>
          ) : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Started</th>
                    <th>Frames</th>
                    <th>Duration</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(runs.data ?? []).map((r) => (
                    <tr key={r.run_id} className={r.run_id === runId ? "fleet-row--accent" : ""}>
                      <td>{r.run_id}</td>
                      <td>{formatStartedAt(r.started_at)}</td>
                      <td>
                        {r.frames_done}/{r.frames_total}
                        {r.unstable_count > 0 ? (
                          <span
                            className="status-pill pill-paused ml-2"
                            title={`${r.unstable_count} unstable frame(s)`}
                          >
                            {r.unstable_count} unstable
                          </span>
                        ) : null}
                      </td>
                      <td>{formatDuration(r.duration_s)}</td>
                      <td>
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={r.run_id === runId}
                            onClick={() => setSelectedRunId(r.run_id)}
                          >
                            View
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={
                              deleteRun.isPending ||
                              (scanActive && live.runId === r.run_id)
                            }
                            title={
                              scanActive && live.runId === r.run_id
                                ? "Cannot delete an active scan"
                                : "Delete this scan run"
                            }
                            onClick={() => setDeleteConfirmRunId(r.run_id)}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <AppConfirmDialog
        open={clearAllConfirm}
        onClose={() => {
          if (!clearAllRuns.isPending) setClearAllConfirm(false);
        }}
        onConfirm={() => clearAllRuns.mutate()}
        title="Clear run history?"
        confirmLabel={clearAllRuns.isPending ? "Clearing…" : "Delete all runs"}
        variant="danger"
        busy={clearAllRuns.isPending}
      >
        <p>
          Delete <strong>{(runs.data ?? []).length}</strong> recorded run(s) with all
          frames, stitched maps, and tiles? An active scan (if running) is kept.
          This cannot be undone.
        </p>
      </AppConfirmDialog>

      <AppConfirmDialog
        open={deleteConfirmRunId !== null}
        onClose={() => {
          if (!deleteRun.isPending) setDeleteConfirmRunId(null);
        }}
        onConfirm={() => {
          if (deleteConfirmRunId !== null) deleteRun.mutate(deleteConfirmRunId);
        }}
        title="Delete scan?"
        confirmLabel={deleteRun.isPending ? "Deleting…" : "Delete scan"}
        variant="danger"
        busy={deleteRun.isPending}
      >
        <p>
          Delete scan <code>{deleteConfirmRunId}</code> and all of its frames,
          stitched maps, and tiles? This cannot be undone.
        </p>
      </AppConfirmDialog>
    </div>
  );
}
