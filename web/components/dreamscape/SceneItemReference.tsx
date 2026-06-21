"use client";

/* eslint-disable @next/next/no-img-element */
import { useMemo, useState } from "react";
import { galleryImageUrl } from "@/lib/api";
import type { DreamscapeSceneDetail } from "@/lib/types";

/** Preview URL for a gallery image: the onboarded repo image when present, else
 * the community public asset seeded by the static catalog (image is empty for
 * community-imported scenes). Mirrors ``scenePreviewUrl`` in DreamscapeGuides. */
function imageUrl(slug: string, img: string): string {
  return img ? galleryImageUrl(img) : `/dreamscape/${slug}.webp`;
}

type Props = {
  detail: DreamscapeSceneDetail;
  /** Highlighted item number — shared with the editor's pins/calibrator. */
  hovered: number | null;
  onHover: (n: number | null) => void;
};

/** Read-only item-set reference for a scene: the intro note, a gallery switcher
 * to flip through the scene's reference-image variants, the (primary) image with
 * numbered item markers, and a hover-synced item list. Mirrors the Guides scene
 * panel so operators can consult the established item set while placing points. */
export function SceneItemReference({ detail, hovered, onHover }: Props) {
  // Selected gallery image (0 = primary / item-mapped image).
  const [imgIndex, setImgIndex] = useState(0);

  // Full image list (primary first); single-image scenes fall back to primary.
  // Only image 0 carries the item map, so markers render on the primary only.
  const images = useMemo(() => {
    const list = detail.images.length
      ? detail.images
      : [detail.source_image].filter(Boolean);
    return list.length ? list : [""]; // sentinel → community public asset
  }, [detail.images, detail.source_image]);

  // Stable URLs (galleryImageUrl is cache-busted per call — memoize to avoid an
  // image-reload loop on every hover-driven re-render).
  const urls = useMemo(
    () => images.map((img) => imageUrl(detail.slug, img)),
    [images, detail.slug],
  );

  const activeIndex = Math.min(imgIndex, Math.max(0, images.length - 1));
  const isPrimary = activeIndex === 0;

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold">Item set reference</h2>
        <span className="text-sm text-wos-text-muted">
          {detail.points.length} item{detail.points.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="mb-4 text-sm text-wos-text-muted">
        Item names &amp; locations are identical for every player within a scene.
        Hover a marker or list row to highlight it.
      </p>

      <div className="grid gap-4 md:grid-cols-[auto_1fr]">
        <div className="w-full max-w-md space-y-2">
          {images.length > 1 ? (
            <div className="flex gap-2">
              {urls.map((url, i) => (
                <button
                  key={`${i}-${url}`}
                  type="button"
                  onClick={() => setImgIndex(i)}
                  title={
                    i === 0
                      ? "Mapped view (item locations)"
                      : `Reference view ${i + 1}`
                  }
                  className={`relative h-16 w-12 shrink-0 overflow-hidden rounded border transition ${
                    i === activeIndex
                      ? "border-wos-accent ring-1 ring-wos-accent"
                      : "border-wos-border hover:border-wos-border-hover"
                  }`}
                >
                  <img
                    src={url}
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

          <div className="relative w-full select-none overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep">
            <img
              src={urls[activeIndex]}
              alt={`${detail.title} — reference view ${activeIndex + 1}`}
              className="block h-auto w-full"
            />
            {isPrimary
              ? detail.points.map((p) => {
                  const on = hovered === p.n;
                  return (
                    <span
                      key={p.n}
                      title={`${p.n}. ${p.name || `Item ${p.n}`}`}
                      onMouseEnter={() => onHover(p.n)}
                      onMouseLeave={() => onHover(null)}
                      style={{ left: `${p.xPct}%`, top: `${p.yPct}%` }}
                      className={`absolute flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-default items-center justify-center rounded-full border text-[10px] font-bold leading-none ${
                        on
                          ? "z-10 scale-125 border-white bg-orange-500 text-white"
                          : "border-white/80 bg-black/70 text-white"
                      }`}
                    >
                      {p.n}
                    </span>
                  );
                })
              : null}
          </div>

          {!isPrimary ? (
            <p className="meta">Reference view — the item map is on image 1.</p>
          ) : null}
        </div>

        <div>
          <h3 className="mb-2 text-sm font-semibold">
            Items ({detail.points.length})
          </h3>
          <ol className="grid max-h-[60vh] grid-cols-1 gap-x-4 gap-y-0.5 overflow-auto pr-2 text-sm sm:grid-cols-2">
            {detail.points.map((p) => (
              <li
                key={p.n}
                onMouseEnter={() => onHover(p.n)}
                onMouseLeave={() => onHover(null)}
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
                <span
                  className={hovered === p.n ? "font-medium text-orange-400" : ""}
                >
                  {p.name || `Item ${p.n}`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
