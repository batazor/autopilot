"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { DreamscapeGuides } from "@/components/dreamscape/DreamscapeGuides";
import { LiveEditorTab } from "@/components/dreamscape/LiveEditorTab";
import { RegionEditorTab } from "@/components/dreamscape/RegionEditorTab";
import { TestTab } from "@/components/dreamscape/TestTab";
import {
  DREAMSCAPE_MULTIPLAYER_WORD_REGIONS,
  DREAMSCAPE_MULTIPLAYER_WORDS_REF,
  DREAMSCAPE_WORD_REGIONS,
  DREAMSCAPE_WORDS_REF,
} from "@/lib/dreamscape-live";

type View = "guides" | "solo" | "multiplayer" | "editor" | "test";

const VIEW_TABS: { key: View; label: string }[] = [
  { key: "guides", label: "Guides" },
  { key: "solo", label: "Solo · 3 words" },
  { key: "multiplayer", label: "Multiplayer · 6 words" },
  { key: "editor", label: "Region editor" },
  { key: "test", label: "Test" },
];

const VIEW_KEYS = VIEW_TABS.map((t) => t.key);
const isView = (v: string | null | undefined): v is View =>
  !!v && VIEW_KEYS.includes(v as View);

function DreamscapePageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  // The active tab lives in the URL (?view=guides|solo|multiplayer|editor) so
  // it's shareable/deep-linkable and restored on reload; default to Guides.
  const viewParam = params.get("view");
  const view: View = isView(viewParam) ? viewParam : "guides";

  const setView = (next: View) => {
    const q = new URLSearchParams(params.toString());
    q.set("view", next);
    router.replace(`${pathname}?${q.toString()}`, { scroll: false });
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
      ) : view === "test" ? (
        <FleetContextProvider>
          <TestTab />
        </FleetContextProvider>
      ) : isLive ? (
        <FleetContextProvider>
          {/* Remount on mode switch so the live view re-keys its OCR poll to
              the mode's reference screen and word-zone badges. */}
          <LiveEditorTab key={view} wordRegions={wordRegions} wordsRef={wordsRef} />
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
