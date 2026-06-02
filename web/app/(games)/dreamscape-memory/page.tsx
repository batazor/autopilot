"use client";

import Image from "next/image";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { LiveEditorTab } from "@/components/dreamscape/LiveEditorTab";
import {
  DREAMSCAPE_ACTIVE,
  DREAMSCAPE_ARCHIVE,
  type DreamscapeScene,
} from "@/lib/dreamscape";

type Rotation = "active" | "archive";
type View = "guides" | "live";

const ROTATION_TABS: { key: Rotation; label: string }[] = [
  { key: "active", label: "Current event" },
  { key: "archive", label: "Archive" },
];

const VIEW_TABS: { key: View; label: string }[] = [
  { key: "guides", label: "Guides" },
  { key: "live", label: "Live editor" },
];

function SceneTile({
  scene,
  selected,
  onSelect,
}: {
  scene: DreamscapeScene;
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
        <Image
          src={scene.src}
          alt={scene.title}
          fill
          sizes="160px"
          className="object-cover"
        />
      </div>
      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        <span className="truncate text-sm font-medium">{scene.title}</span>
        <span className="shrink-0 rounded bg-wos-panel-raised px-1.5 py-0.5 text-xs text-wos-text-muted">
          {scene.images.length} map{scene.images.length === 1 ? "" : "s"}
        </span>
      </div>
    </button>
  );
}

