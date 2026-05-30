"use client";

import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import { useEffect, useState } from "react";
import { AppListbox, AppSwitch } from "@/components/headless";
import { Icon } from "@/components/ui/Icon";
import { createModule } from "@/lib/api";
import type { ModuleRow } from "@/lib/config-pages";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (row: ModuleRow) => void;
  onError: (message: string) => void;
};

const PARENT_OPTIONS = [
  { value: "", label: "(root) — modules/<id>/" },
  { value: "core", label: "core — modules/core/<id>/" },
  { value: "deals", label: "deals — modules/deals/<id>/" },
  { value: "alliance", label: "alliance — modules/alliance/<id>/" },
  { value: "events", label: "events — modules/events/<id>/" },
];

const ID_PATTERN = /^[a-z][a-z0-9_]*$/;

export function NewModuleDialog({ open, onClose, onCreated, onError }: Props) {
  const [moduleId, setModuleId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [parent, setParent] = useState("");
  const [wiki, setWiki] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setModuleId("");
    setTitle("");
    setDescription("");
    setParent("");
    setWiki(false);
    setBusy(false);
  }, [open]);

  const idValid = ID_PATTERN.test(moduleId.trim());
  const titleValid = title.trim().length > 0;
  const canSubmit = !busy && idValid && titleValid;
  const cleanId = moduleId.trim();
  const locationPreview = `modules/${parent ? `${parent}/` : ""}${
    cleanId || "<id>"
  }/`;

  const handleSubmit = async () => {
    setBusy(true);
    try {
      const row = await createModule({
        id: moduleId.trim(),
        title: title.trim(),
        description: description.trim(),
        parent,
        wiki,
      });
      onCreated(row);
      onClose();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel module-create-dialog">
          <div className="module-create-dialog__header">
            <span className="module-create-dialog__icon">
              <Icon name="modules" size="md" />
            </span>
            <div>
              <DialogTitle className="headless-dialog__title">
                New module
              </DialogTitle>
              <p className="meta">
                Create a manifest, scenario directory, and optional wiki entry.
              </p>
            </div>
          </div>
          <div className="headless-dialog__body">
            <div className="module-create-dialog__preview">
              <span>Target</span>
              <code>{locationPreview}</code>
            </div>
            <div className="module-create-dialog__fields">
              <label className="module-create-dialog__field">
                <span>
                  ID <em className="muted">(lowercase, digits, underscores)</em>
                </span>
                <input
                  type="text"
                  value={moduleId}
                  onChange={(e) => setModuleId(e.target.value)}
                  placeholder="e.g. my_feature"
                  className="module-create-dialog__input"
                  autoFocus
                />
                {moduleId && !idValid ? (
                  <em className="module-create-dialog__hint">
                    Must start with a lowercase letter; only a-z, 0-9, _ allowed.
                  </em>
                ) : null}
              </label>

              <label className="module-create-dialog__field">
                <span>Title</span>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. My Feature"
                  className="module-create-dialog__input"
                />
              </label>

              <label className="module-create-dialog__field">
                <span>Description</span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What this module automates…"
                  className="module-create-dialog__input"
                  rows={2}
                />
              </label>

              <AppListbox
                label="Parent"
                value={parent}
                onChange={setParent}
                options={PARENT_OPTIONS}
              />

              <div className="module-create-dialog__switch-row">
                <AppSwitch
                  checked={wiki}
                  onChange={setWiki}
                  label="Include in wiki"
                  inline
                />
                <span>Expose this module in generated documentation.</span>
              </div>
            </div>
          </div>
          <div className="headless-dialog__actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canSubmit}
              onClick={() => void handleSubmit()}
            >
              {busy ? "Creating…" : "Create"}
            </button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
