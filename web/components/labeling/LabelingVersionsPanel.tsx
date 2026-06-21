"use client";

import { AppListbox } from "@/components/headless";
import type { LabelingVersionMeta } from "@/lib/types";

type Props = {
  versions: LabelingVersionMeta[];
  activeVersion: string | null;
  isPending: boolean;
  busy: boolean;
  hasEntry: boolean;
  editVersionCond: string;
  newVersionId: string;
  newVersionCond: string;
  onEditCondChange: (v: string) => void;
  onNewIdChange: (v: string) => void;
  onNewCondChange: (v: string) => void;
  onVersionSelect: (v: string | null) => void;
  onSaveCond: () => void;
  onSyncRegions: () => void;
  onBindCanvas: () => void;
  onDeleteVersion: () => void;
  onAddVersion: () => void;
};

export function LabelingVersionsPanel({
  versions,
  activeVersion,
  isPending,
  busy,
  hasEntry,
  editVersionCond,
  newVersionId,
  newVersionCond,
  onEditCondChange,
  onNewIdChange,
  onNewCondChange,
  onVersionSelect,
  onSaveCond,
  onSyncRegions,
  onBindCanvas,
  onDeleteVersion,
  onAddVersion,
}: Props) {
  const versionOptions = ["default", ...versions.map((v) => v.id)];

  return (
    <details className="labeling-panel-block" open={Boolean(activeVersion)}>
      <summary className="labeling-panel-block__title">Versions</summary>
      <div className="labeling-panel-block__body">
        <label className="meta">
          Active editing version
          <AppListbox
            value={activeVersion ?? "default"}
            onChange={(v) => onVersionSelect(v === "default" ? null : v)}
            disabled={busy || isPending || !hasEntry}
            options={versionOptions.map((v) => ({ value: v, label: v }))}
            minWidth={140}
          />
        </label>

        {versions.length === 0 ? (
          <p className="meta">No declared versions (implicit default).</p>
        ) : (
          <ul className="labeling-version-meta meta">
            {versions.map((v) => (
              <li key={v.id}>
                <strong>{v.id}</strong>
                {v.ocr ? ` · ${v.ocr}` : " · inherits default image"}
                {v.cond ? ` · cond: ${v.cond}` : ""}
              </li>
            ))}
          </ul>
        )}

        {activeVersion ? (
          <div className="labeling-version-actions">
            <label className="meta">
              cond ({activeVersion})
              <input
                value={editVersionCond}
                onChange={(e) => onEditCondChange(e.target.value)}
              />
            </label>
            <div className="toolbar" style={{ flexWrap: "wrap", gap: "0.35rem" }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={onSaveCond}
              >
                Save cond
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={onSyncRegions}
              >
                Sync from default
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={onBindCanvas}
              >
                Bind canvas image
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={onDeleteVersion}
              >
                Delete version
              </button>
            </div>
          </div>
        ) : null}

        <div className="labeling-add-version">
          <p className="meta">Add version</p>
          <label className="meta">
            id
            <input value={newVersionId} onChange={(e) => onNewIdChange(e.target.value)} />
          </label>
          <label className="meta">
            cond
            <input
              value={newVersionCond}
              onChange={(e) => onNewCondChange(e.target.value)}
              placeholder="heroes.norah.level >= 6"
            />
          </label>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || isPending || !hasEntry}
            onClick={onAddVersion}
          >
            Add version
          </button>
        </div>
      </div>
    </details>
  );
}
