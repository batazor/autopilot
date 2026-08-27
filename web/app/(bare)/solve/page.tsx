"use client";

// Bare one-page Dreamscape runner: pick the season + scene, press Play — no
// dashboard chrome (AppShell renders /solve without the sidebar/banners).
// Solo vs multiplayer is a query param (?mode=multiplayer), mirroring the tabs
// of the full /dreamscape-memory page.

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
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

function SolvePage() {
  const params = useSearchParams();
  const multiplayer = params.get("mode") === "multiplayer";
  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-lg font-semibold">
        Dreamscape Memory — {multiplayer ? "multiplayer" : "solo"}
      </h1>
      <FleetContextProvider>
        <LiveEditorTab
          key={multiplayer ? "multiplayer" : "solo"}
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
