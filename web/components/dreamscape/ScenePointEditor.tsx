"use client";

/* eslint-disable @next/next/no-img-element */
import { useRef, useState, type ReactNode } from "react";

/** A numbered pin on a scene's guide image (position in % of the image). */
export type ScenePin = {
  n: number;
  name: string;
  xPct: number;
  yPct: number;
  /** OCR confidence when auto-detected (null = manual / unknown). */
  conf?: number | null;
  /** False = name had no OCR'd marker yet; operator must place it (amber). */
  placed?: boolean;
};

type Props = {
  imageUrl: string;
  pins: ScenePin[];
  selectedN: number | null;
  onSelectN: (n: number | null) => void;
  onChange: (next: ScenePin[]) => void;
  /** Extra controls under the image (e.g. onboarding's "Detect numbers"). */
  imageFooter?: ReactNode;
  /** Content above the pin list (e.g. onboarding's item-names paste box). */
  listHeader?: ReactNode;
};

/** Shared scene-point editor: click the guide image to place/move numbered pins,
 * rename or delete them in the list. The single editing surface behind both
 * scene onboarding (create) and the Region editor (edit an existing scene) —
 * one flow, one set of interactions, persisted to the scene DB by the caller. */
export function ScenePointEditor({
  imageUrl,
  pins,
  selectedN,
  onSelectN,
  onChange,
  imageFooter,
  listHeader,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredN, setHoveredN] = useState<number | null>(null);
  // Active drag: which pin, where the press started (client px), and whether it
  // has moved past the click threshold yet. Null when no pin is being dragged.
  const dragRef = useRef<{
    n: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);

  /** Pointer client coords → pin position (% of the image), clamped to [0,100]
   * so a pin can't be dragged off the guide. */
  const pctFromClient = (clientX: number, clientY: number) => {
    const el = containerRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const clamp = (v: number) => Math.min(100, Math.max(0, v));
    return {
      xPct: Math.round(clamp(((clientX - r.left) / r.width) * 100) * 100) / 100,
      yPct: Math.round(clamp(((clientY - r.top) / r.height) * 100) * 100) / 100,
    };
  };

  const onImageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const pos = pctFromClient(e.clientX, e.clientY);
    if (!pos) return;
    const { xPct, yPct } = pos;
    // Add a new pin with the next free number.
    const nextN = pins.length ? Math.max(...pins.map((p) => p.n)) + 1 : 1;
    onChange(
      [...pins, { n: nextN, name: "", xPct, yPct, conf: null, placed: true }].sort(
        (a, b) => a.n - b.n,
      ),
    );
    onSelectN(nextN);
  };

  const renamePin = (n: number, name: string) =>
    onChange(pins.map((p) => (p.n === n ? { ...p, name } : p)));
  const deletePin = (n: number) => {
    onChange(pins.filter((p) => p.n !== n));
    if (selectedN === n) onSelectN(null);
  };

  const unplaced = pins.filter((p) => p.placed === false).length;
  const unnamed = pins.filter((p) => !p.name.trim()).length;
  const highlightedN = hoveredN ?? selectedN;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {/* Guide image with pins */}
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs text-wos-text-muted">
          <span>
            {selectedN != null
              ? `Pin #${selectedN} selected. Drag a pin to move it, or click empty space to add one`
              : "Drag a pin to move it, click to select, or click empty space to add one"}
          </span>
          {selectedN != null ? (
            <button
              type="button"
              className="rounded border border-wos-border px-1.5 hover:border-wos-border-hover"
              onClick={() => onSelectN(null)}
            >
              deselect
            </button>
          ) : null}
        </div>
        <div
          ref={containerRef}
          className="relative mx-auto w-full max-w-md cursor-crosshair select-none overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep"
          onClick={onImageClick}
        >
          <img
            src={imageUrl}
            alt="guide"
            className="pointer-events-none block h-auto w-full"
          />
          {pins.map((p) => {
            const highlighted = highlightedN === p.n;
            return (
              <button
                key={p.n}
                type="button"
                title={`${p.n}. ${p.name || "(unnamed)"} — drag to move`}
                onMouseEnter={() => setHoveredN(p.n)}
                onMouseLeave={() => setHoveredN(null)}
                onFocus={() => setHoveredN(p.n)}
                onBlur={() => setHoveredN(null)}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  dragRef.current = {
                    n: p.n,
                    startX: e.clientX,
                    startY: e.clientY,
                    moved: false,
                  };
                  e.currentTarget.setPointerCapture(e.pointerId);
                }}
                onPointerMove={(e) => {
                  const d = dragRef.current;
                  if (!d || d.n !== p.n) return;
                  // Ignore sub-threshold jitter so a plain click still selects.
                  if (
                    !d.moved &&
                    Math.hypot(e.clientX - d.startX, e.clientY - d.startY) < 3
                  )
                    return;
                  d.moved = true;
                  const pos = pctFromClient(e.clientX, e.clientY);
                  if (!pos) return;
                  onChange(
                    pins.map((pp) =>
                      pp.n === p.n ? { ...pp, ...pos, placed: true } : pp,
                    ),
                  );
                }}
                onPointerUp={(e) => {
                  const d = dragRef.current;
                  if (!d || d.n !== p.n) return;
                  e.currentTarget.releasePointerCapture(e.pointerId);
                  dragRef.current = null;
                  // A press without a drag is a plain click → toggle selection.
                  if (!d.moved) onSelectN(selectedN === p.n ? null : p.n);
                }}
                onPointerCancel={() => {
                  if (dragRef.current?.n === p.n) dragRef.current = null;
                }}
                onClick={(e) => e.stopPropagation()}
                style={{ left: `${p.xPct}%`, top: `${p.yPct}%`, touchAction: "none" }}
                className={`absolute flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-grab items-center justify-center rounded-full border text-[10px] font-bold leading-none transition active:cursor-grabbing ${
                  highlighted
                    ? "z-10 scale-125 border-white bg-wos-accent text-wos-on-accent shadow-[0_0_0_4px_rgba(20,184,166,0.22)]"
                    : p.placed === false
                      ? "border-amber-300/80 bg-amber-500/80 text-black"
                      : p.conf != null && p.conf < 0.5
                        ? "border-orange-300/80 bg-orange-600/80 text-white"
                        : "border-white/80 bg-black/70 text-white"
                }`}
              >
                {p.n}
              </button>
            );
          })}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {imageFooter}
          {unplaced ? (
            <span className="rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-400">
              {unplaced} unplaced (amber) — click to position
            </span>
          ) : null}
          {unnamed ? (
            <span className="rounded bg-wos-panel-raised px-2 py-1 text-xs text-wos-text-muted">
              {unnamed} unnamed
            </span>
          ) : null}
        </div>
      </div>

      {/* Names + pin list */}
      <div className="flex min-h-0 flex-col">
        {listHeader}
        <p className="meta mb-1 mt-3">{pins.length} point(s)</p>
        <ol className="max-h-[70vh] flex-1 space-y-1 overflow-auto pr-1">
          {pins.map((p) => {
            const highlighted = highlightedN === p.n;
            return (
              <li
                key={p.n}
                onMouseEnter={() => setHoveredN(p.n)}
                onMouseLeave={() => setHoveredN(null)}
                className={`flex items-center gap-2 rounded px-1 py-0.5 transition ${
                  highlighted ? "bg-wos-option-hover" : ""
                }`}
              >
                <button
                  type="button"
                  onFocus={() => setHoveredN(p.n)}
                  onBlur={() => setHoveredN(null)}
                  onClick={() => onSelectN(selectedN === p.n ? null : p.n)}
                  className={`w-6 shrink-0 rounded text-right text-xs ${
                    highlighted ? "text-wos-accent" : "text-wos-text-muted"
                  }`}
                >
                  {p.n}
                </button>
                <input
                  type="text"
                  value={p.name}
                  onFocus={() => setHoveredN(p.n)}
                  onBlur={() => setHoveredN(null)}
                  onChange={(e) => renamePin(p.n, e.target.value)}
                  placeholder={`Item ${p.n}`}
                  className={`min-w-0 flex-1 rounded border bg-wos-bg-deep px-2 py-1 text-sm text-wos-text transition ${
                    highlighted
                      ? "border-wos-accent"
                      : "border-wos-border"
                  }`}
                />
                <button
                  type="button"
                  onFocus={() => setHoveredN(p.n)}
                  onBlur={() => setHoveredN(null)}
                  onClick={() => deletePin(p.n)}
                  className="rounded px-1.5 text-sm text-wos-text-muted hover:text-rose-400"
                  title="Remove"
                >
                  ✕
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
