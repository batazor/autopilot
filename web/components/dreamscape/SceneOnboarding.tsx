"use client";

/* eslint-disable @next/next/no-img-element */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import { SceneCalibrator } from "@/components/dreamscape/SceneCalibrator";
import type { EditorRegion } from "@/lib/bbox";
import { defaultRegion } from "@/lib/labeling-utils";
import { DREAMSCAPE_WORDS_REF } from "@/lib/dreamscape-live";
import { Button } from "./Button";
import {
  detectDreamscapeMarkers,
  galleryImageUrl,
  overlayTestImageUrl,
  parseDreamscapeNames,
  saveDreamscapeScene,
  uploadDreamscapeSceneImage,
} from "@/lib/api";

const FRAME_W = 720;
const FRAME_H = 1280;

/** A pin on the guide image: number, name, position (% of guide image). */
type Pin = {
  n: number;
  name: string;
  xPct: number;
  yPct: number;
  conf: number | null;
  /** False = name had no OCR'd marker; operator must place it. */
  placed: boolean;
};

function slugify(title: string): string {
  return (
    title
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "scene"
  );
}

/** Onboard a Dreamscape scene into the solver's scene database: persist a numbered
 * guide image, OCR its markers, paste the item-name list, calibrate where the
 * scene sits in the game frame, and save. */
