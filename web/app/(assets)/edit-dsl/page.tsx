"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AppListbox } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading, Spinner } from "@/components/ui/Spinner";
import { ScenarioEditor } from "@/components/edit-dsl/ScenarioEditor";
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

function TreePicker({
  nodes,
  selected,
  onSelect,
}: {
  nodes: ScenarioTreeNode[];
  selected: string;
  onSelect: (rel: string) => void;
}) {
  return (
    <ul className="scenario-tree">
      {nodes.map((n) =>
        n.is_dir ? (
          <li key={n.value}>
            <span className="muted">{n.title}</span>
            {n.children && (
              <TreePicker nodes={n.children} selected={selected} onSelect={onSelect} />
            )}
          </li>
        ) : (
          <li key={n.value}>
            <button
              type="button"
              className={selected === n.value ? "tree-link active" : "tree-link"}
              onClick={() => onSelect(n.value)}
            >
              {n.title}
            </button>
          </li>
        ),
      )}
    </ul>
  );
}

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

  const scenarioQuery = searchParams.get("scenario");

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
        <aside className="panel edit-dsl-sidebar">
          <div className="toolbar" style={{ flexDirection: "column", alignItems: "stretch" }}>
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
            <label>
              Filter
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="path, module…"
              />
            </label>
          </div>
          {files.length === 0 ? (
            <p className="muted">No editable DSL files for this scope.</p>
          ) : filter ? (
            <ul className="scenario-tree">
              {filteredFiles.map((f) => (
                <li key={f.rel}>
                  <button
                    type="button"
                    className={selectedRel === f.rel ? "tree-link active" : "tree-link"}
                    onClick={() => setSelectedRel(f.rel)}
                  >
                    {f.stem}
                  </button>
                  <span className="muted"> {f.module}</span>
                </li>
              ))}
            </ul>
          ) : (
            <TreePicker nodes={tree} selected={selectedRel} onSelect={setSelectedRel} />
          )}

          <hr />
          <h3>New scenario</h3>
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
          <label>
            File key
            <input
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="dismiss_popup"
            />
          </label>
          <button type="button" className="btn-secondary" disabled={busy} onClick={handleCreate}>
            Create
          </button>
        </aside>

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
