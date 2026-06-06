"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppListbox } from "@/components/headless";
import {
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  labelingImageUrl,
  saveDreamscapeScene,
} from "@/lib/api";
import {
  dreamscapeNewCapturesEventName,
  loadDreamscapeNewCaptures,
  removeDreamscapeNewCapture,
  type DreamscapeNewCapture,
} from "@/lib/dreamscape-new-captures";
import type { DreamscapeScenePoint, DreamscapeSceneRect } from "@/lib/types";
import {
  ScenePointEditor,
  type ScenePin,
} from "@/components/dreamscape/ScenePointEditor";
import { Button } from "./Button";

function normalizeWord(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

function clampPct(value: number): number {
  return Math.min(100, Math.max(0, value));
}

function pointToFrame(point: DreamscapeScenePoint, rect: DreamscapeSceneRect | null): ScenePin {
  if (!rect) return { ...point, placed: true, conf: null };
  return {
    n: point.n,
    name: point.name,
    xPct: rect.left + (point.xPct / 100) * rect.width,
    yPct: rect.top + (point.yPct / 100) * rect.height,
    placed: true,
    conf: null,
  };
}

function pinToScenePoint(pin: ScenePin, rect: DreamscapeSceneRect | null): DreamscapeScenePoint {
  if (!rect || rect.width <= 0 || rect.height <= 0) {
    return {
      n: pin.n,
      name: pin.name,
      xPct: clampPct(pin.xPct),
      yPct: clampPct(pin.yPct),
    };
  }
  return {
    n: pin.n,
    name: pin.name,
    xPct: clampPct(((pin.xPct - rect.left) / rect.width) * 100),
    yPct: clampPct(((pin.yPct - rect.top) / rect.height) * 100),
  };
}

function captureLabel(c: DreamscapeNewCapture): string {
  const date = new Date(c.createdAt).toLocaleString();
  const reason = c.reason === "unknown_scene" ? "unknown scene" : "new word";
  const words = c.words.length ? ` · ${c.words.join(", ")}` : "";
  return `${date} · ${reason}${words}`;
}

export function NewItemsTab() {
  const queryClient = useQueryClient();
  const [captures, setCaptures] = useState<DreamscapeNewCapture[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selectedSlug, setSelectedSlug] = useState("");
  const [wordInput, setWordInput] = useState("");
  const [pins, setPins] = useState<ScenePin[]>([]);
  const [selectedN, setSelectedN] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const seededKey = useRef("");

  useEffect(() => {
    const sync = () => {
      const next = loadDreamscapeNewCaptures();
      setCaptures(next);
      setSelectedId((cur) => (cur && next.some((c) => c.id === cur) ? cur : next[0]?.id ?? ""));
    };
    sync();
    window.addEventListener(dreamscapeNewCapturesEventName(), sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(dreamscapeNewCapturesEventName(), sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const selectedCapture = captures.find((c) => c.id === selectedId) ?? null;

  const scenesQuery = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const sceneOptions = useMemo(
    () =>
      (scenesQuery.data?.scenes ?? []).map((s) => ({
        value: s.slug,
        label: `${s.title} · ${s.slug}`,
      })),
    [scenesQuery.data],
  );

  useEffect(() => {
    if (!selectedCapture) return;
    setSelectedSlug((cur) => selectedCapture.sceneSlug || cur || scenesQuery.data?.active || "");
    setWordInput(selectedCapture.words[0] ?? "");
    setMessage(null);
  }, [selectedCapture, scenesQuery.data]);

  const sceneQuery = useQuery({
    queryKey: ["dreamscape-scene", selectedSlug],
    queryFn: () => fetchDreamscapeScene(selectedSlug),
    enabled: Boolean(selectedSlug),
  });
  const detail = sceneQuery.data ?? null;

  useEffect(() => {
    if (!selectedCapture || !detail) return;
    const key = `${selectedCapture.id}:${detail.slug}`;
    if (seededKey.current === key) return;
    seededKey.current = key;
    const base = detail.points.map((p) => pointToFrame(p, detail.scene_rect));
    const firstWord = selectedCapture.words.find(
      (w) => !base.some((p) => normalizeWord(p.name) === normalizeWord(w)),
    );
    setPins(
      firstWord
        ? [
            ...base,
            {
              n: base.length ? Math.max(...base.map((p) => p.n)) + 1 : 1,
              name: firstWord,
              xPct: 50,
              yPct: 50,
              placed: false,
              conf: null,
            },
          ]
        : base,
    );
    setSelectedN(firstWord ? (base.length ? Math.max(...base.map((p) => p.n)) + 1 : 1) : null);
    setDirty(Boolean(firstWord));
  }, [detail, selectedCapture]);

  const captureOptions = captures.map((c) => ({ value: c.id, label: captureLabel(c) }));
  const imageUrl = selectedCapture
    ? labelingImageUrl(selectedCapture.ref, selectedCapture.createdAt)
    : "";

  const addWord = () => {
    const word = wordInput.trim();
    if (!word) return;
    const existing = pins.find((p) => normalizeWord(p.name) === normalizeWord(word));
    if (existing) {
      setSelectedN(existing.n);
      return;
    }
    const nextN = pins.length ? Math.max(...pins.map((p) => p.n)) + 1 : 1;
    setPins([...pins, { n: nextN, name: word, xPct: 50, yPct: 50, placed: false, conf: null }]);
    setSelectedN(nextN);
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error("select a scene first");
      return saveDreamscapeScene(detail.slug, {
        title: detail.title,
        alt_titles: detail.alt_titles,
        source_image: detail.source_image,
        scene_rect: detail.scene_rect,
        points: pins
          .filter((p) => p.name.trim())
          .map((p) => pinToScenePoint(p, detail.scene_rect)),
        activate: detail.active,
      });
    },
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ["dreamscape-scene"] });
      await queryClient.invalidateQueries({ queryKey: ["dreamscape-scenes"] });
      if (selectedCapture) {
        removeDreamscapeNewCapture(selectedCapture.id);
      }
      setDirty(false);
      setMessage(`Saved ${res.point_count} point(s) to ${res.slug}.`);
    },
    onError: (err: unknown) => setMessage(`Save failed: ${String(err)}`),
  });

  return (
    <div className="mt-4 space-y-4">
      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <section className="panel">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <AppListbox
            label="Captured screenshot"
            options={captureOptions}
            value={selectedId}
            onChange={setSelectedId}
            placeholder="No captured unknowns"
            minWidth={320}
            inline
          />
          <AppListbox
            label="Scene"
            options={sceneOptions}
            value={selectedSlug}
            onChange={(slug) => {
              setSelectedSlug(slug);
              seededKey.current = "";
            }}
            loading={scenesQuery.isLoading}
            placeholder="Assign scene"
            minWidth={260}
            inline
          />
          <label className="flex min-w-[220px] flex-col gap-1 text-sm">
            <span className="text-xs text-wos-text-muted">New word</span>
            <input
              value={wordInput}
              onChange={(e) => setWordInput(e.target.value)}
              placeholder="e.g. Lantern"
              className="rounded border border-wos-border bg-wos-bg-deep px-2 py-1.5 text-sm text-wos-text"
            />
          </label>
          <Button variant="secondary" disabled={!detail || !wordInput.trim()} onClick={addWord}>
            Add word
          </Button>
          <Button
            variant="primary"
            disabled={!detail || !dirty || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? "Saving..." : "Save to scene"}
          </Button>
          {selectedCapture ? (
            <Button
              variant="secondary"
              onClick={() => removeDreamscapeNewCapture(selectedCapture.id)}
            >
              Dismiss
            </Button>
          ) : null}
        </div>

        {selectedCapture && detail ? (
          <ScenePointEditor
            imageUrl={imageUrl}
            pins={pins}
            selectedN={selectedN}
            onSelectN={setSelectedN}
            onChange={(next) => {
              setPins(next);
              setDirty(true);
            }}
            listHeader={
              <p className="meta">
                {detail.title} · frame screenshot · points save back to scene coordinates
              </p>
            }
          />
        ) : (
          <p className="meta">
            Start solving in Solo or Multiplayer. Unknown scenes and unmapped words will appear here.
          </p>
        )}
      </section>
    </div>
  );
}
