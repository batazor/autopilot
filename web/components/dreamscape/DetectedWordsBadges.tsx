"use client";

import type { WordBadge } from "@/lib/dreamscape-live";

function badgeTitle(b: WordBadge): string {
  const conf = b.confidence != null ? ` · conf ${b.confidence.toFixed(2)}` : "";
  const dur = b.durationMs != null ? ` · ${b.durationMs} ms` : "";
  switch (b.status) {
    case "ok":
      return `${b.text}${conf}${dur}`;
    case "empty":
      return `no text read${dur}`;
    case "no_region":
      return `region ${b.region} not in area.json`;
    case "no_frame":
      return "no live frame yet";
    case "error":
      return `OCR error${dur}`;
    default:
      return b.region;
  }
}

/** The three word buttons the bot currently reads at the bottom of a level. */
export function DetectedWordsBadges({ badges }: { badges: WordBadge[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {badges.map((b) => (
        <span
          key={b.region}
          title={badgeTitle(b)}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm transition ${
            b.dimmed
              ? "border-wos-border-subtle bg-wos-panel-raised text-wos-text-muted"
              : "border-wos-accent bg-wos-accent/10 text-wos-text"
          }`}
        >
          <span className="text-xs text-wos-text-muted">{b.index}</span>
          <span className="font-medium">
            {b.text || (b.status === "empty" ? "—" : "…")}
          </span>
          {b.durationMs != null ? (
            <span className="text-[10px] tabular-nums text-wos-text-muted">
              {Math.round(b.durationMs)}ms
            </span>
          ) : null}
        </span>
      ))}
    </div>
  );
}
