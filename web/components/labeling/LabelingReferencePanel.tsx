"use client";

import { useMemo, useState } from "react";
import { AppListbox } from "@/components/headless";
import { LabelingReferenceTree } from "@/components/labeling/LabelingReferenceTree";
import { filterReferences, referenceSelectLabel } from "@/lib/labeling-utils";
import type { LabelingReferenceMeta } from "@/lib/types";

type Props = {
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
  const [groupByScreenId, setGroupByScreenId] = useState(true);
  const filteredRefs = useMemo(() => {
    const list = filterReferences(refs, filter);
    if (refRel && !list.some((r) => r.rel === refRel)) {
      const cur = refs.find((r) => r.rel === refRel);
      if (cur) return [cur, ...list];
    }
    return list;
  }, [refs, filter, refRel]);

  const selectOptions = useMemo(() => {
    const sorted = [...filteredRefs].sort((a, b) =>
      referenceSelectLabel(a).localeCompare(referenceSelectLabel(b), undefined, {
        sensitivity: "base",
      }),
    );
    return sorted.map((r) => ({
      value: r.rel,
      label: referenceSelectLabel(r),
    }));
  }, [filteredRefs]);

  return (
    <details className="labeling-panel-block" open>
      <summary className="labeling-panel-block__title">Reference image</summary>
      <div className="labeling-panel-block__body labeling-ref-picker">
        <label className="meta">
          Filter
          <input
            type="search"
            className="labeling-search"
            placeholder="Search by name, screen, path…"
            value={filter}
            onChange={(e) => onFilterChange(e.target.value)}
          />
        </label>

        <AppListbox
          fullWidth
          label="Reference PNG"
          value={refRel}
          onChange={onSelect}
          options={selectOptions}
          placeholder={refs.length ? "Select reference…" : "No references in scope"}
          disabled={busy || !selectOptions.length}
          title={refRel || undefined}
        />

        <LabelingReferenceTree
          refs={filteredRefs}
          refRel={refRel}
          groupByScreenId={groupByScreenId}
          onGroupByScreenIdChange={setGroupByScreenId}
          onSelect={onSelect}
          disabled={busy}
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
