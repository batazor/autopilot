const SECTIONS_KEY = "wos.nav.sectionsCollapsed";
const RECENT_KEY = "wos.nav.recent";
const RECENT_MAX = 5;
const QUICK_ACCESS_KEY = "wos.nav.quickAccessCollapsed";
const SIDEBAR_KEY = "wos.nav.sidebarCollapsed";
const DOCK_POS_KEY = "wos.nav.dockPos";

export type RecentNavItem = { href: string; label: string };
export type DockPos = { x: number; y: number };

export function loadDockPos(): DockPos | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(DOCK_POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as unknown;
    if (
      typeof p === "object" &&
      p !== null &&
      typeof (p as DockPos).x === "number" &&
      typeof (p as DockPos).y === "number"
    ) {
      return p as DockPos;
    }
    return null;
  } catch {
    return null;
  }
}

export function saveDockPos(pos: DockPos): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DOCK_POS_KEY, JSON.stringify(pos));
  } catch {
    /* quota / private mode */
  }
}

export function clearDockPos(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(DOCK_POS_KEY);
  } catch {
    /* quota / private mode */
  }
}

function loadBoolPref(key: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function saveBoolPref(key: string, value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* quota / private mode */
  }
}

export const loadQuickAccessCollapsed = (): boolean =>
  loadBoolPref(QUICK_ACCESS_KEY);
export const saveQuickAccessCollapsed = (value: boolean): void =>
  saveBoolPref(QUICK_ACCESS_KEY, value);
export const loadSidebarCollapsed = (): boolean => loadBoolPref(SIDEBAR_KEY);
export const saveSidebarCollapsed = (value: boolean): void =>
  saveBoolPref(SIDEBAR_KEY, value);

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