function InteractiveScene({
  scene,
  showMarkers,
  hovered,
  onHover,
}: {
  scene: DreamscapeScene;
  showMarkers: boolean;
  hovered: number | null;
  onHover: (n: number | null) => void;
}) {
  return (
    <div
      className="relative mx-auto w-full max-w-md overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep"
      style={{ aspectRatio: `${scene.width} / ${scene.height}` }}
    >
      <Image
        src={scene.src}
        alt={`${scene.title} scene`}
        fill
        sizes="(max-width: 768px) 100vw, 448px"
        className="object-contain"
      />
      {showMarkers &&
        scene.points.map((p) => (
          <button
            key={p.n}
            type="button"
            title={`${p.n}. ${p.name}${p.tentative ? " (unconfirmed)" : ""}`}
            onMouseEnter={() => onHover(p.n)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(p.n)}
            onBlur={() => onHover(null)}
            style={{ left: `${p.xPct}%`, top: `${p.yPct}%` }}
            className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full border text-[10px] font-bold leading-none transition ${
              hovered === p.n
                ? "z-10 scale-125 border-white bg-wos-accent text-wos-on-accent"
                : p.tentative
                  ? "border-amber-300/80 bg-amber-500/70 text-black"
                  : "border-white/80 bg-black/70 text-white"
            }`}
          >
            <span className="flex h-5 w-5 items-center justify-center">{p.n}</span>
          </button>
        ))}
    </div>
  );
}

function SceneGallery({
  scene,
  onZoom,
}: {
  scene: DreamscapeScene;
  onZoom: (src: string) => void;
}) {
  const [showMarkers, setShowMarkers] = useState(true);
  const [hovered, setHovered] = useState<number | null>(null);

  return (
    <div className="panel">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">{scene.title}</h2>
        <span className="text-sm text-wos-text-muted">
          {scene.points.length} items · {scene.images.length} map
          {scene.images.length === 1 ? "" : "s"} · {scene.width}×{scene.height}
        </span>
      </div>
      <p className="mb-4 text-sm text-wos-text-muted">
        Item names &amp; locations are identical for every player within a scene —
        these are 1:1 references. Hover a marker or list row to highlight it.
      </p>

      {scene.points.length > 0 ? (
        <div className="mb-5 grid gap-4 md:grid-cols-[auto_1fr]">
          <div>
            <label className="mb-2 flex items-center gap-2 text-sm text-wos-text-muted">
              <input
                type="checkbox"
                checked={showMarkers}
                onChange={(e) => setShowMarkers(e.target.checked)}
              />
              Show markers
            </label>
            <InteractiveScene
              scene={scene}
              showMarkers={showMarkers}
              hovered={hovered}
              onHover={setHovered}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-semibold">
              Items ({scene.points.length})
            </h3>
            <ol className="grid max-h-[60vh] grid-cols-1 gap-x-4 gap-y-0.5 overflow-auto pr-2 text-sm sm:grid-cols-2">
              {scene.points.map((p) => (
                <li
                  key={p.n}
                  onMouseEnter={() => setHovered(p.n)}
                  onMouseLeave={() => setHovered(null)}
                  className={`flex items-baseline gap-2 rounded px-1 py-0.5 ${
                    hovered === p.n ? "bg-wos-option-hover" : ""
                  }`}
                >
                  <span className="w-5 shrink-0 text-right text-wos-text-muted">
                    {p.n}
                  </span>
                  <span>{p.name}</span>
                  {p.tentative ? (
                    <span
                      className="text-amber-400"
                      title="Community-flagged as unconfirmed"
                    >
                      ?
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        </div>
      ) : (
        <p className="mb-4 rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          No marker data for this scene yet — the screenshots below are still 1:1
          references.
        </p>
      )}

      <h3 className="mb-2 text-sm font-semibold">
        Stage screenshots ({scene.images.length})
      </h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {scene.images.map((src, i) => (
          <button
            key={src}
            type="button"
            onClick={() => onZoom(src)}
            className="group relative aspect-[3/4] overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep transition hover:border-wos-accent"
          >
            <Image
              src={src}
              alt={`${scene.title} map ${i + 1}`}
              fill
              sizes="(max-width: 640px) 50vw, 240px"
              className="object-cover transition group-hover:scale-[1.03]"
            />
            <span className="absolute left-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">
              {i === 0 ? "Scene" : `Map ${i}`}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Lightbox({
  scene,
  src,
  onClose,
  onNav,
}: {
  scene: DreamscapeScene;
  src: string;
  onClose: () => void;
  onNav: (dir: -1 | 1) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onNav(-1);
      if (e.key === "ArrowRight") onNav(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onNav]);

  const idx = scene.images.indexOf(src);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-full max-w-3xl flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex w-full items-center justify-between text-sm text-white/80">
          <span>
            {scene.title} — {idx === 0 ? "Scene" : `Map ${idx}`} ({idx + 1}/
            {scene.images.length})
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 hover:bg-white/10"
          >
            Close ✕
          </button>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={scene.title}
          className="max-h-[80vh] w-auto rounded-lg object-contain"
        />
        {scene.images.length > 1 ? (
          <div className="mt-3 flex gap-3">
            <button
              type="button"
              onClick={() => onNav(-1)}
              className="rounded border border-white/30 px-3 py-1 text-white hover:bg-white/10"
            >
              ← Prev
            </button>
            <button
              type="button"
              onClick={() => onNav(1)}
              className="rounded border border-white/30 px-3 py-1 text-white hover:bg-white/10"
            >
              Next →
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DreamscapePageInner() {
  const params = useSearchParams();
  const [view, setView] = useState<View>("guides");
  const [rotation, setRotation] = useState<Rotation>("active");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);

  const scenes = rotation === "active" ? DREAMSCAPE_ACTIVE : DREAMSCAPE_ARCHIVE;

  // Deep-link: ?scene=ballroom
  useEffect(() => {
    const slug = params.get("scene")?.trim();
    if (!slug) return;
    const all = [...DREAMSCAPE_ACTIVE, ...DREAMSCAPE_ARCHIVE];
    const match = all.find((s) => s.slug === slug);
    if (match) {
      setRotation(match.active ? "active" : "archive");
      setSelectedSlug(slug);
    }
  }, [params]);

  // Keep a valid selection when switching rotation.
  useEffect(() => {
    if (selectedSlug && !scenes.some((s) => s.slug === selectedSlug)) {
      setSelectedSlug(scenes[0]?.slug ?? null);
    }
    if (!selectedSlug) setSelectedSlug(scenes[0]?.slug ?? null);
  }, [scenes, selectedSlug]);

  const selected = useMemo(
    () => scenes.find((s) => s.slug === selectedSlug) ?? null,
    [scenes, selectedSlug],
  );

  const navZoom = (dir: -1 | 1) => {
    if (!selected || !zoom) return;
    const i = selected.images.indexOf(zoom);
    const next = (i + dir + selected.images.length) % selected.images.length;
    setZoom(selected.images[next]);
  };

  return (
    <>
      <PageHeader title="Dreamscape Memory">
        Item-location guides for the Dreamscape Memory scavenger-hunt event.
        Pick a scene to view its hidden-item maps. Source:{" "}
        <a
          href="https://wostools.net/wiki/events/dreamscape-memory"
          target="_blank"
          rel="noreferrer"
          className="text-wos-link hover:text-wos-link-hover"
        >
          wostools.net
        </a>
        .
      </PageHeader>

      <AppTabs
        tabs={VIEW_TABS}
        selectedKey={view}
        onChange={(key) => setView(key as View)}
        renderPanels={false}
      />

      {view === "live" ? (
        <FleetContextProvider>
          <LiveEditorTab />
        </FleetContextProvider>
      ) : (
        <DreamscapeGuides
          scenes={scenes}
          rotation={rotation}
          setRotation={setRotation}
          selectedSlug={selectedSlug}
          setSelectedSlug={setSelectedSlug}
          selected={selected}
          onZoom={setZoom}
        />
      )}

      {view === "guides" && selected && zoom ? (
        <Lightbox
          scene={selected}
          src={zoom}
          onClose={() => setZoom(null)}
          onNav={navZoom}
        />
      ) : null}
    </>
  );
}

function DreamscapeGuides({
  scenes,
  rotation,
  setRotation,
  selectedSlug,
  setSelectedSlug,
  selected,
  onZoom,
}: {
  scenes: DreamscapeScene[];
  rotation: Rotation;
  setRotation: (r: Rotation) => void;
  selectedSlug: string | null;
  setSelectedSlug: (s: string) => void;
  selected: DreamscapeScene | null;
  onZoom: (src: string) => void;
}) {
  return (
    <>
      <AppTabs
        tabs={ROTATION_TABS}
        selectedKey={rotation}
        onChange={(key) => setRotation(key as Rotation)}
        renderPanels={false}
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-[300px_1fr]">
        <section className="panel">
          <p className="meta mb-3">{scenes.length} scenes</p>
          <div className="grid grid-cols-2 gap-2">
            {scenes.map((scene) => (
              <SceneTile
                key={scene.slug}
                scene={scene}
                selected={scene.slug === selectedSlug}
                onSelect={() => setSelectedSlug(scene.slug)}
              />
            ))}
          </div>
        </section>

        {selected ? (
          <SceneGallery scene={selected} onZoom={onZoom} />
        ) : (
          <section className="panel">
            <p className="meta">Select a scene to view its item-location maps.</p>
          </section>
        )}
      </div>

      <section className="panel mt-4 text-sm text-wos-text-muted">
        <h2 className="mb-2 text-base font-semibold text-wos-text">
          Free &amp; community-powered 💛
        </h2>
        <p className="mb-2">
          Dreamscape Memory automation is <strong>free in the bot</strong> — the
          item-location maps come from the community, not from us. Full credit to{" "}
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
        <p className="mb-2">
          We give back: this bot helps keep the community&apos;s
          information up to date, and the maps here can be used to{" "}
          <strong>train object-detection on the source site</strong> so item
          spots are found automatically across scenes.
        </p>
        <p>
          Found a new scene or a wrong marker? Contribute it back to{" "}
          <a
            href="https://wostools.net/wiki/events/dreamscape-memory"
            target="_blank"
            rel="noreferrer"
            className="text-wos-link hover:text-wos-link-hover"
          >
            wostools.net
          </a>{" "}
          and re-run{" "}
          <code className="rounded bg-wos-panel-raised px-1">
            web/scripts/fetch_dreamscape.py
          </code>{" "}
          to refresh.
        </p>
      </section>
    </>
  );
}

export default function DreamscapeMemoryPage() {
  return (
    <Suspense fallback={null}>
      <DreamscapePageInner />
    </Suspense>
  );
}
