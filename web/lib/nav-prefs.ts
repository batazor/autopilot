const SECTIONS_KEY = "wos.nav.sectionsCollapsed";
const RECENT_KEY = "wos.nav.recent";
const RECENT_MAX = 5;

export type RecentNavItem = { href: string; label: string };

export function loadSectionCollapsed(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(SECTIONS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, boolean>)
      : {};
  } catch {
    return {};
  }
}

export function saveSectionCollapsed(state: Record<string, boolean>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SECTIONS_KEY, JSON.stringify(state));
  } catch {
    /* quota / private mode */
  }
}

export function loadRecent(): RecentNavItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x): x is RecentNavItem =>
        typeof x === "object" &&
        x !== null &&
        typeof (x as RecentNavItem).href === "string" &&
        typeof (x as RecentNavItem).label === "string",
    );
  } catch {
    return [];
  }
}

export function pushRecent(href: string, label: string): void {
  if (typeof window === "undefined" || !href) return;
  const prev = loadRecent().filter((x) => x.href !== href);
  const next = [{ href, label }, ...prev].slice(0, RECENT_MAX);
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}
