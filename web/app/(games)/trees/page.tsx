"use client";

import { Suspense } from "react";
import { TreesContent } from "@/components/trees/TreesContent";
import { PageLoading } from "@/components/ui";

export default function TreesPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <TreesContent />
    </Suspense>
  );
}
