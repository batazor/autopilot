"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { KonvaImageEditor } from "@/components/konva/KonvaImageEditor";
import { LabelingCard } from "@/components/labeling/LabelingCard";
import { LabelingReferencePanel } from "@/components/labeling/LabelingReferencePanel";
import { LabelingRegionsPanel } from "@/components/labeling/LabelingRegionsPanel";
import { LabelingStaleCropsBanner } from "@/components/labeling/LabelingStaleCropsBanner";
import { LabelingVersionsPanel } from "@/components/labeling/LabelingVersionsPanel";
import { LabelingWorkflowStrip } from "@/components/labeling/LabelingWorkflowStrip";
import { AppConfirmDialog, AppListbox } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { useInstances } from "@/lib/hooks";
import { instanceSelectPlaceholder } from "@/lib/fleet-select";
import {
  addLabelingVersion,
  bindLabelingVersionOcr,
  captureLabelingScreenshot,
  deleteLabelingVersion,
  discardLabelingCapture,
  exportLabelingCrops,
  fetchLabelingDocument,
  fetchLabelingReferences,
  fetchLabelingScopes,
  fetchLabelingScreenIds,
  fetchLabelingStaleCrops,
  fetchRoboflowStatus,
  importLabelingPng,
  labelingImageUrl,
  promoteLabelingReference,
  refreshLabelingReference,
  renameLabelingReference,
  saveLabelingRegions,
  suggestLabelingVersionId,
  syncLabelingVersionRegions,
  updateLabelingVersionCond,
  uploadLabelingToRoboflow,
} from "@/lib/api";
import type { EditorRegion } from "@/lib/bbox";
import {
  apiToEditorRegions,
  editorToApiRegions,
  isPendingCapture,
  inferScopeFromRef,
  labelingWorkflowSteps,
} from "@/lib/labeling-utils";
import type {
  LabelingDocument,
  LabelingReferenceMeta,
  LabelingScopeOption,
  LabelingStaleCrop,
  RoboflowStatus,
} from "@/lib/types";

