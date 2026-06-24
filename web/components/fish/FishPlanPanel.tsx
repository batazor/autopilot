"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { ScreenStream } from "@/components/ScreenStream";
import { FishPlayControl } from "@/components/fish/FishPlayControl";
import { AppCheckbox } from "@/components/headless";
import {
  fetchFishPlan,
  fishDetectImageUrl,
  overlayTestImageUrl,
} from "@/lib/api";
import { useStableCacheKey } from "@/lib/hooks";
import type { FishPlanResult, OverlayShape } from "@/lib/types";

const POLL_MS = 900;
const DODGE_COLOR = "#ef4444"; // red — flee the fish
const COLLECT_COLOR = "#22c55e"; // green — chase the fish
const FISH_COLOR = "#facc15"; // amber — detections
const TARGET_COLOR = "#ffffff"; // white — the fish being acted on

function planToOverlays(r: FishPlanResult | null): OverlayShape[] {
  if (!r) return [];
  const shapes: OverlayShape[] = [];
  r.detections.forEach((d, i) => {
    shapes.push({
      type: "rect",
      x: d.x,
      y: d.y,
      w: d.width,
      h: d.height,
      stroke: i === r.target_index ? TARGET_COLOR : FISH_COLOR,
      label: i === r.target_index ? "target" : undefined,
    });
  });
  if (r.hook_x != null && r.hook_y != null) {
    shapes.push({ type: "crosshair", x: r.hook_x, y: r.hook_y });
  }
  if (r.swipe) {
    shapes.push({
      type: "arrow",
      x1: r.swipe.from_x,
      y1: r.swipe.from_y,
      x2: r.swipe.to_x,
      y2: r.swipe.to_y,
      stroke: r.phase === "collect" ? COLLECT_COLOR : DODGE_COLOR,
      label: `${r.phase} ${r.swipe.direction}`,
    });
  }
  return shapes;
}

/**
 * The single live view for fish detection: one device frame with the detector's
 * boxes + the drive decision (target / hook / swipe) drawn over it, beside the
 * raw detections list and the decision read-out. The plan endpoint returns the
 * detections *and* the decision, so there's no separate "Frame / Detections"
 * panel — this is both. Read-only: it never taps the device.
 */
