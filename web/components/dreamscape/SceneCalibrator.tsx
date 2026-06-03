"use client";

import Konva from "konva";
import { useEffect, useRef, useState } from "react";
import {
  Circle,
  Image as KonvaImage,
  Layer,
  Rect,
  Stage,
  Text,
  Transformer,
} from "react-konva";
import type { EditorRegion } from "@/lib/bbox";
import { pctToPixels, pixelsToPct } from "@/lib/bbox";

/** A scene item marker: number + position as a percentage of the guide image. */
export type CalibratorPoint = {
  n: number;
  xPct: number;
  yPct: number;
  /** Item name — shown in the hover tooltip over the marker. */
  name?: string;
};

/** Load a URL into an HTMLImageElement (null until ready / on error). */
function useHtmlImage(url: string | null): HTMLImageElement | null {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (!url) {
      setImg(null);
      return;
    }
    let cancelled = false;
    const im = new window.Image();
    im.crossOrigin = "anonymous";
    im.onload = () => {
      if (!cancelled) setImg(im);
    };
    im.onerror = () => {
      if (!cancelled) setImg(null);
    };
    im.src = url;
    return () => {
      cancelled = true;
    };
  }, [url]);
  return img;
}

type Props = {
  /** Game frame size (e.g. 720×1280) — the full-screen zone. */
  frameWidth: number;
  frameHeight: number;
  /** Real game-screen reference filling the screen zone (e.g. practice level). */
  backgroundUrl: string | null;
  /** Cropped scene guide image, placed as a movable/resizable region. */
  sceneUrl: string | null;
  /** Scene rectangle (% of the frame) — where the guide maps onto the screen. */
  rect: EditorRegion;
  onRectChange: (rect: EditorRegion) => void;
  /** Guide overlay opacity so the operator can line it up with the background. */
  opacity?: number;
  /** Item markers (guide-relative %), drawn through ``rect`` onto the frame. */
  points?: CalibratorPoint[];
  /** Currently highlighted marker number (shared with the items list). */
  hovered?: number | null;
  onHover?: (n: number | null) => void;
};

/** Calibration canvas: a screen-sized zone filled with a real game frame, with
 * the (cropped) scene guide image overlaid as a draggable/resizable region.
 * Dragging/resizing the guide to match the real screen yields ``scene_rect`` —
 * which maps each guide-relative point % onto the full game frame. This is what
 * lets cropped spreadsheet guides still produce correct in-game tap positions. */
