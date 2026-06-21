"use client";

import { useState } from "react";
import type { LabelingStaleCrop } from "@/lib/types";

type Props = {
  count: number;
  stale: LabelingStaleCrop[];
  onResync: () => void;
  busy: boolean;
};

export function LabelingStaleCropsBanner({
  count,
  stale,
  onResync,
  busy,
}: Props) {
  const [open, setOpen] = useState(false);
  if (count <= 0) return null;
  return (
    <div className="labeling-stale-banner">
      <div className="labeling-stale-banner__main">
        <p>
          <strong>{count}</strong> crop(s) out of sync with current bboxes — re-export
          to fix template matches.
        </p>
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={onResync}
        >
          Resync now
        </button>
      </div>
      <button
        type="button"
        className="btn-secondary labeling-stale-banner__toggle"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Hide" : `Show ${Math.min(stale.length, count)} stale crop(s)`}
      </button>
      {open ? (
        <ul className="labeling-stale-list meta">
          {stale.map((s) => (
            <li key={`${s.crop_path}:${s.region}`}>
              {s.region.padEnd(36)}{" "}
              bbox={s.expected_w}×{s.expected_h} crop={s.actual_w}×{s.actual_h}{" "}
              ({s.ocr})
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
