"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import { tip } from "@/components/AppTooltip";
import { PageHeader } from "@/components/PageHeader";
import { Icon } from "@/components/ui/Icon";
import { PageLoading, Spinner } from "@/components/ui/Spinner";
import { ScenarioEditor } from "@/components/edit-dsl/ScenarioEditor";
import { ScenarioTree } from "@/components/edit-dsl/ScenarioTree";
import type { EditorMeta } from "@/components/edit-dsl/StepCard";
import {
  createEditDslFile,
  fetchEditDslCatalog,
  fetchEditDslMeta,
  fetchEditDslProblems,
  fetchEditScenarioFile,
  fetchWikiScopes,
  type EditDslProblem,
} from "@/lib/api";
import type {
  EditableModuleEntry,
  ScenarioFileEntry,
  ScenarioTreeNode,
} from "@/lib/config-pages";
import type { ScenarioDocument } from "@/lib/edit-dsl/dsl";
import type { WikiScope } from "@/lib/wiki";

function resolveQueryScenario(
  files: ScenarioFileEntry[],
  scenarioParam: string | null,
): string | null {
  if (!scenarioParam?.trim()) return null;
  const s = scenarioParam.trim().replace(/\\/g, "/");
  const rel = files.find((f) => f.rel === s);
  if (rel) return rel.rel;
  const byStem = files.find((f) => f.stem === s);
  return byStem?.rel ?? null;
}

const SIDEBAR_OPEN_KEY = "edit-dsl:sidebar-open";

