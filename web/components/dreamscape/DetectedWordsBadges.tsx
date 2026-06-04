"use client";

import type { WordBadge } from "@/lib/dreamscape-live";

/** The three word buttons the bot currently reads at the bottom of a level. */
export function DetectedWordsBadges({
  badges,
  wordKnown,
}: {
  badges: WordBadge[];
  wordKnown?: (boolean | null)[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {badges.map((b, i) => {
        const known = wordKnown?.[i] === true;
        return (
          <span
            key={b.region}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm transition ${
              known
                ? "border-emerald-500/55 bg-emerald-500/10 text-wos-text"
                : b.dimmed
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
            {known ? (
              <span className="text-[10px] font-semibold uppercase text-emerald-300">
                mapped
              </span>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}
