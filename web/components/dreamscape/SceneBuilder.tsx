"use client";

/* eslint-disable @next/next/no-img-element */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import {
  overlayTestImageUrl,
  saveDreamscapeScene,
  uploadDreamscapeSceneImage,
} from "@/lib/api";

type BuilderPoint = { n: number; name: string; xPct: number; yPct: number };

function slugify(title: string): string {
  return (
    title
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "scene"
  );
}

/** Client-side Dreamscape scene builder: upload an image, click to drop named
 * item markers, then export the scene as JSON to contribute upstream. Nothing
 * is persisted server-side. */
export function SceneBuilder({ onClose }: { onClose: () => void }) {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [points, setPoints] = useState<BuilderPoint[]>([]);
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const objectUrlRef = useRef<string | null>(null);
  const imageBlobRef = useRef<Blob | null>(null);

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

  const loadImageBlob = (blob: Blob, defaultTitle: string) => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(blob);
    objectUrlRef.current = url;
    imageBlobRef.current = blob;
    const img = new Image();
    img.onload = () => setDims({ w: img.naturalWidth, h: img.naturalHeight });
    img.src = url;
    setImageUrl(url);
    setPoints([]);
    setError(null);
    setSavedMsg(null);
    if (!title.trim() && defaultTitle) setTitle(defaultTitle);
  };

  const onPickImage = (file: File) =>
    loadImageBlob(file, file.name.replace(/\.[^.]+$/, ""));

  const captureFromDevice = async () => {
    if (!instanceId) return;
    setCapturing(true);
    setError(null);
    try {
      const res = await fetch(overlayTestImageUrl(instanceId, Date.now()));
      if (!res.ok) throw new Error(`no live frame (${res.status})`);
      loadImageBlob(await res.blob(), "");
    } catch (e) {
      setError(`Capture failed: ${String(e)}`);
    } finally {
      setCapturing(false);
    }
  };

  const addPoint = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const xPct = ((e.clientX - rect.left) / rect.width) * 100;
    const yPct = ((e.clientY - rect.top) / rect.height) * 100;
    setPoints((prev) => [
      ...prev,
      {
        n: prev.length + 1,
        name: "",
        xPct: Math.round(xPct * 100) / 100,
        yPct: Math.round(yPct * 100) / 100,
      },
    ]);
  };

  const renamePoint = (n: number, name: string) =>
    setPoints((prev) => prev.map((p) => (p.n === n ? { ...p, name } : p)));
  const deletePoint = (n: number) =>
    setPoints((prev) =>
      prev.filter((p) => p.n !== n).map((p, i) => ({ ...p, n: i + 1 })),
    );

  // Save persists the screenshot as a new scene: stores the image in the
  // module references and writes the scene (title + item markers) into the
  // solver's map.yaml, making it the active scene.
  const save = async () => {
    if (!imageBlobRef.current) return;
    const slug = slugify(title);
    setSaving(true);
    setError(null);
    setSavedMsg(null);
    try {
      const file = new File([imageBlobRef.current], `${slug}.png`, {
        type: imageBlobRef.current.type || "image/png",
      });
      const img = await uploadDreamscapeSceneImage(slug, file);
      const res = await saveDreamscapeScene(slug, {
        title: title.trim() || slug,
        source_image: img.source_image,
        scene_rect: null,
        points: points.map((p) => ({
          n: p.n,
          name: p.name.trim() || `Item ${p.n}`,
          xPct: p.xPct,
          yPct: p.yPct,
        })),
        activate: true,
      });
      setSavedMsg(
        `Saved scene "${res.slug}" — ${res.point_count} item(s), now the active scene.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["dreamscape-scenes"] });
    } catch (e) {
      setError(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        className="panel flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Create new scene</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-sm hover:bg-white/10"
          >
            Close ✕
          </button>
        </div>

        <div className="mb-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-wos-text-muted">
            Scene title
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Ballroom"
              className="w-56 rounded border border-wos-border bg-wos-bg-deep px-2 py-1.5 text-sm text-wos-text"
            />
          </label>
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
          <button
            type="button"
            className="rounded border border-wos-border px-3 py-1.5 text-sm hover:border-wos-border-hover"
            onClick={() => fileInput.current?.click()}
          >
            {imageUrl ? "Replace image" : "Upload scene image"}
          </button>
          <AppListbox
            label="Device"
            options={instances.map((id) => ({ value: id, label: id }))}
            value={instanceId}
            onChange={setInstanceId}
            loading={instancesLoading}
            placeholder="Select"
            inline
          />
          <button
            type="button"
            className="rounded border border-wos-border px-3 py-1.5 text-sm hover:border-wos-border-hover disabled:opacity-50"
            disabled={!instanceId || capturing}
            onClick={captureFromDevice}
            title="Capture the current game frame from the selected device"
          >
            {capturing ? "Capturing…" : "Capture from device"}
          </button>
          <button
            type="button"
            className="rounded border border-emerald-500 bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm shadow-emerald-950/30 hover:border-emerald-400 hover:bg-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/50 disabled:opacity-50"
            disabled={!imageUrl || saving}
            onClick={save}
            title="Save this screenshot as a new scene (image → module references, markers → solver scene DB, set active)"
          >
            {saving ? "Saving…" : "Save as scene"}
          </button>
        </div>

        {error ? (
          <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-sm text-rose-400">
            {error}
          </p>
        ) : null}
        {savedMsg ? (
          <p className="mb-3 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-400">
            {savedMsg}
          </p>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-4 overflow-hidden md:grid-cols-[1fr_280px]">
          <div className="min-h-0 overflow-auto">
            {imageUrl ? (
              <div
                className="relative mx-auto w-full max-w-md cursor-crosshair select-none overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep"
                style={{ aspectRatio: dims ? `${dims.w} / ${dims.h}` : "3 / 4" }}
                onClick={addPoint}
              >
                <img
                  src={imageUrl}
                  alt="new scene"
                  className="pointer-events-none h-full w-full object-contain"
                />
                {points.map((p) => (
                  <span
                    key={p.n}
                    style={{ left: `${p.xPct}%`, top: `${p.yPct}%` }}
                    className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/80 bg-wos-accent px-1.5 text-[10px] font-bold leading-5 text-wos-on-accent"
                  >
                    {p.n}
                  </span>
                ))}
              </div>
            ) : (
              <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-wos-border text-sm text-wos-text-muted">
                Upload a scene image, then click it to drop item markers.
              </div>
            )}
          </div>

          <div className="min-h-0 overflow-auto">
            <p className="meta mb-2">{points.length} item(s)</p>
            <ol className="space-y-1.5">
              {points.map((p) => (
                <li key={p.n} className="flex items-center gap-2">
                  <span className="w-5 shrink-0 text-right text-xs text-wos-text-muted">
                    {p.n}
                  </span>
                  <input
                    type="text"
                    value={p.name}
                    onChange={(e) => renamePoint(p.n, e.target.value)}
                    placeholder={`Item ${p.n}`}
                    className="min-w-0 flex-1 rounded border border-wos-border bg-wos-bg-deep px-2 py-1 text-sm text-wos-text"
                  />
                  <button
                    type="button"
                    onClick={() => deletePoint(p.n)}
                    className="rounded px-1.5 text-sm text-wos-text-muted hover:text-rose-400"
                    title="Remove"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ol>
            {points.length === 0 && imageUrl ? (
              <p className="meta">Click the image to add the first marker.</p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
