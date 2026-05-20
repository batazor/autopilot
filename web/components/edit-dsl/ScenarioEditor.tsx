"use client";

import { useCallback, useEffect, useState } from "react";
import { saveEditScenarioDocument, validateEditScenarioDocument } from "@/lib/api";
import {
  cloneDocument,
  ensureStepsList,
  type ScenarioDocument,
} from "@/lib/edit-dsl/dsl";
import { ScenarioHeaderForm } from "./ScenarioHeaderForm";
import { StepsList } from "./StepsList";
import type { EditorMeta } from "./StepCard";

type Props = {
  rel: string;
  initialDoc: ScenarioDocument;
  meta: EditorMeta;
  onSaved: () => void;
};

export function ScenarioEditor({ rel, initialDoc, meta, onSaved }: Props) {
  const [doc, setDoc] = useState<ScenarioDocument>(() => cloneDocument(initialDoc));
  const [dirty, setDirty] = useState(false);
  const [valid, setValid] = useState(true);
  const [validationError, setValidationError] = useState("");
  const [yamlPreview, setYamlPreview] = useState("");
  const [showYaml, setShowYaml] = useState(false);
  const [collisions, setCollisions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDoc(cloneDocument(initialDoc));
    setDirty(false);
    setMessage(null);
    setError(null);
  }, [rel, initialDoc]);

  const runValidate = useCallback(async (d: ScenarioDocument) => {
    const r = await validateEditScenarioDocument(d);
    setValid(r.valid);
    setValidationError(r.error);
    setYamlPreview(r.preview);
    return r;
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      runValidate(doc).catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [doc, runValidate]);

  const updateDoc = (next: ScenarioDocument) => {
    setDoc(next);
    setDirty(true);
  };

  const nameValue = String(doc.name ?? "").trim();
  const saveDisabled =
    busy || !valid || !nameValue || collisions.length > 0 || !dirty;

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      const r = await runValidate(doc);
      if (!r.valid) return;
      await saveEditScenarioDocument(rel, doc);
      setMessage(`Saved ${rel} (backup written)`);
      setDirty(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const steps = ensureStepsList(doc);

  return (
    <div className="edit-scenario-editor">
      <ScenarioHeaderForm
        doc={doc}
        rel={rel}
        meta={meta}
        collisions={collisions}
        onCollisionsChange={setCollisions}
        onChange={updateDoc}
      />

      <h3>Steps</h3>
      <StepsList
        steps={steps}
        parentPath={[]}
        depth={0}
        meta={meta}
        onStepsChange={(s) => updateDoc({ ...doc, steps: s })}
      />

      <hr />
      <div className="toolbar">
        <button
          type="button"
          className="btn-primary"
          disabled={saveDisabled}
          onClick={handleSave}
        >
          Save
        </button>
        {!valid && <span className="error-banner">Schema errors — fix before saving.</span>}
        {valid && nameValue && !collisions.length && (
          <span className="muted">Schema OK</span>
        )}
      </div>
      {!valid && validationError && (
        <details className="edit-scenario-validation">
          <summary>Validation details</summary>
          <pre className="code-block">{validationError}</pre>
        </details>
      )}

      <details
        className="edit-scenario-yaml-preview"
        open={showYaml}
        onToggle={(e) => setShowYaml((e.target as HTMLDetailsElement).open)}
      >
        <summary>YAML preview</summary>
        <pre className="code-block">{yamlPreview || "(validate to preview)"}</pre>
      </details>

      {message && <p className="muted">{message}</p>}
      {error && <p className="error-banner">{error}</p>}
    </div>
  );
}
