"use client";

import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import { useMemo, useState } from "react";
import type { LabelingImportConflict } from "@/lib/types";

type Region = Record<string, unknown>;

type Props = {
  open: boolean;
  conflict: LabelingImportConflict;
  /** Regions from the imported bundle (area.json shape, keyed by ``name``). */
  incomingRegions: Region[];
  incomingImageUrl: string;
  existingImageUrl: string;
  busy: boolean;
  onCancel: () => void;
  onApply: (regions: Region[], useIncomingImage: boolean) => void;
};

const byName = (regions: Region[]): Map<string, Region> => {
  const m = new Map<string, Region>();
  for (const r of regions) {
    const name = String(r.name ?? "");
    if (name) m.set(name, r);
  }
  return m;
};

/**
 * Resolve a screen-label import conflict: pick which regions to keep on a name-by-name
 * diff (added / removed / changed), and which reference screenshot to publish. The assembled
 * region set + image choice are handed back to the page, which calls the apply endpoint.
 */
export function LabelingImportConflictDialog({
  open,
  conflict,
  incomingRegions,
  incomingImageUrl,
  existingImageUrl,
  busy,
  onCancel,
  onApply,
}: Props) {
  const { diff } = conflict;
  const incoming = useMemo(() => byName(incomingRegions), [incomingRegions]);
  const existing = useMemo(
    () => byName(conflict.existing_regions),
    [conflict.existing_regions],
  );

  // changed: which side to take (default incoming). added: include? (default yes).
  // removed: keep the existing region the bundle dropped? (default yes — non-destructive).
  const [changedChoice, setChangedChoice] = useState<Record<string, "incoming" | "existing">>(
    () => Object.fromEntries(diff.changed.map((n) => [n, "incoming" as const])),
  );
  const [addInclude, setAddInclude] = useState<Record<string, boolean>>(
    () => Object.fromEntries(diff.added.map((n) => [n, true])),
  );
  const [keepRemoved, setKeepRemoved] = useState<Record<string, boolean>>(
    () => Object.fromEntries(diff.removed.map((n) => [n, true])),
  );
  const [useIncomingImage, setUseIncomingImage] = useState(false);

  const assemble = (): Region[] => {
    const out: Region[] = [];
    for (const n of diff.unchanged) {
      const r = incoming.get(n) ?? existing.get(n);
      if (r) out.push(r);
    }
    for (const n of diff.changed) {
      const r = changedChoice[n] === "existing" ? existing.get(n) : incoming.get(n);
      if (r) out.push(r);
    }
    for (const n of diff.added) {
      if (addInclude[n]) {
        const r = incoming.get(n);
        if (r) out.push(r);
      }
    }
    for (const n of diff.removed) {
      if (keepRemoved[n]) {
        const r = existing.get(n);
        if (r) out.push(r);
      }
    }
    return out;
  };

  return (
    <Dialog open={open} onClose={onCancel} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel labeling-conflict__panel">
          <DialogTitle className="headless-dialog__title">
            Screen already labeled — resolve conflict
          </DialogTitle>
          <div className="headless-dialog__body">
            <p className="meta">
              Matched existing screen{" "}
              <code>{conflict.existing_screen_id || conflict.existing_ref}</code> by{" "}
              <strong>{conflict.matched_by}</strong>. Choose what to keep, then apply.
            </p>

            <fieldset className="labeling-conflict__images">
              <legend>Reference screenshot</legend>
              <label>
                <input
                  type="radio"
                  name="conflict-image"
                  checked={!useIncomingImage}
                  onChange={() => setUseIncomingImage(false)}
                />{" "}
                Keep current screenshot
                {existingImageUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={existingImageUrl} alt="existing screenshot" width={90} />
                ) : null}
              </label>
              <label>
                <input
                  type="radio"
                  name="conflict-image"
                  checked={useIncomingImage}
                  onChange={() => setUseIncomingImage(true)}
                />{" "}
                Use bundle screenshot
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={incomingImageUrl} alt="imported screenshot" width={90} />
              </label>
            </fieldset>

            {diff.changed.length > 0 ? (
              <div className="labeling-conflict__group">
                <h4>Changed ({diff.changed.length})</h4>
                {diff.changed.map((n) => (
                  <div key={n} className="labeling-conflict__row">
                    <code>{n}</code>
                    <label>
                      <input
                        type="radio"
                        name={`chg-${n}`}
                        checked={changedChoice[n] === "incoming"}
                        onChange={() =>
                          setChangedChoice((c) => ({ ...c, [n]: "incoming" }))
                        }
                      />{" "}
                      imported
                    </label>
                    <label>
                      <input
                        type="radio"
                        name={`chg-${n}`}
                        checked={changedChoice[n] === "existing"}
                        onChange={() =>
                          setChangedChoice((c) => ({ ...c, [n]: "existing" }))
                        }
                      />{" "}
                      existing
                    </label>
                  </div>
                ))}
              </div>
            ) : null}

            {diff.added.length > 0 ? (
              <div className="labeling-conflict__group">
                <h4>New in bundle ({diff.added.length})</h4>
                {diff.added.map((n) => (
                  <label key={n} className="labeling-conflict__row">
                    <input
                      type="checkbox"
                      checked={addInclude[n] ?? true}
                      onChange={(e) =>
                        setAddInclude((c) => ({ ...c, [n]: e.target.checked }))
                      }
                    />
                    <code>{n}</code> add
                  </label>
                ))}
              </div>
            ) : null}

            {diff.removed.length > 0 ? (
              <div className="labeling-conflict__group">
                <h4>Only in existing ({diff.removed.length})</h4>
                {diff.removed.map((n) => (
                  <label key={n} className="labeling-conflict__row">
                    <input
                      type="checkbox"
                      checked={keepRemoved[n] ?? true}
                      onChange={(e) =>
                        setKeepRemoved((c) => ({ ...c, [n]: e.target.checked }))
                      }
                    />
                    <code>{n}</code> keep (bundle dropped it)
                  </label>
                ))}
              </div>
            ) : null}

            {diff.unchanged.length > 0 ? (
              <p className="meta">{diff.unchanged.length} region(s) unchanged.</p>
            ) : null}
          </div>
          <div className="headless-dialog__actions">
            <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => onApply(assemble(), useIncomingImage)}
            >
              Apply &amp; save
            </button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
