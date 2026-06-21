"use client";

import { useState } from "react";
import { toPng, toSvg } from "html-to-image";
import {
  getNodesBounds,
  getViewportForBounds,
  useReactFlow,
} from "@xyflow/react";

/** PNG/SVG export of the whole graph (all nodes, not just the viewport — the
 *  snapshot is re-transformed to fit the full node bounds). PNG is raster; SVG
 *  is vector, lossless for large trees / printing. Shared by the tech-tree and
 *  DSL-editor canvases so both export identically. Must render inside
 *  <ReactFlow> — it uses the flow context. */
export function GraphExport({ name }: { name: string }) {
  const { getNodes } = useReactFlow();
  const [busy, setBusy] = useState(false);

  const exportAs = async (format: "png" | "svg") => {
    if (busy) return;
    setBusy(true);
    try {
      const viewport =
        document.querySelector<HTMLElement>(".react-flow__viewport");
      if (!viewport) return;
      const bounds = getNodesBounds(getNodes());
      const pad = 48;
      const width = Math.min(8000, Math.ceil(bounds.width) + pad * 2);
      const height = Math.min(8000, Math.ceil(bounds.height) + pad * 2);
      const vp = getViewportForBounds(bounds, width, height, 0.2, 2, pad);
      const bg =
        getComputedStyle(document.documentElement)
          .getPropertyValue("--wos-bg")
          .trim() || "#1c2433";
      const options = {
        backgroundColor: bg,
        width,
        height,
        style: {
          width: `${width}px`,
          height: `${height}px`,
          transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`,
        },
      };
      const render = format === "svg" ? toSvg : toPng;
      const dataUrl = await render(viewport, options);
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = `${name}.${format}`;
      a.click();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => void exportAs("png")}
      >
        {busy ? "Exporting…" : "Export PNG"}
      </button>
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => void exportAs("svg")}
      >
        SVG
      </button>
    </>
  );
}
