// Whiteout Survival buildings — curated reference data (WoS only).
// Shared types live in buildings-types.ts. Sourced once from
// https://www.whiteoutsurvival.wiki/buildings/ (catalog) and
// https://www.whiteoutsurvival.wiki/buildings/furnace/ (Furnace upgrade
// requirements, levels 2-16 explicit). The Furnace gates and caps every other
// building. Edit here to correct WoS data; the page renders from this file.

import type { Building, BuildingGame, HubRequirement } from "@/lib/buildings-types";

const HUB_MAX = 30;

const BUILDINGS: Building[] = [
  // Inner city
  { id: "furnace", name: "Furnace", category: "inner", maxLevel: HUB_MAX },
  { id: "embassy", name: "Embassy", category: "inner", maxLevel: HUB_MAX },
  { id: "storehouse", name: "Storehouse", category: "inner", maxLevel: HUB_MAX },
  { id: "clinic", name: "Clinic", category: "inner" },
  { id: "shelter", name: "Shelter", category: "inner", maxLevel: HUB_MAX },
  { id: "cookhouse", name: "Cookhouse", category: "inner", maxLevel: HUB_MAX },
  { id: "hero_hall", name: "Hero Hall", category: "inner", maxLevel: HUB_MAX },
  // Military
  { id: "enlistment", name: "Enlistment Office", category: "military" },
  { id: "barricade", name: "Barricade", category: "military" },
  { id: "infantry_camp", name: "Infantry Camp", category: "military", maxLevel: HUB_MAX },
  { id: "marksman_camp", name: "Marksman Camp", category: "military", maxLevel: HUB_MAX },
  { id: "lancer_camp", name: "Lancer Camp", category: "military", maxLevel: HUB_MAX },
  { id: "research_center", name: "Research Center", category: "military", maxLevel: HUB_MAX },
  { id: "infirmary", name: "Infirmary", category: "military", maxLevel: HUB_MAX },
  { id: "command_center", name: "Command Center", category: "military", maxLevel: HUB_MAX },
  // Resource
  { id: "iron_mine", name: "Iron Mine", category: "resource", maxLevel: HUB_MAX },
  { id: "sawmill", name: "Sawmill", category: "resource", maxLevel: HUB_MAX },
  { id: "coal_mine", name: "Coal Mine", category: "resource", maxLevel: HUB_MAX },
  { id: "hunters_hut", name: "Hunter's Hut", category: "resource", maxLevel: HUB_MAX },
  // Other
  { id: "dawn_academy", name: "Dawn Academy", category: "other" },
  { id: "beast_cage", name: "Beast Cage", category: "other" },
  { id: "lighthouse", name: "Lighthouse", category: "other" },
  { id: "arena", name: "Arena", category: "other" },
  { id: "chiefs_house", name: "Chief's House", category: "other" },
  { id: "explorers_cabin", name: "Explorers Cabin", category: "other" },
  { id: "war_academy", name: "War Academy", category: "other" },
];

// Furnace upgrade prerequisites, levels 2-16 (explicit on the wiki). Levels
// 17-30 keep raising Embassy + the three camps + Research Center; see `note`.
const HUB_REQUIREMENTS: HubRequirement[] = [
  { hubLevel: 2, requires: [{ building: "sawmill", level: 1 }] },
  { hubLevel: 3, requires: [{ building: "shelter", level: 2 }] },
  { hubLevel: 4, requires: [{ building: "coal_mine", level: 3 }] },
  { hubLevel: 5, requires: [{ building: "hero_hall", level: 1 }, { building: "shelter", level: 3 }] },
  { hubLevel: 6, requires: [{ building: "iron_mine", level: 5 }] },
  { hubLevel: 7, requires: [{ building: "hunters_hut", level: 6 }] },
  { hubLevel: 8, requires: [{ building: "infantry_camp", level: 7 }] },
  { hubLevel: 9, requires: [{ building: "embassy", level: 8 }, { building: "infirmary", level: 1 }] },
  { hubLevel: 10, requires: [{ building: "marksman_camp", level: 9 }, { building: "research_center", level: 1 }] },
  { hubLevel: 11, requires: [{ building: "embassy", level: 10 }, { building: "lancer_camp", level: 10 }] },
  { hubLevel: 12, requires: [{ building: "embassy", level: 11 }, { building: "command_center", level: 1 }] },
  { hubLevel: 13, requires: [{ building: "embassy", level: 12 }, { building: "infantry_camp", level: 12 }] },
  { hubLevel: 14, requires: [{ building: "embassy", level: 13 }, { building: "marksman_camp", level: 13 }] },
  { hubLevel: 15, requires: [{ building: "embassy", level: 14 }, { building: "lancer_camp", level: 14 }] },
  { hubLevel: 16, requires: [{ building: "embassy", level: 15 }, { building: "research_center", level: 15 }] },
];

export const WOS_BUILDINGS: BuildingGame = {
  id: "wos",
  label: "Whiteout Survival",
  sourceUrl: "https://www.whiteoutsurvival.wiki/buildings/",
  sourceLabel: "whiteoutsurvival.wiki/buildings",
  hubId: "furnace",
  hubMaxLevel: HUB_MAX,
  buildings: BUILDINGS,
  hubRequirements: HUB_REQUIREMENTS,
  note: "Furnace levels 2-16 are explicit; 17-30 progressively raise Embassy, the three troop camps and Research Center. Building max level is capped by the Furnace level.",
};
