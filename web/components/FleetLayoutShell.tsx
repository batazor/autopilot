"use client";

import { Suspense, type ReactNode } from "react";
import { FleetContextProvider } from "@/components/FleetContextProvider";
import { SectionTabs } from "@/components/SectionTabs";
import { PageLoading } from "@/components/ui/Spinner";
import type { NavGroupId } from "@/lib/nav-groups";

export function FleetLayoutShell({
  groupId,
  children,
}: {
  groupId: Extract<NavGroupId, "operate" | "debug">;
  children: ReactNode;
}) {
  return (
    <Suspense fallback={<FleetLayoutFallback groupId={groupId} />}>
      <FleetContextProvider>
        <SectionTabs groupId={groupId} />
        <div className="app-main flex-1">{children}</div>
      </FleetContextProvider>
    </Suspense>
  );
}

function FleetLayoutFallback({
  groupId,
}: {
  groupId: Extract<NavGroupId, "operate" | "debug">;
}) {
  return (
    <>
      <SectionTabs groupId={groupId} />
      <div className="app-main flex-1">
        <PageLoading />
      </div>
    </>
  );
}
