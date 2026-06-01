"use client";

import { Dialog, DialogBackdrop, DialogPanel } from "@headlessui/react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AppCombobox } from "@/components/headless";
import { ErrorBanner } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import {
  fetchGallery,
  fetchLabelingDocument,
  fetchLabelingScopes,
  galleryImageUrl,
  setActiveGame,
} from "@/lib/api";
import type { EditorRegion } from "@/lib/bbox";
import type { GalleryItem } from "@/lib/config-pages";
import {
  apiToEditorRegions,
  inferScopeFromRef,
} from "@/lib/labeling-utils";
import type { LabelingScopeOption } from "@/lib/types";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

// Keep in sync with config/games.py::GAMES (mirrors the labeling page).
const GAME_OPTIONS: { value: string; label: string }[] = [
  { value: "wos", label: "Whiteout Survival" },
  { value: "kingshot", label: "Kingshot" },
];

function normalizeGame(value: string | null): string {
  return GAME_OPTIONS.some((g) => g.value === value) ? (value as string) : "wos";
}

function GalleryPageInner() {
  const router = useRouter();
  const params = useSearchParams();

  const [game, setGame] = useState<string>(() =>
    normalizeGame(params.get("game")),
  );
  // References are game-specific. This page lives outside FleetContextProvider,
  // so mirror the selection into lib/api's active-game cache (read by
  // gameQueryEntries) during render — guarded — so the scope/gallery fetches
  // emit ?game= on the same commit.
  const gameSyncRef = useRef<string | null>(null);
  if (gameSyncRef.current !== game) {
    setActiveGame(game);
    gameSyncRef.current = game;
  }

  const [scopes, setScopes] = useState<LabelingScopeOption[]>([]);
  const [scope, setScope] = useState(params.get("module")?.trim() || "all");
  const [rawQuery, setRawQuery] = useState(params.get("q") ?? "");
  const [query, setQuery] = useState(rawQuery);
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<GalleryItem | null>(null);
  const [previewRegions, setPreviewRegions] = useState<EditorRegion[]>([]);
  const [previewScope, setPreviewScope] = useState<string | null>(null);
  const [showRegions, setShowRegions] = useState(true);

  useEffect(() => {
    fetchLabelingScopes()
      .then((list) => {
        setScopes(list);
        if (!list.some((s) => s.key === scope)) {
          const fallback =
            list.find((s) => s.key === "all")?.key || list[0]?.key || "all";
          setScope(fallback);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [scope, game]);

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(rawQuery.trim()), 250);
    return () => window.clearTimeout(t);
  }, [rawQuery]);

  useEffect(() => {
    const url = new URLSearchParams();
    if (game && game !== "wos") url.set("game", game);
    if (scope && scope !== "all") url.set("module", scope);
    if (query) url.set("q", query);
    const qs = url.toString();
    router.replace(qs ? `/gallery?${qs}` : "/gallery", { scroll: false });
  }, [game, scope, query, router]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGallery(scope, query)
      .then((data) => {
        if (!cancelled) {
          setItems(data.items);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setItems([]);
          setError(e.message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope, query, game]);

  const scopeOptions = useMemo(
    () =>
      scopes
        .map((s) => ({ value: s.key, label: s.label }))
        .sort((a, b) => {
          if (a.value === "all") return -1;
          if (b.value === "all") return 1;
          return a.value.localeCompare(b.value, undefined, {
            sensitivity: "base",
          });
        }),
    [scopes],
  );

  const groups = useMemo(() => {
    const byGroup = new Map<string, GalleryItem[]>();
    for (const it of items) {
      const key = it.group || "(unassigned)";
      const arr = byGroup.get(key) ?? [];
      arr.push(it);
      byGroup.set(key, arr);
    }
    return [...byGroup.entries()]
      .map(([name, list]) => ({
        name,
        list: list.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [items]);

  const clearSearch = useCallback(() => {
    setRawQuery("");
    setQuery("");
  }, []);

  const handleGameChange = useCallback((value: string) => {
    const next = normalizeGame(value);
    setGame((prev) => {
      if (prev === next) return prev;
      // Scopes/references are game-specific — reset the module filter so we
      // don't render the previous game's scope against the new game.
      setActiveGame(next);
      setScope("all");
      return next;
    });
  }, []);

  useEffect(() => {
    if (!preview) {
      setPreviewRegions([]);
      setPreviewScope(null);
      return;
    }
    const refRel = preview.rel;
    const scopeForRef = inferScopeFromRef(refRel) ?? "all";
    setPreviewScope(scopeForRef);
    let cancelled = false;
    fetchLabelingDocument(refRel, scopeForRef)
      .then((d) => {
        if (cancelled) return;
        setPreviewRegions(
          apiToEditorRegions(d.regions as Record<string, unknown>[]),
        );
      })
      .catch(() => {
        if (!cancelled) setPreviewRegions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [preview]);

  const labelingHref = useMemo(() => {
    if (!preview) return null;
    const url = new URLSearchParams();
    url.set("ref", preview.rel);
    if (previewScope) url.set("module", previewScope);
    return `/labeling?${url.toString()}`;
  }, [preview, previewScope]);

  return (
    <>
      <PageHeader title="Gallery">
        Browse reference screenshots grouped by module / screen. Search matches
        file paths and screen IDs.
      </PageHeader>

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs uppercase tracking-wide text-wos-text-muted">
              Game
            </span>
            <select
              value={game}
              onChange={(e) => handleGameChange(e.target.value)}
              className="rounded border border-wos-border bg-wos-surface px-3 py-2 text-sm text-wos-text outline-none focus:border-accent"
            >
              {GAME_OPTIONS.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>

          <div className="min-w-[220px]">
            <AppCombobox
              label="Module"
              value={scope}
              onChange={setScope}
              options={scopeOptions}
              placeholder="Search module…"
              disabled={scopes.length === 0}
            />
          </div>

          <label className="flex min-w-[280px] flex-1 flex-col gap-1">
            <span className="text-xs uppercase tracking-wide text-wos-text-muted">
              Region / path
            </span>
            <div className="relative">
              <input
                type="text"
                value={rawQuery}
                onChange={(e) => setRawQuery(e.target.value)}
                placeholder="e.g. claim_button, mail, popup_close"
                className="w-full rounded border border-wos-border bg-wos-surface px-3 py-2 text-sm text-wos-text outline-none focus:border-accent"
              />
              {rawQuery ? (
                <button
                  type="button"
                  onClick={clearSearch}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-wos-text-muted hover:text-wos-text"
                  aria-label="Clear search"
                >
                  ✕
                </button>
              ) : null}
            </div>
          </label>

          <div className="ml-auto text-sm text-wos-text-secondary">
            {loading ? (
              "Loading…"
            ) : (
              <>
                <strong className="text-wos-text">{items.length}</strong> images
                {groups.length > 0 ? (
                  <>
                    {" "}
                    in{" "}
                    <strong className="text-wos-text">{groups.length}</strong>{" "}
                    groups
                  </>
                ) : null}
              </>
            )}
          </div>
        </div>

        {error ? <ErrorBanner message={error} /> : null}

        {!loading && groups.length === 0 ? (
          <div className="rounded border border-dashed border-wos-border p-8 text-center text-sm text-wos-text-muted">
            No references match the current filters.
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-auto">
          {groups.map((g) => (
            <section key={g.name} className="flex flex-col gap-2">
              <div className="sticky top-0 z-10 flex items-baseline gap-2 bg-wos-bg pb-1">
                <h2 className="text-sm font-semibold text-wos-text">
                  {g.name}
                </h2>
                <span className="text-xs text-wos-text-muted">
                  {g.list.length}
                </span>
              </div>
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
                {g.list.map((it) => (
                  <li key={it.rel}>
                    <button
                      type="button"
                      onClick={() => setPreview(it)}
                      className="group flex w-full flex-col gap-1 rounded border border-wos-border bg-wos-surface p-2 text-left transition hover:border-accent"
                      title={it.rel}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={galleryImageUrl(it.rel)}
                        alt={it.name}
                        loading="lazy"
                        className="h-28 w-full rounded bg-black/40 object-contain"
                      />
                      <span className="truncate text-xs text-wos-text">
                        {it.name}
                      </span>
                      <span className="truncate text-[10px] text-wos-text-muted">
                        {it.screen_ids.length > 0
                          ? it.screen_ids.join(", ")
                          : "—"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>

      <Dialog
        open={preview !== null}
        onClose={() => setPreview(null)}
        className="headless-dialog-root"
      >
        <DialogBackdrop transition className="headless-dialog__backdrop" />
        <div className="headless-dialog__container">
          <DialogPanel
            transition
            className="headless-dialog__panel max-w-[90vw]"
          >
            {preview ? (
              <div className="flex flex-col gap-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-wos-text">
                      {preview.name}
                    </div>
                    <div className="truncate text-xs text-wos-text-muted">
                      {preview.rel}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-wos-text-secondary">
                      <span>group: {preview.group}</span>
                      <span>{formatBytes(preview.size_bytes)}</span>
                      {preview.screen_ids.length > 0 ? (
                        <span>screens: {preview.screen_ids.join(", ")}</span>
                      ) : null}
                      <span>regions: {previewRegions.length}</span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {previewRegions.length > 0 ? (
                      <label className="flex items-center gap-1 text-xs text-wos-text-secondary">
                        <input
                          type="checkbox"
                          checked={showRegions}
                          onChange={(e) => setShowRegions(e.target.checked)}
                        />
                        Show regions
                      </label>
                    ) : null}
                    {labelingHref ? (
                      <Link href={labelingHref} className="btn-secondary">
                        Open in Labeling
                      </Link>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setPreview(null)}
                      className="btn-secondary"
                    >
                      Close
                    </button>
                  </div>
                </div>
                <div
                  className="relative self-center overflow-hidden rounded bg-black/40"
                  style={{
                    aspectRatio: "720 / 1280",
                    maxHeight: "75vh",
                    maxWidth: "100%",
                    height: "75vh",
                  }}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={galleryImageUrl(preview.rel)}
                    alt={preview.name}
                    className="h-full w-full object-contain"
                  />
                  {showRegions
                    ? previewRegions.map((r) => (
                        <div
                          key={r.id}
                          className="pointer-events-none absolute border border-accent/90 bg-accent/10"
                          style={{
                            left: `${r.bbox.x}%`,
                            top: `${r.bbox.y}%`,
                            width: `${r.bbox.width}%`,
                            height: `${r.bbox.height}%`,
                          }}
                          title={r.name}
                        >
                          <span className="absolute left-0 top-0 -translate-y-full whitespace-nowrap rounded-t bg-accent/80 px-1 text-[10px] font-medium text-black">
                            {r.name}
                          </span>
                        </div>
                      ))
                    : null}
                </div>
              </div>
            ) : null}
          </DialogPanel>
        </div>
      </Dialog>
    </>
  );
}

export default function GalleryPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <GalleryPageInner />
    </Suspense>
  );
}
