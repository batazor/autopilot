"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { AppCheckbox } from "@/components/headless";
import {
  deleteMapStitchJob,
  fetchMapStitchJob,
  fetchSavedMaps,
  mapStitchFrameImageUrl,
  mapStitchMapImageUrl,
  savedMapImageUrl,
  saveMapStitch,
  startMapCapture,
  startMapStitch,
} from "@/lib/api";
import type { MapStitchJob, MapStitchState } from "@/lib/types";

const POLL_MS = 1000;

const PILL: Record<MapStitchState, string> = {
  queued: "status-idle",
  capturing: "status-idle",
  captured: "pill-live",
  stitching: "status-idle",
  done: "pill-live",
  error: "pill-stale",
};

const ACTIVE: ReadonlySet<MapStitchState> = new Set([
  "queued",
  "capturing",
  "stitching",
]);

export default function MapStitchPage() {
  const queryClient = useQueryClient();
  const { instanceId, instancesError } = useFleet();

  // capture parameters
  const [rows, setRows] = useState(3);
  const [cols, setCols] = useState(5);
  const [overlap, setOverlap] = useState(0.3);
  const [swipeMs, setSwipeMs] = useState(300);
  const [settleS, setSettleS] = useState(1.0);
  const [home, setHome] = useState(true);
  const [mapName, setMapName] = useState("map");

  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const jobQuery = useQuery({
    queryKey: ["mapStitchJob", jobId],
    queryFn: () => fetchMapStitchJob(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = (q.state.data as MapStitchJob | undefined)?.state;
      return s && ACTIVE.has(s) ? POLL_MS : false;
    },
  });
  const job = jobQuery.data;

  const savedQuery = useQuery({
    queryKey: ["savedMaps"],
    queryFn: fetchSavedMaps,
    refetchInterval: false,
  });

  const capture = useMutation({
    mutationFn: () => {
      if (!instanceId) throw new Error("no instance selected");
      return startMapCapture({
        instance_id: instanceId,
        rows,
        cols,
        overlap,
        swipe_ms: swipeMs,
        settle_s: settleS,
        home,
      });
    },
    onSuccess: (res) => {
      setJobId(res.job_id);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const stitch = useMutation({
    mutationFn: () => startMapStitch(jobId as string),
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const save = useMutation({
    mutationFn: () => saveMapStitch(jobId as string, mapName),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["savedMaps"] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const clear = () => {
    if (jobId) void deleteMapStitchJob(jobId).catch(() => undefined);
    setJobId(null);
    setError(null);
  };

  const pct = useMemo(() => {
    if (!job || !job.total) return 0;
    return Math.min(100, Math.round((job.captured / job.total) * 100));
  }, [job]);

  const jobErr = jobQuery.isError
    ? jobQuery.error instanceof Error
      ? jobQuery.error.message
      : String(jobQuery.error)
    : null;

  const capturing = !!job && job.state === "capturing";
  const stitching = !!job && job.state === "stitching";
  const busy = capture.isPending || capturing || stitching;
  const canStitch = !!job && job.frames.length > 0 && !busy;
  const savedMaps = savedQuery.data?.maps ?? [];

  return (
    <>
      <FleetPageHeader title="Map stitch">
        {job ? (
          <span className={`status-pill ${PILL[job.state]}`}>
            {job.state}
            {job.total ? ` · ${job.captured}/${job.total}` : ""}
          </span>
        ) : null}
      </FleetPageHeader>

      {error || jobErr || instancesError ? (
        <div className="error-banner">{error ?? jobErr ?? instancesError}</div>
      ) : null}
      {job?.state === "error" && job.error ? (
        <div className="error-banner">{job.error}</div>
      ) : null}

      <p className="meta" style={{ marginBottom: "0.75rem" }}>
        Captures a grid of frames by swiping the camera over the world map, then
        stitches them into one image. Capture grabs the device exclusively
        (scrcpy) — stop the bot / leave the device idle on the map first.
      </p>

      {/* Controls */}
      <div
        className="toolbar"
        style={{ flexWrap: "wrap", alignItems: "flex-end", marginBottom: "1rem", gap: "1rem" }}
      >
        <label>
          Rows
          <input
            type="number"
            min={1}
            max={12}
            value={rows}
            onChange={(e) => setRows(Number(e.target.value))}
            style={{ display: "block", width: 70 }}
          />
        </label>
        <label>
          Cols
          <input
            type="number"
            min={1}
            max={12}
            value={cols}
            onChange={(e) => setCols(Number(e.target.value))}
            style={{ display: "block", width: 70 }}
          />
        </label>
        <label>
          Overlap: <strong>{overlap.toFixed(2)}</strong>
          <input
            type="range"
            min={0.1}
            max={0.5}
            step={0.05}
            value={overlap}
            onChange={(e) => setOverlap(Number(e.target.value))}
            style={{ display: "block", width: 160 }}
          />
        </label>
        <label>
          Swipe: <strong>{swipeMs}ms</strong>
          <input
            type="range"
            min={100}
            max={1000}
            step={50}
            value={swipeMs}
            onChange={(e) => setSwipeMs(Number(e.target.value))}
            style={{ display: "block", width: 160 }}
          />
        </label>
        <label>
          Settle: <strong>{settleS.toFixed(1)}s</strong>
          <input
            type="range"
            min={0.5}
            max={3.0}
            step={0.1}
            value={settleS}
            onChange={(e) => setSettleS(Number(e.target.value))}
            style={{ display: "block", width: 160 }}
          />
        </label>
        <AppCheckbox
          inline
          checked={home}
          onChange={setHome}
          label="Home to top-left first"
        />
        <button
          type="button"
          className="btn-primary"
          onClick={() => capture.mutate()}
          disabled={busy || !instanceId}
          title="Start the grid-swipe capture"
        >
          {capturing ? "Capturing…" : "Start capture"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => stitch.mutate()}
          disabled={!canStitch}
          title="Stitch the captured frames into one map"
        >
          {stitching ? "Stitching…" : "Stitch map"}
        </button>
        {job ? (
          <button type="button" className="btn-secondary" onClick={clear}>
            Clear
          </button>
        ) : null}
      </div>

      {/* Progress */}
      {job && ACTIVE.has(job.state) && job.total ? (
        <div style={{ marginBottom: "1rem", maxWidth: 420 }}>
          <div
            style={{
              height: 8,
              borderRadius: 9999,
              background: "rgba(148,163,184,0.25)",
              overflow: "hidden",
            }}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={job.total}
            aria-valuenow={job.captured}
          >
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: "linear-gradient(90deg, #0284c7, #38bdf8)",
                transition: "width 0.3s ease-out",
              }}
            />
          </div>
          <span className="meta">
            {job.state === "stitching"
              ? "stitching…"
              : `${job.captured}/${job.total} frames · ${pct}%`}
          </span>
        </div>
      ) : null}

      {/* Stitched map */}
      {job?.map_ready ? (
        <section className="panel" style={{ marginBottom: "1rem" }}>
          <h2>Stitched map</h2>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={mapStitchMapImageUrl(job.job_id, job.state)}
            alt="stitched world map"
            style={{ width: "100%", height: "auto", display: "block" }}
          />
          <div
            className="toolbar"
            style={{ marginTop: "0.75rem", alignItems: "flex-end" }}
          >
            <label>
              Map name
              <input
                type="text"
                value={mapName}
                onChange={(e) => setMapName(e.target.value)}
                style={{ display: "block", width: 200 }}
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              onClick={() => save.mutate()}
              disabled={save.isPending}
            >
              {save.isPending ? "Saving…" : "Save map"}
            </button>
          </div>
        </section>
      ) : null}

      {/* Captured frames */}
      {job && job.frames.length > 0 ? (
        <section className="panel" style={{ marginBottom: "1rem" }}>
          <h2>Captured frames ({job.frames.length})</h2>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: `repeat(${Math.min(cols, 6)}, minmax(0, 1fr))`,
              gap: "0.5rem",
            }}
          >
            {job.frames.map((name) => (
              <figure key={name} style={{ margin: 0 }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={mapStitchFrameImageUrl(job.job_id, name)}
                  alt={name}
                  style={{
                    width: "100%",
                    height: "auto",
                    display: "block",
                    borderRadius: 6,
                  }}
                />
                <figcaption className="meta" style={{ textAlign: "center" }}>
                  {name.replace("frame_", "").replace(".png", "")}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      ) : null}

      {/* Status log */}
      {job?.log ? (
        <section className="panel" style={{ marginBottom: "1rem" }}>
          <h2>Status log</h2>
          <pre
            style={{
              maxHeight: 220,
              overflowY: "auto",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              margin: 0,
            }}
          >
            {job.log}
          </pre>
        </section>
      ) : null}

      {/* Saved maps gallery */}
      {savedMaps.length > 0 ? (
        <section className="panel">
          <h2>Saved maps ({savedMaps.length})</h2>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
              gap: "0.75rem",
            }}
          >
            {savedMaps.map((name) => (
              <a
                key={name}
                href={savedMapImageUrl(name)}
                target="_blank"
                rel="noreferrer"
                title={`Open ${name} full size`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={savedMapImageUrl(name)}
                  alt={name}
                  style={{
                    width: "100%",
                    height: "auto",
                    display: "block",
                    borderRadius: 6,
                  }}
                />
                <span className="meta">{name}</span>
              </a>
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
