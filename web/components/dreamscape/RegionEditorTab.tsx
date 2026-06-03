"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import { KonvaImageEditor } from "@/components/konva/KonvaImageEditor";
import { LabelingRegionsPanel } from "@/components/labeling/LabelingRegionsPanel";
import type { EditorRegion } from "@/lib/bbox";
import {
  captureLabelingScreenshot,
  fetchLabelingDocument,
  fetchLabelingReferences,
  labelingImageUrl,
  promoteLabelingReference,
  saveLabelingRegions,
} from "@/lib/api";
import {
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_WORDS_REF,
  isSystemRegion,
  screenRefOptions,
} from "@/lib/dreamscape-live";
import { apiToEditorRegions, defaultRegion, editorToApiRegions } from "@/lib/labeling-utils";
import { Button } from "./Button";

const FRAME_W = 720;
const FRAME_H = 1280;

/** Filename-safe basename from a typed screen id (e.g. "x.coming_soon" → "x_coming_soon"). */
function basenameFromScreen(name: string, instanceId: string): string {
  const slug = name.trim().replace(/[^a-zA-Z0-9._-]+/g, "_").replace(/\./g, "_");
  const raw = (instanceId ? `${instanceId}_` : "") + slug;
  return raw.replace(/^_|_$/g, "") || slug;
}

/** Standalone area.yaml region editor for Dreamscape Memory screens: pick a
 * labeled reference (or capture a new one from a live device), draw/edit item
 * point regions, and save back to area.yaml + crops. Mode-agnostic — the editor
 * opens to the solo words reference and can switch to any labeled screen. */
export function RegionEditorTab() {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const queryClient = useQueryClient();

  const [message, setMessage] = useState<string | null>(null);

  // ── Editor (frozen reference frame) ──
  const [refRel, setRefRel] = useState<string>(DREAMSCAPE_WORDS_REF);
  const [regions, setRegions] = useState<EditorRegion[]>([]);
  // Solver-managed word/title zones: hidden from the editor but preserved on
  // save so they stay in area.yaml. The editor is for item points only.
  const [systemRegions, setSystemRegions] = useState<EditorRegion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [imageNonce, setImageNonce] = useState(0);
  const [screenId, setScreenId] = useState("dreamscape_memory");
  const [newScreenName, setNewScreenName] = useState("");

  const refsQuery = useQuery({
    queryKey: ["dreamscape-refs"],
    queryFn: () => fetchLabelingReferences(DREAMSCAPE_SCOPE),
  });
  const screenOptions = useMemo(
    () => screenRefOptions(refsQuery.data),
    [refsQuery.data],
  );
  // Keep the current ref visible in the dropdown even if the list is loading.
  const listboxOptions = useMemo(() => {
    const opts = screenOptions.map((s) => ({
      value: s.rel,
      label: s.label,
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
    const all = apiToEditorRegions(docQuery.data.regions);
    setSystemRegions(all.filter((r) => isSystemRegion(r.name)));
    setRegions(all.filter((r) => !isSystemRegion(r.name)));
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
        // Merge the hidden system zones back so they persist in area.yaml.
        editorToApiRegions([...regions, ...systemRegions]),
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
          <Button
            type="submit"
            variant="primary"
            disabled={!instanceId || !newScreenName.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? "Capturing…" : "Create new screen from game"}
          </Button>
        </form>
      </div>

      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <section className="panel">
        {/* Screen selector */}
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <AppListbox
            label="Screen"
            options={listboxOptions}
            value={refRel}
            onChange={selectScreen}
            loading={refsQuery.isLoading}
            minWidth={220}
            inline
          />
        </div>

        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold">Region editor</h2>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              className={drawMode ? "border-wos-accent text-wos-accent" : ""}
              onClick={() => setDrawMode((d) => !d)}
            >
              {drawMode ? "Drawing…" : "Draw region"}
            </Button>
            <Button variant="secondary" onClick={addRegion}>
              Add region
            </Button>
            <Button
              variant="accent"
              disabled={!dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              {saveMutation.isPending ? "Saving…" : dirty ? "Save area.json" : "Saved"}
            </Button>
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
  );
}
