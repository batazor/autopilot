"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { H264StreamClient, isWebCodecsSupported } from "@/lib/h264VideoStream";
import type { OverlayShape } from "@/lib/types";

type Props = {
  /** Still-image source (rolling preview PNG). Mutually exclusive with `streamUrl`. */
  imageUrl?: string | null;
  /** Live H.264 WebSocket URL (WebCodecs). Mutually exclusive with `imageUrl`. */
  streamUrl?: string | null;
  /** Game-space width (720); overlays use this coordinate system. */
  width: number;
  /** Game-space height (1280); overlays use this coordinate system. */
  height: number;
  overlays: OverlayShape[];
  /** Called when the stream closes (e.g. scrcpy stopped). Lets the page
   *  fall back to the still-image source. */
  onStreamClosed?: (reason: string) => void;
  /** Worker is alive (fresh heartbeat) but hasn't produced a preview yet.
   *  Controls the empty-state copy: warming-up vs. start-the-bot. */
  workerActive?: boolean;
};

type BitmapSource = HTMLImageElement | VideoFrame;

export function ApprovalCanvas({
  imageUrl,
  streamUrl,
  width,
  height,
  overlays,
  onStreamClosed,
  workerActive,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [aspectRatio, setAspectRatio] = useState<string | undefined>();
  const [streamFps, setStreamFps] = useState<number | null>(null);
  // Holds the current frame (image OR VideoFrame). For VideoFrame we must
  // close the previous one before replacing or GPU memory leaks accumulate
  // at 30 FPS very quickly.
  const sourceRef = useRef<BitmapSource | null>(null);
  const rafRef = useRef<number | null>(null);
  const overlaysRef = useRef(overlays);
  const sizeRef = useRef({ width, height });
  const fpsRef = useRef({ frames: 0, windowStartMs: 0 });

  overlaysRef.current = overlays;
  sizeRef.current = { width, height };

  const draw = useCallback(() => {
    rafRef.current = null;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    const source = sourceRef.current;
    if (!canvas || !container || !source) return;

    const { sw, sh } = sourceDimensions(source);
    if (sw <= 0 || sh <= 0) return;

    const { width: logicalW, height: logicalH } = sizeRef.current;
    const gameW = logicalW > 0 ? logicalW : sw;
    const gameH = logicalH > 0 ? logicalH : sh;

    const maxW = container.clientWidth || sw;
    const scale = Math.min(1, maxW / sw);
    const dispW = Math.max(1, Math.round(sw * scale));
    const dispH = Math.max(1, Math.round(sh * scale));

    if (canvas.width !== dispW) canvas.width = dispW;
    if (canvas.height !== dispH) canvas.height = dispH;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;
    ctx.clearRect(0, 0, dispW, dispH);
    ctx.drawImage(source as CanvasImageSource, 0, 0, dispW, dispH);

    const sx = dispW / gameW;
    const sy = dispH / gameH;

    for (const o of overlaysRef.current) {
      if (o.type === "rect") {
        const stroke = o.stroke || "#00dcff";
        const x = o.x * sx;
        const y = o.y * sy;
        const w = o.w * sx;
        const h = o.h * sy;
        ctx.strokeStyle = "#000";
        ctx.lineWidth = 3;
        ctx.strokeRect(x, y, w, h);
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y, w, h);
        if (o.label) {
          ctx.font = "12px system-ui, sans-serif";
          const pad = 5;
          const tw = ctx.measureText(o.label).width;
          const labH = 18;
          const by0 = Math.max(0, y - labH - 4);
          ctx.fillStyle = "rgba(0,0,0,0.85)";
          ctx.fillRect(x, by0, tw + pad * 2, labH);
          ctx.strokeStyle = stroke;
          ctx.strokeRect(x, by0, tw + pad * 2, labH);
          ctx.fillStyle = "#fff";
          ctx.fillText(o.label, x + pad, by0 + 13);
        }
      } else if (o.type === "crosshair") {
        const px = o.x * sx;
        const py = o.y * sy;
        ctx.strokeStyle = "#ff0000";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(px, py, 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "#ff0000";
        ctx.beginPath();
        ctx.arc(px, py, 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(px - 18, py);
        ctx.lineTo(px + 18, py);
        ctx.moveTo(px, py - 18);
        ctx.lineTo(px, py + 18);
        ctx.stroke();
      } else if (o.type === "arrow") {
        const x1 = o.x1 * sx;
        const y1 = o.y1 * sy;
        const x2 = o.x2 * sx;
        const y2 = o.y2 * sy;
        ctx.strokeStyle = "#000";
        ctx.lineWidth = 6;
        drawArrow(ctx, x1, y1, x2, y2);
        ctx.strokeStyle = "#00dcff";
        ctx.lineWidth = 3;
        drawArrow(ctx, x1, y1, x2, y2);
      }
    }
  }, []);

  const scheduleDraw = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = window.requestAnimationFrame(draw);
  }, [draw]);

  // ---- Source: still image ----
  //
  // Decoupled from layout so resizes don't re-fetch the bitmap and an
  // in-flight load that's been superseded can't race with a newer URL.
  useEffect(() => {
    if (streamUrl) return; // stream mode owns the source
    if (!imageUrl) {
      releaseSource(sourceRef.current);
      sourceRef.current = null;
      return;
    }
    let cancelled = false;
    const img = new Image();
    img.src = imageUrl;
    const onReady = () => {
      if (cancelled) return;
      releaseSource(sourceRef.current);
      sourceRef.current = img;
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      if (nw > 0 && nh > 0) {
        const next = `${nw} / ${nh}`;
        setAspectRatio((prev) => (prev === next ? prev : next));
      }
      scheduleDraw();
    };
    if (img.complete && img.naturalWidth > 0) {
      onReady();
    } else {
      img.onload = onReady;
    }
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [imageUrl, streamUrl, scheduleDraw]);

  // ---- Source: live stream ----
  useEffect(() => {
    if (!streamUrl) return;
    if (!isWebCodecsSupported()) {
      onStreamClosed?.("WebCodecs not supported — falling back to image");
      return;
    }
    const client = new H264StreamClient(streamUrl, {
      onHandshake: ({ width: w, height: h }) => {
        if (w > 0 && h > 0) {
          const next = `${w} / ${h}`;
          setAspectRatio((prev) => (prev === next ? prev : next));
        }
      },
      onFrame: (frame) => {
        // Replace the current frame and schedule one canvas repaint. This
        // intentionally avoids React state per video frame, so 30+ FPS does
        // not become 30+ React renders/ResizeObserver rebuilds per second.
        releaseSource(sourceRef.current);
        sourceRef.current = frame;
        recordFrameForFps(fpsRef, setStreamFps);
        scheduleDraw();
      },
      onError: (e) => {
        // Decoder errors typically mean the stream is unrecoverable for
        // this connection — surface as "closed" so the page can fall back.
        onStreamClosed?.(e.message);
      },
      onClose: (reason) => onStreamClosed?.(reason),
    });
    client.start();
    return () => {
      client.stop();
      releaseSource(sourceRef.current);
      sourceRef.current = null;
      fpsRef.current = { frames: 0, windowStartMs: 0 };
      setStreamFps(null);
    };
  }, [streamUrl, onStreamClosed, scheduleDraw]);

  // ---- Layout / redraw ----
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

  useEffect(() => {
    return () => {
      if (rafRef.current != null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      releaseSource(sourceRef.current);
      sourceRef.current = null;
    };
  }, []);

  const hasSource = streamUrl || imageUrl;
  if (!hasSource) {
    return (
      <div className="preview-empty">
        {workerActive ? (
          <span className="preview-empty__warming">
            <span className="preview-empty__spinner" aria-hidden />
            Bot running — warming up capture…
          </span>
        ) : (
          "No preview yet — start the bot to see a rolling screenshot."
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="preview-wrap"
      style={aspectRatio ? { aspectRatio } : undefined}
    >
      <canvas ref={canvasRef} className="preview-canvas" />
      {streamUrl && streamFps != null ? (
        <div className="preview-fps" aria-label="Live video frame rate">
          {streamFps.toFixed(1)} FPS
        </div>
      ) : null}
    </div>
  );
}

function recordFrameForFps(
  fpsRef: MutableRefObject<{ frames: number; windowStartMs: number }>,
  setStreamFps: Dispatch<SetStateAction<number | null>>,
): void {
  const now = performance.now();
  const stats = fpsRef.current;
  if (stats.windowStartMs <= 0) {
    stats.windowStartMs = now;
    stats.frames = 0;
  }
  stats.frames += 1;
  const elapsedMs = now - stats.windowStartMs;
  if (elapsedMs < 1000) return;
  const fps = (stats.frames * 1000) / elapsedMs;
  fpsRef.current = { frames: 0, windowStartMs: now };
  setStreamFps(Math.round(fps * 10) / 10);
}

function releaseSource(source: BitmapSource | null): void {
  if (source && "close" in source && typeof source.close === "function") {
    // VideoFrame must be closed to free GPU memory; HTMLImageElement has no
    // close() so the typeof guard skips it.
    try {
      source.close();
    } catch {
      // ignore
    }
  }
}

function sourceDimensions(source: BitmapSource): { sw: number; sh: number } {
  if ("naturalWidth" in source) {
    return { sw: source.naturalWidth, sh: source.naturalHeight };
  }
  // VideoFrame uses displayWidth/displayHeight which already account for
  // sample aspect ratio; codedWidth/codedHeight may include padding.
  return { sw: source.displayWidth, sh: source.displayHeight };
}

function drawArrow(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
) {
  const head = 10;
  const angle = Math.atan2(y2 - y1, x2 - x1);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(
    x2 - head * Math.cos(angle - Math.PI / 6),
    y2 - head * Math.sin(angle - Math.PI / 6),
  );
  ctx.lineTo(
    x2 - head * Math.cos(angle + Math.PI / 6),
    y2 - head * Math.sin(angle + Math.PI / 6),
  );
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}