function EditDslPageInner() {
  const searchParams = useSearchParams();
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const scopeParam =
    searchParams.get("scope") ?? searchParams.get("module");
  const [scope, setScope] = useState(scopeParam?.trim() || "all");
  const [files, setFiles] = useState<ScenarioFileEntry[]>([]);
  const [tree, setTree] = useState<ScenarioTreeNode[]>([]);
  const [modules, setModules] = useState<EditableModuleEntry[]>([]);
  const [selectedRel, setSelectedRel] = useState("");
  const [document, setDocument] = useState<ScenarioDocument | null>(null);
  const [meta, setMeta] = useState<EditorMeta | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newModule, setNewModule] = useState("");
  const [newKey, setNewKey] = useState("");
  const [loadKey, setLoadKey] = useState(0);
  const newKeyInputRef = useRef<HTMLInputElement | null>(null);
  // Rendered open on both server and first client paint (hydration-safe),
  // then synced from localStorage.
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [problems, setProblems] = useState<EditDslProblem[]>([]);
  const [problemsOpen, setProblemsOpen] = useState(false);

  const scenarioQuery = searchParams.get("scenario");
  const focusNew = searchParams.get("new") === "1";

  useEffect(() => {
    if (window.localStorage.getItem(SIDEBAR_OPEN_KEY) === "0") {
      setSidebarOpen(false);
    }
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((open) => {
      window.localStorage.setItem(SIDEBAR_OPEN_KEY, open ? "0" : "1");
      return !open;
    });
  }, []);

  // The "new scenario" deep link needs the sidebar visible to focus its input.
  useEffect(() => {
    if (focusNew) setSidebarOpen(true);
  }, [focusNew]);

  useEffect(() => {
    if (!focusNew) return;
    const id = window.setTimeout(() => {
      const el = newKeyInputRef.current;
      if (!el) return;
      el.focus();
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 50);
    return () => window.clearTimeout(id);
  }, [focusNew, newModule]);

  useEffect(() => {
    if (scopeParam?.trim()) setScope(scopeParam.trim());
  }, [scopeParam]);

  const loadCatalog = useCallback(async () => {
    setError(null);
    try {
      const cat = await fetchEditDslCatalog(scope);
      setFiles(cat.files);
      setTree(cat.tree);
      setModules(cat.modules);
      setNewModule((prev) =>
        prev && cat.modules.some((m) => m.key === prev)
          ? prev
          : (cat.modules[0]?.key ?? ""),
      );
      const deep = resolveQueryScenario(cat.files, scenarioQuery);
      if (deep) {
        setSelectedRel(deep);
      } else if (cat.files.length && !cat.files.some((f) => f.rel === selectedRel)) {
        setSelectedRel(cat.files[0].rel);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [scope, selectedRel, scenarioQuery]);

  useEffect(() => {
    fetchWikiScopes().then(setScopes).catch(() => {});
    fetchEditDslMeta()
      .then((m) => setMeta(m))
      .catch(() => {});
  }, []);

  // Re-sweep the catalog after every save (loadKey bumps on save).
  useEffect(() => {
    fetchEditDslProblems()
      .then((r) => setProblems(r.problems))
      .catch(() => {});
  }, [loadKey]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (!selectedRel) {
      setDocument(null);
      return;
    }
    setBusy(true);
    fetchEditScenarioFile(selectedRel)
      .then((f) => setDocument(f.document as ScenarioDocument))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [selectedRel, loadKey]);

  const filteredFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return files;
    return files.filter(
      (f) =>
        f.rel.toLowerCase().includes(q) ||
        f.stem.toLowerCase().includes(q) ||
        f.module.toLowerCase().includes(q),
    );
  }, [files, filter]);

  const selectedFile = useMemo(
    () => files.find((f) => f.rel === selectedRel) ?? null,
    [files, selectedRel],
  );
  const scopeOptions = useMemo(
    () =>
      scopes.length
        ? scopes.map((s) => ({ value: s.key, label: s.label }))
        : [{ value: "all", label: "All" }],
    [scopes],
  );
  const selectedScopeLabel =
    scopeOptions.find((s) => s.value === scope)?.label ?? scope;
  const moduleOptions = useMemo(
    () =>
      modules.map((m) => ({
        value: m.key,
        label: `${m.title} (${m.key})`,
      })),
    [modules],
  );

  async function handleCreate() {
    if (!newKey.trim() || !newModule) return;
    setBusy(true);
    try {
      const r = await createEditDslFile({
        module: newModule,
        file_key: newKey,
        template_rel: selectedRel,
      });
      setMessage(`Created ${r.rel}`);
      setSelectedRel(r.rel);
      await loadCatalog();
      setLoadKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const editorKey = `${selectedRel}::${loadKey}`;

  return (
    <>
      <PageHeader title="DSL editor">
        <div className="edit-dsl-header-metrics">
          <span className="edit-dsl-metric">
            <span className="edit-dsl-metric__label">Scope</span>
            <strong>{selectedScopeLabel}</strong>
          </span>
          <span className="edit-dsl-metric">
            <span className="edit-dsl-metric__label">Scenarios</span>
            <strong>{files.length}</strong>
          </span>
          <span className="edit-dsl-metric">
            <span className="edit-dsl-metric__label">Modules</span>
            <strong>{modules.length}</strong>
          </span>
          {selectedFile ? (
            <span className="edit-dsl-metric edit-dsl-metric--wide">
              <span className="edit-dsl-metric__label">Editing</span>
              <strong>{selectedFile.stem}</strong>
            </span>
          ) : null}
          {problems.length > 0 ? (
            <button
              type="button"
              className="edit-dsl-metric edit-dsl-metric--problems"
              onClick={() => setProblemsOpen((o) => !o)}
              aria-expanded={problemsOpen}
            >
              <span className="edit-dsl-metric__label">Problems</span>
              <strong>{problems.length}</strong>
            </button>
          ) : null}
        </div>
      </PageHeader>

      {problemsOpen && problems.length > 0 ? (
        <div className="panel edit-dsl-problems">
          <div className="edit-dsl-panel-head">
            <div>
              <h2>Problems</h2>
              <p className="meta">
                Unknown regions / scenario keys / exec functions across the whole catalog
              </p>
            </div>
            <button
              type="button"
              className="btn-icon"
              onClick={() => setProblemsOpen(false)}
              aria-label="Close problems list"
            >
              <Icon name="clear" size="sm" />
            </button>
          </div>
          <ul className="edit-dsl-problems__list">
            {problems.map((p, i) => {
              const file = files.find((f) => f.path === p.rel);
              return (
                <li key={`${p.rel}:${p.step}:${i}`}>
                  <button
                    type="button"
                    className="tree-link"
                    disabled={!file}
                    title={file ? p.rel : `${p.rel} — switch scope to All to open`}
                    onClick={() => {
                      if (file) setSelectedRel(file.rel);
                    }}
                  >
                    {file?.stem ?? p.rel}
                  </button>
                  <code>step {p.step}</code>
                  <span className="muted">{p.issue}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <div
        className={
          sidebarOpen
            ? "edit-dsl-layout"
            : "edit-dsl-layout edit-dsl-layout--collapsed"
        }
      >
        {!sidebarOpen ? (
          <aside className="edit-dsl-rail">
            <button
              type="button"
              className="btn-icon"
              onClick={toggleSidebar}
              aria-label="Show scenario list"
              {...tip("Show scenarios")}
            >
              <Icon name="chevron-right" size="sm" />
            </button>
            <span className="edit-dsl-rail__label">Scenarios</span>
            <span className="edit-dsl-rail__count">{files.length}</span>
          </aside>
        ) : (
        <div className="edit-dsl-sidebar-stack">
          <aside className="panel edit-dsl-sidebar">
            <div className="edit-dsl-panel-head">
              <div>
                <h2>Scenarios</h2>
                <p className="meta">{filteredFiles.length} shown</p>
              </div>
              <span className="edit-dsl-panel-head__actions">
                {filter ? (
                  <button
                    type="button"
                    className="btn-icon"
                    onClick={() => setFilter("")}
                    aria-label="Clear scenario filter"
                    title="Clear filter"
                  >
                    <Icon name="clear" size="sm" />
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn-icon"
                  onClick={toggleSidebar}
                  aria-label="Hide scenario list"
                  {...tip("Hide scenarios")}
                >
                  <Icon name="chevron-left" size="sm" />
                </button>
              </span>
            </div>
            <div className="edit-dsl-sidebar-controls">
              <AppListbox
                fullWidth
                label="Module scope"
                value={scope}
                onChange={setScope}
                options={scopeOptions}
              />
              <div className="edit-dsl-search">
                <Icon name="search" size="sm" />
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter scenarios"
                  type="search"
                />
              </div>
            </div>
            <div className="edit-dsl-tree-panel">
              {files.length === 0 ? (
                <div className="edit-dsl-empty">
                  <Icon name="list-empty" size="md" />
                  <p>No editable DSL files for this scope.</p>
                </div>
              ) : filter ? (
                filteredFiles.length ? (
                <ul className="scenario-tree edit-dsl-filter-results">
                  {filteredFiles.map((f) => (
                    <li key={f.rel}>
                      <button
                        type="button"
                        className={
                          selectedRel === f.rel
                            ? "tree-link active"
                            : "tree-link"
                        }
                        onClick={() => setSelectedRel(f.rel)}
                      >
                        {f.stem}
                      </button>
                      <span className="muted"> {f.module}</span>
                    </li>
                  ))}
                </ul>
                ) : (
                  <div className="edit-dsl-empty">
                    <Icon name="search" size="md" />
                    <p>No scenarios match this filter.</p>
                  </div>
                )
              ) : (
                <ScenarioTree
                  nodes={tree}
                  selected={selectedRel}
                  onSelect={setSelectedRel}
                />
              )}
            </div>
          </aside>

          <aside className="panel edit-dsl-create-panel">
            <div className="edit-dsl-panel-head">
              <span className="edit-dsl-panel-icon" aria-hidden>
                <Icon name="edit-dsl" size="sm" />
              </span>
              <h2>New scenario</h2>
            </div>
            <div className="edit-dsl-create-fields">
              <AppListbox
                fullWidth
                label="Module"
                value={newModule}
                onChange={setNewModule}
                options={moduleOptions}
              />
              <label className="edit-dsl-field">
                <span>File key</span>
                <input
                  ref={newKeyInputRef}
                  value={newKey}
                  onChange={(e) => setNewKey(e.target.value)}
                  placeholder="dismiss_popup"
                />
              </label>
              <button
                type="button"
                className="btn-success mt-1 w-full"
                disabled={busy || !newKey.trim() || !newModule}
                onClick={handleCreate}
              >
                Create
              </button>
            </div>
          </aside>
        </div>
        )}

        <main className="panel edit-dsl-main">
          <div className="edit-dsl-active-file">
            <span className="edit-dsl-active-file__icon" aria-hidden>
              <Icon name="edit-dsl" size="sm" />
            </span>
            <div className="edit-dsl-active-file__body">
              <span className="edit-dsl-active-file__eyebrow">Active scenario</span>
              <strong>{selectedFile?.stem || "No file selected"}</strong>
              {selectedRel ? <code>{selectedRel}</code> : null}
            </div>
            {selectedFile ? (
              <span className="edit-dsl-active-file__module">
                {selectedFile.module}
              </span>
            ) : null}
          </div>
          {busy && !document ? (
            <div className="ui-page-loading">
              <Spinner />
              <span className="ui-page-loading__text">Loading scenario…</span>
            </div>
          ) : null}
          {document && meta && selectedRel && (
            <ScenarioEditor
              key={editorKey}
              rel={selectedRel}
              initialDoc={document}
              meta={meta}
              onSaved={() => setLoadKey((k) => k + 1)}
            />
          )}
          {message && <p className="muted">{message}</p>}
          {error && <p className="error-banner">{error}</p>}
        </main>
      </div>
    </>
  );
}

export default function EditDslPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <EditDslPageInner />
    </Suspense>
  );
}
