"use client";

import { useMemo, useState } from "react";
import { AppCheckbox, AppListbox } from "@/components/headless";
import { defaultRegion } from "@/lib/labeling-utils";
import type { EditorRegion } from "@/lib/bbox";

const ACTIONS = ["exist", "text", "color_check", "click"] as const;
const OCR_TYPES = ["integer", "string", "boolean", "time"] as const;
const COLOR_TYPES = ["red", "blue", "gray", "green"] as const;

type Props = {
  regions: EditorRegion[];
  selectedId: string | null;
  activeVersion: string | null;
  onSelect: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[]) => void;
  onDirty: () => void;
};

export function LabelingRegionsPanel({
  regions,
  selectedId,
  activeVersion,
  onSelect,
  onRegionsChange,
  onDirty,
}: Props) {
  const [filter, setFilter] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const selected = regions.find((r) => r.id === selectedId) ?? null;

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return regions;
    return regions.filter((r) => r.name.toLowerCase().includes(q));
  }, [regions, filter]);

  const patchSelected = (patch: Partial<EditorRegion>) => {
    if (!selected) return;
    const next = regions.map((r) => {
      if (r.id !== selected.id) return r;
      const merged = { ...r, ...patch, id: patch.name ?? r.id };
      if (!patch.has_red_dot && "has_red_dot" in patch) delete merged.has_red_dot;
      if (!patch.isSearch && "isSearch" in patch) delete merged.isSearch;
      if (!patch.overlay_auxiliary && "overlay_auxiliary" in patch)
        delete merged.overlay_auxiliary;
      return merged;
    });
    onRegionsChange(next);
    if (patch.name && patch.name !== selected.id) onSelect(patch.name);
    onDirty();
  };

  const onAddRegion = () => {
    const base = defaultRegion();
    let name = base.name;
    let n = 1;
    while (regions.some((r) => r.name === name)) {
      name = `region_${n++}`;
    }
    const reg = { ...base, id: name, name };
    onRegionsChange([...regions, reg]);
    onSelect(name);
    onDirty();
  };

  const onDelete = () => {
    if (!selectedId) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    onRegionsChange(regions.filter((r) => r.id !== selectedId));
    onSelect(null);
    setConfirmDelete(false);
    onDirty();
  };

  return (
    <details className="labeling-panel-block" open>
      <summary className="labeling-panel-block__title">Region properties</summary>
      <div className="labeling-panel-block__body">
        {activeVersion ? (
          <p className="meta labeling-regions-hint">
            Editing version <code>{activeVersion}</code> — overrides in{" "}
            <code>versions[{activeVersion}].regions[]</code>.
          </p>
        ) : null}

        <button type="button" className="btn-secondary labeling-add-region" onClick={onAddRegion}>
          Add region
        </button>

        <input
          type="search"
          className="labeling-search"
          placeholder="Filter regions…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />

        <ul className="region-list">
          {visible.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className={r.id === selectedId ? "active" : undefined}
                onClick={() => {
                  onSelect(r.id);
                  setConfirmDelete(false);
                }}
              >
                {r.name}
                {r.overlay_auxiliary ? " · aux" : ""}
                {r.has_red_dot ? " · dot" : ""}
              </button>
            </li>
          ))}
        </ul>

        {selected ? (
          <div className="labeling-region-form">
            <label className="meta">
              name
              <input
                value={selected.name}
                onChange={(e) => patchSelected({ name: e.target.value, id: e.target.value })}
              />
            </label>
            <label className="meta">
              action
              <AppListbox
                value={selected.action}
                onChange={(action) => {
                  const patch: Partial<EditorRegion> = { action };
                  if (action === "exist") patch.type = undefined;
                  else if (action === "color_check" && !selected.type)
                    patch.type = "red";
                  else if (action !== "color_check" && !selected.type)
                    patch.type = "string";
                  patchSelected(patch);
                }}
                options={ACTIONS.map((a) => ({ value: a, label: a }))}
                minWidth={140}
              />
            </label>
            <label className="meta">
              threshold
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={selected.threshold}
                onChange={(e) => patchSelected({ threshold: Number(e.target.value) })}
              />
            </label>
            {selected.action === "color_check" ? (
              <label className="meta">
                expected color (type)
                <AppListbox
                  value={selected.type || "red"}
                  onChange={(v) => patchSelected({ type: v })}
                  options={COLOR_TYPES.map((t) => ({ value: t, label: t }))}
                  minWidth={120}
                />
              </label>
            ) : selected.action !== "exist" ? (
              <label className="meta">
                type
                <AppListbox
                  value={selected.type || "string"}
                  onChange={(v) => patchSelected({ type: v })}
                  options={OCR_TYPES.map((t) => ({ value: t, label: t }))}
                  minWidth={120}
                />
              </label>
            ) : null}
            <AppCheckbox
              fieldClassName="labeling-check meta"
              checked={Boolean(selected.has_red_dot)}
              onChange={(checked) =>
                patchSelected({ has_red_dot: checked ? true : false })
              }
              label="Has red dot"
            />
            <AppCheckbox
              fieldClassName="labeling-check meta"
              checked={Boolean(selected.isSearch)}
              onChange={(checked) =>
                patchSelected({ isSearch: checked ? true : false })
              }
              label="Search full frame"
            />
            <AppCheckbox
              fieldClassName="labeling-check meta"
              checked={Boolean(selected.overlay_auxiliary)}
              onChange={(checked) =>
                patchSelected({
                  overlay_auxiliary: checked ? true : false,
                })
              }
              label="Overlay auxiliary"
            />
            <button
              type="button"
              className={confirmDelete ? "btn-primary" : "btn-secondary"}
              onClick={onDelete}
            >
              {confirmDelete ? "Confirm delete" : "Delete region"}
            </button>
          </div>
        ) : (
          <p className="meta">Select a region, draw on the canvas, or add one.</p>
        )}
      </div>
    </details>
  );
}
