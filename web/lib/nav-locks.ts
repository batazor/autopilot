/**
 * Per-route navigation locks: badges + tooltips for routes that are
 * intentionally disabled because they require a higher license tier
 * (PRO) or because the feature isn't shipped yet (SOON), plus a
 * non-disabling "WIP" badge for routes that work but are still being
 * built out.
 *
 * AppNav and SectionTabs consult this so badges and disabled behavior
 * stay in one place. Only "pro" and "soon" disable/dim the tab; "wip"
 * is purely a label and the route stays clickable.
 */

export type NavLockKind = "pro" | "soon" | "wip";

export type NavLock = {
  kind: NavLockKind;
  tooltip: string;
};

export const NAV_LOCK_BADGE: Record<NavLockKind, string> = {
  pro: "PRO",
  soon: "SOON",
  wip: "WIP",
};

/** Kinds that disable navigation / dim the tab. "wip" intentionally does not. */
export function isLockDisabling(lock: NavLock | null | undefined): boolean {
  return lock?.kind === "pro" || lock?.kind === "soon";
}

const PRO_ONLY_HREFS = new Set<string>(["/alliance-stats"]);
const COMING_SOON_HREFS = new Set<string>(["/optimizer", "/balance"]);
// "wip" is resolved before pro/soon below, so a route here shows the WIP badge
// and stays clickable even if it also appears in PRO_ONLY/COMING_SOON. Removing
// it from this set restores whatever lock those sets imply.
const WIP_HREFS = new Set<string>([
  "/notify-monitor",
  "/alliance-stats",
  "/fish-detect",
  "/player-state",
]);

export function getNavLock(href: string, tier: string | null): NavLock | null {
  if (WIP_HREFS.has(href)) {
    return { kind: "wip", tooltip: "In progress — actively being built" };
  }
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
