/**
 * Per-route navigation locks: badges + tooltips for routes that are
 * intentionally disabled because they require a higher license tier
 * (PRO) or because the feature isn't shipped yet (SOON).
 *
 * AppNav and SectionTabs consult this so badges and disabled behavior
 * stay in one place.
 */

export type NavLockKind = "pro" | "soon";

export type NavLock = {
  kind: NavLockKind;
  tooltip: string;
};

export const NAV_LOCK_BADGE: Record<NavLockKind, string> = {
  pro: "PRO",
  soon: "SOON",
};

const PRO_ONLY_HREFS = new Set<string>(["/alliance-stats"]);
const COMING_SOON_HREFS = new Set<string>(["/optimizer", "/balance"]);

export function getNavLock(href: string, tier: string | null): NavLock | null {
  if (COMING_SOON_HREFS.has(href)) {
    return { kind: "soon", tooltip: "Coming soon — not yet available" };
  }
  if (PRO_ONLY_HREFS.has(href) && tier !== "pro") {
    return {
      kind: "pro",
      tooltip: "Requires PRO license — open License to upgrade",
    };
  }
  return null;
}
