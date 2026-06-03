"use client";

/* eslint-disable @next/next/no-img-element */
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { AppTabs } from "@/components/headless";
import { SceneCalibrator } from "@/components/dreamscape/SceneCalibrator";
import { SceneOnboarding } from "@/components/dreamscape/SceneOnboarding";
import { Button } from "@/components/dreamscape/Button";
import {
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  galleryImageUrl,
  saveDreamscapeScene,
} from "@/lib/api";
import type { EditorRegion } from "@/lib/bbox";
import { defaultRegion } from "@/lib/labeling-utils";
import { DREAMSCAPE_WORDS_REF } from "@/lib/dreamscape-live";
import type { DreamscapeSceneDetail, DreamscapeSceneSummary } from "@/lib/types";

const FRAME_W = 720;
const FRAME_H = 1280;

// Reserved ``season`` values that act as Guides categories beyond the numbered
// content seasons (kept in sync with config/dreamscape_db.py).
const PRACTICE_SEASON = 0;
const MULTIPLAYER_SEASON = 100;

/** Human label for a scene category (``season`` field). */
function categoryLabel(season: number): string {
  if (season === PRACTICE_SEASON) return "Practice game";
  if (season === MULTIPLAYER_SEASON) return "Multiplayer";
  return `Season ${season}`;
}

/** Sort key so tabs read: seasons ascending, then Multiplayer, then Practice. */
function categoryRank(season: number): number {
  if (season === PRACTICE_SEASON) return Number.MAX_SAFE_INTEGER;
  if (season === MULTIPLAYER_SEASON) return Number.MAX_SAFE_INTEGER - 1;
  return season;
}

/** Editable scene-rect region from a scene's stored rect (full frame if unset). */
function rectFromScene(detail: DreamscapeSceneDetail): EditorRegion {
  const r = defaultRegion(FRAME_W, FRAME_H, "scene_rect");
  const sr = detail.scene_rect;
  r.bbox = sr
    ? { ...r.bbox, x: sr.left, y: sr.top, width: sr.width, height: sr.height }
    : { ...r.bbox, x: 0, y: 0, width: 100, height: 100 };
  return r;
}

/** Preview URL for a scene: the onboarded repo image if present, else the
 * community public asset seeded by the static catalog (source_image is empty
 * for community-imported scenes). */
function scenePreviewUrl(slug: string, sourceImage: string): string {
  return sourceImage ? galleryImageUrl(sourceImage) : `/dreamscape/${slug}.webp`;
}

/** Scene thumbnail with graceful fallback to a titled placeholder when neither
 * a repo image nor a community asset exists for the slug. */
function ScenePreview({
  slug,
  sourceImage,
  title,
  className,
}: {
  slug: string;
  sourceImage: string;
  title: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className={`flex items-center justify-center bg-wos-panel-raised text-xs text-wos-text-muted ${className ?? ""}`}
      >
        <span className="px-2 text-center">{title}</span>
      </div>
    );
  }
  return (
    <img
      src={scenePreviewUrl(slug, sourceImage)}
      alt={title}
      className={className}
      onError={() => setFailed(true)}
    />
  );
}

