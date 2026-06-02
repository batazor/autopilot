"use client";

/* eslint-disable @next/next/no-img-element */
import type { LiveStatus, WordBadge } from "@/lib/dreamscape-live";
import { DetectedWordsBadges } from "./DetectedWordsBadges";

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

/** Live device frame + the two status pills + the detected-word badges. */
export function LiveStatusCard({
  imageUrl,
  status,
  badges,
  loading,
  instanceSelected,
}: {
  imageUrl: string | null;
  status: LiveStatus;
  badges: WordBadge[];
  loading: boolean;
  instanceSelected: boolean;
}) {
  return (
    <section className="panel">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Current screen</h2>
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
        {instanceSelected && imageUrl ? (
          <img
            src={imageUrl}
            alt="live device frame"
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-wos-text-muted">
            {instanceSelected ? "Waiting for a live frame…" : "Select an instance"}
          </div>
        )}
      </div>

      <div className="mt-3">
        <p className="meta mb-1.5">
          Detected words {loading ? <span className="text-wos-text-muted">· refreshing…</span> : null}
        </p>
        <DetectedWordsBadges badges={badges} />
      </div>

      {status.detectedScreen ? (
        <p className="meta mt-2">
          Screen: <span className="text-wos-text">{status.detectedScreen}</span>
        </p>
      ) : null}
    </section>
  );
}
