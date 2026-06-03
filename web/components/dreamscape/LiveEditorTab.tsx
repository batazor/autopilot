"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox, AppSwitch } from "@/components/headless";
import { KonvaImageEditor } from "@/components/konva/KonvaImageEditor";
import { LabelingRegionsPanel } from "@/components/labeling/LabelingRegionsPanel";
import type { EditorRegion } from "@/lib/bbox";
import {
  captureLabelingScreenshot,
  fetchLabelingDocument,
  fetchLabelingReferences,
  fetchOverlayTest,
  fetchRegionOcr,
  labelingImageUrl,
  overlayTestImageUrl,
  promoteLabelingReference,
  saveLabelingRegions,
  testRegionOcr,
} from "@/lib/api";
import {
  DREAMSCAPE_ARCHIVED_KEY,
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  deriveLiveStatus,
  screenRefOptions,
  statusFromDetectedScreen,
  wordBadges,
} from "@/lib/dreamscape-live";
import type { RegionOcrTestResult } from "@/lib/types";
import { apiToEditorRegions, defaultRegion, editorToApiRegions } from "@/lib/labeling-utils";
import { LiveStatusCard } from "./LiveStatusCard";

const POLL_MS = 1500;
const FRAME_W = 720;
const FRAME_H = 1280;

/** Filename-safe basename from a typed screen id (e.g. "x.coming_soon" → "x_coming_soon"). */
function basenameFromScreen(name: string, instanceId: string): string {
  const slug = name.trim().replace(/[^a-zA-Z0-9._-]+/g, "_").replace(/\./g, "_");
  const raw = (instanceId ? `${instanceId}_` : "") + slug;
  return raw.replace(/^_|_$/g, "") || slug;
}

