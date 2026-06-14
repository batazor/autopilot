"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { ApiStatusIndicator } from "@/components/ApiStatusIndicator";
import { ApiStatusProvider } from "@/components/ApiStatusProvider";
import { AppNav } from "@/components/AppNav";
import { AppTooltipHost } from "@/components/AppTooltip";
import { EarlyDevBanner } from "@/components/EarlyDevBanner";
import { FeedbackProvider } from "@/components/feedback";
import { useFleetOptional } from "@/components/FleetContextProvider";
import { AttentionBanner } from "@/components/attention/AttentionBanner";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Icon } from "@/components/ui";
import { VersionBadge } from "@/components/VersionBadge";

function GameBadge() {
  // Pages outside the fleet-context layouts (e.g. ``/onboarding``) render
  // AppShell without FleetContextProvider — fall back silently in that case
  // so the badge is purely additive and never blocks rendering.
  const fleet = useFleetOptional();
  if (!fleet?.game) return null;
  return (
    <span
      className="inline-flex items-center rounded-md border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-wos-text-soft"
      title={`Active game: ${fleet.game}`}
    >
      {fleet.game}
    </span>
  );
}

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
          className="fixed inset-0 z-40 backdrop-blur-sm md:hidden"
          style={{ backgroundColor: "var(--wos-overlay-scrim)" }}
          aria-label="Close menu"
          onClick={() => setNavOpen(false)}
        />
      )}

      <AppNav open={navOpen} onNavigate={() => setNavOpen(false)} />

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-wos-border-subtle/80 bg-wos-bg/95 px-4 py-3 backdrop-blur-md md:hidden">
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-wos-border-subtle bg-wos-surface text-wos-text hover:bg-wos-panel-raised"
            aria-label="Open menu"
            onClick={() => setNavOpen(true)}
          >
            <Icon name="menu" size="md" />
          </button>
          <span className="flex min-w-0 flex-1 items-center gap-2">
            <Image
              src="/logo.png"
              alt=""
              width={28}
              height={28}
              priority
              className="h-7 w-7 shrink-0 rounded-md object-contain"
            />
            <span className="truncate text-sm font-semibold tracking-tight text-wos-text">
              Autopilot
            </span>
          </span>
          <VersionBadge />
          <GameBadge />
          <ThemeToggle compact />
          <ApiStatusIndicator variant="header" />
        </header>

        <EarlyDevBanner />
        <AttentionBanner />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
      </div>
      <OnboardingWizard />
      <AppTooltipHost />
    </div>
    </FeedbackProvider>
    </ApiStatusProvider>
  );
}
