"use client";

/* eslint-disable @next/next/no-img-element */
import { useRef, useState } from "react";
import type { LiveStatus, WordBadge } from "@/lib/dreamscape-live";
import { DetectedWordsBadges } from "./DetectedWordsBadges";
import { Button } from "./Button";

function StatusPill({
  ok,
  label,
  title,
}: {
  ok: boolean;
  label: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        ok
          ? "bg-emerald-500/15 text-emerald-400"
          : "bg-rose-500/15 text-rose-400"
      }`}
    >
      <span aria-hidden>{ok ? "●" : "○"}</span>
      {label}
    </span>
  );
}

/** Live device frame + the two status pills + the detected-word badges.
 *
 * When ``testMode`` is on, ``imageUrl``/``status``/``badges`` reflect an
 * uploaded test image instead of the live device.
 */
export function LiveStatusCard({
  imageUrl,
  status,
  badges,
  loading,
  instanceSelected,
  testMode = false,
  uploading = false,
  onUploadTestImage,
  onClearTest,
  showWords = true,
}: {
  imageUrl: string | null;
  status: LiveStatus;
  badges: WordBadge[];
  loading: boolean;
  instanceSelected: boolean;
  /** Test-image controls are shown only when ``onUploadTestImage`` is given. */
  testMode?: boolean;
  uploading?: boolean;
  onUploadTestImage?: (file: File) => void;
  onClearTest?: () => void;
  /** Render the detected-words badges inside the card (default). Disable to
   * show the word list in a separate panel beside the frame. */
  showWords?: boolean;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  // A rolling-preview URL that 404'd (worker hasn't written a frame yet).
  // Cache-busting changes the URL each tick, so a failure auto-retries on the
  // next frame rather than sticking on a broken image.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = Boolean(imageUrl) && imageUrl !== failedSrc;

  return (
    <section className="panel">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          Current screen
          {testMode ? (
            <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-400">
              TEST IMAGE
            </span>
          ) : null}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill
            ok={status.screenDetected}
            label={status.screenDetected ? "Screen detected" : "No screen"}
            title={
              status.detectedScreen
                ? `Detected: ${status.detectedScreen}`
                : "Screen detection found no labeled screen on this frame"
            }
          />
          <StatusPill
            ok={status.areaCovered}
            label={status.areaCovered ? "Area covered" : "No area"}
            title="A Dreamscape area/screen definition matches the current display"
          />
        </div>
      </div>

      <div className="relative mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-lg border border-wos-border bg-wos-bg-deep">
        {showImage && imageUrl ? (
          <img
            src={imageUrl}
            alt={testMode ? "uploaded test image" : "live device frame"}
            className="h-full w-full object-contain"
            onError={() => setFailedSrc(imageUrl)}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-wos-text-muted">
            {instanceSelected ? "Waiting for a live frame…" : "Select an instance"}
          </div>
        )}
      </div>

      {onUploadTestImage ? (
        <div className="mt-3 flex items-center gap-2">
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUploadTestImage(f);
              e.target.value = "";
            }}
          />
          <Button
            variant="primary"
            size="sm"
            disabled={!instanceSelected || uploading}
            onClick={() => fileInput.current?.click()}
            title="Upload a custom screenshot and run our detection + OCR logic on it"
          >
            {uploading ? "Testing…" : testMode ? "Upload another" : "Upload test image"}
          </Button>
          {testMode && onClearTest ? (
            <Button variant="secondary" size="sm" onClick={onClearTest}>
              Back to live
            </Button>
          ) : null}
        </div>
      ) : null}

      {showWords ? (
        <div className="mt-3">
          <p className="meta mb-1.5">
            Detected words {loading ? <span className="text-wos-text-muted">· refreshing…</span> : null}
          </p>
          <DetectedWordsBadges badges={badges} />
        </div>
      ) : null}

      {status.detectedScreen ? (
        <p className="meta mt-2">
          Screen: <span className="text-wos-text">{status.detectedScreen}</span>
        </p>
      ) : null}
    </section>
  );
}
