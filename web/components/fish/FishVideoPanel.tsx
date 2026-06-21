"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  deleteFishVideoJob,
  fetchFishVideoJob,
  fishVideoFrameImageUrl,
  uploadFishVideo,
} from "@/lib/api";
import type { FishVideoFrame, FishVideoJob } from "@/lib/types";

const MAX_MB = 100;

function fmtTime(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function UploadIcon() {
  return (
    <svg
      className="fish-dropzone__icon"
      aria-hidden="true"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M12 5v9m-5 0H5a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-2M8 9l4-5 4 5m1 8h.01"
      />
    </svg>
  );
}

export function FishVideoPanel({ threshold }: { threshold: number }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const job = useQuery({
    queryKey: ["fishVideoJob", jobId],
    queryFn: () => fetchFishVideoJob(jobId as string),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = (q.state.data as FishVideoJob | undefined)?.state;
      return s === "running" || s === "queued" ? 1000 : false;
    },
  });

  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadFishVideo(file, { threshold, intervalMs: 500 }),
    onSuccess: (res) => {
      setJobId(res.job_id);
      setSelected(null);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const onPick = useCallback(
    (file: File | undefined) => {
      if (file) upload.mutate(file);
    },
    [upload],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      onPick(e.dataTransfer.files?.[0]);
    },
    [onPick],
  );

  const clear = useCallback(() => {
    if (jobId) void deleteFishVideoJob(jobId).catch(() => undefined);
    setJobId(null);
    setSelected(null);
    setError(null);
    if (fileRef.current) fileRef.current.value = "";
  }, [jobId]);

  const data = job.data;
  const frames = data?.frames ?? [];
  const activeIndex = selected ?? (frames.length ? frames.length - 1 : null);
  const activeFrame: FishVideoFrame | undefined =
    activeIndex != null ? frames[activeIndex] : undefined;

  const pct = useMemo(() => {
    if (!data || !data.total) return 0;
    return Math.min(100, Math.round((data.processed / data.total) * 100));
  }, [data]);

  const jobErr = job.isError
    ? job.error instanceof Error
      ? job.error.message
      : String(job.error)
    : null;

  const busy = upload.isPending;

  return (
    <section className="panel panel--mb">
      <h2>Validate on video</h2>
      <p className="fish-video__intro">
        Upload a Fishing Tournament clip. It is sampled every 500&nbsp;ms; each
        frame is run through the detector and a swipe is predicted from fish
        motion.
      </p>

      {error || jobErr ? (
        <div className="error-banner">{error ?? jobErr}</div>
      ) : null}
      {data && !data.available && data.error ? (
        <div className="error-banner">Inference unavailable: {data.error}</div>
      ) : null}

      {/* Upload dropzone (hidden input + drag & drop) */}
      {!jobId ? (
        <div
          className={`fish-dropzone${dragging ? " fish-dropzone--active" : ""}${busy ? " fish-dropzone--busy" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => fileRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") fileRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <UploadIcon />
          <p className="fish-dropzone__title">
            {busy ? "Uploading…" : "Click to upload or drag & drop a clip"}
          </p>
          <p className="fish-dropzone__hint">
            MP4 / MOV / WebM · Max file size:{" "}
            <span className="font-semibold">{MAX_MB}MB</span>
          </p>
          <button
            type="button"
            className="fish-dropzone__browse"
            onClick={(e) => {
              e.stopPropagation();
              fileRef.current?.click();
            }}
            disabled={busy}
          >
            <svg
              className="h-4 w-4"
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <path
                stroke="currentColor"
                strokeLinecap="round"
                strokeWidth="2"
                d="m21 21-3.5-3.5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"
              />
            </svg>
            Browse file
          </button>
        </div>
      ) : null}
      <input
        ref={fileRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={(e) => onPick(e.target.files?.[0])}
      />

      {/* Status row */}
      {data ? (
        <div className="fish-status-row">
          <span
            className={`status-pill ${
              data.state === "done"
                ? "pill-live"
                : data.state === "error"
                  ? "pill-stale"
                  : "status-idle"
            }`}
          >
            {data.state}
          </span>
          {frames.length ? (
            <span className="fish-meta">
              {data.model_id} · {data.fps_in} fps in · {fmtTime(data.duration_ms)}{" "}
              · threshold {data.threshold}
            </span>
          ) : null}
          <button type="button" className="btn-secondary" onClick={clear}>
            Clear
          </button>
        </div>
      ) : null}

      {/* Progress */}
      {data && (data.state === "running" || data.state === "queued") ? (
        <div className="fish-progress">
          <div className="fish-progress__track">
            <div className="fish-progress__fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="fish-meta">
            {data.processed}/{data.total || "?"} frames · {pct}%
          </span>
        </div>
      ) : null}

      {/* Filmstrip */}
      {frames.length ? (
        <>
          <div className="fish-legend">
            <span className="fish-legend__item">
              <span className="fish-legend__dot" style={{ background: "#facc15" }} />
              detection
            </span>
            <span className="fish-legend__item">
              <span className="fish-legend__dot" style={{ background: "#ff5050" }} />
              escape
            </span>
            <span className="fish-legend__item">
              <span className="fish-legend__dot" style={{ background: "#22c55e" }} />
              catch swipe
            </span>
          </div>
          <div className="fish-filmstrip">
            {frames.map((f) => (
              <button
                key={f.index}
                type="button"
                className={`fish-thumb${f.index === activeIndex ? " fish-thumb--active" : ""}`}
                onClick={() => setSelected(f.index)}
                title={`t=${fmtTime(f.t_ms)} · ${f.detections.length} fish`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  className="fish-thumb__img"
                  src={fishVideoFrameImageUrl(data!.job_id, f.index)}
                  alt={`frame ${f.index}`}
                />
                <span className="fish-thumb__cap">
                  {fmtTime(f.t_ms)} · {f.detections.length}🐟
                </span>
              </button>
            ))}
          </div>
        </>
      ) : null}

      {/* Selected frame + swipe card */}
      {activeFrame && data ? (
        <div className="approvals-grid" style={{ marginTop: "0.75rem" }}>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>
              Frame {activeFrame.index} · t={fmtTime(activeFrame.t_ms)}
            </h3>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              className="fish-frame-img"
              src={fishVideoFrameImageUrl(data.job_id, activeFrame.index)}
              alt={`frame ${activeFrame.index}`}
            />
          </div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>
              Predicted swipes ({activeFrame.swipes.length})
            </h3>
            {activeFrame.swipes.length === 0 ? (
              <p className="fish-swipe-empty">
                No motion-based prediction on this frame (need a fish tracked from
                the previous sample, moving more than a few pixels).
              </p>
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Fish</th>
                      <th>Escape</th>
                      <th>Catch swipe</th>
                      <th>Speed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeFrame.swipes.map((s) => (
                      <tr key={s.fish_index}>
                        <td>#{s.fish_index + 1}</td>
                        <td>
                          <code>{s.escape_compass}</code> ({s.escape_deg}°)
                        </td>
                        <td>
                          <code>{s.catch_compass}</code>
                        </td>
                        <td>{s.speed_px_s} px/s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
