"use client";

// Bare one-page Dreamscape runner: pick the mode + season + scene, press Play —
// no dashboard chrome (AppShell renders /solve without the sidebar/banners).
// The mode lives in the URL (?mode=multiplayer) so a run is shareable and
// survives reload, mirroring the tabs of the full /dreamscape-memory page.

import { Suspense, useEffect, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { LiveEditorTab } from "@/components/dreamscape/LiveEditorTab";
import {
  DREAMSCAPE_MULTIPLAYER_MANUAL_SCENARIO,
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_MULTIPLAYER_WORDS_REF,
  DREAMSCAPE_SOLO_MANUAL_SCENARIO,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
} from "@/lib/dreamscape-live";

type Mode = "solo" | "multiplayer";

const MODES: { key: Mode; label: string }[] = [
  { key: "solo", label: "Solo · 3 words" },
  { key: "multiplayer", label: "PvP · 6 words" },
];

function SolvePage() {
  const params = useSearchParams();
  const pathname = usePathname();
  const urlMode = params.get("mode") === "multiplayer" ? "multiplayer" : "solo";
  const [mode, setMode] = useState<Mode>(urlMode);

  // Keep local state in sync for deep-links and browser back/forward.
  useEffect(() => setMode(urlMode), [urlMode]);

  // Shallow URL sync via the History API — the mode is pure client state, and
  // ``router.replace`` would fire an RSC round-trip on every toggle.
  const selectMode = (next: Mode) => {
    setMode(next);
    if (typeof window === "undefined") return;
    const q = new URLSearchParams(window.location.search);
    if (next === "solo") q.delete("mode");
    else q.set("mode", next);
    const query = q.toString();
    window.history.replaceState(null, "", query ? `${pathname}?${query}` : pathname);
  };

  const multiplayer = mode === "multiplayer";

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Dreamscape Memory</h1>
        <div
          role="tablist"
          aria-label="Mode"
          className="inline-flex rounded-md border border-wos-border p-0.5"
        >
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              role="tab"
              aria-selected={mode === m.key}
              onClick={() => selectMode(m.key)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition ${
                mode === m.key
                  ? "bg-wos-accent text-wos-on-accent"
                  : "text-wos-text-soft hover:text-wos-text"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <FleetContextProvider>
        {/* Remount on mode switch so the live view re-keys its OCR poll to the
            mode's word zones and reference screen. */}
        <LiveEditorTab
          key={mode}
          wordRegions={
            multiplayer ? DREAMSCAPE_MULTIPLAYER_WORD_REGIONS : DREAMSCAPE_WORD_REGIONS
          }
          wordsRef={multiplayer ? DREAMSCAPE_MULTIPLAYER_WORDS_REF : DREAMSCAPE_WORDS_REF}
          scenarioKey={
            multiplayer
              ? DREAMSCAPE_MULTIPLAYER_MANUAL_SCENARIO
              : DREAMSCAPE_SOLO_MANUAL_SCENARIO
          }
          autoCapture={false}
        />
      </FleetContextProvider>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <SolvePage />
    </Suspense>
  );
}