function LabelingPageInner() {
  const { showSuccess } = useFeedback();
  const params = useSearchParams();
  const router = useRouter();
  const versionParam = params.get("version") ?? "";
  const moduleParam = params.get("module") ?? "";

  const [scopes, setScopes] = useState<LabelingScopeOption[]>([]);
  const [moduleScope, setModuleScope] = useState(moduleParam || "core");
  const {
    instances,
    instanceId,
    setInstanceId,
    loading: instancesLoading,
    error: instancesError,
  } = useInstances();
  const [refs, setRefs] = useState<LabelingReferenceMeta[]>([]);
  const [refRel, setRefRel] = useState(params.get("ref") ?? "");
  const [doc, setDoc] = useState<LabelingDocument | null>(null);
  const [screenId, setScreenId] = useState("");
  const [regions, setRegions] = useState<EditorRegion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [screenDirty, setScreenDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refFilter, setRefFilter] = useState("");
  const [imageNonce, setImageNonce] = useState(0);
  const [basename, setBasename] = useState("");
  const [roboflow, setRoboflow] = useState<RoboflowStatus | null>(null);
  const [newVersionId, setNewVersionId] = useState("v2");
  const [newVersionCond, setNewVersionCond] = useState("");
  const [editVersionCond, setEditVersionCond] = useState("");
  const [staleCrops, setStaleCrops] = useState<{
    count: number;
    stale: LabelingStaleCrop[];
  }>({ count: 0, stale: [] });
  const [refreshPending, setRefreshPending] = useState(false);
  const [confirmAction, setConfirmAction] = useState<
    "discard" | "delete-version" | null
  >(null);
  const [screenIdOptions, setScreenIdOptions] = useState<string[]>([]);

  const activeVersion = versionParam.trim() || null;
  const displayRef = doc?.display_ref ?? refRel;
  const isPending = doc?.is_pending ?? isPendingCapture(refRel);
  const anyDirty = dirty || screenDirty;

  const screenNodeListboxOptions = useMemo(() => {
    const ids = [...screenIdOptions];
    const cur = screenId.trim();
    if (cur && !ids.includes(cur)) {
      ids.push(cur);
      ids.sort((a, b) => a.localeCompare(b));
    }
    return ids.map((sid) => ({
      value: sid,
      label: sid === "" ? "None (not in node graph)" : sid,
    }));
  }, [screenIdOptions, screenId]);

  const workflowSteps = useMemo(
    () =>
      labelingWorkflowSteps({
        refRel,
        doc,
        regionCount: regions.length,
        dirty: anyDirty,
      }),
    [refRel, doc, regions.length, anyDirty],
  );

  const activeScopeMeta = useMemo(
    () => scopes.find((s) => s.key === moduleScope),
    [scopes, moduleScope],
  );

  const reloadRefs = useCallback(async (scope: string) => {
    const list = await fetchLabelingReferences(scope);
    setRefs(list);
    return list;
  }, []);

  const reloadStale = useCallback(async (scope: string) => {
    try {
      const data = await fetchLabelingStaleCrops(scope);
      setStaleCrops(data);
    } catch {
      setStaleCrops({ count: 0, stale: [] });
    }
  }, []);

  useEffect(() => {
    fetchLabelingScopes()
      .then((list) => {
        setScopes(list);
        const fromUrl = params.get("module");
        const fromRef = inferScopeFromRef(params.get("ref") ?? "");
        const initial =
          (fromUrl && list.some((s) => s.key === fromUrl) && fromUrl) ||
          (fromRef && list.some((s) => s.key === fromRef) && fromRef) ||
          list.find((s) => s.key === "core")?.key ||
          list[0]?.key ||
          "core";
        setModuleScope(initial);
      })
      .catch((e: Error) => setError(e.message));
    fetchRoboflowStatus()
      .then(setRoboflow)
      .catch(() => setRoboflow({ configured: false }));
  }, [params]);

  useEffect(() => {
    if (!moduleScope) return;
    reloadStale(moduleScope);
  }, [moduleScope, reloadStale]);

  useEffect(() => {
    if (!moduleScope) return;
    fetchLabelingScreenIds(moduleScope, screenId)
      .then(setScreenIdOptions)
      .catch(() => setScreenIdOptions([]));
  }, [moduleScope, screenId]);

  const deleteSelectedRegion = useCallback(() => {
    if (!selectedId || drawMode || busy || isPending) return;
    setRegions((prev) => prev.filter((r) => r.id !== selectedId));
    setSelectedId(null);
    setDirty(true);
  }, [selectedId, drawMode, busy, isPending]);

  useEffect(() => {
    if (!moduleScope) return;
    reloadRefs(moduleScope)
      .then((list) => {
        const fromUrl = params.get("ref");
        if (fromUrl && list.some((r) => r.rel === fromUrl)) {
          setRefRel(fromUrl);
        } else if (list.length && !refRel) {
          setRefRel(list[0].rel);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [moduleScope, params, refRel, reloadRefs]);

  const updateUrl = useCallback(
    (rel: string, version: string | null, module?: string) => {
      const url = new URL(window.location.href);
      url.searchParams.set("ref", rel);
      url.searchParams.set("module", module ?? moduleScope);
      if (version) url.searchParams.set("version", version);
      else url.searchParams.delete("version");
      router.replace(url.pathname + url.search);
    },
    [router, moduleScope],
  );

  const setModuleScopeAndUrl = useCallback(
    (nextScope: string) => {
      const meta = scopes.find((s) => s.key === nextScope);
      setModuleScope(nextScope);
      setRefRel("");
      setDoc(null);
      setDirty(false);
      setScreenDirty(false);
      setSelectedId(null);
      const url = new URL(window.location.href);
      url.searchParams.set("module", nextScope);
      if (meta?.default_ref) url.searchParams.set("ref", meta.default_ref);
      else url.searchParams.delete("ref");
      url.searchParams.delete("version");
      router.replace(url.pathname + url.search);
    },
    [router, scopes],
  );

  const selectRef = useCallback(
    (rel: string, version?: string | null) => {
      setRefRel(rel);
      updateUrl(rel, version ?? activeVersion);
    },
    [activeVersion, updateUrl],
  );

  const setActiveVersion = useCallback(
    (version: string | null) => {
      updateUrl(refRel, version);
    },
    [refRel, updateUrl],
  );

  const loadDoc = useCallback(
    async (rel: string, version?: string | null) => {
      if (!rel || !moduleScope) return;
      try {
        const d = await fetchLabelingDocument(rel, moduleScope, version);
        if (d.redirect_version) {
          selectRef(d.ref, d.redirect_version);
          return;
        }
        setDoc(d);
        setRegions(apiToEditorRegions(d.regions as Record<string, unknown>[]));
        setScreenId(d.screen_id || "");
        setBasename(d.basename || "");
        setEditVersionCond(
          d.versions.find((v) => v.id === d.active_version)?.cond ?? "",
        );
        setSelectedId(null);
        setDirty(false);
        setScreenDirty(false);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [selectRef, moduleScope],
  );

  useEffect(() => {
    if (refRel && moduleScope) loadDoc(refRel, activeVersion);
  }, [refRel, activeVersion, moduleScope, loadDoc]);

  const imageUrl = displayRef
    ? `${labelingImageUrl(displayRef)}&n=${imageNonce}`
    : null;
  const canDiscard = Boolean(refRel && isPendingCapture(refRel));

  const runBusy = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDropReferencePng = useCallback(
    async (file: File) => {
      if (!instanceId || busy) return;
      await runBusy(async () => {
        const out = await importLabelingPng(instanceId, moduleScope, file);
        await reloadRefs(moduleScope);
        selectRef(out.ref, null);
        setImageNonce((n) => n + 1);
        showSuccess(`Imported ${out.ref}`);
      });
    },
    [instanceId, busy, moduleScope, reloadRefs, selectRef, showSuccess],
  );

  const onSave = async () => {
    if (!refRel || busy) return;
    await runBusy(async () => {
      await saveLabelingRegions(
        refRel,
        moduleScope,
        editorToApiRegions(regions),
        activeVersion,
        screenId,
      );
      setDirty(false);
      setScreenDirty(false);
      await loadDoc(refRel, activeVersion);
      await reloadStale(moduleScope);
      showSuccess("Saved to area.json");
    });
  };

  const onNewScreenshot = async () => {
    if (!instanceId || busy) return;
    await runBusy(async () => {
      const out = await captureLabelingScreenshot(instanceId, moduleScope);
      await reloadRefs(moduleScope);
      selectRef(out.ref, null);
      setImageNonce((n) => n + 1);
      showSuccess(`Captured ${out.ref}`);
    });
  };

  const onRefreshSelected = async () => {
    if (!refRel || !instanceId || busy) return;
    setRefreshPending(true);
  };

  const confirmRefresh = async () => {
    if (!refRel || !instanceId || busy) return;
    await runBusy(async () => {
      await refreshLabelingReference(refRel, instanceId, moduleScope);
      setImageNonce((n) => n + 1);
      await loadDoc(refRel, activeVersion);
      setRefreshPending(false);
      showSuccess(`Refreshed ${displayRef}`);
    });
  };

  const onDiscard = () => {
    if (!refRel || !canDiscard || busy) return;
    setConfirmAction("discard");
  };

  const runDiscard = async () => {
    if (!refRel) return;
    setConfirmAction(null);
    await runBusy(async () => {
      await discardLabelingCapture(refRel, moduleScope);
      const list = await reloadRefs(moduleScope);
      const next =
        list.find((r) => !isPendingCapture(r.rel))?.rel ?? list[0]?.rel ?? "";
      if (next) selectRef(next, null);
      else setRefRel("");
      showSuccess("Discarded pending capture");
    });
  };

  const onWriteCrops = async () => {
    if (busy) return;
    await runBusy(async () => {
      const out = await exportLabelingCrops(moduleScope);
      await reloadStale(moduleScope);
      showSuccess(`Wrote ${out.written_count} crop(s)`);
      if (out.warnings.length) {
        setError(out.warnings.slice(0, 3).join(" · "));
      }
    });
  };

  const onPromoteOrRename = async () => {
    if (!refRel || !instanceId || !basename.trim() || busy) return;
    await runBusy(async () => {
      if (isPending) {
        const out = await promoteLabelingReference(
          refRel,
          basename.trim(),
          instanceId,
          moduleScope,
          {
            regions: editorToApiRegions(regions),
            screenId: screenId || doc?.screen_id || undefined,
          },
        );
        await reloadRefs(moduleScope);
        selectRef(out.ref, null);
        setImageNonce((n) => n + 1);
        showSuccess(out.message || `Published ${out.ref}`);
      } else {
        const out = await renameLabelingReference(
          refRel,
          basename.trim(),
          instanceId,
          moduleScope,
        );
        await reloadRefs(moduleScope);
        selectRef(out.ref, activeVersion);
        showSuccess(out.message || `Renamed to ${out.ref}`);
      }
    });
  };

  const onRoboflowUpload = async () => {
    if (!refRel || busy || isPending) return;
    await runBusy(async () => {
      const out = await uploadLabelingToRoboflow(refRel, moduleScope, activeVersion);
      showSuccess(
        `Uploaded ${out.annotation_count} annotation(s) to batch ${out.batch_name}`,
      );
    });
  };

  const onAddVersion = async () => {
    if (!refRel || busy) return;
    await runBusy(async () => {
      await addLabelingVersion(
        refRel,
        newVersionId.trim(),
        newVersionCond.trim(),
        moduleScope,
      );
      await loadDoc(refRel, activeVersion);
      showSuccess(`Added version ${newVersionId}`);
      const sug = await suggestLabelingVersionId(refRel, moduleScope);
      setNewVersionId(sug.suggested_id);
      setNewVersionCond("");
    });
  };

  const onSaveVersionCond = async () => {
    if (!refRel || !activeVersion || busy) return;
    await runBusy(async () => {
      await updateLabelingVersionCond(
        refRel,
        activeVersion,
        editVersionCond.trim(),
        moduleScope,
      );
      await loadDoc(refRel, activeVersion);
      showSuccess(`Saved cond for ${activeVersion}`);
    });
  };

  const onDeleteVersion = () => {
    if (!refRel || !activeVersion || busy) return;
    setConfirmAction("delete-version");
  };

  const runDeleteVersion = async () => {
    if (!refRel || !activeVersion) return;
    const version = activeVersion;
    setConfirmAction(null);
    await runBusy(async () => {
      await deleteLabelingVersion(refRel, version, moduleScope);
      setActiveVersion(null);
      await loadDoc(refRel, null);
      showSuccess(`Deleted version ${version}`);
    });
  };

  const onSyncVersionRegions = async () => {
    if (!refRel || !activeVersion || busy) return;
    await runBusy(async () => {
      const out = await syncLabelingVersionRegions(refRel, activeVersion, moduleScope);
      await loadDoc(refRel, activeVersion);
      showSuccess(`Synced ${out.added} region(s) (${out.skipped} skipped)`);
    });
  };

  const onBindVersionToCanvas = async () => {
    if (!refRel || !activeVersion || busy) return;
    await runBusy(async () => {
      await bindLabelingVersionOcr(refRel, activeVersion, displayRef, moduleScope);
      await loadDoc(refRel, activeVersion);
      showSuccess(`Bound ${displayRef} to version ${activeVersion}`);
    });
  };

  useEffect(() => {
    if (!refRel || isPending) return;
    suggestLabelingVersionId(refRel, moduleScope)
      .then((s) => setNewVersionId(s.suggested_id))
      .catch(() => {});
  }, [refRel, isPending, doc?.versions?.length, moduleScope]);

  return (
    <>
      <PageHeader title="Labeling">
        {anyDirty ? (
          <span className="status-pill status-pending">Unsaved</span>
        ) : (
          <span className="status-pill status-idle">Saved</span>
        )}
        {isPending ? (
          <span className="status-pill status-pending">Pending capture</span>
        ) : null}
        {activeVersion ? (
          <span className="status-pill status-running">{activeVersion}</span>
        ) : null}
      </PageHeader>
      <p className="meta">
        Canvas left (draw regions on the screenshot). Sidebar right: reference select, region
        properties, save. Bboxes are percentages in the active module&apos;s area file.
        {activeScopeMeta ? (
          <>
            {" "}
            · <strong>{activeScopeMeta.title}</strong> ·{" "}
            <code>{activeScopeMeta.references_prefix}</code>
            {doc?.area_path || activeScopeMeta.area_path ? (
              <>
                {" "}
                · <code>{doc?.area_path ?? activeScopeMeta.area_path}</code>
              </>
            ) : null}
          </>
        ) : null}
      </p>
      <ErrorBanner message={error ?? instancesError} />

      <div className="labeling-header-toolbar toolbar">
        <AppListbox
          inline
          label="Instance"
          value={instanceId}
          onChange={setInstanceId}
          disabled={busy || instancesLoading}
          loading={instancesLoading}
          placeholder={instanceSelectPlaceholder(
            instancesLoading,
            !instancesLoading && instances.length === 0,
          )}
          options={instances.map((id) => ({ value: id, label: id }))}
          minWidth={170}
        />
        <button
          type="button"
          className="btn-primary"
          disabled={!instanceId || busy}
          onClick={onNewScreenshot}
        >
          New screenshot
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!refRel || !instanceId || busy}
          onClick={onRefreshSelected}
        >
          Refresh selected
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!canDiscard || busy}
          onClick={onDiscard}
        >
          Discard screenshot
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={onWriteCrops}
        >
          Write crops
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!refRel || isPending || busy || !roboflow?.configured}
          title={
            roboflow?.configured
              ? `Upload to ${roboflow.project}`
              : `Missing: ${roboflow?.missing?.join(", ") ?? "ROBOFLOW_* env"}`
          }
          onClick={onRoboflowUpload}
        >
          Upload to Roboflow
        </button>
      </div>

      {refreshPending ? (
        <div className="labeling-refresh-confirm">
          <p className="meta">
            This will overwrite <code>{displayRef}</code> with the latest rolling
            preview from <code>{instanceId}</code> (regions unchanged).
          </p>
          <div className="toolbar">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={confirmRefresh}
            >
              Confirm overwrite
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => setRefreshPending(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <LabelingStaleCropsBanner
        count={staleCrops.count}
        stale={staleCrops.stale}
        busy={busy}
        onResync={onWriteCrops}
      />

      <LabelingWorkflowStrip steps={workflowSteps} />

      <div className="labeling-main">
        <section className="panel labeling-canvas-panel">
          <h2 className="labeling-canvas-heading">Region editor</h2>
          <div className="toolbar labeling-canvas-toolbar">
            <button
              type="button"
              className={drawMode ? "btn-primary" : "btn-secondary"}
              onClick={() => setDrawMode((v) => !v)}
            >
              {drawMode ? "Draw mode ON" : "Draw rectangle"}
            </button>
            <span className="meta labeling-canvas-tool-hint">
              Click canvas to focus · Drag regions to move · Backspace deletes · Drop
              PNG to import · Draw mode for new boxes →
            </span>
          </div>
          <KonvaImageEditor
            imageUrl={imageUrl}
            imageWidth={720}
            imageHeight={1280}
            regions={regions}
            selectedId={selectedId}
            drawMode={drawMode}
            onSelect={setSelectedId}
            onDeleteSelected={deleteSelectedRegion}
            onDropImageFile={onDropReferencePng}
            dropDisabled={!instanceId || busy}
            onRegionsChange={(next) => {
              setRegions(next);
              setDirty(true);
            }}
          />
          {doc ? (
            <p className="meta labeling-canvas-meta">
              {displayRef}
              {doc.screen_id ? ` · screen: ${doc.screen_id}` : ""}
              {activeVersion ? ` · editing ${activeVersion}` : ""}
              {" · "}
              {regions.length} region(s)
            </p>
          ) : null}
        </section>

        <aside className="labeling-sidebar">
          <LabelingCard>
            <LabelingReferencePanel
              scopes={scopes}
              moduleScope={moduleScope}
              onModuleChange={setModuleScopeAndUrl}
              refs={refs}
              refRel={refRel}
              filter={refFilter}
              onFilterChange={setRefFilter}
              onSelect={(rel) => selectRef(rel, null)}
              basename={basename}
              onBasenameChange={setBasename}
              isPending={isPending}
              busy={busy}
              onPromoteOrRename={onPromoteOrRename}
            />
          </LabelingCard>

          <LabelingCard>
            <LabelingRegionsPanel
              regions={regions}
              selectedId={selectedId}
              activeVersion={activeVersion}
              onSelect={setSelectedId}
              onRegionsChange={setRegions}
              onDirty={() => setDirty(true)}
            />
          </LabelingCard>

          <LabelingCard>
            <LabelingVersionsPanel
              versions={doc?.versions ?? []}
              activeVersion={activeVersion}
              isPending={isPending}
              busy={busy}
              hasEntry={Boolean(doc?.entry_id)}
              editVersionCond={editVersionCond}
              newVersionId={newVersionId}
              newVersionCond={newVersionCond}
              onEditCondChange={setEditVersionCond}
              onNewIdChange={setNewVersionId}
              onNewCondChange={setNewVersionCond}
              onVersionSelect={setActiveVersion}
              onSaveCond={onSaveVersionCond}
              onSyncRegions={onSyncVersionRegions}
              onBindCanvas={onBindVersionToCanvas}
              onDeleteVersion={onDeleteVersion}
              onAddVersion={onAddVersion}
            />
          </LabelingCard>

          <LabelingCard>
            <details className="labeling-panel-block" open>
              <summary className="labeling-panel-block__title">Screen entry</summary>
              <div className="labeling-panel-block__body">
                {doc?.entry_id != null ? (
                  <p className="meta">
                    Entry id={doc.entry_id}
                    {doc.screen_id ? ` · node ${doc.screen_id}` : ""}
                  </p>
                ) : (
                  <p className="meta">No area.json entry yet — save or promote first.</p>
                )}
                <AppListbox
                  fullWidth
                  label="Screen node"
                  value={screenId}
                  onChange={(sid) => {
                    setScreenId(sid);
                    setScreenDirty(true);
                  }}
                  disabled={busy || isPending}
                  placeholder="Select node…"
                  options={screenNodeListboxOptions}
                />
              </div>
            </details>
          </LabelingCard>

          <LabelingCard className="labeling-card--save">
            <div className="labeling-save-block">
              <button
                type="button"
                className="btn-primary labeling-save-btn"
                disabled={(!dirty && !screenDirty) || busy || isPending}
                onClick={onSave}
              >
                Save area.json
              </button>
              {doc?.area_path ? (
                <p className="meta">File: {doc.area_path}</p>
              ) : null}
            </div>
          </LabelingCard>
        </aside>
      </div>

      <AppConfirmDialog
        open={confirmAction === "discard"}
        onClose={() => setConfirmAction(null)}
        onConfirm={runDiscard}
        title="Discard capture?"
        confirmLabel="Discard"
        variant="danger"
        busy={busy}
      >
        Delete unsaved capture <code>{refRel}</code>? This cannot be undone.
      </AppConfirmDialog>

      <AppConfirmDialog
        open={confirmAction === "delete-version"}
        onClose={() => setConfirmAction(null)}
        onConfirm={runDeleteVersion}
        title="Delete version?"
        confirmLabel="Delete version"
        variant="danger"
        busy={busy}
      >
        Delete version <code>{activeVersion}</code> and its region overrides?
      </AppConfirmDialog>
    </>
  );
}

export default function LabelingPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <LabelingPageInner />
    </Suspense>
  );
}
