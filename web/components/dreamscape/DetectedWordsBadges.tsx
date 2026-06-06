"use client";

import { useState } from "react";

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

/**
 * The word-slot lifecycle, in order, with what each status actually means. The
 * key invariant: a slot only becomes `clicked` once the background colour
 * confirms our tap greyed the pill — a tap that never landed is never "clicked".
 */
const STATUS_LEGEND: {
  state: DreamscapeWordRunState;
  dot: string;
  text: string;
  desc: string;
}[] = [
  {
    state: "unknown",
    dot: "bg-wos-text-muted",
    text: "text-wos-text-muted",
    desc: "Slot not read yet (or its word hasn't resolved).",
  },
  {
    state: "determined",
    dot: "bg-emerald-400",
    text: "text-emerald-200",
    desc: "Word recognised and located on the scene map — ready to tap.",
  },
  {
    state: "clicked",
    dot: "bg-sky-400",
    text: "text-sky-200",
    desc: "We tapped AND the background confirmed it (pill greyed out). Only ever set after colour confirmation — a tap that didn't land stays 'detected' and is re-tapped.",
  },
  {
    state: "found",
    dot: "bg-emerald-400",
    text: "text-emerald-200",
    desc: "Pill greyed out without a tap of ours — already solved, or solved by a teammate.",
  },
  {
    state: "help_requested",
    dot: "bg-amber-300",
    text: "text-amber-100",
    desc: "Word isn't in the scene map — asked the in-game helper to reveal it.",
  },
  {
    state: "detecting_on_map",
    dot: "bg-amber-300",
    text: "text-amber-100",
    desc: "Scanning the scene for the helper highlight to learn the word's position.",
  },
  {
    state: "rejected",
    dot: "bg-rose-300",
    text: "text-rose-100",
    desc: "Tap was rejected, or the colour never confirmed it after the re-tap budget (likely a wrong map coordinate).",
  },
];

function StatusLegendButton() {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label="What do the word statuses mean?"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-wos-border-subtle bg-wos-panel-raised text-[11px] font-semibold text-wos-text-muted transition hover:text-wos-text"
      >
        ?
      </button>
      {open ? (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="absolute left-0 top-7 z-20 w-80 rounded-lg border border-wos-border-subtle bg-wos-panel p-3 shadow-xl">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
                Word statuses
              </p>
              <span className="text-[10px] text-wos-text-muted">
                detected → clicked → found
              </span>
            </div>
            <ul className="flex flex-col gap-2">
              {STATUS_LEGEND.map((row) => (
                <li key={row.state} className="flex gap-2">
                  <span
                    className={`mt-1 h-2 w-2 shrink-0 rounded-full ${row.dot}`}
                    aria-hidden
                  />
                  <span className="text-xs leading-snug text-wos-text-muted">
                    <span className={`font-semibold ${row.text}`}>
                      {runStateLabel(row.state)}
                    </span>{" "}
                    — {row.desc}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : null}
    </span>
  );
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
      <StatusLegendButton />
    </div>
  );
}
