"use client";

import { Suspense } from "react";
import { GiftCodesContent } from "@/components/gift-codes/GiftCodesContent";

export default function GiftCodesPage() {
  return (
    <Suspense fallback={null}>
      <GiftCodesContent />
    </Suspense>
  );
}
