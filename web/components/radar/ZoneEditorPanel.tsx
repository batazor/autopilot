"use client";

// Side panel for the global-map zone editor (MVP: rectangular zones only).
// Zones are stored in game coordinates [col,row] on the 1200×1200 grid; the map
// (RadarMapViewer) renders + selects them, draw is two corner clicks routed from
// the page, and move/resize is precise numeric editing here.

import { Button } from "@/components/ui/Button";
import type { RadarZone } from "@/lib/radar-api";

const SWATCHES = ["#E24B4A", "#BA7517", "#639922", "#22d3ee", "#a78bfa", "#3b82f6"];

export function ZoneEditorPanel({
  zones,
  selectedId,
  onSelect,
  onChange,
  onSave,
  saving,
  drawing,
  onToggleDraw,
  onClose,
  gridSize = 1200,
}: {
  zones: RadarZone[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChange: (zones: RadarZone[]) => void;
  onSave: () => void;
  saving: boolean;
  drawing: boolean;
  onToggleDraw: () => void;
  onClose: () => void;
  gridSize?: number;
}) {
  const patch = (id: string, p: Partial<RadarZone>) =>
    onChange(zones.map((z) => (z.id === id ? { ...z, ...p } : z)));
  const remove = (id: string) => onChange(zones.filter((z) => z.id !== id));
  const setNum = (id: string, key: keyof RadarZone, raw: string) => {
    const n = Math.max(0, Math.min(gridSize, Math.round(Number(raw) || 0)));
    patch(id, { [key]: n } as Partial<RadarZone>);
  };

  const numField = (z: RadarZone, key: "min_col" | "min_row" | "max_col" | "max_row") => (
    <label className="flex items-center gap-1 text-xs text-zinc-400">
      {key.replace("_", " ")}
      <input
        type="number"
        min={0}
        max={gridSize}
        value={z[key]}
        onChange={(e) => setNum(z.id, key, e.target.value)}
        onClick={(e) => e.stopPropagation()}
        className="w-16 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-right tabular-nums text-zinc-100"
      />
    </label>
  );

  return (
    <div className="panel flex flex-col gap-3 p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Zone editor</h3>
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant={drawing ? "secondary" : "primary"}
          onClick={onToggleDraw}
          title="Click two opposite corners on the map to add a zone"
        >
          {drawing ? "Cancel draw" : "+ Add zone"}
        </Button>
        <Button onClick={onSave} pending={saving}>
          Save
        </Button>
      </div>
      {drawing ? (
        <p className="text-xs text-amber-400">
          Click two opposite corners on the map to place the zone…
        </p>
      ) : null}
      {zones.length === 0 ? (
        <p className="text-xs text-zinc-500">No zones yet — add one or save to keep it empty.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {zones.map((z) => {
            const selected = z.id === selectedId;
            return (
              <li
                key={z.id}
                onClick={() => onSelect(z.id)}
                className={`cursor-pointer rounded-lg border p-2 ${
                  selected ? "border-cyan-500 bg-cyan-500/10" : "border-zinc-700 bg-zinc-900/40"
                }`}
              >
                <div className="flex items-center gap-2">
                  <input
                    value={z.label}
                    placeholder="label"
                    onChange={(e) => patch(z.id, { label: e.target.value })}
                    onClick={(e) => e.stopPropagation()}
                    className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-sm text-zinc-100"
                  />
                  <Button
                    variant="danger"
                    onClick={(e) => {
                      e?.stopPropagation?.();
                      remove(z.id);
                    }}
                  >
                    Delete
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {numField(z, "min_col")}
                  {numField(z, "min_row")}
                  {numField(z, "max_col")}
                  {numField(z, "max_row")}
                </div>
                <div className="mt-2 flex items-center gap-1">
                  {SWATCHES.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={`colour ${c}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        patch(z.id, { color: c });
                      }}
                      style={{ background: c }}
                      className={`h-5 w-5 rounded-full border ${
                        (z.color ?? "") === c ? "border-white" : "border-transparent"
                      }`}
                    />
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