export function SceneCalibrator({
  frameWidth,
  frameHeight,
  backgroundUrl,
  sceneUrl,
  rect,
  onRectChange,
  opacity = 0.05,
  points = [],
  hovered = null,
  onHover,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<Konva.Image>(null);
  const trRef = useRef<Konva.Transformer>(null);
  const [containerW, setContainerW] = useState(0);
  const bg = useHtmlImage(backgroundUrl);
  const scene = useHtmlImage(sceneUrl);

  const scale = containerW > 0 ? Math.min(1, containerW / frameWidth) : 0;
  const stageW = Math.round(frameWidth * scale);
  const stageH = Math.round(frameHeight * scale);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const sync = () => setContainerW(Math.floor(el.getBoundingClientRect().width));
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    sync();
    return () => ro.disconnect();
  }, []);

  // Keep the transformer attached to the scene image once both exist.
  useEffect(() => {
    const tr = trRef.current;
    const node = sceneRef.current;
    if (tr && node) {
      tr.nodes([node]);
      tr.getLayer()?.batchDraw();
    } else if (tr) {
      tr.nodes([]);
    }
  }, [scene, stageW, stageH]);

  const px = pctToPixels(rect.bbox, frameWidth, frameHeight);
  const rx = px.x * scale;
  const ry = px.y * scale;
  const rw = px.width * scale;
  const rh = px.height * scale;

  // The marker currently highlighted (hovered in the canvas or the items list).
  const hoveredPoint =
    hovered != null ? points.find((p) => p.n === hovered) : undefined;

  const commit = (x: number, y: number, w: number, h: number) => {
    onRectChange({
      ...rect,
      bbox: pixelsToPct(x / scale, y / scale, w / scale, h / scale, frameWidth, frameHeight),
    });
  };

  if (containerW === 0 || scale === 0) {
    return (
      <div
        ref={containerRef}
        className="w-full"
        style={{ aspectRatio: `${frameWidth} / ${frameHeight}` }}
      />
    );
  }

  return (
    <div ref={containerRef} className="w-full">
      <div
        className="konva-editor-wrap relative"
        style={{ width: stageW, height: stageH, maxWidth: "100%" }}
      >
        <Stage width={stageW} height={stageH} className="konva-stage">
          <Layer>
            {bg ? (
              <KonvaImage image={bg} width={stageW} height={stageH} listening={false} />
            ) : (
              <Rect width={stageW} height={stageH} fill="#0b0f14" listening={false} />
            )}
            {scene ? (
              <KonvaImage
                ref={sceneRef}
                image={scene}
                x={rx}
                y={ry}
                width={rw}
                height={rh}
                opacity={opacity}
                draggable
                onDragEnd={(e) => commit(e.target.x(), e.target.y(), rw, rh)}
                onTransformEnd={(e) => {
                  const node = e.target;
                  const w = node.width() * node.scaleX();
                  const h = node.height() * node.scaleY();
                  node.scaleX(1);
                  node.scaleY(1);
                  commit(node.x(), node.y(), w, h);
                }}
              />
            ) : null}
            {/* Outline so the scene region is visible even at low opacity. */}
            <Rect
              x={rx}
              y={ry}
              width={rw}
              height={rh}
              stroke="#00dcff"
              strokeWidth={1.5}
              dash={[6, 4]}
              listening={false}
            />
            {/* Item markers, mapped guide-% → rect → frame, so they track the
                guide as it is moved/resized. Hover syncs with the items list. */}
            {points.map((p) => {
              const cx = rx + (p.xPct / 100) * rw;
              const cy = ry + (p.yPct / 100) * rh;
              const on = hovered === p.n;
              return (
                <Circle
                  key={p.n}
                  x={cx}
                  y={cy}
                  radius={on ? 11 : 9}
                  fill={on ? "#f97316" : "rgba(0,0,0,0.7)"}
                  stroke="#ffffff"
                  strokeWidth={on ? 1.5 : 1}
                  onMouseEnter={() => onHover?.(p.n)}
                  onMouseLeave={() => onHover?.(null)}
                />
              );
            })}
            {points.map((p) => (
              <Text
                key={`t-${p.n}`}
                x={rx + (p.xPct / 100) * rw - 9}
                y={ry + (p.yPct / 100) * rh - 5}
                width={18}
                height={10}
                text={String(p.n)}
                fontSize={9}
                fontStyle="bold"
                fill="#ffffff"
                align="center"
                listening={false}
              />
            ))}
            <Transformer
              ref={trRef}
              rotateEnabled={false}
              boundBoxFunc={(oldBox, newBox) =>
                newBox.width < 8 || newBox.height < 8 ? oldBox : newBox
              }
            />
          </Layer>
        </Stage>

        {/* Hover tooltip for the highlighted marker — an HTML overlay so the
            item name renders crisply above the canvas. */}
        {hoveredPoint ? (
          <div
            className="pointer-events-none absolute z-10 max-w-[200px] -translate-x-1/2 -translate-y-full truncate rounded bg-black/85 px-1.5 py-0.5 text-[11px] font-medium text-white"
            style={{
              left: rx + (hoveredPoint.xPct / 100) * rw,
              top: ry + (hoveredPoint.yPct / 100) * rh - 8,
            }}
          >
            {hoveredPoint.n}. {hoveredPoint.name ?? `Item ${hoveredPoint.n}`}
          </div>
        ) : null}
      </div>
    </div>
  );
}