function SceneTile({
  scene,
  selected,
  onSelect,
}: {
  scene: DreamscapeSceneSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex flex-col overflow-hidden rounded-lg border text-left transition ${
        selected
          ? "border-wos-accent ring-1 ring-wos-accent"
          : "border-wos-border hover:border-wos-border-hover"
      }`}
    >
      <div className="relative aspect-[3/4] w-full bg-wos-bg-deep">
        <ScenePreview
          slug={scene.slug}
          sourceImage={scene.source_image}
          title={scene.title}
          className="h-full w-full object-cover"
        />
      </div>
      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        <span className="truncate text-sm font-medium">{scene.title}</span>
        <span className="shrink-0 rounded bg-wos-panel-raised px-1.5 py-0.5 text-xs text-wos-text-muted">
          {scene.point_count} item{scene.point_count === 1 ? "" : "s"}
        </span>
      </div>
    </button>
  );
}

function SceneDetailView({ slug }: { slug: string }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [rect, setRect] = useState<EditorRegion | null>(null);
  const [opacity, setOpacity] = useState(0.05);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  // Selected gallery image (0 = primary / item-mapped image).
  const [imgIndex, setImgIndex] = useState(0);
  const queryClient = useQueryClient();
  const loadedSlug = useRef<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["dreamscape-scene", slug],
    queryFn: () => fetchDreamscapeScene(slug),
  });

  // Initialize the editable rect when a new scene loads — but keep in-progress
  // drags for the current scene (don't reset on refetch).
  useEffect(() => {
    if (data && loadedSlug.current !== data.slug) {
      loadedSlug.current = data.slug;
      setRect(rectFromScene(data));
      setImgIndex(0);
    }
  }, [data]);

  // Stable URLs for the Konva calibrator (galleryImageUrl is cache-busted, so
  // memoize to avoid an image-reload loop).
  const calibBg = useMemo(() => galleryImageUrl(DREAMSCAPE_WORDS_REF), []);
  const sceneSrc = useMemo(
    () => (data ? scenePreviewUrl(data.slug, data.source_image) : null),
    [data?.slug, data?.source_image],
  );

  if (isLoading) return <p className="meta">Loading {slug}…</p>;
  if (error || !data)
    return <p className="meta text-rose-400">Could not load {slug}.</p>;

  const scene = data;
  // Gallery: full image list (primary first); single-image scenes fall back to
  // the primary. Only image 0 carries the item map / calibration.
  const images = scene.images.length
    ? scene.images
    : [scene.source_image].filter(Boolean);
  const activeIndex = Math.min(imgIndex, Math.max(0, images.length - 1));
  const isPrimary = activeIndex === 0;

  const handleSave = async () => {
    if (!rect) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await saveDreamscapeScene(scene.slug, {
        title: scene.title,
        source_image: scene.source_image,
        scene_rect: {
          left: rect.bbox.x,
          top: rect.bbox.y,
          width: rect.bbox.width,
          height: rect.bbox.height,
        },
        points: scene.points.map((p) => ({
          n: p.n,
          name: p.name,
          xPct: p.xPct,
          yPct: p.yPct,
        })),
        activate: scene.active,
      });
      setSaveMsg("Saved scene size.");
      void queryClient.invalidateQueries({ queryKey: ["dreamscape-scene", scene.slug] });
      void queryClient.invalidateQueries({ queryKey: ["dreamscape-scenes"] });
    } catch (e) {
      setSaveMsg(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">{data.title}</h2>
        <span className="text-sm text-wos-text-muted">
          {data.points.length} item{data.points.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="mb-4 text-sm text-wos-text-muted">
        Item names &amp; locations are identical for every player within a scene.
        Hover a marker or list row to highlight it.
      </p>

      <div className="grid gap-4 md:grid-cols-[auto_1fr]">
        <div className="w-full max-w-md">
          <div className="space-y-2">
            {images.length > 1 ? (
              <div className="flex gap-2">
                {images.map((img, i) => (
                  <button
                    key={img}
                    type="button"
                    onClick={() => setImgIndex(i)}
                    title={i === 0 ? "Mapped view (item locations)" : `Reference view ${i + 1}`}
                    className={`relative h-16 w-12 shrink-0 overflow-hidden rounded border transition ${
                      i === activeIndex
                        ? "border-wos-accent ring-1 ring-wos-accent"
                        : "border-wos-border hover:border-wos-border-hover"
                    }`}
                  >
                    <img
                      src={galleryImageUrl(img)}
                      alt={`view ${i + 1}`}
                      className="h-full w-full object-cover"
                    />
                    <span className="absolute bottom-0 right-0 bg-black/60 px-1 text-[10px] leading-tight text-white">
                      {i + 1}
                    </span>
                  </button>
                ))}
              </div>
            ) : null}

            {isPrimary && rect ? (
              <SceneCalibrator
                frameWidth={FRAME_W}
                frameHeight={FRAME_H}
                backgroundUrl={calibBg}
                sceneUrl={sceneSrc}
                rect={rect}
                onRectChange={setRect}
                opacity={opacity}
                points={scene.points}
                hovered={hovered}
                onHover={setHovered}
              />
            ) : (
              <img
                src={galleryImageUrl(images[activeIndex])}
                alt={`${scene.title} — reference view ${activeIndex + 1}`}
                className="w-full rounded-lg border border-wos-border bg-wos-bg-deep"
              />
            )}

            {isPrimary ? (
              <div className="flex flex-wrap items-center gap-2 text-xs text-wos-text-muted">
                <label className="flex items-center gap-1">
                  opacity
                  <input
                    type="range"
                    min={0.05}
                    max={1}
                    step={0.05}
                    value={opacity}
                    onChange={(e) => setOpacity(Number(e.target.value))}
                  />
                </label>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    setRect((r) =>
                      r ? { ...r, bbox: { ...r.bbox, x: 0, y: 0, width: 100, height: 100 } } : r,
                    )
                  }
                >
                  Full frame
                </Button>
                <Button variant="primary" size="sm" disabled={saving} onClick={handleSave}>
                  {saving ? "Saving…" : "Save size"}
                </Button>
                <span>{scene.scene_rect ? "custom size" : "full frame"}</span>
                {saveMsg ? <span className="text-emerald-400">{saveMsg}</span> : null}
              </div>
            ) : (
              <p className="meta">Reference view — the item map is on image 1.</p>
            )}
          </div>
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold">
            Items ({data.points.length})
          </h3>
          <ol className="grid max-h-[60vh] grid-cols-1 gap-x-4 gap-y-0.5 overflow-auto pr-2 text-sm sm:grid-cols-2">
            {data.points.map((p) => (
              <li
                key={p.n}
                onMouseEnter={() => setHovered(p.n)}
                onMouseLeave={() => setHovered(null)}
                className={`flex items-baseline gap-2 rounded px-1 py-0.5 ${
                  hovered === p.n ? "bg-wos-option-hover" : ""
                }`}
              >
                <span
                  className={`w-5 shrink-0 text-right ${
                    hovered === p.n ? "text-orange-400" : "text-wos-text-muted"
                  }`}
                >
                  {p.n}
                </span>
                <span className={hovered === p.n ? "font-medium text-orange-400" : ""}>
                  {p.name}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

/** Shared scene catalog for Dreamscape Memory, driven by the solver's scene DB.
 * Item-location maps are identical across solo and multiplayer modes, so both
 * the solo page and the multiplayer page render this same component. */
export function DreamscapeGuides() {
  const [onboarding, setOnboarding] = useState(false);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  // Select a scene and reflect it in the URL (?scene=slug) so the view is
  // shareable/deep-linkable without a full navigation.
  const selectScene = (slug: string) => {
    setSelectedSlug(slug);
    const next = new URLSearchParams(params.toString());
    next.set("scene", slug);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const scenes = useMemo(() => data?.scenes ?? [], [data]);

  // Default/deep-link selection: ?scene=slug, else the active scene, else first.
  useEffect(() => {
    if (!scenes.length) return;
    setSelectedSlug((current) => {
      if (current && scenes.some((s) => s.slug === current)) return current;
      const wanted = params.get("scene")?.trim();
      if (wanted && scenes.some((s) => s.slug === wanted)) return wanted;
      return data?.active || scenes[0]?.slug || null;
    });
  }, [scenes, data, params]);

  const selected = scenes.find((s) => s.slug === selectedSlug) ?? null;

  // Group the catalog into Guides categories, keyed by the scene's ``season``
  // DB field: numbered content seasons (1, 2, 3, …) plus the reserved buckets
  // 0 = Practice game and 100 = Multiplayer (Recall Road). Display order:
  // seasons ascending, then Multiplayer, then Practice game.
  const seasons = useMemo(() => {
    const byNum: Record<number, DreamscapeSceneSummary[]> = {};
    for (const s of scenes) {
      (byNum[s.season] ??= []).push(s);
    }
    return Object.keys(byNum)
      .map(Number)
      .sort((a, b) => categoryRank(a) - categoryRank(b))
      .map((n) => ({ n, scenes: byNum[n] }));
  }, [scenes]);

  // Selected category tab; default to the open scene's category (else the
  // first) so the panel is never empty and tracks the open scene.
  const [seasonTab, setSeasonTab] = useState<number | null>(null);
  const effectiveSeason =
    seasonTab != null && seasons.some((s) => s.n === seasonTab)
      ? seasonTab
      : (selected?.season ?? seasons[0]?.n ?? null);
  const shownScenes =
    seasons.find((s) => s.n === effectiveSeason)?.scenes ?? [];
  const sceneTabs = seasons.map((s) => ({
    key: String(s.n),
    label: `${categoryLabel(s.n)} · ${s.scenes.length}`,
  }));

  const renderSceneGrid = (group: DreamscapeSceneSummary[]) => (
    <div className="grid grid-cols-2 gap-2">
      {group.map((scene) => (
        <SceneTile
          key={scene.slug}
          scene={scene}
          selected={scene.slug === selectedSlug}
          onSelect={() => selectScene(scene.slug)}
        />
      ))}
    </div>
  );

  return (
    <>
      <div className="mt-4 grid gap-4 lg:grid-cols-[300px_1fr]">
        <section className="panel">
          <div className="mb-3 flex items-center justify-between gap-2">
            <p className="meta">
              {isLoading ? "loading…" : `${scenes.length} scenes`}
            </p>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setOnboarding(true)}
              title="Create a new scene: image + OCR markers + names + calibration → solver scene DB"
            >
              + New scene
            </Button>
          </div>
          {scenes.length ? (
            <div className="flex flex-col gap-3">
              <AppTabs
                tabs={sceneTabs}
                selectedKey={String(effectiveSeason)}
                onChange={(key) => setSeasonTab(Number(key))}
                renderPanels={false}
              />
              {shownScenes.length ? (
                renderSceneGrid(shownScenes)
              ) : (
                <p className="meta">No scenes in this category.</p>
              )}
            </div>
          ) : (
            <p className="meta">
              No scenes yet — press <strong>+ New scene</strong> to onboard one.
            </p>
          )}
        </section>

        {selected ? (
          <SceneDetailView slug={selected.slug} />
        ) : (
          <section className="panel">
            <p className="meta">Select a scene to view its item-location map.</p>
          </section>
        )}
      </div>

      <section className="panel mt-4 text-sm text-wos-text-muted">
        <h2 className="mb-2 text-base font-semibold text-wos-text">
          Free &amp; community-powered 💛
        </h2>
        <p>
          Dreamscape Memory automation is <strong>free in the bot</strong>. Scenes
          shown here are onboarded into the solver&apos;s scene DB; item-location
          maps come from the community. Credit to{" "}
          <a
            href="https://wostools.net/wiki/events/dreamscape-memory"
            target="_blank"
            rel="noreferrer"
            className="text-wos-link hover:text-wos-link-hover"
          >
            wostools.net
          </a>{" "}
          for maintaining the scene guides.
        </p>
      </section>

      {onboarding ? (
        <FleetContextProvider>
          <SceneOnboarding onClose={() => setOnboarding(false)} />
        </FleetContextProvider>
      ) : null}
    </>
  );
}