export function SceneOnboarding({ onClose }: { onClose: () => void }) {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const queryClient = useQueryClient();

  const [title, setTitle] = useState("");
  const slug = useMemo(() => slugify(title), [title]);

  // ── Step 1: guide image ──
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [sourceImage, setSourceImage] = useState<string | null>(null); // repo-rel, after upload
  const fileInput = useRef<HTMLInputElement>(null);
  const objectUrlRef = useRef<string | null>(null);

  // ── Pins (markers joined with names) ──
  const [pins, setPins] = useState<Pin[]>([]);
  const [selectedN, setSelectedN] = useState<number | null>(null);
  const [missing, setMissing] = useState<number[]>([]);

  // ── Names ──
  const [namesText, setNamesText] = useState("");
  const [nameWarnings, setNameWarnings] = useState<string[]>([]);

  // ── Calibration (scene rectangle on a live game frame) ──
  const [liveNonce, setLiveNonce] = useState(0);
  const [bgLive, setBgLive] = useState(false);
  const [sceneOpacity, setSceneOpacity] = useState(0.05);
  const [rect, setRect] = useState<EditorRegion>(() => {
    const r = defaultRegion(FRAME_W, FRAME_H, "scene_rect");
    r.bbox = { ...r.bbox, x: 0, y: 6, width: 100, height: 72 };
    return r;
  });

  // Calibration background: a real game-screen reference behind the guide. The
  // practice-level screenshot by default; the live device frame when toggled.
  const calibrationBg = useMemo(
    () =>
      bgLive && instanceId
        ? overlayTestImageUrl(instanceId, liveNonce)
        : galleryImageUrl(DREAMSCAPE_WORDS_REF),
    [bgLive, instanceId, liveNonce],
  );

  const [activate, setActivate] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, [onClose]);

  const loadImageBlob = (blob: Blob, file: File | null) => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(blob);
    objectUrlRef.current = url;
    setImageUrl(url);
    setImageFile(file);
    setSourceImage(null);
    setPins([]);
    setMissing([]);
    setError(null);
  };

  const onPickImage = (file: File) => {
    loadImageBlob(file, file);
    if (!title.trim()) setTitle(file.name.replace(/\.[^.]+$/, ""));
  };

  const captureGuideFromDevice = async () => {
    if (!instanceId) return;
    setBusy("capture");
    setError(null);
    try {
      const res = await fetch(overlayTestImageUrl(instanceId, Date.now()));
      if (!res.ok) throw new Error(`no live frame (${res.status})`);
      const blob = await res.blob();
      loadImageBlob(blob, new File([blob], `${slug}.png`, { type: "image/png" }));
    } catch (e) {
      setError(`Capture failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const addToCollection = async () => {
    if (!imageFile) return;
    setBusy("upload");
    setError(null);
    try {
      const res = await uploadDreamscapeSceneImage(slug, imageFile);
      setSourceImage(res.source_image);
      setMessage(`Image added to collection: ${res.source_image}`);
    } catch (e) {
      setError(`Add to collection failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  // ── Join markers + names into pins ──
  const expectedCount = useMemo(() => {
    const ns = pins.map((p) => p.n);
    return ns.length ? Math.max(...ns) : undefined;
  }, [pins]);

  const detect = async () => {
    if (!imageFile) return;
    setBusy("detect");
    setError(null);
    try {
      const res = await detectDreamscapeMarkers(imageFile, { expected: expectedCount });
      // Preserve any names already typed/parsed by joining on n.
      const nameByN = new Map(pins.map((p) => [p.n, p.name] as const));
      const next: Pin[] = res.markers.map((m) => ({
        n: m.value,
        name: nameByN.get(m.value) ?? "",
        xPct: m.xPct,
        yPct: m.yPct,
        conf: m.conf,
        placed: true,
      }));
      setPins(next.sort((a, b) => a.n - b.n));
      setMissing(res.missing);
      setMessage(
        `Detected ${res.markers.length} marker(s) (psm ${res.psm})` +
          (res.missing.length ? ` · missing ${res.missing.join(", ")}` : ""),
      );
    } catch (e) {
      setError(`Detect failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const parseNames = async () => {
    setBusy("names");
    setError(null);
    try {
      const res = await parseDreamscapeNames(namesText);
      setNameWarnings(res.warnings);
      // Merge names onto existing detected pins; create center pins for names
      // with no detected marker so the operator can place them.
      const markerByN = new Map(pins.map((p) => [p.n, p] as const));
      const next: Pin[] = res.items.map((it) => {
        const m = markerByN.get(it.n);
        return {
          n: it.n,
          name: it.name,
          xPct: m?.xPct ?? 50,
          yPct: m?.yPct ?? 50,
          conf: m?.conf ?? null,
          placed: m != null,
        };
      });
      setPins(next.sort((a, b) => a.n - b.n));
      setMessage(`Parsed ${res.items.length} name(s).`);
    } catch (e) {
      setError(`Parse failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  // ── Pin editing on the guide image ──
  const onImageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const xPct = Math.round(((e.clientX - r.left) / r.width) * 10000) / 100;
    const yPct = Math.round(((e.clientY - r.top) / r.height) * 10000) / 100;
    if (selectedN != null) {
      // Relocate the selected pin.
      setPins((prev) =>
        prev.map((p) => (p.n === selectedN ? { ...p, xPct, yPct, placed: true } : p)),
      );
      return;
    }
    // Add a new pin with the next free number.
    const nextN = pins.length ? Math.max(...pins.map((p) => p.n)) + 1 : 1;
    setPins((prev) =>
      [...prev, { n: nextN, name: "", xPct, yPct, conf: null, placed: true }].sort(
        (a, b) => a.n - b.n,
      ),
    );
    setSelectedN(nextN);
  };

  const renamePin = (n: number, name: string) =>
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, name } : p)));
  const deletePin = (n: number) => {
    setPins((prev) => prev.filter((p) => p.n !== n));
    if (selectedN === n) setSelectedN(null);
  };

  const unplaced = pins.filter((p) => !p.placed).length;
  const unnamed = pins.filter((p) => !p.name.trim()).length;

  const save = async () => {
    if (!sourceImage) {
      setError("Add the image to the collection first.");
      return;
    }
    if (!pins.length) {
      setError("No points to save.");
      return;
    }
    setBusy("save");
    setError(null);
    try {
      const res = await saveDreamscapeScene(slug, {
        title: title.trim() || slug,
        source_image: sourceImage,
        scene_rect: {
          left: rect.bbox.x,
          top: rect.bbox.y,
          width: rect.bbox.width,
          height: rect.bbox.height,
        },
        points: pins.map((p) => ({ n: p.n, name: p.name, xPct: p.xPct, yPct: p.yPct })),
        activate,
      });
      setMessage(
        `Saved "${res.slug}" — ${res.point_count} point(s)` +
          (res.active === res.slug ? " · active" : "") +
          ".",
      );
      // Refresh the scene card list so the new scene shows immediately.
      void queryClient.invalidateQueries({ queryKey: ["dreamscape-scenes"] });
      void queryClient.invalidateQueries({ queryKey: ["dreamscape-scene", slug] });
    } catch (e) {
      setError(`Save failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        className="panel flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Onboard a scene → solver DB</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-sm hover:bg-white/10"
          >
            Close ✕
          </button>
        </div>

        {error ? (
          <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-400">
            {error}
          </p>
        ) : null}
        {message ? (
          <p className="mb-3 rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-1.5 text-sm text-wos-text-muted">
            {message}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto pr-1">
          {/* Step 1 — image */}
          <section className="mb-4">
            <h3 className="mb-2 text-sm font-semibold">1 · Scene image</h3>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-xs text-wos-text-muted">
                Scene title
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Yard"
                  className="w-56 rounded border border-wos-border bg-wos-bg-deep px-2 py-1.5 text-sm text-wos-text"
                />
              </label>
              <span className="text-xs text-wos-text-muted">
                slug: <code className="rounded bg-wos-panel-raised px-1">{slug}</code>
              </span>
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onPickImage(f);
                  e.target.value = "";
                }}
              />
              <Button variant="secondary" onClick={() => fileInput.current?.click()}>
                {imageUrl ? "Replace image" : "Upload numbered guide"}
              </Button>
              <AppListbox
                label="Device"
                options={instances.map((id) => ({ value: id, label: id }))}
                value={instanceId}
                onChange={setInstanceId}
                loading={instancesLoading}
                placeholder="Select"
                inline
              />
              <Button
                variant="secondary"
                disabled={!instanceId || busy === "capture"}
                onClick={captureGuideFromDevice}
              >
                {busy === "capture" ? "Capturing…" : "Capture from device"}
              </Button>
              <Button
                variant="accent"
                disabled={!imageFile || busy === "upload"}
                onClick={addToCollection}
                title="Persist the guide image into the module's reference collection"
              >
                {busy === "upload" ? "Adding…" : sourceImage ? "✓ In collection" : "Add to collection"}
              </Button>
            </div>
          </section>

          {imageUrl ? (
            <div className="grid min-h-0 gap-4 md:grid-cols-[1fr_300px]">
              {/* Guide image with pins */}
              <div>
                <div className="mb-2 flex items-center gap-2 text-xs text-wos-text-muted">
                  <span>
                    {selectedN != null
                      ? `Click the image to move pin #${selectedN}`
                      : "Click a pin to select it, or click empty space to add one"}
                  </span>
                  {selectedN != null ? (
                    <button
                      type="button"
                      className="rounded border border-wos-border px-1.5 hover:border-wos-border-hover"
                      onClick={() => setSelectedN(null)}
                    >
                      deselect
                    </button>
                  ) : null}
                </div>
                <div
                  className="relative mx-auto w-full max-w-md cursor-crosshair select-none overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep"
                  onClick={onImageClick}
                >
                  <img
                    src={imageUrl}
                    alt="guide"
                    className="pointer-events-none block h-auto w-full"
                  />
                  {pins.map((p) => (
                    <button
                      key={p.n}
                      type="button"
                      title={`${p.n}. ${p.name || "(unnamed)"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedN((cur) => (cur === p.n ? null : p.n));
                      }}
                      style={{ left: `${p.xPct}%`, top: `${p.yPct}%` }}
                      className={`absolute flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border text-[10px] font-bold leading-none ${
                        selectedN === p.n
                          ? "z-10 scale-125 border-white bg-wos-accent text-wos-on-accent"
                          : !p.placed
                            ? "border-amber-300/80 bg-amber-500/80 text-black"
                            : p.conf != null && p.conf < 0.5
                              ? "border-orange-300/80 bg-orange-600/80 text-white"
                              : "border-white/80 bg-black/70 text-white"
                      }`}
                    >
                      {p.n}
                    </button>
                  ))}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    disabled={!imageFile || busy === "detect"}
                    onClick={detect}
                  >
                    {busy === "detect" ? "Detecting…" : "2 · Detect numbers (OCR)"}
                  </Button>
                  {missing.length ? (
                    <span className="rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-400">
                      missing: {missing.join(", ")}
                    </span>
                  ) : null}
                  {unplaced ? (
                    <span className="rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-400">
                      {unplaced} unplaced (amber) — click to position
                    </span>
                  ) : null}
                  {unnamed ? (
                    <span className="rounded bg-wos-panel-raised px-2 py-1 text-xs text-wos-text-muted">
                      {unnamed} unnamed
                    </span>
                  ) : null}
                </div>
              </div>

              {/* Names + pin list */}
              <div className="min-h-0">
                <h3 className="mb-1 text-sm font-semibold">3 · Item names</h3>
                <p className="meta mb-1">Paste the sheet&apos;s numbered list.</p>
                <textarea
                  value={namesText}
                  onChange={(e) => setNamesText(e.target.value)}
                  rows={5}
                  placeholder={"1. Parachutte\n2. Envelope\n3. Pipe\n…"}
                  className="w-full rounded border border-wos-border bg-wos-bg-deep px-2 py-1.5 font-mono text-xs text-wos-text"
                />
                <Button
                  variant="secondary"
                  className="mt-1"
                  disabled={busy === "names"}
                  onClick={parseNames}
                >
                  {busy === "names" ? "Parsing…" : "Parse & join"}
                </Button>
                {nameWarnings.length ? (
                  <ul className="mt-1 space-y-0.5 text-xs text-amber-400">
                    {nameWarnings.map((w) => (
                      <li key={w}>⚠ {w}</li>
                    ))}
                  </ul>
                ) : null}

                <p className="meta mb-1 mt-3">{pins.length} point(s)</p>
                <ol className="max-h-48 space-y-1 overflow-auto pr-1">
                  {pins.map((p) => (
                    <li key={p.n} className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setSelectedN((c) => (c === p.n ? null : p.n))}
                        className={`w-6 shrink-0 rounded text-right text-xs ${
                          selectedN === p.n ? "text-wos-accent" : "text-wos-text-muted"
                        }`}
                      >
                        {p.n}
                      </button>
                      <input
                        type="text"
                        value={p.name}
                        onChange={(e) => renamePin(p.n, e.target.value)}
                        placeholder={`Item ${p.n}`}
                        className="min-w-0 flex-1 rounded border border-wos-border bg-wos-bg-deep px-2 py-1 text-sm text-wos-text"
                      />
                      <button
                        type="button"
                        onClick={() => deletePin(p.n)}
                        className="rounded px-1.5 text-sm text-wos-text-muted hover:text-rose-400"
                        title="Remove"
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          ) : (
            <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-wos-border text-sm text-wos-text-muted">
              Upload or capture a numbered guide image to begin.
            </div>
          )}

          {/* Step 4 — calibration */}
          {imageUrl ? (
            <section className="mt-5">
              <h3 className="mb-1 text-sm font-semibold">4 · Calibrate scene rectangle</h3>
              <p className="meta mb-2">
                The background is a real 720×1280 game screen; the guide image is
                overlaid as a movable/resizable region. Drag &amp; size it so the
                scene art lines up with the real screen — this fixes cropped guides,
                since point %s are mapped through this box onto the full frame.
              </p>
              <div className="grid gap-3 md:grid-cols-[320px_1fr]">
                <div className="mx-auto w-full max-w-[280px]">
                  <SceneCalibrator
                    frameWidth={FRAME_W}
                    frameHeight={FRAME_H}
                    backgroundUrl={calibrationBg}
                    sceneUrl={imageUrl}
                    rect={rect}
                    onRectChange={setRect}
                    opacity={sceneOpacity}
                  />
                </div>
                <div className="space-y-2 text-xs text-wos-text-muted">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={bgLive}
                      onChange={(e) => setBgLive(e.target.checked)}
                      disabled={!instanceId}
                    />
                    Use live device frame as background
                  </label>
                  {bgLive && instanceId ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setLiveNonce((n) => n + 1)}
                    >
                      Refresh live frame
                    </Button>
                  ) : null}
                  <label className="flex items-center gap-2">
                    Guide opacity
                    <input
                      type="range"
                      min={0.05}
                      max={1}
                      step={0.05}
                      value={sceneOpacity}
                      onChange={(e) => setSceneOpacity(Number(e.target.value))}
                    />
                    <span>{Math.round(sceneOpacity * 100)}%</span>
                  </label>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() =>
                      setRect((r) => ({
                        ...r,
                        bbox: { ...r.bbox, x: 0, y: 0, width: 100, height: 100 },
                      }))
                    }
                  >
                    Reset to full frame
                  </Button>
                  <p>
                    rect: left {rect.bbox.x.toFixed(1)}% · top {rect.bbox.y.toFixed(1)}% ·
                    w {rect.bbox.width.toFixed(1)}% · h {rect.bbox.height.toFixed(1)}%
                  </p>
                </div>
              </div>
            </section>
          ) : null}
        </div>

        {/* Footer — save */}
        <div className="mt-3 flex items-center justify-end gap-3 border-t border-wos-border-subtle pt-3">
          <label className="flex items-center gap-2 text-sm text-wos-text-muted">
            <input
              type="checkbox"
              checked={activate}
              onChange={(e) => setActivate(e.target.checked)}
            />
            Set active (the scene the bot solves)
          </label>
          <Button
            variant="primary"
            className="px-4"
            disabled={!sourceImage || pins.length === 0 || busy === "save"}
            onClick={save}
          >
            {busy === "save" ? "Saving…" : "5 · Save scene"}
          </Button>
        </div>
      </div>
    </div>
  );
}
