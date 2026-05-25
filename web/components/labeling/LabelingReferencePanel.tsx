"use client";

import { useMemo } from "react";
import { AppListbox } from "@/components/headless";
import { LabelingModuleCombobox } from "@/components/labeling/LabelingModuleCombobox";
import {
  filterReferences,
  isPendingCapture,
  referenceSelectLabel,
  syntheticReferenceMeta,
} from "@/lib/labeling-utils";
import type { LabelingReferenceMeta, LabelingScopeOption } from "@/lib/types";

type Props = {
  scopes: LabelingScopeOption[];
  moduleScope: string;
  onModuleChange: (key: string) => void;
  refs: LabelingReferenceMeta[];
  refRel: string;
  filter: string;
  onFilterChange: (q: string) => void;
  onSelect: (rel: string) => void;
  basename: string;
  onBasenameChange: (v: string) => void;
  isPending: boolean;
  busy: boolean;
  onPromoteOrRename: () => void;
};

export function LabelingReferencePanel({
  scopes,
  moduleScope,
  onModuleChange,
  refs,
  refRel,
  filter,
  onFilterChange,
  onSelect,
  basename,
  onBasenameChange,
  isPending,
  busy,
  onPromoteOrRename,
}: Props) {
  const filteredRefs = useMemo(() => {
    const list = filterReferences(refs, filter);
    if (refRel && !list.some((r) => r.rel === refRel)) {
      const cur = refs.find((r) => r.rel === refRel);
      if (cur) return [cur, ...list];
      if (isPendingCapture(refRel)) return [syntheticReferenceMeta(refRel), ...list];
    }
    return list;
  }, [refs, filter, refRel]);

  const imageOptions = useMemo(() => {
    const sorted = [...filteredRefs].sort((a, b) =>
      referenceSelectLabel(a).localeCompare(referenceSelectLabel(b), undefined, {
        sensitivity: "base",
      }),
    );
    const options = sorted.map((r) => ({
      value: r.rel,
      label: referenceSelectLabel(r),
    }));
    if (refRel && !options.some((o) => o.value === refRel)) {
      const syn = syntheticReferenceMeta(refRel);
      return [
        { value: syn.rel, label: referenceSelectLabel(syn) },
        ...options,
      ];
    }
    return options;
  }, [filteredRefs, refRel]);

  return (
    <details className="labeling-panel-block" open>
      <summary className="labeling-panel-block__title">Reference image</summary>
      <div className="labeling-panel-block__body labeling-ref-picker">
        <LabelingModuleCombobox
          scopes={scopes}
          scope={moduleScope}
          onChange={onModuleChange}
          busy={busy}
        />

        <label className="meta">
          Filter images
          <input
            type="search"
            className="labeling-search"
            placeholder="Name, screen, path…"
            value={filter}
            onChange={(e) => onFilterChange(e.target.value)}
          />
        </label>

        <AppListbox
          fullWidth
          label="Reference PNG"
          value={refRel}
          onChange={onSelect}
          options={imageOptions}
          placeholder={
            refs.length
              ? "Select reference PNG…"
              : "No references in this module"
          }
          disabled={busy || (!refRel && !imageOptions.length)}
          title={refRel || undefined}
        />

        {refRel ? (
          <p className="meta labeling-ref-picker__path">
            <code>{refRel}</code>
          </p>
        ) : null}

        <div className="labeling-basename">
          <span className="meta">Basename</span>
          <div className="labeling-basename__row">
            <input
              value={basename}
              onChange={(e) => onBasenameChange(e.target.value)}
              placeholder={isPending ? "publish from temporal/" : "without .png"}
              disabled={busy}
            />
            <button
              type="button"
              className="btn-primary"
              disabled={!refRel || !basename.trim() || busy}
              onClick={onPromoteOrRename}
              title={isPending ? "Promote to references/" : "Rename on disk"}
            >
              {isPending ? "Promote" : "Rename"}
            </button>
          </div>
        </div>
      </div>
    </details>
  );
}
