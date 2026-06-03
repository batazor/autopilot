"use client";

/* eslint-disable @next/next/no-img-element */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchDreamscapeScene, fetchDreamscapeScenes, galleryImageUrl } from "@/lib/api";

function ActiveBadge() {
  return (
    <span className="rounded-full bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
      active
    </span>
  );
}

function SceneDetailView({ slug }: { slug: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dreamscape-scene", slug],
    queryFn: () => fetchDreamscapeScene(slug),
  });

  if (isLoading) return <p className="meta">Loading {slug}…</p>;
  if (error || !data)
    return <p className="meta text-rose-400">Could not load {slug}.</p>;

  return (
    <div className="grid gap-4 md:grid-cols-[auto_1fr]">
      <div className="relative inline-block max-w-md">
        {data.source_image ? (
          <img
            src={galleryImageUrl(data.source_image)}
            alt={data.title}
            className="max-h-[70vh] w-auto rounded-lg border border-wos-border"
          />
        ) : (
          <div className="flex h-48 w-40 items-center justify-center rounded-lg border border-wos-border text-sm text-wos-text-muted">
            no image
          </div>
        )}
        {data.points.map((p) => (
          <span
            key={p.n}
            title={`${p.n}. ${p.name}`}
            style={{ left: `${p.xPct}%`, top: `${p.yPct}%` }}
            className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/80 bg-wos-accent px-1.5 text-[10px] font-bold leading-5 text-wos-on-accent"
          >
            {p.n}
          </span>
        ))}
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">
          Items ({data.points.length})
        </h3>
        <ol className="grid max-h-[60vh] grid-cols-1 gap-x-4 gap-y-0.5 overflow-auto pr-2 text-sm sm:grid-cols-2">
          {data.points.map((p) => (
            <li key={p.n} className="flex items-baseline gap-2">
              <span className="w-5 shrink-0 text-right text-wos-text-muted">
                {p.n}
              </span>
              <span>{p.name}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** Scenes saved to the solver's map.yaml (via the scene builder / onboarding).
 * Distinct from the static community Guides above. */
export function SavedScenes() {
  const { data, isLoading } = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const [selected, setSelected] = useState<string | null>(null);

  const scenes = data?.scenes ?? [];
  const activeSlug = selected ?? data?.active ?? scenes[0]?.slug ?? null;

  return (
    <section className="panel mt-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Saved scenes</h2>
        <span className="meta">
          {isLoading ? "loading…" : `${scenes.length} in map.yaml`}
        </span>
      </div>

      {scenes.length === 0 ? (
        <p className="meta">
          No saved scenes yet. Use <strong>+ New scene</strong> to capture a
          screenshot and save it as a scene.
        </p>
      ) : (
        <>
          <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {scenes.map((s) => (
              <button
                key={s.slug}
                type="button"
                onClick={() => setSelected(s.slug)}
                className={`flex flex-col overflow-hidden rounded-lg border text-left transition ${
                  s.slug === activeSlug
                    ? "border-wos-accent ring-1 ring-wos-accent"
                    : "border-wos-border hover:border-wos-border-hover"
                }`}
              >
                <div className="relative aspect-[3/4] w-full bg-wos-bg-deep">
                  {s.source_image ? (
                    <img
                      src={galleryImageUrl(s.source_image)}
                      alt={s.title}
                      className="h-full w-full object-cover"
                    />
                  ) : null}
                </div>
                <div className="flex items-center justify-between gap-1 px-1.5 py-1">
                  <span className="truncate text-xs font-medium">{s.title}</span>
                  {s.active ? <ActiveBadge /> : null}
                </div>
                <span className="px-1.5 pb-1 text-[10px] text-wos-text-muted">
                  {s.point_count} item{s.point_count === 1 ? "" : "s"}
                </span>
              </button>
            ))}
          </div>
          {activeSlug ? <SceneDetailView slug={activeSlug} /> : null}
        </>
      )}
    </section>
  );
}
