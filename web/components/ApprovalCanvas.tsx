"use client";

import { useEffect, useRef, useState } from "react";
import type { OverlayShape } from "@/lib/types";

type Props = {
  imageUrl: string | null;
  /** Game-space width (720); overlays use this coordinate system. */
  width: number;
  /** Game-space height (1280); overlays use this coordinate system. */
  height: number;
  overlays: OverlayShape[];
};

export function ApprovalCanvas({ imageUrl, width, height, overlays }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [aspectRatio, setAspectRatio] = useState<string | undefined>();
  // We hold the decoded HTMLImageElement across redraws so resize-triggered
  // redraws don't re-fetch the bitmap. Only `imageUrl` swaps invalidate it.
  const imgRef = useRef<HTMLImageElement | null>(null);

  // 1) Image loading is decoupled from layout so a window resize doesn't
  //    re-decode the screenshot, and an in-flight load that's been superseded
  //    can't race with a newer URL.
  useEffect(() => {
    if (!imageUrl) {
      imgRef.current = null;
      return;
    }
    let cancelled = false;
    const img = new Image();
    img.src = imageUrl;
    const onReady = () => {
      if (cancelled) return;
      imgRef.current = img;
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      if (nw > 0 && nh > 0) {
        const next = `${nw} / ${nh}`;
        // Avoid a no-op setState — most ticks reuse the same 720x1280 frame.
        setAspectRatio((prev) => (prev === next ? prev : next));
      }
      // Trigger a draw via the layout effect by bumping the canvas attr;
      // easier: just call draw() inline once we have a context handle.
      draw();
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
    // `draw` is stable across renders via the layout effect below; including
    // overlays/width/height here would re-fetch the image on every overlay
    // update, which is the perf bug we're fixing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageUrl]);

  // 2) Layout/redraw is its own effect so changing overlays redraws onto the
  //    already-decoded image, and a container resize uses ResizeObserver
  //    (cheaper + scoped) instead of a global window listener.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    draw();
    const ro = new ResizeObserver(() => draw());
    ro.observe(container);
    return () => ro.disconnect();
    // `draw` is defined below and reads the latest refs/props; we deliberately
    // depend only on the inputs that affect rendering to avoid spurious work.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlays, width, height, imageUrl]);

  function draw() {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    const img = imgRef.current;
    if (!canvas || !container || !img) return;
    const nw = img.naturalWidth;
    const nh = img.naturalHeight;
    if (nw <= 0 || nh <= 0) return;

    const gameW = width > 0 ? width : nw;
    const gameH = height > 0 ? height : nh;

    const maxW = container.clientWidth || nw;
    const scale = Math.min(1, maxW / nw);
    const dispW = Math.max(1, Math.round(nw * scale));
    const dispH = Math.max(1, Math.round(nh * scale));

    // Avoid clearing+redrawing if the canvas backing-store is already the
    // right size and nothing meaningful changed. The cheap proof: assigning
    // canvas.width to its current value still clears the bitmap, so we only
    // resize when the dims actually changed.
    if (canvas.width !== dispW) canvas.width = dispW;
    if (canvas.height !== dispH) canvas.height = dispH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, dispW, dispH);
    ctx.drawImage(img, 0, 0, dispW, dispH);

    const sx = dispW / gameW;
    const sy = dispH / gameH;

    for (const o of overlays) {
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
  }

  if (!imageUrl) {
    return (
      <div className="preview-empty">
        No screenshot yet — start the bot or wait for a rolling preview.
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
    </div>
  );
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
