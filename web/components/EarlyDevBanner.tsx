"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";

const STORAGE_KEY = "autopilot:early-dev-banner-dismissed";

export function EarlyDevBanner() {
  // Render hidden on first paint to avoid an SSR/CSR flash before localStorage
  // is read; flip to visible only if the user hasn't dismissed it.
  const [show, setShow] = useState(false);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(STORAGE_KEY) !== "1") setShow(true);
    } catch {
      setShow(true);
    }
  }, []);

  if (!show) return null;

  const dismiss = () => {
    setShow(false);
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // localStorage blocked — banner just stays gone for this session.
    }
  };

  return (
    <div className="flex items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-100">
      <span className="rounded-full bg-amber-500 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-amber-950">
        Early
      </span>
      <span className="flex-1">
        Autopilot is in early active development — scenarios, APIs and UI can change
        without notice until the base feature set lands. Pin a specific image tag if you
        need a build that won&apos;t move under you.
      </span>
      <button
        type="button"
        onClick={dismiss}
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-amber-200 hover:bg-amber-500/20"
        aria-label="Dismiss"
        title="Dismiss"
      >
        <Icon name="close" size="sm" />
      </button>
    </div>
  );
}
