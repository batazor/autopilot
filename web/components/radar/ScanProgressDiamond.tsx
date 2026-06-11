"use client";

// SVG diamond of grid cells, one per tap point. Plotting cells at their raw
// (ix, iy) raster indices reproduces the minimap diamond shape because the
// scanner's grid is already clipped to it.

import { useMemo } from "react";
import type { RadarGridCell } from "@/lib/radar-api";

export type CellMark = "done" | "unstable";

const CELL = 10;
const GAP = 2;

const FILL: Record<"pending" | "current" | CellMark, string> = {
  pending: "var(--color-wos-panel-raised, #2a3344)",
  current: "#f59e0b", // amber
  done: "#34d399", // green
  unstable: "#facc15", // yellow
};

export function cellKey(c: { ix: number; iy: number }): string {
  return `${c.ix}_${c.iy}`;
}

export default function ScanProgressDiamond({
  grid,
  cells,
  scanning,
}: {
  grid: RadarGridCell[];
  /** cellKey → mark for frames already captured (missing key = pending). */
  cells: Partial<Record<string, CellMark>>;
  /** Highlight the next pending cell as "current". */
  scanning: boolean;
}) {
  const { minX, minY, width, height, currentKey } = useMemo(() => {
    const xs = grid.map((c) => c.ix);
    const ys = grid.map((c) => c.iy);
    const minX = Math.min(...xs, 0);
    const minY = Math.min(...ys, 0);
    const next = scanning ? grid.find((c) => !(cellKey(c) in cells)) : undefined;
    return {
      minX,
      minY,
      width: (Math.max(...xs, 0) - minX + 1) * (CELL + GAP),
      height: (Math.max(...ys, 0) - minY + 1) * (CELL + GAP),
      currentKey: next ? cellKey(next) : null,
    };
  }, [grid, cells, scanning]);

  if (grid.length === 0) {
    return <p className="text-sm text-wos-text-muted">No grid data for this run.</p>;
  }

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="mx-auto block max-h-72 w-full max-w-md"
      role="img"
      aria-label="Scan progress grid"
    >
      {grid.map((c) => {
        const key = cellKey(c);
        const mark = cells[key];
        const state = mark ?? (key === currentKey ? "current" : "pending");
        return (
          <rect
            key={key}
            x={(c.ix - minX) * (CELL + GAP)}
            y={(c.iy - minY) * (CELL + GAP)}
            width={CELL}
            height={CELL}
            rx={2}
            fill={FILL[state]}
            opacity={state === "pending" ? 0.45 : 1}
          >
            <title>{`(${c.ix}, ${c.iy}) — ${state}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}