export function FishPlanPanel({
  instanceId,
  inferenceReady,
  onResult,
}: {
  instanceId: string;
  inferenceReady: boolean;
  onResult?: (result: FishPlanResult | null) => void;
}) {
  const [threshold, setThreshold] = useState(0.4);
  // Auto-refresh re-reads the frame + decision on a timer so the still preview
  // keeps up with the device on its own. Independent of the scrcpy video toggle
  // below — `live` only swaps the *display source*, not whether we poll.
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [live, setLive] = useState(false);
  const [result, setResult] = useState<FishPlanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const planQuery = useQuery({
    queryKey: ["fishPlan", instanceId, threshold],
    queryFn: () => fetchFishPlan(instanceId, { threshold }),
    enabled: !!instanceId && inferenceReady,
    // Poll while auto-refresh (or the video stream) is on — react-query pauses
    // the timer automatically when the tab is backgrounded.
    refetchInterval: inferenceReady && (autoRefresh || live) ? POLL_MS : false,
  });

  useEffect(() => {
    if (planQuery.data) setResult(planQuery.data);
  }, [planQuery.data]);
  useEffect(() => {
    if (planQuery.isError) {
      setError(
        planQuery.error instanceof Error
          ? planQuery.error.message
          : String(planQuery.error),
      );
    } else if (planQuery.isSuccess) {
      setError(null);
    }
  }, [planQuery.isError, planQuery.isSuccess, planQuery.error]);

  // The InferenceControl widget owns the "not running" state — drop any stale
  // frame/decision when the sidecar isn't Ready so we don't show counts from a
  // previous run.
  useEffect(() => {
    if (!inferenceReady) {
      setResult(null);
      setError(null);
    }
  }, [inferenceReady]);

  // Bubble the result up for the page header pill (fish count + model).
  useEffect(() => {
    onResult?.(result);
  }, [result, onResult]);

  const resetMutation = useMutation({
    mutationFn: () => {
      if (!instanceId) throw new Error("no instance selected");
      return fetchFishPlan(instanceId, { threshold, reset: true });
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const overlays = useMemo(() => planToOverlays(result), [result]);
  const cacheKey = useStableCacheKey(
    result?.preview_available ? (result.preview_mtime ?? "pending") : null,
  );
  const imageUrl =
    result?.preview_available && instanceId
      ? overlayTestImageUrl(instanceId, cacheKey, { previewSource: "live" })
      : null;
  const annotatedUrl =
    result?.preview_available && instanceId
      ? fishDetectImageUrl(instanceId, cacheKey, { threshold })
      : null;

  const phase = result?.phase ?? "dodge";
  const phaseColor = phase === "collect" ? COLLECT_COLOR : DODGE_COLOR;
  const levelLabel =
    result?.level != null
      ? `${result.level}/${result.level_total ?? "?"}`
      : "—";

  return (
    <section className="panel">
      <h2>Drive logic (live decision)</h2>
      <p className="fish-plan__intro">
        The live frame with the detector's boxes and what the bot <em>would</em>{" "}
        do on it — read-only, never taps the device. Steers the hook (crosshair){" "}
        <strong>away</strong> from the nearest fish while dodging, then{" "}
        <strong>toward</strong> it once the altitude counter climbs (набор
        высоты). Reset clears the altitude baseline at the start of a round.
      </p>

      {/* Fishing run control — start/stop an isolated worker for this device
          and play the Fishing Tournament, without spinning up the whole fleet. */}
      <div className="fish-play-card">
        <FishPlayControl instanceId={instanceId} />
      </div>

      {inferenceReady && error ? (
        <div className="error-banner">{error}</div>
      ) : null}
      {inferenceReady && result && !result.available && result.error ? (
        <div className="error-banner">Inference unavailable: {result.error}</div>
      ) : null}

      <div className="toolbar" style={{ flexWrap: "wrap", alignItems: "center" }}>
        <span
          className="status-pill"
          style={{ background: phaseColor, color: "#0b1220" }}
          title="Phase: hook position (top=down / bottom=up) > shield > altitude counter"
        >
          {phase === "collect" ? "COLLECT" : "DODGE"}
        </span>
        {result?.hook_direction ? (
          <span
            className="status-pill status-idle"
            title="Travel direction from the hook's vertical position"
          >
            {result.hook_direction === "down" ? "↓ descending" : "↑ ascending"}
          </span>
        ) : null}
        <span className="status-pill status-idle" title="Altitude counter (fishing_tournament.level)">
          height {levelLabel}
        </span>
        {result?.protected != null ? (
          <span
            className="status-pill status-idle"
            title="Blue protection ring detected around the hook"
          >
            shield {result.protected ? "up" : "down"}
          </span>
        ) : null}
        {result?.swipe ? (
          <span className="fish-meta">
            swipe {result.swipe.direction} · {result.swipe.reason}
          </span>
        ) : (
          <span className="fish-meta">no swipe (aligned / holding)</span>
        )}
        <label style={{ marginLeft: "auto" }}>
          Confidence threshold: <strong>{threshold.toFixed(2)}</strong>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            style={{ display: "block", width: 200 }}
          />
        </label>
        <AppCheckbox
          inline
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
        />
        <AppCheckbox
          inline
          checked={live}
          onChange={setLive}
          label="Live video (scrcpy)"
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={() => resetMutation.mutate()}
          disabled={resetMutation.isPending || !instanceId || !inferenceReady}
          title="Clear the altitude history (start of a new round)"
        >
          {resetMutation.isPending ? "Resetting…" : "Reset round"}
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={() => planQuery.refetch()}
          disabled={planQuery.isFetching || !instanceId || !inferenceReady}
          title={
            inferenceReady
              ? "Read the fish detector + drive decision on the current frame"
              : "Start the inference service above first"
          }
        >
          {planQuery.isFetching ? "Reading…" : "Read frame"}
        </button>
        {annotatedUrl ? (
          <a
            href={annotatedUrl}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary"
            title="Open the server-rendered annotated PNG in a new tab"
          >
            Annotated PNG
          </a>
        ) : null}
      </div>

      <div className="approvals-grid">
        <div className="panel">
          {live ? (
            <ScreenStream
              instanceId={instanceId}
              width={result?.frame_width ?? 0}
              height={result?.frame_height ?? 0}
              overlays={overlays}
            />
          ) : (
            <ApprovalCanvas
              imageUrl={imageUrl}
              width={result?.frame_width ?? 0}
              height={result?.frame_height ?? 0}
              overlays={overlays}
            />
          )}
          <p className="meta">
            <span style={{ color: FISH_COLOR }}>■ fish</span>{" "}
            <span style={{ color: TARGET_COLOR }}>■ target</span>{" "}
            <span style={{ color: "#ff0000" }}>+ hook</span>{" "}
            <span style={{ color: DODGE_COLOR }}>→ dodge</span>{" "}
            <span style={{ color: COLLECT_COLOR }}>→ collect</span>
          </p>
        </div>
        <div style={{ display: "grid", gap: "1rem" }}>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Decision</h3>
            <table className="data-table">
              <tbody>
                <tr>
                  <td>Phase</td>
                  <td>
                    <code>{phase}</code>
                  </td>
                </tr>
                <tr>
                  <td>Altitude</td>
                  <td>
                    <code>{levelLabel}</code>{" "}
                    {result?.level_text ? (
                      <span className="fish-meta">(ocr: “{result.level_text}”)</span>
                    ) : null}
                  </td>
                </tr>
                <tr>
                  <td>Hook</td>
                  <td>
                    <code>
                      {result?.hook_x ?? "—"}, {result?.hook_y ?? "—"}
                    </code>
                  </td>
                </tr>
                <tr>
                  <td>Swipe</td>
                  <td>
                    {result?.swipe ? (
                      <code>
                        {result.swipe.direction} {Math.abs(result.swipe.dx)}px
                      </code>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>
              Detections ({result?.detections.length ?? 0})
            </h3>
            {!inferenceReady ? (
              <p className="meta">
                Start the <strong>inference service</strong> above — live
                detection unlocks once it reads <strong>Ready</strong>.
              </p>
            ) : !result?.detections.length ? (
              <p className="meta">
                No fish detected on this frame — lower the threshold or capture a
                frame on the Fishing Tournament screen, then Read frame.
              </p>
            ) : (
              <div
                className="data-table-wrap"
                style={{ maxHeight: 420, overflowY: "auto" }}
              >
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Class</th>
                      <th>Confidence</th>
                      <th>Center (x, y)</th>
                      <th>Size (w × h)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.detections.map((d, i) => (
                      <tr
                        key={`${d.center_x}:${d.center_y}:${i}`}
                        style={
                          i === result.target_index
                            ? { background: "rgba(255,255,255,0.06)" }
                            : undefined
                        }
                      >
                        <td>{i + 1}</td>
                        <td>
                          <code>{d.class_name || "fish"}</code>
                          {i === result.target_index ? (
                            <span className="fish-meta"> · target</span>
                          ) : null}
                        </td>
                        <td>{(d.confidence * 100).toFixed(1)}%</td>
                        <td>
                          <code>
                            {d.center_x}, {d.center_y}
                          </code>
                        </td>
                        <td>
                          {d.width} × {d.height}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
