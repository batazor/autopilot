import type { HeroMissingRow, HeroStateRow } from "@/lib/types";

/** Sections stay expanded when the list is short. */
export const COLLAPSE_BUILDINGS_ABOVE = 18;
export const COLLAPSE_HEROES_ABOVE = 10;
export const AVATAR_IDENTITY_HELP =
  "Avatar identity works best when each account on this device uses a different in-game avatar. Shared avatars fall back to the chief-profile identity probe.";

export function filterHeroRows<T extends HeroStateRow | HeroMissingRow>(
  rows: T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) => Object.values(r).join(" ").toLowerCase().includes(q));
}

export function countLabel(shown: number, total: number): string {
  return shown === total ? String(total) : `${shown} / ${total}`;
}

export function matchesBuilding(
  r: { id: string; building: string; category: string; level: number | string },
  q: string,
): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [r.id, r.building, r.category, String(r.level)]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}
