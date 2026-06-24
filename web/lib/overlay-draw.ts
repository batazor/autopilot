import type { OverlayShape } from "@/lib/types";

/**
 * Draw fish/drive overlay shapes onto a 2D canvas context. Coordinates in the
 * shapes are game-space (e.g. 720×1280); ``sx``/``sy`` scale them to the
 * canvas's displayed pixel size. Shared by the still-frame {@link ApprovalCanvas}
 * and the live {@link ScreenStream} so the box/arrow/crosshair styling stays in
 * one place.
 */
export function drawOverlays(
  ctx: CanvasRenderingContext2D,
  overlays: OverlayShape[],
  sx: number,
  sy: number,
): void {
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
      ctx.strokeStyle = o.stroke || "#00dcff";
      ctx.lineWidth = 3;
      drawArrow(ctx, x1, y1, x2, y2);
    }
  }
}

export function drawArrow(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): void {
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
