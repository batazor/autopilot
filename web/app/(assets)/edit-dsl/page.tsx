"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading, Spinner } from "@/components/ui/Spinner";
import { ScenarioEditor } from "@/components/edit-dsl/ScenarioEditor";
import { ScenarioTree } from "@/components/edit-dsl/ScenarioTree";
import type { EditorMeta } from "@/components/edit-dsl/StepCard";
import {
  createEditDslFile,
  fetchEditDslCatalog,
  fetchEditDslMeta,
  fetchEditScenarioFile,
  fetchWikiScopes,
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

  const scenarioQuery = searchParams.get("scenario");
  const focusNew = searchParams.get("new") === "1";

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

  const stem = files.find((f) => f.rel === selectedRel)?.stem ?? "";
  const editorKey = `${selectedRel}::${loadKey}`;

  return (
    <>
      <PageHeader title="DSL editor">
        <p className="muted">
          Edit module scenario YAML under <code>modules/*/scenarios/</code>.{" "}
          <code>drafts/</code> and <code>by_cron/</code> are read-only. Saves validate against
          the DSL schema and back up the previous file under{" "}
          <code>.backups/&lt;timestamp&gt;/</code>.
          {stem && (
            <>
              {" "}
              <Link href={`/debug-run?scenario=${encodeURIComponent(stem)}`}>
                Open in runner
              </Link>
            </>
          )}
        </p>
      </PageHeader>

      <div className="edit-dsl-layout">
        <div className="flex flex-col gap-4 min-w-0">
          <aside className="panel edit-dsl-sidebar">
            <div className="flex flex-col gap-2">
              <AppListbox
                fullWidth
                label="Module scope"
                value={scope}
                onChange={setScope}
                options={[
                  ...(scopes.length
                    ? scopes.map((s) => ({ value: s.key, label: s.label }))
                    : [{ value: "all", label: "All" }]),
                ]}
              />
              <label className="flex flex-col gap-1">
                <span className="text-xs uppercase tracking-wide text-wos-text-muted">
                  Filter
                </span>
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="path, module…"
                  type="search"
                  className="rounded-lg border border-wos-border-subtle bg-wos-input px-2.5 py-1.5 text-sm text-wos-text focus:border-sky-400/70 focus:outline-none focus:ring-2 focus:ring-sky-400/25"
                />
              </label>
            </div>
            <div className="mt-3">
              {files.length === 0 ? (
                <p className="muted">No editable DSL files for this scope.</p>
              ) : filter ? (
                <ul className="scenario-tree">
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
                <ScenarioTree
                  nodes={tree}
                  selected={selectedRel}
                  onSelect={setSelectedRel}
                />
              )}
            </div>
          </aside>

          <aside className="panel">
            <div className="mb-2 flex items-center gap-2">
              <span
                className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300"
                aria-hidden
              >
                <svg
                  className="ui-icon ui-icon--sm"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M12 5v14" />
                  <path d="M5 12h14" />
                </svg>
              </span>
              <h3 className="m-0 text-base font-semibold text-wos-text">
                New scenario
              </h3>
            </div>
            <p className="muted mt-0 mb-3 text-xs">
              Pick a module and a short file key. A blank DSL skeleton will be
              created at <code>scenarios/&lt;key&gt;.yaml</code>.
            </p>
            <div className="flex flex-col gap-2">
              <AppListbox
                fullWidth
                label="Module"
                value={newModule}
                onChange={setNewModule}
                options={modules.map((m) => ({
                  value: m.key,
                  label: `${m.title} (${m.key})`,
                }))}
              />
              <label className="flex flex-col gap-1">
                <span className="text-xs uppercase tracking-wide text-wos-text-muted">
                  File key
                </span>
                <input
                  ref={newKeyInputRef}
                  value={newKey}
                  onChange={(e) => setNewKey(e.target.value)}
                  placeholder="dismiss_popup"
                  className="rounded-lg border border-wos-border-subtle bg-wos-input px-2.5 py-1.5 text-sm text-wos-text focus:border-emerald-400/70 focus:outline-none focus:ring-2 focus:ring-emerald-400/25"
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

        <main className="panel edit-dsl-main">
          {selectedRel && (
            <p className="muted">
              <code>{selectedRel}</code>
            </p>
          )}
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
