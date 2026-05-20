"use client";

import Konva from "konva";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Image as KonvaImage, Layer, Rect, Stage, Transformer } from "react-konva";
import type { KonvaEventObject } from "konva/lib/Node";
import type { EditorRegion } from "@/lib/bbox";
import { pctToPixels, pixelsToPct } from "@/lib/bbox";

export type { EditorRegion };

type Props = {
  imageUrl: string | null;
  imageWidth: number;
  imageHeight: number;
  regions: EditorRegion[];
  selectedId: string | null;
  drawMode: boolean;
  onSelect: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[]) => void;
  onDeleteSelected?: () => void;
  onDropImageFile?: (file: File) => void;
  dropDisabled?: boolean;
};

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return Boolean(
    target.closest(
      "input, textarea, select, [role='combobox'], [role='listbox'], [data-headlessui-portal]",
    ),
  );
}

type DraftRect = { x: number; y: number; width: number; height: number };

function useBackgroundImage(url: string | null) {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [loadError, setLoadError] = useState(false);
  useEffect(() => {
    if (!url) {
      setImage(null);
      setLoadError(false);
      return;
    }
    setLoadError(false);
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = url;
    const onLoad = () => {
      setImage(img);
      setLoadError(false);
    };
    const onError = () => {
      setImage(null);
      setLoadError(true);
    };
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    return () => {
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
    };
  }, [url]);
  return { image, loadError };
}

