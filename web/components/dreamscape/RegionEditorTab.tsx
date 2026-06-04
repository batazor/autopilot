"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { AppListbox } from "@/components/headless";
import { SceneCalibrator } from "@/components/dreamscape/SceneCalibrator";
import {
  ScenePointEditor,
  type ScenePin,
} from "@/components/dreamscape/ScenePointEditor";
import type { EditorRegion } from "@/lib/bbox";
import {
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  galleryImageUrl,
  saveDreamscapeScene,
} from "@/lib/api";
import { DREAMSCAPE_WORDS_REF } from "@/lib/dreamscape-live";
import { defaultRegion } from "@/lib/labeling-utils";
import type { DreamscapeSceneDetail, DreamscapeSceneSummary } from "@/lib/types";
import { Button } from "./Button";

const FRAME_W = 720;
const FRAME_H = 1280;

// Guides categories (kept in sync with config/dreamscape_db.py / DreamscapeGuides).
const PRACTICE_SEASON = 0;
const MULTIPLAYER_SEASON = 100;

function categoryTag(season: number): string {
  if (season === PRACTICE_SEASON) return "Practice";
  if (season === MULTIPLAYER_SEASON) return "MP";
  return `S${season}`;
}

function categoryRank(season: number): number {
  if (season === PRACTICE_SEASON) return Number.MAX_SAFE_INTEGER;
  if (season === MULTIPLAYER_SEASON) return Number.MAX_SAFE_INTEGER - 1;
  return season;
}

/** scene_rect (% of frame) → editable region; full-frame when unset. */
function rectFromScene(detail: DreamscapeSceneDetail): EditorRegion {
  const r = defaultRegion(FRAME_W, FRAME_H, "scene_rect");
  const sr = detail.scene_rect;
  r.bbox = sr
    ? { ...r.bbox, x: sr.left, y: sr.top, width: sr.width, height: sr.height }
    : { ...r.bbox, x: 0, y: 0, width: 100, height: 100 };
  return r;
}

function sceneImageUrl(slug: string, img: string): string {
  return img ? galleryImageUrl(img) : `/dreamscape/${slug}.webp`;
}

/** Per-scene point editor: pick a scene (grouped by season, with a preview),
 * place/name its item pins on the guide image, calibrate where the guide maps
 * onto the game frame, and save back to the **scene DB**. Shares the pin-editing
 * surface (``ScenePointEditor``) with scene onboarding — one flow for create and
 * edit — and deep-links via ``?scene=slug``. */
