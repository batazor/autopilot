"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AppTabs } from "@/components/headless";
import { Icon } from "@/components/ui/Icon";
import {
  saveEditScenarioDocument,
  saveEditScenarioFile,
  validateEditScenarioDocument,
  validateEditScenarioYaml,
} from "@/lib/api";
import {
  cloneDocument,
  ensureStepsList,
  type ScenarioDocument,
} from "@/lib/edit-dsl/dsl";
import { ScenarioFlow } from "./ScenarioFlow";
import type { EditorMeta } from "./StepCard";
import {
  YamlMonacoEditor,
  parseYamlErrorLocation,
  type YamlMarker,
} from "./YamlMonacoEditor";

type Props = {
  rel: string;
  initialDoc: ScenarioDocument;
  meta: EditorMeta;
  onSaved: () => void;
};

type EditorTab = "flow" | "yaml";
type StatusTone = "ok" | "warn" | "danger" | "busy";

/**
 * Module-level ref so the active editor tab survives `key={editorKey}`
 * remounts when the user switches scenarios in the sidebar.
 */
const persistedTabRef: { current: EditorTab } = { current: "flow" };

export function ScenarioEditor({ rel, initialDoc, meta, onSaved }: Props) {
  const [doc, setDoc] = useState<ScenarioDocument>(() => cloneDocument(initialDoc));
  const [dirty, setDirty] = useState(false);
  const [valid, setValid] = useState(true);
  const [validationError, setValidationError] = useState("");
  const [yamlPreview, setYamlPreview] = useState("");
  const [collisions, setCollisions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTabState] = useState<EditorTab>(persistedTabRef.current);
  const setTab = useCallback((t: EditorTab) => {
    persistedTabRef.current = t;
    setTabState(t);
  }, []);
  const regionMeta = useMemo(
    () => ({ regions: meta.regions, region_refs: meta.region_refs }),
    [meta.regions, meta.region_refs],
  );
  const [yamlDraft, setYamlDraft] = useState("");
  const [yamlDirty, setYamlDirty] = useState(false);
  const [yamlValid, setYamlValid] = useState(true);
  const [yamlError, setYamlError] = useState("");
  const [yamlMarkers, setYamlMarkers] = useState<YamlMarker[]>([]);

  useEffect(() => {
    setDoc(cloneDocument(initialDoc));
    setDirty(false);
    setMessage(null);
    setError(null);
    setYamlDirty(false);
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

  // Refill YAML draft from preview when entering the tab (unless user has unsaved edits).
  useEffect(() => {
    if (tab === "yaml" && !yamlDirty) {
      setYamlDraft(yamlPreview);
      setYamlValid(true);
      setYamlError("");
      setYamlMarkers([]);
    }
  }, [tab, yamlPreview, yamlDirty]);

  const updateDoc = (next: ScenarioDocument) => {
    setDoc(next);
    setDirty(true);
  };

  const nameValue = String(doc.name ?? "").trim();
  const saveDisabled =
    busy || !valid || !nameValue || collisions.length > 0 || !dirty;
  const docStatus = busy
    ? { label: "Saving", tone: "busy" as StatusTone }
    : !valid
      ? { label: "Schema errors", tone: "danger" as StatusTone }
      : collisions.length > 0
        ? { label: "Name conflict", tone: "danger" as StatusTone }
        : dirty
          ? { label: "Unsaved", tone: "warn" as StatusTone }
          : { label: "Saved", tone: "ok" as StatusTone };
  const yamlStatus = busy
    ? { label: "Saving", tone: "busy" as StatusTone }
    : !yamlValid
      ? { label: "YAML errors", tone: "danger" as StatusTone }
      : yamlDirty
        ? { label: "Unsaved YAML", tone: "warn" as StatusTone }
        : { label: "Synced", tone: "ok" as StatusTone };
  const activeStatus = tab === "yaml" ? yamlStatus : docStatus;
  const activeSaveDisabled =
    tab === "yaml" ? busy || !yamlDirty || !yamlValid : saveDisabled;
  const activeSaveLabel =
    tab === "yaml"
      ? busy
        ? "Saving YAML..."
        : "Save YAML"
      : busy
        ? "Saving..."
        : "Save";

  async function handleSaveDoc() {
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

  const runYamlValidate = useCallback(async (text: string) => {
    try {
      const r = await validateEditScenarioYaml(text);
      setYamlValid(r.valid);
      setYamlError(r.error);
      if (!r.valid && r.error) {
        const loc = parseYamlErrorLocation(r.error);
        setYamlMarkers([
          {
            message: r.error,
            line: loc.line,
            column: loc.column,
            severity: "error",
          },
        ]);
      } else {
        setYamlMarkers([]);
      }
      return r;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setYamlValid(false);
      setYamlError(msg);
      setYamlMarkers([{ message: msg, line: 1, severity: "error" }]);
      return { valid: false, error: msg, preview: "" };
    }
  }, []);

  async function handleValidateYaml() {
    return runYamlValidate(yamlDraft);
  }

  // Debounced live validation as the user types.
  useEffect(() => {
    if (tab !== "yaml") return;
    if (!yamlDirty) return;
    const t = setTimeout(() => {
      runYamlValidate(yamlDraft).catch(() => {});
    }, 500);
    return () => clearTimeout(t);
  }, [tab, yamlDraft, yamlDirty, runYamlValidate]);

  async function handleSaveYaml() {
    setBusy(true);
    setError(null);
    try {
      const r = await handleValidateYaml();
      if (!r.valid) return;
      await saveEditScenarioFile(rel, yamlDraft);
      setMessage(`Saved ${rel} (backup written)`);
      setYamlDirty(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const steps = ensureStepsList(doc);
  const stepCount = steps.length;
  const enabled = Boolean(doc.enabled);
  const node = String(doc.node ?? "").trim();

  const handleActiveSave = () => {
    if (tab === "yaml") {
      void handleSaveYaml();
    } else {
      void handleSaveDoc();
    }
  };

  const editorToolbar = (
    <div className="edit-scenario-editor-toolbar">
      <span
        className={`edit-scenario-status edit-scenario-status--${activeStatus.tone}`}
      >
        <span className="edit-scenario-status__dot" aria-hidden />
        {activeStatus.label}
      </span>
      <button
        type="button"
        className="btn-success"
        disabled={activeSaveDisabled}
        onClick={handleActiveSave}
      >
        {activeSaveLabel}
      </button>
    </div>
  );

  const flowPanel = (
    <>
      <ScenarioFlow
        doc={doc}
        rel={rel}
        meta={meta}
        collisions={collisions}
        onCollisionsChange={setCollisions}
        onChange={updateDoc}
      />
      {!valid && (
        <div className="edit-scenario-validation-row">
          <span className="error-banner">Schema errors — fix before saving.</span>
        </div>
      )}
      {!valid && validationError && (
        <details className="edit-scenario-validation">
          <summary>Validation details</summary>
          <pre className="code-block">{validationError}</pre>
        </details>
      )}
    </>
  );

  const yamlPanel = (
    <div className="edit-scenario-yaml-tab">
      <YamlMonacoEditor
        value={yamlDraft}
        onChange={(v) => {
          setYamlDraft(v);
          setYamlDirty(true);
        }}
        markers={yamlMarkers}
        scenarioTimeline
        regionMeta={regionMeta}
      />
      <div className="toolbar">
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || !yamlDirty}
          onClick={handleValidateYaml}
        >
          Validate
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || !yamlDirty}
          onClick={() => {
            setYamlDraft(yamlPreview);
            setYamlDirty(false);
            setYamlValid(true);
            setYamlError("");
            setYamlMarkers([]);
          }}
        >
          Reset
        </button>
        {!yamlValid && (
          <span className="error-banner">YAML invalid — fix before saving.</span>
        )}
        {yamlValid && yamlDirty && (
          <span className="edit-scenario-status edit-scenario-status--warn">
            <span className="edit-scenario-status__dot" aria-hidden />
            Unsaved changes
          </span>
        )}
      </div>
      {!yamlValid && yamlError && (
        <details className="edit-scenario-validation">
          <summary>Validation details</summary>
          <pre className="code-block">{yamlError}</pre>
        </details>
      )}
    </div>
  );

  return (
    <div className="edit-scenario-editor">
      <div className="edit-scenario-summary-bar">
        <span className="edit-scenario-summary-bar__icon" aria-hidden>
          <Icon name={enabled ? "check" : "warning"} size="sm" />
        </span>
        <span className="edit-scenario-summary-bar__item">
          <span>Name</span>
          <strong>{nameValue || "Untitled scenario"}</strong>
        </span>
        <span className="edit-scenario-summary-bar__item">
          <span>Node</span>
          <strong>{node || "anywhere"}</strong>
        </span>
        <span className="edit-scenario-summary-bar__item">
          <span>Steps</span>
          <strong>{stepCount}</strong>
        </span>
        <span className="edit-scenario-summary-bar__item">
          <span>State</span>
          <strong>{enabled ? "enabled" : "disabled"}</strong>
        </span>
      </div>
      <AppTabs
        selectedKey={tab}
        onChange={(k) => setTab(k as EditorTab)}
        tabs={[
          { key: "flow", label: dirty ? "Flow *" : "Flow", panel: flowPanel },
          { key: "yaml", label: yamlDirty ? "YAML *" : "YAML", panel: yamlPanel },
        ]}
        afterTabs={editorToolbar}
        listClassName="edit-scenario-tabs"
      />

      {message && <p className="muted">{message}</p>}
      {error && <p className="error-banner">{error}</p>}
    </div>
  );
}
