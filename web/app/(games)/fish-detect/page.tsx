"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { FishVideoPanel } from "@/components/fish/FishVideoPanel";
import {
  FleetContextProvider,
  useFleet,
} from "@/components/FleetContextProvider";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { AppCheckbox } from "@/components/headless";
import { PageLoading } from "@/components/ui/Spinner";
import {
  fetchFishDetections,
  fishDetectImageUrl,
  overlayTestImageUrl,
} from "@/lib/api";
import { useStableCacheKey } from "@/lib/hooks";
import type { FishDetectResult, OverlayShape } from "@/lib/types";

const POLL_MS = 1500;
const BOX_STROKE = "#facc15"; // amber — readable on the icy palette

function detectionsToOverlays(result: FishDetectResult | null): OverlayShape[] {
  if (!result) return [];
  const shapes: OverlayShape[] = [];
  for (const d of result.detections) {
    const label = `${d.class_name || "fish"} ${(d.confidence * 100).toFixed(0)}%`;
    shapes.push({
      type: "rect",
      x: d.x,
      y: d.y,
      w: d.width,
      h: d.height,
      label,
      stroke: BOX_STROKE,
    });
    shapes.push({ type: "crosshair", x: d.center_x, y: d.center_y });
  }
  return shapes;
}

function FishDetectPageContent() {
  const { instanceId, instancesError } = useFleet();
  const [threshold, setThreshold] = useState(0.4);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [result, setResult] = useState<FishDetectResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const detectQuery = useQuery({
    queryKey: ["fishDetect", instanceId, threshold],
    queryFn: () => fetchFishDetections(instanceId, { threshold }),
    enabled: !!instanceId,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  // Mirror overlay-test: keep the last good result and surface query errors.
  useEffect(() => {
    if (detectQuery.data) setResult(detectQuery.data);
  }, [detectQuery.data]);
  useEffect(() => {
    if (detectQuery.isError) {
      setError(
        detectQuery.error instanceof Error
          ? detectQuery.error.message
          : String(detectQuery.error),
      );
    } else if (detectQuery.isSuccess) {
      setError(null);
    }
  }, [detectQuery.isError, detectQuery.isSuccess, detectQuery.error]);

  const detectMutation = useMutation({
    mutationFn: () => {
      if (!instanceId) throw new Error("no instance selected");
      return fetchFishDetections(instanceId, { threshold });
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const overlays = useMemo(() => detectionsToOverlays(result), [result]);

  const cacheKey = useStableCacheKey(
    result?.preview_available ? (result.preview_mtime ?? "pending") : null,
  );

  // Backdrop is the raw live frame; detection boxes are drawn as canvas
  // overlays from the JSON so each is hoverable / individually inspectable.
  const imageUrl =
    result?.preview_available && instanceId
      ? overlayTestImageUrl(instanceId, cacheKey, { previewSource: "live" })
      : null;

  const annotatedUrl =
    result?.preview_available && instanceId
      ? fishDetectImageUrl(instanceId, cacheKey, { threshold })
      : null;

  const detecting = detectMutation.isPending;

  return (
    <>
      <FleetPageHeader title="Fish detect">
        {result ? (
          <span
            className={`status-pill ${result.available ? "status-idle" : "pill-stale"}`}
            title="Detections found on this frame"
          >
            {result.available
              ? `${result.detections.length} fish · model ${result.model_id}`
              : "inference unavailable"}
          </span>
        ) : null}
      </FleetPageHeader>

      {error || instancesError ? (
        <div className="error-banner">{error ?? instancesError}</div>
      ) : null}

      {result && !result.available && result.error ? (
        <div className="error-banner">
          Inference unavailable: {result.error}
        </div>
      ) : null}

      <div
        className="toolbar"
        style={{ flexWrap: "wrap", alignItems: "flex-end", marginBottom: "1rem" }}
      >
        <label>
          Confidence threshold: <strong>{threshold.toFixed(2)}</strong>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            style={{ display: "block", width: 220 }}
          />
        </label>
        <AppCheckbox
          inline
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
        />
        <button
          type="button"
          className="btn-primary"
          onClick={() => detectMutation.mutate()}
          disabled={detecting || !instanceId}
          title="Run the fish detector on the current rolling screenshot"
        >
          {detecting ? "Detecting…" : "Run detection"}
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

      <div className="approvals-grid" style={{ marginBottom: "1rem" }}>
        <section className="panel">
          <h2>Frame</h2>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={result?.frame_width ?? 0}
            height={result?.frame_height ?? 0}
            overlays={overlays}
          />
          <p className="meta">
            <span style={{ color: BOX_STROKE }}>■ detected fish</span>{" "}
            <span style={{ color: "#ff0000" }}>+ tap center</span>
          </p>
        </section>

        <section className="panel">
          <h2>Detections ({result?.detections.length ?? 0})</h2>
          {!result?.detections.length ? (
            <p className="meta">
              No fish detected on this frame — lower the threshold or capture a
              frame on the Fishing Tournament screen, then Run detection.
            </p>
          ) : (
            <div
              className="data-table-wrap"
              style={{ maxHeight: 640, overflowY: "auto" }}
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
                    <tr key={`${d.center_x}:${d.center_y}:${i}`}>
                      <td>{i + 1}</td>
                      <td>
                        <code>{d.class_name || "fish"}</code>
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
        </section>
      </div>

      <FishVideoPanel threshold={threshold} />
    </>
  );
}

export default function FishDetectPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <FishDetectPageContent />
      </FleetContextProvider>
    </Suspense>
  );
}
