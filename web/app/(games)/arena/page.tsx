"use client";

import { Suspense } from "react";
import { ArenaOptimizer } from "@/components/arena/ArenaOptimizer";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui";

export default function ArenaPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <PageHeader title="Arena Optimizer" fleet={{ showPlayer: true }}>
          <p className="muted">
            Add the heroes you can field (or load them from the selected account) and the
            enemy&apos;s lineup, then press Optimize for the best arrangement. Front seats 1 &amp; 5
            take hits first (tanks / crowd control); back seats 2, 3, 4 deal damage; seat 4 is the
            only one that reaches all five enemies — the carry seat. Class counters (Infantry &gt;
            Lancer &gt; Marksman &gt; Infantry, +10%) and exploration skills are factored in. Drag a
            hero onto a seat to pin it and optimize the rest around it. Enter each hero&apos;s in-game
            Power for the most accurate win prediction.
          </p>
        </PageHeader>
        <ArenaOptimizer />
      </FleetContextProvider>
    </Suspense>
  );
}
