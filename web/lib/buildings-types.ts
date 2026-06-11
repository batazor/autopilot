// Shared types for the per-game building dependency pages. Data lives in one
// file per game (wos-buildings.ts, …) — never mixed here.
//
// WoS model: the Furnace (hub) gates and caps every other building. Its level
// determines which buildings unlock and how far they can be upgraded, and each
// Furnace level requires specific OTHER buildings at specific levels first.

export type BuildingCategory = "inner" | "military" | "resource" | "other";

export type Building = {
  id: string;
  name: string;
  category: BuildingCategory;
  /** Max level, when known (most hub-gated buildings cap at the Furnace max). */
  maxLevel?: number;
};

/** To reach `hubLevel`, the listed buildings must first be at the given level. */
export type HubRequirement = {
  hubLevel: number;
  requires: { building: string; level: number }[];
};

export type BuildingGame = {
  id: string;
  label: string;
  sourceUrl: string;
  sourceLabel: string;
  /** Building whose level caps/gates all others (Furnace in WoS). */
  hubId: string;
  hubMaxLevel: number;
  buildings: Building[];
  /** Hub (Furnace) upgrade prerequisites, ordered by hub level. */
  hubRequirements: HubRequirement[];
  /** Free-text note about data beyond what's explicitly listed. */
  note?: string;
};

export const CATEGORY_LABEL: Record<BuildingCategory, string> = {
  inner: "Inner city",
  military: "Military",
  resource: "Resource",
  other: "Other",
};