export function RegionEditorTab() {
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const pathname = usePathname();

  const [message, setMessage] = useState<string | null>(null);

  // ── Scene selection ──
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  // ── Editable scene state (seeded from the loaded scene) ──
  const [pins, setPins] = useState<ScenePin[]>([]);
  const [selectedN, setSelectedN] = useState<number | null>(null);
  const [rect, setRect] = useState<EditorRegion>(() =>
    defaultRegion(FRAME_W, FRAME_H, "scene_rect"),
  );
  const [activate, setActivate] = useState(false);
  const [sceneOpacity, setSceneOpacity] = useState(0.5);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [dirty, setDirty] = useState(false);

  const scenesQuery = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const scenes = scenesQuery.data?.scenes ?? [];

  const sceneOptions = useMemo(() => {
    const sorted = [...scenes].sort(
      (a, b) =>
        categoryRank(a.season) - categoryRank(b.season) ||
        a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
    );
    return sorted.map((s) => ({
      value: s.slug,
      label: `${categoryTag(s.season)} · ${s.title}`,
    }));
  }, [scenes]);

  const selectedSummary: DreamscapeSceneSummary | null =
    scenes.find((s) => s.slug === selectedSlug) ?? null;

  // Default/deep-link selection: ?scene=slug, else the active scene, else first.
  useEffect(() => {
    if (!scenes.length) return;
    setSelectedSlug((current) => {
      if (current && scenes.some((s) => s.slug === current)) return current;
      const wanted = params.get("scene")?.trim();
      if (wanted && scenes.some((s) => s.slug === wanted)) return wanted;
      return scenesQuery.data?.active || scenes[0]?.slug || null;
    });
  }, [scenes, params, scenesQuery.data]);

  const sceneQuery = useQuery({
    queryKey: ["dreamscape-scene", selectedSlug],
    queryFn: () => fetchDreamscapeScene(selectedSlug as string),
    enabled: !!selectedSlug,
  });
  const detail = sceneQuery.data ?? null;

  // Seed editable state when a *new* scene loads — keep in-progress edits across
  // refetches of the same scene (don't stomp unsaved work).
  const loadedSlug = useRef<string | null>(null);
  useEffect(() => {
    if (!detail || loadedSlug.current === detail.slug) return;
    loadedSlug.current = detail.slug;
    setPins(
      detail.points.map((p) => ({
        n: p.n,
        name: p.name,
        xPct: p.xPct,
        yPct: p.yPct,
        placed: true,
        conf: null,
      })),
    );
    setRect(rectFromScene(detail));
    setActivate(detail.active);
    setSelectedN(null);
    setPreviewIndex(0);
    setDirty(false);
  }, [detail]);

  // Stable image URLs (galleryImageUrl is cache-busted per call — memoize to
  // avoid an image-reload loop in the editor / calibrator).
  const previewImages = useMemo(() => {
    if (!detail) return [];
    const list = detail.images.length
      ? detail.images
      : [detail.source_image].filter(Boolean);
    return list.length ? list : [""];
  }, [detail]);
  const previewUrls = useMemo(
    () =>
      detail
        ? previewImages.map((img) => sceneImageUrl(detail.slug, img))
        : [],
    [detail, previewImages],
  );
  const activePreviewIndex = Math.min(
    previewIndex,
    Math.max(0, previewUrls.length - 1),
  );
  const sceneSrc = previewUrls[activePreviewIndex] ?? "";
  const calibBg = useMemo(() => galleryImageUrl(DREAMSCAPE_WORDS_REF), []);
  const calibPoints = useMemo(
    () => pins.map((p) => ({ n: p.n, xPct: p.xPct, yPct: p.yPct, name: p.name })),
    [pins],
  );

  const selectScene = (slug: string) => {
    if (slug === selectedSlug) return;
    setSelectedSlug(slug);
    // Shallow URL update (History API, not router.replace) so the view is
    // shareable / survives reload without an RSC round-trip.
    const next = new URLSearchParams(params.toString());
    next.set("scene", slug);
    window.history.replaceState(null, "", `${pathname}?${next.toString()}`);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error("no scene loaded");
      return saveDreamscapeScene(detail.slug, {
        title: detail.title,
        source_image: detail.source_image,
        scene_rect: {
          left: rect.bbox.x,
          top: rect.bbox.y,
          width: rect.bbox.width,
          height: rect.bbox.height,
        },
        points: pins.map((p) => ({
          n: p.n,
          name: p.name,
          xPct: p.xPct,
          yPct: p.yPct,
        })),
        activate,
      });
    },
    onSuccess: async (res) => {
      setDirty(false);
      await queryClient.invalidateQueries({ queryKey: ["dreamscape-scene"] });
      await queryClient.invalidateQueries({ queryKey: ["dreamscape-scenes"] });
      setMessage(
        `Saved "${res.slug}" — ${res.point_count} point(s)` +
          (res.active === res.slug ? " · active" : "") +
          ".",
      );
    },
    onError: (err: unknown) => setMessage(`Save failed: ${String(err)}`),
  });

  const onPinsChange = (next: ScenePin[]) => {
    setPins(next);
    setDirty(true);
  };

  return (
    <div className="mt-4 space-y-4">
      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <section className="panel">
        {/* Scene selector + preview */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {selectedSummary ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={galleryImageUrl(selectedSummary.source_image)}
                alt={selectedSummary.title}
                className="h-16 w-12 shrink-0 rounded border border-wos-border object-cover"
              />
            ) : (
              <div className="h-16 w-12 shrink-0 rounded border border-wos-border-subtle bg-wos-bg-deep" />
            )}
            <AppListbox
              label="Scene"
              options={sceneOptions}
              value={selectedSlug ?? ""}
              onChange={selectScene}
              loading={scenesQuery.isLoading}
              placeholder="Select a scene"
              minWidth={240}
              inline
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-wos-text-muted">
              <input
                type="checkbox"
                checked={activate}
                onChange={(e) => {
                  setActivate(e.target.checked);
                  setDirty(true);
                }}
              />
              Make active (the scene the solver taps)
            </label>
            <Button
              variant="primary"
              disabled={!detail || !dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              {saveMutation.isPending
                ? "Saving…"
                : dirty
                  ? "Save scene"
                  : "Saved"}
            </Button>
          </div>
        </div>

        {detail ? (
          <div className="space-y-5">
            <ScenePointEditor
              imageUrl={sceneSrc}
              pins={pins}
              selectedN={selectedN}
              onSelectN={setSelectedN}
              onChange={onPinsChange}
              imageFooter={
                <PreviewSelector
                  urls={previewUrls}
                  activeIndex={activePreviewIndex}
                  onSelect={setPreviewIndex}
                />
              }
              listHeader={
                <p className="meta">
                  {detail.title} · {detail.points.length} saved point(s)
                </p>
              }
            />
          </div>
        ) : (
          <p className="meta">
            {sceneQuery.isLoading ? "Loading scene…" : "Select a scene to edit."}
          </p>
        )}
      </section>

      {detail ? (
        <section className="panel">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">
              Calibration{" "}
              <span className="text-sm font-normal text-wos-text-muted">
                (where the guide maps onto the game screen)
              </span>
            </h2>
            <label className="flex items-center gap-2 text-xs text-wos-text-muted">
              Guide opacity
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={sceneOpacity}
                onChange={(e) => setSceneOpacity(Number(e.target.value))}
              />
            </label>
          </div>
          <div className="mx-auto w-full max-w-[320px]">
            <SceneCalibrator
              frameWidth={FRAME_W}
              frameHeight={FRAME_H}
              backgroundUrl={calibBg}
              sceneUrl={sceneSrc}
              rect={rect}
              onRectChange={(r) => {
                setRect(r);
                setDirty(true);
              }}
              opacity={sceneOpacity}
              points={calibPoints}
              hovered={selectedN}
              onHover={setSelectedN}
            />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function PreviewSelector({
  urls,
  activeIndex,
  onSelect,
}: {
  urls: string[];
  activeIndex: number;
  onSelect: (index: number) => void;
}) {
  if (urls.length <= 1) return null;
  return (
    <div className="flex max-w-full items-center gap-2 overflow-auto pb-1">
      <span className="shrink-0 text-xs text-wos-text-muted">Preview</span>
      {urls.map((url, i) => (
        <button
          key={`${i}-${url}`}
          type="button"
          onClick={() => onSelect(i)}
          title={`Use preview ${i + 1} for point placement`}
          className={`relative h-14 w-10 shrink-0 overflow-hidden rounded border transition ${
            i === activeIndex
              ? "border-wos-accent ring-1 ring-wos-accent"
              : "border-wos-border hover:border-wos-border-hover"
          }`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={`preview ${i + 1}`} className="h-full w-full object-cover" />
          <span className="absolute bottom-0 right-0 bg-black/60 px-1 text-[10px] leading-tight text-white">
            {i + 1}
          </span>
        </button>
      ))}
    </div>
  );
}
