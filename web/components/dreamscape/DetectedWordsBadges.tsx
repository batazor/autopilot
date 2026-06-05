"use client";

import type { DreamscapeWordRunState, WordBadge } from "@/lib/dreamscape-live";

function runStateLabel(state: DreamscapeWordRunState): string {
  if (state === "unknown") return "unknown";
  if (state === "determined") return "detected";
  if (state === "clicked") return "clicked";
  if (state === "help_requested") return "helper";
  if (state === "detecting_on_map") return "map scan";
  if (state === "found") return "found";
  if (state === "rejected") return "rejected";
  return "";
}

/** The three word buttons the bot currently reads at the bottom of a level. */
export function DetectedWordsBadges({
  badges,
  wordKnown,
  wordRunState,
}: {
  badges: WordBadge[];
  wordKnown?: (boolean | null)[];
  wordRunState?: DreamscapeWordRunState[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {badges.map((b, i) => {
        const known = wordKnown?.[i] === true;
        const rawRunState = wordRunState?.[i] ?? null;
        const runState =
          rawRunState === "unknown" && known ? "determined" : rawRunState;
        const badgeTone =
          runState === "found"
            ? "border-emerald-400/70 bg-emerald-500/20 text-wos-text"
            : runState === "clicked"
              ? "border-sky-400/60 bg-sky-500/15 text-wos-text"
              : runState === "determined"
                ? "border-emerald-500/55 bg-emerald-500/10 text-wos-text"
              : runState === "help_requested" || runState === "detecting_on_map"
                ? "border-amber-300/65 bg-amber-500/15 text-wos-text"
              : runState === "rejected"
                ? "border-rose-300/65 bg-rose-500/15 text-wos-text"
              : runState === "unknown"
                ? "border-wos-border-subtle bg-wos-panel-raised text-wos-text-muted"
              : known
                ? "border-emerald-500/55 bg-emerald-500/10 text-wos-text"
                : b.dimmed
                  ? "border-wos-border-subtle bg-wos-panel-raised text-wos-text-muted"
                  : "border-wos-accent bg-wos-accent/10 text-wos-text";
        return (
          <span
            key={b.region}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm transition ${badgeTone}`}
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
            {known && !runState ? (
              <span className="text-[10px] font-semibold uppercase text-emerald-300">
                mapped
              </span>
            ) : null}
            {runState ? (
              <span
                className={`text-[10px] font-semibold uppercase ${
                  runState === "found" || runState === "determined"
                    ? "text-emerald-200"
                    : runState === "help_requested" || runState === "detecting_on_map"
                      ? "text-amber-100"
                      : runState === "rejected"
                        ? "text-rose-100"
                        : "text-sky-200"
                }`}
              >
                {runStateLabel(runState)}
              </span>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}
