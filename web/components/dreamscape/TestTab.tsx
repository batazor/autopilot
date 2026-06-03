"use client";

/* eslint-disable @next/next/no-img-element */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { AppListbox } from "@/components/headless";
import { fetchLabelingDocument, testRegionOcr } from "@/lib/api";
import type { PercentBBox } from "@/lib/bbox";
import {
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_MULTIPLAYER_WORDS_REF,
  DREAMSCAPE_SCOPE,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
  statusFromDetectedScreen,
  wordBadges,
} from "@/lib/dreamscape-live";
import { apiToEditorRegions } from "@/lib/labeling-utils";
import type { RegionOcrTestResult } from "@/lib/types";
import { DetectedWordsBadges } from "./DetectedWordsBadges";
import { Button } from "./Button";

type Mode = "solo" | "multiplayer";

const MODE_TABS: { key: Mode; label: string }[] = [
  { key: "solo", label: "Solo · 3 words" },
  { key: "multiplayer", label: "Multiplayer · 6 words" },
];

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
    mutationFn: (file: File) => testRegionOcr(instanceId, file, [...wordRegions]),
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
  const badges = wordBadges(testResult?.rows, wordRegions);
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
        <Button
          variant="primary"
          disabled={!instanceId || uploadMutation.isPending}
          onClick={() => fileInput.current?.click()}
          title="Upload a custom screenshot and run our screen detection + word OCR on it"
        >
          {uploadMutation.isPending
            ? "Detecting…"
            : testResult
              ? "Upload another"
              : "Upload test image"}
        </Button>
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
          <div className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep">
            {testImageUrl ? (
              <>
                <img
                  src={testImageUrl}
                  alt="uploaded test image"
                  className="h-full w-full object-contain"
                />
                {zones.map((z) =>
                  z.bbox ? (
                    <ZoneBox
                      key={z.name}
                      bbox={z.bbox}
                      index={z.index}
                      text={textByRegion.get(z.name) ?? ""}
                    />
                  ) : null,
                )}
              </>
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-sm text-wos-text-muted">
                {instanceId
                  ? "Upload a screenshot to detect words and click-zones"
                  : "Select an instance, then upload a screenshot"}
              </div>
            )}
          </div>
          {testResult ? (
            <p className="meta mt-2">
              Screen:{" "}
              <span className="text-wos-text">
                {status.detectedScreen || "—"}
              </span>
              {status.areaCovered ? " · area covered" : ""}
            </p>
          ) : null}
        </section>

        <section className="panel">
          <h2 className="mb-3 text-base font-semibold">
            Detected words{" "}
            <span className="text-sm font-normal text-wos-text-muted">
              ({mode === "multiplayer" ? "6" : "3"})
            </span>
          </h2>
          {testResult ? (
            <DetectedWordsBadges badges={badges} />
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
 * percent bbox and labeled with its index + the detected OCR text. */
function ZoneBox({
  bbox,
  index,
  text,
}: {
  bbox: PercentBBox;
  index: number;
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
      <span className="absolute -top-5 left-0 whitespace-nowrap rounded bg-wos-bg-deep/90 px-1 text-[10px] font-medium text-wos-text">
        {index}
        {text ? ` · ${text}` : ""}
      </span>
    </div>
  );
}
