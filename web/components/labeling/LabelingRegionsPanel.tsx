"use client";

import { useMemo, useState } from "react";
import { AppCheckbox, AppListbox } from "@/components/headless";
import { labelingImageUrl } from "@/lib/api";
import { defaultRegion } from "@/lib/labeling-utils";
import type { EditorRegion, PercentBBox } from "@/lib/bbox";

const ACTIONS = ["exist", "text", "color_check", "click"] as const;
const OCR_TYPES = ["integer", "string", "boolean", "time"] as const;
const COLOR_TYPES = ["red", "blue", "gray", "green"] as const;

const CROP_PREVIEW_MAX_HEIGHT = 220;

type Props = {
  regions: EditorRegion[];
  selectedId: string | null;
  activeVersion: string | null;
  refRel?: string | null;
  imageNonce?: number | string;
  onSelect: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[]) => void;
  onDirty: () => void;
};

function cropPreviewStyle(
  bbox: PercentBBox,
  imageUrl: string,
): React.CSSProperties | null {
  const w = bbox.width;
  const h = bbox.height;
  if (!(w > 0) || !(h > 0) || w >= 100 || h >= 100) return null;
  const bgW = (100 / w) * 100;
  const bgH = (100 / h) * 100;
  const posX = (bbox.x * 100) / (100 - w);
  const posY = (bbox.y * 100) / (100 - h);
  const aspect =
    bbox.original_width > 0 && bbox.original_height > 0
      ? (w * bbox.original_width) / (h * bbox.original_height)
      : w / h;
  return {
    backgroundImage: `url("${imageUrl}")`,
    backgroundSize: `${bgW}% ${bgH}%`,
    backgroundPosition: `${posX}% ${posY}%`,
    backgroundRepeat: "no-repeat",
    aspectRatio: `${aspect}`,
    maxWidth: `${CROP_PREVIEW_MAX_HEIGHT * aspect}px`,
  };
}

export function LabelingRegionsPanel({
  regions,
  selectedId,
  activeVersion,
  refRel,
  imageNonce,
  onSelect,
  onRegionsChange,
  onDirty,
}: Props) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  const selected = regions.find((r) => r.id === selectedId) ?? null;

  const cropStyle = useMemo(() => {
    if (!selected || !refRel) return null;
    const url = labelingImageUrl(refRel, imageNonce);
    return cropPreviewStyle(selected.bbox, url);
  }, [selected, refRel, imageNonce]);

  const regionOptions = useMemo(
    () =>
      regions.map((r) => {
        const flags = [
          r.overlay_auxiliary ? "aux" : null,
          r.has_red_dot ? "dot" : null,
          r.tap_hold_ms && r.tap_hold_ms > 0 ? `hold ${r.tap_hold_ms}ms` : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return {
          value: r.id,
          label: flags ? `${r.name} · ${flags}` : r.name,
        };
      }),
    [regions],
  );

  const patchSelected = (patch: Partial<EditorRegion>) => {
    if (!selected) return;
    const next = regions.map((r) => {
      if (r.id !== selected.id) return r;
      const merged = { ...r, ...patch, id: patch.name ?? r.id };
      if (!patch.has_red_dot && "has_red_dot" in patch) delete merged.has_red_dot;
      if (!patch.isSearch && "isSearch" in patch) delete merged.isSearch;
      if (!patch.overlay_auxiliary && "overlay_auxiliary" in patch)
        delete merged.overlay_auxiliary;
      if ("tap_hold_ms" in patch && !(Number(patch.tap_hold_ms ?? 0) > 0))
        delete merged.tap_hold_ms;
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

        <button type="button" className="btn-primary labeling-add-region" onClick={onAddRegion}>
          Add region
        </button>

        <AppListbox
          fullWidth
          label="Region"
          value={selectedId ?? ""}
          onChange={(id) => {
            onSelect(id || null);
            setConfirmDelete(false);
          }}
          options={regionOptions}
          placeholder={regions.length ? "Select region…" : "No regions yet"}
          disabled={regions.length === 0}
        />

        {selected ? (
          <div className="labeling-region-form">
            {cropStyle ? (
              <div className="labeling-crop-preview" aria-label="Crop preview">
                <div className="labeling-crop-preview__img" style={cropStyle} />
                <span className="meta labeling-crop-preview__caption">
                  {Math.round((selected.bbox.width * selected.bbox.original_width) / 100)}
                  ×
                  {Math.round((selected.bbox.height * selected.bbox.original_height) / 100)}
                  {" px"}
                </span>
              </div>
            ) : refRel && selected ? (
              <p className="meta labeling-crop-preview__empty">
                Draw a region to see the crop preview.
              </p>
            ) : null}
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
            <label className="meta">
              tap hold (ms)
              <input
                type="number"
                min={0}
                step={50}
                value={selected.tap_hold_ms ?? 0}
                onChange={(e) =>
                  patchSelected({ tap_hold_ms: Math.max(0, Number(e.target.value) || 0) })
                }
              />
            </label>
            <button
              type="button"
              className={
                confirmDelete
                  ? "btn-secondary labeling-delete-region labeling-delete-region--confirm"
                  : "btn-secondary labeling-delete-region"
              }
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