export function KonvaImageEditor({
  imageUrl,
  imageWidth,
  imageHeight,
  regions,
  selectedId,
  drawMode,
  onSelect,
  onRegionsChange,
  onDeleteSelected,
  onDropImageFile,
  dropDisabled = false,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<Konva.Stage | null>(null);
  const trRef = useRef<Konva.Transformer>(null);
  const [containerW, setContainerW] = useState(720);
  const [dropActive, setDropActive] = useState(false);
  const [draft, setDraft] = useState<DraftRect | null>(null);
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const { image: bg, loadError } = useBackgroundImage(imageUrl);

  const imgW = imageWidth > 0 ? imageWidth : bg?.naturalWidth ?? 720;
  const imgH = imageHeight > 0 ? imageHeight : bg?.naturalHeight ?? 1280;
  const scale = useMemo(() => {
    if (imgW <= 0) return 1;
    return Math.min(1, containerW / imgW);
  }, [containerW, imgW]);
  const stageW = Math.max(1, Math.round(imgW * scale));
  const stageH = Math.max(1, Math.round(imgH * scale));

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const sync = () => setContainerW(el.clientWidth || 720);
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    sync();
    return () => ro.disconnect();
  }, []);

  const focusCanvas = useCallback(() => {
    containerRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Backspace" && e.key !== "Delete") return;
      if (e.repeat || isTypingTarget(e.target)) return;
      if (!selectedId || drawMode || !onDeleteSelected) return;
      e.preventDefault();
      e.stopPropagation();
      onDeleteSelected();
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [selectedId, drawMode, onDeleteSelected]);

  useEffect(() => {
    const tr = trRef.current;
    if (!tr) return;
    const stage = stageRef.current ?? tr.getStage();
    const node =
      selectedId && stage
        ? stage.findOne<Konva.Rect>((n) => n.name() === selectedId)
        : null;
    if (node) {
      tr.nodes([node]);
    } else {
      tr.nodes([]);
    }
    tr.getLayer()?.batchDraw();
  }, [selectedId, regions, stageW, stageH]);

  const updateRegionPixels = useCallback(
    (id: string, x: number, y: number, width: number, height: number) => {
      const next = regions.map((r) => {
        if (r.id !== id) return r;
        return {
          ...r,
          bbox: pixelsToPct(x / scale, y / scale, width / scale, height / scale, imgW, imgH),
        };
      });
      onRegionsChange(next);
    },
    [regions, onRegionsChange, scale, imgW, imgH],
  );

  const onStageMouseDown = (e: KonvaEventObject<MouseEvent>) => {
    focusCanvas();
    const stage = e.target.getStage();
    if (!stage) return;
    if (!drawMode) {
      if (e.target === stage) onSelect(null);
      return;
    }
    if (!bg) return;
    if (e.target !== stage) return;
    const pos = stage.getPointerPosition();
    if (!pos) return;
    onSelect(null);
    setDrawStart({ x: pos.x, y: pos.y });
    setDraft({ x: pos.x, y: pos.y, width: 0, height: 0 });
  };

  const onStageMouseMove = (e: KonvaEventObject<MouseEvent>) => {
    if (!drawStart || !draft) return;
    const pos = e.target.getStage()?.getPointerPosition();
    if (!pos) return;
    const x = Math.min(drawStart.x, pos.x);
    const y = Math.min(drawStart.y, pos.y);
    const width = Math.abs(pos.x - drawStart.x);
    const height = Math.abs(pos.y - drawStart.y);
    setDraft({ x, y, width, height });
  };

  const onStageMouseUp = () => {
    if (!drawStart || !draft) {
      setDrawStart(null);
      setDraft(null);
      return;
    }
    if (draft.width >= 4 && draft.height >= 4) {
      const id = `region-${Date.now()}`;
      const bbox = pixelsToPct(
        draft.x / scale,
        draft.y / scale,
        draft.width / scale,
        draft.height / scale,
        imgW,
        imgH,
      );
      const n = regions.length + 1;
      const region: EditorRegion = {
        id,
        name: `region_${n}`,
        action: "exist",
        threshold: 0.9,
        bbox,
      };
      onRegionsChange([...regions, region]);
      onSelect(id);
    }
    setDrawStart(null);
    setDraft(null);
  };

  const onDragOver = (e: React.DragEvent) => {
    if (dropDisabled || !onDropImageFile) return;
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDropActive(true);
  };

  const onDragLeave = (e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDropActive(false);
  };

  const onDrop = (e: React.DragEvent) => {
    setDropActive(false);
    if (dropDisabled || !onDropImageFile) return;
    e.preventDefault();
    const file = [...(e.dataTransfer.files ?? [])].find(
      (f) => f.type.startsWith("image/") || /\.png$/i.test(f.name),
    );
    if (file) onDropImageFile(file);
  };

  const dropZoneProps = onDropImageFile
    ? { onDragOver, onDragLeave, onDrop }
    : {};

  if (!imageUrl) {
    return (
      <div
        className={
          dropActive
            ? "preview-empty konva-editor-measure--drop-active"
            : "preview-empty"
        }
        {...dropZoneProps}
      >
        Select a reference PNG in the left column, or drop a PNG here to import.
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="preview-empty">
        Failed to load image. Is the API running (<code>uv run api</code>)?
      </div>
    );
  }

  const handleCanvasKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Backspace" && e.key !== "Delete") return;
    if (!selectedId || drawMode || !onDeleteSelected) return;
    e.preventDefault();
    onDeleteSelected();
  };

  return (
    <div
      ref={containerRef}
      className={
        dropActive
          ? "konva-editor-measure konva-editor-measure--drop-active"
          : "konva-editor-measure"
      }
      tabIndex={0}
      role="application"
      aria-label="Region editor canvas"
      onKeyDown={handleCanvasKeyDown}
      onPointerDown={focusCanvas}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div
        className="konva-editor-wrap"
        style={{ width: stageW, height: stageH }}
      >
      <Stage
        ref={(node) => {
          stageRef.current = node;
        }}
        width={stageW}
        height={stageH}
        onMouseDown={onStageMouseDown}
        onMouseMove={onStageMouseMove}
        onMouseUp={onStageMouseUp}
        className="konva-stage"
      >
        <Layer>
          {bg ? (
            <KonvaImage image={bg} width={stageW} height={stageH} listening={false} />
          ) : null}
          {regions.map((r) => {
            const px = pctToPixels(r.bbox, imgW, imgH);
            const w = px.width * scale;
            const h = px.height * scale;
            return (
              <Rect
                key={r.id}
                name={r.id}
                x={px.x * scale}
                y={px.y * scale}
                width={w}
                height={h}
                stroke={r.id === selectedId ? "#3b82f6" : "#00dcff"}
                strokeWidth={r.id === selectedId ? 2.5 : 1.5}
                fill="rgba(0, 220, 255, 0.08)"
                hitStrokeWidth={12}
                draggable={!drawMode}
                dragBoundFunc={(pos) => ({
                  x: Math.max(0, Math.min(pos.x, stageW - w)),
                  y: Math.max(0, Math.min(pos.y, stageH - h)),
                })}
                onDragStart={() => onSelect(r.id)}
                onClick={() => onSelect(r.id)}
                onTap={() => onSelect(r.id)}
                onDragEnd={(e) => {
                  const node = e.target;
                  updateRegionPixels(
                    r.id,
                    node.x(),
                    node.y(),
                    node.width() * node.scaleX(),
                    node.height() * node.scaleY(),
                  );
                  node.scaleX(1);
                  node.scaleY(1);
                }}
                onTransformEnd={(e) => {
                  const node = e.target;
                  const w = node.width() * node.scaleX();
                  const h = node.height() * node.scaleY();
                  updateRegionPixels(r.id, node.x(), node.y(), w, h);
                  node.scaleX(1);
                  node.scaleY(1);
                }}
              />
            );
          })}
          {draft ? (
            <Rect
              x={draft.x}
              y={draft.y}
              width={draft.width}
              height={draft.height}
              stroke="#22c55e"
              dash={[6, 4]}
              strokeWidth={2}
              fill="rgba(34, 197, 94, 0.12)"
              listening={false}
            />
          ) : null}
          <Transformer
            ref={trRef}
            rotateEnabled={false}
            boundBoxFunc={(oldBox, newBox) => {
              if (newBox.width < 4 || newBox.height < 4) return oldBox;
              return newBox;
            }}
          />
        </Layer>
      </Stage>
      </div>
    </div>
  );
}
