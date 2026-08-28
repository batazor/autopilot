"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { screenStreamUrl } from "@/lib/api";
import { drawOverlays } from "@/lib/overlay-draw";
import type { OverlayShape } from "@/lib/types";

// Default game-space dimensions (emulator portrait) used for overlay scaling and
// the container aspect ratio until the caller passes real frame dimensions.
const DEFAULT_W = 720;
const DEFAULT_H = 1280;

/**
 * Real-time device screen via MJPEG (scrcpy → /screen/stream). The browser
 * natively animates a ``multipart/x-mixed-replace`` ``<img>``; a transparent
 * canvas on top draws the detection/drive overlays (reusing {@link drawOverlays}).
 *
 * Mounting opens the HTTP stream (ref-counts a scrcpy client server-side);
 * unmounting closes it. Overlays are optional, so this is reusable for any
 * live-screen view, not just fish-detect.
 *
 * Pass ``onTap`` to make the view interactive: clicks report where they landed
 * as fractions of the frame (0..1), which is the only coordinate space the
 * browser can speak — it knows the displayed size, not the device's. A ping
 * marks the spot so a helper on a laggy phone can see the click registered
 * before the frame catches up.
 */
export function ScreenStream({
  instanceId,
  streamUrl,
  width = 0,
  height = 0,
  overlays = [],
  onTap,
}: {
  instanceId?: string;
  /** Override the stream source (e.g. the token-addressed remote route). */
  streamUrl?: string;
  width?: number;
  height?: number;
  overlays?: OverlayShape[];
  onTap?: (x: number, y: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const overlaysRef = useRef(overlays);
  const sizeRef = useRef({ width, height });
  overlaysRef.current = overlays;
  sizeRef.current = { width, height };

  // Bump to force the <img> to reconnect after an error (new query string).
  const [streamKey, setStreamKey] = useState(0);
  const [ping, setPing] = useState<{ x: number; y: number; id: number } | null>(
    null,
  );
  const pingId = useRef(0);
  const [status, setStatus] = useState<"connecting" | "live" | "error">(
    "connecting",
  );

  const gameW = width > 0 ? width : DEFAULT_W;
  const gameH = height > 0 ? height : DEFAULT_H;

  const draw = useCallback(() => {
    rafRef.current = null;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const { width: w, height: h } = sizeRef.current;
    const gw = w > 0 ? w : DEFAULT_W;
    const gh = h > 0 ? h : DEFAULT_H;
    const dispW = Math.max(1, container.clientWidth);
    const dispH = Math.max(1, Math.round((dispW * gh) / gw));
    if (canvas.width !== dispW) canvas.width = dispW;
    if (canvas.height !== dispH) canvas.height = dispH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, dispW, dispH);
    drawOverlays(ctx, overlaysRef.current, dispW / gw, dispH / gh);
  }, []);

  const scheduleDraw = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = window.requestAnimationFrame(draw);
  }, [draw]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    scheduleDraw();
    const ro = new ResizeObserver(() => scheduleDraw());
    ro.observe(container);
    return () => ro.disconnect();
  }, [scheduleDraw]);

  useEffect(() => {
    scheduleDraw();
  }, [overlays, width, height, scheduleDraw]);

  useEffect(
    () => () => {
      if (rafRef.current != null) window.cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  // Auto-retry a dropped stream (e.g. scrcpy reconnecting) by remounting <img>.
  useEffect(() => {
    if (status !== "error") return;
    const t = window.setTimeout(() => setStreamKey((k) => k + 1), 1500);
    return () => window.clearTimeout(t);
  }, [status]);

  const src = streamUrl
    ? `${streamUrl}${streamUrl.includes("?") ? "&" : "?"}t=${streamKey}`
    : screenStreamUrl(instanceId ?? "", streamKey);

  const handleClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (!onTap) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    // The container's aspect ratio matches the frame's, so the contained image
    // fills it exactly — container fractions are frame fractions.
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    setPing({ x, y, id: pingId.current++ });
    onTap(x, y);
  };

  return (
    <div
      ref={containerRef}
      className="preview-wrap"
      onClick={handleClick}
      style={{
        position: "relative",
        aspectRatio: `${gameW} / ${gameH}`,
        cursor: onTap ? "crosshair" : undefined,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        key={streamKey}
        src={src}
        alt="Live device screen"
        onLoad={() => setStatus("live")}
        onError={() => setStatus("error")}
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          objectFit: "contain",
        }}
      />
      <canvas
        ref={canvasRef}
        className="preview-canvas"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          pointerEvents: "none",
          // .preview-canvas paints bg-black (it's the image surface in
          // ApprovalCanvas). Here it's a transparent overlay *on top of* the
          // live <img>, so the black backdrop must be cleared or it hides the
          // video — the canvas only draws the detection/drive shapes.
          background: "transparent",
        }}
      />
      {ping ? (
        <span
          key={ping.id}
          className="screen-tap-ping"
          style={{ left: `${ping.x * 100}%`, top: `${ping.y * 100}%` }}
          onAnimationEnd={() =>
            setPing((cur) => (cur && cur.id === ping.id ? null : cur))
          }
        />
      ) : null}
      {status !== "live" ? (
        <span
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            padding: "2px 8px",
            borderRadius: 6,
            fontSize: "0.75rem",
            background: "rgba(0,0,0,0.7)",
            color: status === "error" ? "#f59e0b" : "#cbd5e1",
          }}
        >
          {status === "error" ? "reconnecting…" : "connecting…"}
        </span>
      ) : null}
    </div>
  );
}
