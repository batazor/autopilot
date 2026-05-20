"use client";

import { useEffect, useState } from "react";
import { ApiStatusIndicator } from "@/components/ApiStatusIndicator";
import { ApiStatusProvider } from "@/components/ApiStatusProvider";
import { AppNav } from "@/components/AppNav";
import { FeedbackProvider } from "@/components/feedback";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Icon } from "@/components/ui";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <ApiStatusProvider>
    <FeedbackProvider>
    <div className="flex min-h-screen bg-wos-bg text-wos-text">
      {navOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 backdrop-blur-sm lg:hidden"
          style={{ backgroundColor: "var(--wos-overlay-scrim)" }}
          aria-label="Close menu"
          onClick={() => setNavOpen(false)}
        />
      )}

      <AppNav open={navOpen} onNavigate={() => setNavOpen(false)} />

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-wos-border-subtle/80 bg-wos-bg/95 px-4 py-3 backdrop-blur-md lg:hidden">
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-wos-border-subtle bg-wos-surface text-wos-text hover:bg-wos-panel-raised"
            aria-label="Open menu"
            onClick={() => setNavOpen(true)}
          >
            <Icon name="menu" size="md" />
          </button>
          <span className="min-w-0 flex-1 text-sm font-semibold tracking-tight text-wos-text">
            WOS Autopilot
          </span>
          <ThemeToggle compact />
          <ApiStatusIndicator variant="header" />
        </header>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
      </div>
    </div>
    </FeedbackProvider>
    </ApiStatusProvider>
  );
}
