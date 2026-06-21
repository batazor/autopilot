"use client";

import { Suspense } from "react";
import { PlayerStateContent } from "@/components/player-state/PlayerStateContent";
import { PageLoading } from "@/components/ui";

export default function PlayerStatePage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <PlayerStateContent />
    </Suspense>
  );
}
