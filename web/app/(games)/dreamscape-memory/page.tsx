"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { DreamscapeGuides } from "@/components/dreamscape/DreamscapeGuides";
import { LiveEditorTab } from "@/components/dreamscape/LiveEditorTab";
import { NewItemsTab } from "@/components/dreamscape/NewItemsTab";
import { RegionEditorTab } from "@/components/dreamscape/RegionEditorTab";
import { TestTab } from "@/components/dreamscape/TestTab";
import {
  DREAMSCAPE_MULTIPLAYER_SCENARIO,
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_MULTIPLAYER_WORDS_REF,
  DREAMSCAPE_SOLO_SCENARIO,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
} from "@/lib/dreamscape-live";

type View = "guides" | "solo" | "multiplayer" | "new" | "editor" | "test";

const VIEW_TABS: { key: View; label: string }[] = [
  { key: "guides", label: "Guides" },
  { key: "solo", label: "Solo · 3 words" },
  { key: "multiplayer", label: "Multiplayer · 6 words" },
  { key: "new", label: "New" },
  { key: "editor", label: "Region editor" },
  { key: "test", label: "Test" },
];

const VIEW_KEYS = VIEW_TABS.map((t) => t.key);
const isView = (v: string | null | undefined): v is View =>
  !!v && VIEW_KEYS.includes(v as View);

function DreamscapePageInner() {
  const params = useSearchParams();
  const pathname = usePathname();

  // The active tab lives in the URL (?view=guides|solo|multiplayer|editor) so
  // it's shareable/deep-linkable and restored on reload; default to Guides.
  // It's pure client UI — no server data is keyed on it — so the tab is held in
  // local state and the URL is synced *shallowly* via the History API. Using
  // ``router.replace`` here would fire an RSC round-trip on every tab click,
  // which (mid-rebuild / stale client) can 404 and break switching; History
  // updates never hit the server. ``useSearchParams`` reflects them, so reload
  // and browser back/forward still restore the right tab.
  const viewParam = params.get("view");
  const [view, setViewState] = useState<View>(
    isView(viewParam) ? viewParam : "guides",
  );

  // Keep local state in sync with the URL for deep-links and back/forward.
  useEffect(() => {
    if (isView(viewParam)) setViewState(viewParam);
  }, [viewParam]);

  const setView = (next: View) => {
    setViewState(next);
    if (typeof window === "undefined") return;
    const q = new URLSearchParams(window.location.search);
    q.set("view", next);
    window.history.replaceState(null, "", `${pathname}?${q.toString()}`);
  };

  const isLive = view === "solo" || view === "multiplayer";
  const wordRegions =
    view === "multiplayer"
      ? DREAMSCAPE_MULTIPLAYER_WORD_REGIONS
      : DREAMSCAPE_WORD_REGIONS;
  const wordsRef =
    view === "multiplayer"
      ? DREAMSCAPE_MULTIPLAYER_WORDS_REF
      : DREAMSCAPE_WORDS_REF;
  const scenarioKey =
    view === "multiplayer"
      ? DREAMSCAPE_MULTIPLAYER_SCENARIO
      : DREAMSCAPE_SOLO_SCENARIO;

  return (
    <>
      <PageHeader title="Dreamscape Memory">
        Item-location guides for the Dreamscape Memory scavenger-hunt event. Pick
        a scene to view its hidden-item map, or onboard a new one. The live editor
        supports both solo (3 words) and co-op multiplayer (6 words) modes.
      </PageHeader>

      <AppTabs
        tabs={VIEW_TABS}
        selectedKey={view}
        onChange={(key) => setView(key as View)}
        renderPanels={false}
      />

      {view === "editor" ? (
        <FleetContextProvider>
          <RegionEditorTab />
        </FleetContextProvider>
      ) : view === "new" ? (
        <FleetContextProvider>
          <NewItemsTab />
        </FleetContextProvider>
      ) : view === "test" ? (
        <FleetContextProvider>
          <TestTab />
        </FleetContextProvider>
      ) : isLive ? (
        <FleetContextProvider>
          {/* Remount on mode switch so the live view re-keys its OCR poll to
              the mode's reference screen and word-zone badges. */}
          <LiveEditorTab
            key={view}
            wordRegions={wordRegions}
            wordsRef={wordsRef}
            scenarioKey={scenarioKey}
          />
        </FleetContextProvider>
      ) : (
        <DreamscapeGuides />
      )}
    </>
  );
}

export default function DreamscapeMemoryPage() {
  return (
    <Suspense fallback={null}>
      <DreamscapePageInner />
    </Suspense>
  );
}