export function LiveEditorTab() {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const queryClient = useQueryClient();

  // ── Live polling (status + detected words) — independent of the editor ──
  const overlayQuery = useQuery({
    queryKey: ["dreamscape-overlay", instanceId],
    queryFn: () => fetchOverlayTest(instanceId),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });
  const ocrQuery = useQuery({
    queryKey: ["dreamscape-ocr", instanceId],
    queryFn: () => fetchRegionOcr(instanceId, [...DREAMSCAPE_WORD_REGIONS]),
    enabled: Boolean(instanceId),
    refetchInterval: POLL_MS,
  });

  const [message, setMessage] = useState<string | null>(null);

  // ── Test-image override: run our logic on an uploaded screenshot ──
  const [testResult, setTestResult] = useState<RegionOcrTestResult | null>(null);
  const [testImageUrl, setTestImageUrl] = useState<string | null>(null);

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      testRegionOcr(instanceId, file, [...DREAMSCAPE_WORD_REGIONS]),
    onSuccess: (res, file) => {
      setTestImageUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(file);
      });
      setTestResult(res);
    },
    onError: (err: unknown) => setMessage(`Test image failed: ${String(err)}`),
  });

  const clearTest = () => {
    setTestImageUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setTestResult(null);
  };
  useEffect(() => {
    // Revoke the object URL when the tab unmounts.
    return () => {
      if (testImageUrl) URL.revokeObjectURL(testImageUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const testMode = testResult != null;
  const status = useMemo(
    () =>
      testMode
        ? statusFromDetectedScreen(testResult?.detected_screen)
        : deriveLiveStatus(overlayQuery.data),
    [testMode, testResult, overlayQuery.data],
  );
  const badges = useMemo(
    () => wordBadges(testMode ? testResult?.rows : ocrQuery.data?.rows),
    [testMode, testResult, ocrQuery.data],
  );
  const cardImageUrl = testMode
    ? testImageUrl
    : instanceId
      ? overlayTestImageUrl(instanceId, overlayQuery.dataUpdatedAt || 0)
      : null;

  // ── Editor (frozen reference frame) ──
  const [refRel, setRefRel] = useState<string>(DREAMSCAPE_WORDS_REF);
  const [regions, setRegions] = useState<EditorRegion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [imageNonce, setImageNonce] = useState(0);
  const [screenId, setScreenId] = useState("dreamscape_memory");

  // ── Screen list + archive filter (operator-local, no repo data change) ──
  const [showArchived, setShowArchived] = useState(false);
  const [archivedRels, setArchivedRels] = useState<Set<string>>(new Set());
  const [newScreenName, setNewScreenName] = useState("");

  useEffect(() => {
    try {
      const raw = localStorage.getItem(DREAMSCAPE_ARCHIVED_KEY);
      if (raw) setArchivedRels(new Set(JSON.parse(raw) as string[]));
    } catch {
      /* ignore malformed/absent storage */
    }
  }, []);

  const persistArchived = (next: Set<string>) => {
    setArchivedRels(next);
    try {
      localStorage.setItem(DREAMSCAPE_ARCHIVED_KEY, JSON.stringify([...next]));
    } catch {
      /* ignore quota/availability */
    }
  };

  const refsQuery = useQuery({
    queryKey: ["dreamscape-refs"],
    queryFn: () => fetchLabelingReferences(DREAMSCAPE_SCOPE),
  });
  const screenOptions = useMemo(
    () => screenRefOptions(refsQuery.data, { showArchived, archivedRels }),
    [refsQuery.data, showArchived, archivedRels],
  );
  // Keep the current ref visible in the dropdown even if it's archived/hidden.
  const listboxOptions = useMemo(() => {
    const opts = screenOptions.map((s) => ({
      value: s.rel,
      label: (s.archived ? "📦 " : "") + s.label,
    }));
    if (!opts.some((o) => o.value === refRel)) {
      opts.unshift({ value: refRel, label: refRel.split("/").pop() ?? refRel });
    }
    return opts;
  }, [screenOptions, refRel]);

  const docQuery = useQuery({
    queryKey: ["dreamscape-doc", refRel, imageNonce],
    queryFn: () => fetchLabelingDocument(refRel, DREAMSCAPE_SCOPE),
  });

  // Reload editor regions from the document whenever it (re)loads and there are
  // no unsaved edits — never stomp in-progress work.
  useEffect(() => {
    if (!docQuery.data || dirty) return;
    setRegions(apiToEditorRegions(docQuery.data.regions));
    setScreenId(docQuery.data.screen_id || "");
  }, [docQuery.data, dirty]);

  const selectScreen = (rel: string) => {
    if (rel === refRel) return;
    setRefRel(rel);
    setDirty(false);
    setSelectedId(null);
    setDrawMode(false);
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      saveLabelingRegions(
        refRel,
        DREAMSCAPE_SCOPE,
        editorToApiRegions(regions),
        null,
        screenId || null,
      ),
    onSuccess: (res) => {
      setDirty(false);
      setImageNonce((n) => n + 1);
      const crops = res.crops_written_count ?? 0;
      setMessage(`Saved ${regions.length} region(s)${crops ? ` · ${crops} crop(s)` : ""}.`);
    },
    onError: (err: unknown) => setMessage(`Save failed: ${String(err)}`),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const sid = newScreenName.trim();
      if (!sid) throw new Error("enter a screen name");
      const cap = await captureLabelingScreenshot(instanceId, DREAMSCAPE_SCOPE);
      return promoteLabelingReference(
        cap.ref,
        basenameFromScreen(sid, instanceId),
        instanceId,
        DREAMSCAPE_SCOPE,
        { regions: [], screenId: sid },
      );
    },
    onSuccess: async (res) => {
      setMessage(`Created screen "${res.screen_id || newScreenName}".`);
      setNewScreenName("");
      await queryClient.invalidateQueries({ queryKey: ["dreamscape-refs"] });
      if (res.ref) selectScreen(res.ref);
    },
    onError: (err: unknown) => setMessage(`Create failed: ${String(err)}`),
  });

  const toggleArchiveCurrent = () => {
    const next = new Set(archivedRels);
    if (next.has(refRel)) next.delete(refRel);
    else next.add(refRel);
    persistArchived(next);
  };

  const addRegion = () => {
    const name = `dreamscape_memory.region_${regions.length + 1}`;
    setRegions((prev) => [...prev, defaultRegion(FRAME_W, FRAME_H, name)]);
    setSelectedId(name);
    setDirty(true);
  };
  const deleteSelected = () => {
    if (!selectedId) return;
    setRegions((prev) => prev.filter((r) => r.id !== selectedId));
    setSelectedId(null);
    setDirty(true);
  };

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));
  const currentArchived = archivedRels.has(refRel);

  return (
    <div className="mt-4 space-y-4">
      {/* Toolbar: instance + create-new-screen form */}
      <div className="flex flex-wrap items-end gap-3">
        <AppListbox
          label="Instance"
          options={instanceOptions}
          value={instanceId}
          onChange={setInstanceId}
          loading={instancesLoading}
          placeholder="Select a device"
          inline
        />
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (instanceId && newScreenName.trim()) createMutation.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-wos-text-muted">
            New screen name
            <input
              type="text"
              value={newScreenName}
              onChange={(e) => setNewScreenName(e.target.value)}
              placeholder="e.g. dreamscape_memory.practice"
              className="w-64 rounded border border-wos-border bg-wos-bg-deep px-2 py-1.5 text-sm text-wos-text"
            />
          </label>
          <button
            type="submit"
            className="rounded border border-wos-border px-3 py-1.5 text-sm hover:border-wos-border-hover disabled:opacity-50"
            disabled={!instanceId || !newScreenName.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? "Capturing…" : "Create new screen from game"}
          </button>
        </form>
      </div>

      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <LiveStatusCard
          imageUrl={cardImageUrl}
          status={status}
          badges={badges}
          loading={testMode ? uploadMutation.isPending : ocrQuery.isFetching}
          instanceSelected={Boolean(instanceId)}
          testMode={testMode}
          uploading={uploadMutation.isPending}
          onUploadTestImage={(file) => uploadMutation.mutate(file)}
          onClearTest={clearTest}
        />

        <section className="panel">
          {/* Screen selector + archive controls */}
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div className="flex flex-wrap items-end gap-2">
              <AppListbox
                label="Screen"
                options={listboxOptions}
                value={refRel}
                onChange={selectScreen}
                loading={refsQuery.isLoading}
                minWidth={220}
                inline
              />
              <button
                type="button"
                className="rounded border border-wos-border px-2.5 py-1.5 text-sm hover:border-wos-border-hover"
                onClick={toggleArchiveCurrent}
                title="Archive is a local view filter — it does not change area.yaml"
              >
                {currentArchived ? "Unarchive" : "Archive"}
              </button>
            </div>
            <AppSwitch
              checked={showArchived}
              onChange={setShowArchived}
              label="Show archived"
              inline
            />
          </div>

          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Region editor</h2>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={`rounded border px-3 py-1.5 text-sm ${
                  drawMode
                    ? "border-wos-accent text-wos-accent"
                    : "border-wos-border hover:border-wos-border-hover"
                }`}
                onClick={() => setDrawMode((d) => !d)}
              >
                {drawMode ? "Drawing…" : "Draw region"}
              </button>
              <button
                type="button"
                className="rounded border border-wos-border px-3 py-1.5 text-sm hover:border-wos-border-hover"
                onClick={addRegion}
              >
                Add region
              </button>
              <button
                type="button"
                className="rounded bg-wos-accent px-3 py-1.5 text-sm font-medium text-wos-on-accent disabled:opacity-50"
                disabled={!dirty || saveMutation.isPending}
                onClick={() => saveMutation.mutate()}
              >
                {saveMutation.isPending ? "Saving…" : dirty ? "Save area.json" : "Saved"}
              </button>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-[1fr_260px]">
            <div className="mx-auto w-full max-w-[320px]">
              <KonvaImageEditor
                imageUrl={labelingImageUrl(refRel, imageNonce)}
                imageWidth={FRAME_W}
                imageHeight={FRAME_H}
                regions={regions}
                selectedId={selectedId}
                drawMode={drawMode}
                onSelect={setSelectedId}
                onDeleteSelected={deleteSelected}
                onRegionsChange={(next) => {
                  setRegions(next);
                  setDirty(true);
                }}
              />
              <p className="meta mt-1.5">
                {refRel.split("/").pop()} · screen: {screenId || "—"}
                {currentArchived ? " · 📦 archived" : ""}
              </p>
            </div>
            <LabelingRegionsPanel
              regions={regions}
              selectedId={selectedId}
              activeVersion={null}
              refRel={refRel}
              imageNonce={imageNonce}
              onSelect={setSelectedId}
              onRegionsChange={setRegions}
              onDirty={() => setDirty(true)}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
