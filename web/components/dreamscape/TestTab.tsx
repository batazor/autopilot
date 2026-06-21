"use client";

/* eslint-disable @next/next/no-img-element */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import {
  fetchDreamscapeScene,
  fetchDreamscapeScenes,
  fetchLabelingDocument,
  testRegionOcr,
} from "@/lib/api";
import type { PercentBBox } from "@/lib/bbox";
import {
  DREAMSCAPE_LEVEL_NAME_REGION,
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_MULTIPLAYER_WORDS_REF,
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  levelNameRead,
  statusFromDetectedScreen,
  wordBadges,
} from "@/lib/dreamscape-live";
import { apiToEditorRegions } from "@/lib/labeling-utils";
import type { DreamscapeScenePoint, RegionOcrTestResult } from "@/lib/types";
import { DetectedWordsBadges } from "./DetectedWordsBadges";
import { Button } from "./Button";

type Mode = "solo" | "multiplayer";

const MODE_TABS: { key: Mode; label: string }[] = [
  { key: "solo", label: "Solo · 3 words" },
  { key: "multiplayer", label: "Multiplayer · 6 words" },
];

function normalizeWord(raw: string): string {
  return raw.trim().toLowerCase().replace(/\s+/g, " ");
}

function normalizeLevelName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\b\d+(?:\.\d+)?\s*%.*$/i, " ")
    .replace(/(?<=[a-z])[\|/\\]+(?=[a-z])/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function stripSeasonTag(title: string): string {
  return title.replace(/\s*\(s\d+\)\s*$/i, "");
}

function sceneMatchesLevel(
  scene: { slug: string; title: string },
  levelKey: string,
): boolean {
  return (
    normalizeLevelName(stripSeasonTag(scene.title)) === levelKey ||
    normalizeLevelName(scene.slug) === levelKey
  );
}

function ScreenStatusPill({
  detected,
  screen,
}: {
  detected: boolean;
  screen?: string;
}) {
  return (
    <span
      title={
        screen
          ? `Detected: ${screen}`
          : "Screen detection found no labeled screen on this image"
      }
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        detected
          ? "bg-emerald-500/15 text-emerald-400"
          : "bg-rose-500/15 text-rose-400"
      }`}
    >
      <span aria-hidden>{detected ? "●" : "○"}</span>
      {detected ? "Detected" : "No screen"}
    </span>
  );
}

/** Compact two-state segmented toggle for the solo/multiplayer word set. */
function ModeToggle({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-wos-border bg-wos-bg-deep p-0.5 text-sm">
      {MODE_TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => onChange(t.key)}
          className={`rounded-md px-3 py-1.5 font-medium transition ${
            mode === t.key
              ? "bg-wos-accent/15 text-wos-accent"
              : "text-wos-text-muted hover:text-wos-text"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Test tab: upload a screenshot, run our screen-detection + word OCR on it for
 * the chosen mode (solo 3 words / multiplayer 6 words), and overlay the word
 * click-zones — labeled with the detected text — on the uploaded image. */
export function TestTab() {
  const { instanceId, instances, setInstanceId, instancesLoading } = useFleet();
  const [mode, setMode] = useState<Mode>("solo");
  const [message, setMessage] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<RegionOcrTestResult | null>(null);
  const [testImageUrl, setTestImageUrl] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const wordRegions =
    mode === "multiplayer"
      ? DREAMSCAPE_MULTIPLAYER_WORD_REGIONS
      : DREAMSCAPE_WORD_REGIONS;
  const wordsRef =
    mode === "multiplayer"
      ? DREAMSCAPE_MULTIPLAYER_WORDS_REF
      : DREAMSCAPE_WORDS_REF;

  // Click-zone geometry: pull the word-region bboxes from the mode's labeled
  // reference so we can overlay them on the uploaded image (frame is 720×1280,
  // 9/16 — same aspect as the preview box, so percent positions map directly).
  const docQuery = useQuery({
    queryKey: ["dreamscape-doc", wordsRef],
    queryFn: () => fetchLabelingDocument(wordsRef, DREAMSCAPE_SCOPE),
  });
  const zones = useMemo(() => {
    const regions = docQuery.data ? apiToEditorRegions(docQuery.data.regions) : [];
    const byName = new Map(regions.map((r) => [r.name, r.bbox]));
    return wordRegions.map((name, i) => ({
      name,
      index: i + 1,
      bbox: byName.get(name) ?? null,
    }));
  }, [docQuery.data, wordRegions]);

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      testRegionOcr(instanceId, file, [
        DREAMSCAPE_LEVEL_NAME_REGION,
        ...wordRegions,
      ]),
    onSuccess: (res, file) => {
      setTestImageUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(file);
      });
      setTestResult(res);
      setMessage(null);
    },
    onError: (err: unknown) => setMessage(`Test image failed: ${String(err)}`),
  });

  const clearTest = () => {
    setTestImageUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setTestResult(null);
  };

  // Revoke the object URL when the tab unmounts.
  useEffect(() => {
    return () => {
      if (testImageUrl) URL.revokeObjectURL(testImageUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The word set differs per mode, so a stale result no longer maps — reset.
  const switchMode = (next: Mode) => {
    if (next === mode) return;
    clearTest();
    setMode(next);
  };

  const status = statusFromDetectedScreen(testResult?.detected_screen);
  const levelName = levelNameRead(testResult?.rows);
  const badges = wordBadges(testResult?.rows, wordRegions);
  const scenesQuery = useQuery({
    queryKey: ["dreamscape-scenes"],
    queryFn: fetchDreamscapeScenes,
  });
  const matchedSlug = useMemo(() => {
    const level = normalizeLevelName(levelName?.text ?? "");
    if (!level) return null;
    const scenes = scenesQuery.data?.scenes ?? [];
    const hit = scenes.find((s) => sceneMatchesLevel(s, level));
    if (hit) return hit.slug;
    const active = scenesQuery.data?.active;
    return active && normalizeLevelName(active) === level ? active : null;
  }, [levelName, scenesQuery.data]);
  const sceneQuery = useQuery({
    queryKey: ["dreamscape-scene", matchedSlug],
    queryFn: () => fetchDreamscapeScene(matchedSlug as string),
    enabled: !!matchedSlug,
  });
  const knownNames = useMemo(
    () =>
      new Set((sceneQuery.data?.points ?? []).map((p) => normalizeWord(p.name))),
    [sceneQuery.data],
  );
  const wordKnown = useMemo<(boolean | null)[]>(
    () =>
      badges.map((b) => {
        const word = normalizeWord(b.text);
        if (!word || !matchedSlug) return null;
        return knownNames.has(word);
      }),
    [badges, knownNames, matchedSlug],
  );
  const mappedPoints = useMemo(() => {
    if (!sceneQuery.data) return [];
    const byName = new Map(
      sceneQuery.data.points.map((p) => [normalizeWord(p.name), p]),
    );
    const used = new Set<string>();
    return badges.flatMap((b, i) => {
      const key = normalizeWord(b.text);
      if (!key || used.has(key)) return [];
      const point = byName.get(key);
      if (!point) return [];
      used.add(key);
      const rect = sceneQuery.data.scene_rect;
      const xPct = rect ? rect.left + (point.xPct / 100) * rect.width : point.xPct;
      const yPct = rect ? rect.top + (point.yPct / 100) * rect.height : point.yPct;
      return [{ point, xPct, yPct, index: i + 1 }];
    });
  }, [badges, sceneQuery.data]);
  const textByRegion = useMemo(
    () =>
      new Map(
        (testResult?.rows ?? []).map((r) => [r.region, (r.text || "").trim()]),
      ),
    [testResult],
  );

  const instanceOptions = instances.map((id) => ({ value: id, label: id }));

  return (
    <div className="mt-4 space-y-4">
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
        <ModeToggle mode={mode} onChange={switchMode} />
        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadMutation.mutate(f);
            e.target.value = "";
          }}
        />
        {testResult ? (
          <Button variant="secondary" onClick={clearTest}>
            Clear
          </Button>
        ) : null}
      </div>

      {message ? (
        <p className="rounded border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-sm text-wos-text-muted">
          {message}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <section className="panel">
          <h2 className="mb-3 text-base font-semibold">Uploaded image</h2>
          <button
            type="button"
            disabled={!instanceId || uploadMutation.isPending}
            onClick={() => fileInput.current?.click()}
            title="Click to upload a screenshot and run screen detection + word OCR"
            className="group relative mx-auto block aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep enabled:cursor-pointer enabled:hover:border-wos-accent disabled:cursor-not-allowed"
          >
            {testImageUrl ? (
              <>
                <img
                  src={testImageUrl}
                  alt="uploaded test image"
                  className="h-full w-full object-contain"
                />
                <div className="pointer-events-none absolute inset-0">
                  {zones.map((z) =>
                    z.bbox ? (
                      <ZoneBox
                        key={z.name}
                        bbox={z.bbox}
                        text={textByRegion.get(z.name) ?? ""}
                      />
                    ) : null,
                  )}
                  {mappedPoints.map((p) => (
                    <ScenePointMarker
                      key={`${p.point.name}-${p.index}`}
                      point={p.point}
                      xPct={p.xPct}
                      yPct={p.yPct}
                      index={p.index}
                    />
                  ))}
                </div>
                {/* Hover hint: the whole card re-triggers upload. */}
                <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-wos-bg-deep/80 px-2 py-1 text-center text-xs text-wos-text-muted opacity-0 transition group-enabled:group-hover:opacity-100">
                  Click to upload another
                </div>
              </>
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-sm text-wos-text-muted transition group-enabled:group-hover:text-wos-text">
                {uploadMutation.isPending
                  ? "Detecting…"
                  : instanceId
                    ? "Upload a screenshot to detect words and click-zones"
                    : "Select an instance, then upload a screenshot"}
              </div>
            )}
          </button>
        </section>

        <section className="panel">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">
              Detected words{" "}
              <span className="text-sm font-normal text-wos-text-muted">
                ({mode === "multiplayer" ? "6" : "3"})
              </span>
            </h2>
            <ScreenStatusPill
              detected={status.screenDetected || Boolean(matchedSlug)}
              screen={
                status.detectedScreen ||
                sceneQuery.data?.title ||
                matchedSlug ||
                undefined
              }
            />
          </div>
          {testResult ? (
            <>
              <p className="meta mb-3">
                Title (OCR):{" "}
                <span
                  className={
                    levelName && !levelName.dimmed
                      ? "text-wos-text"
                      : "text-wos-text-muted"
                  }
                >
                  {levelName?.text ||
                    (levelName?.status === "empty" ? "— not recognised —" : "—")}
                </span>
                {levelName?.confidence != null
                  ? ` · ${Math.round(levelName.confidence * 100)}%`
                  : ""}
                {sceneQuery.data ? (
                  <>
                    {" "}
                    · Scene:{" "}
                    <span className="text-emerald-300">
                      {sceneQuery.data.title}
                    </span>
                  </>
                ) : scenesQuery.isLoading ? (
                  " · loading scenes…"
                ) : matchedSlug ? (
                  " · loading scene…"
                ) : scenesQuery.isError ? (
                  " · scene list failed"
                ) : levelName?.text ? (
                  " · scene not in DB"
                ) : null}
              </p>
              <DetectedWordsBadges badges={badges} wordKnown={wordKnown} />
            </>
          ) : (
            <p className="meta">
              No result yet — upload an image to run detection.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

/** A single word click-zone overlaid on the uploaded image, positioned by its
 * percent bbox. */
function ZoneBox({
  bbox,
  text,
}: {
  bbox: PercentBBox;
  text: string;
}) {
  return (
    <div
      className={`absolute rounded border-2 ${
        text ? "border-wos-accent bg-wos-accent/10" : "border-rose-400/70"
      }`}
      style={{
        left: `${bbox.x}%`,
        top: `${bbox.y}%`,
        width: `${bbox.width}%`,
        height: `${bbox.height}%`,
      }}
    >
    </div>
  );
}

function ScenePointMarker({
  point,
  xPct,
  yPct,
  index,
}: {
  point: DreamscapeScenePoint;
  xPct: number;
  yPct: number;
  index: number;
}) {
  return (
    <div
      aria-label={`${point.name} (${Math.round(xPct)}%, ${Math.round(yPct)}%)`}
      className="group/point pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2"
      style={{ left: `${xPct}%`, top: `${yPct}%` }}
      title=""
    >
      <span className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-emerald-200 bg-emerald-500/85 text-[11px] font-bold text-emerald-950 shadow-[0_0_0_3px_rgba(16,185,129,0.22)]">
        {index}
      </span>
      <span className="pointer-events-none absolute left-1/2 top-7 z-10 max-w-36 -translate-x-1/2 whitespace-nowrap rounded bg-emerald-950/95 px-1.5 py-0.5 text-[10px] font-medium text-emerald-100 opacity-0 shadow-lg transition group-hover/point:opacity-100">
        {point.name}
        <span className="ml-1 text-emerald-300/80">
          {Math.round(xPct)}%, {Math.round(yPct)}%
        </span>
      </span>
    </div>
  );
}
