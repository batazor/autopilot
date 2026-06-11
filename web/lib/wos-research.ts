// Whiteout Survival research tree — curated reference data (WoS only).
// Shared types live in research-types.ts; Kingshot data lives in
// kingshot-research.ts. Sourced once from
// https://www.whiteoutsurvival.wiki/research/ — node names are from the wiki;
// tier placement and prerequisite chains are a best-effort map of unlock order.
// Edit here to correct WoS data; the page renders straight from this file.

import type { ResearchGame, ResearchNode } from "@/lib/research-types";

const GROWTH: ResearchNode[] = [
  { id: "g_tooling_up", name: "Tooling Up", tier: 1, levels: 5, bonus: "Unlocks tool-based bonuses", requires: [] },
  { id: "g_camp_expansion", name: "Camp Expansion", tier: 1, levels: 5, bonus: "+Troop capacity", requires: [] },
  { id: "g_tool_enhancement", name: "Tool Enhancement", tier: 2, levels: 5, bonus: "+Speedup tool effectiveness", requires: ["g_tooling_up"] },
  { id: "g_ward_expansion", name: "Ward Expansion", tier: 2, levels: 5, bonus: "+Infirmary capacity", requires: ["g_camp_expansion"] },
  { id: "g_bandaging", name: "Bandaging", tier: 3, levels: 5, bonus: "+Healing speed", requires: ["g_ward_expansion"] },
  { id: "g_trainer_tools", name: "Trainer Tools", tier: 3, levels: 5, bonus: "+Training speed", requires: ["g_tool_enhancement"] },
  { id: "g_command_tactics", name: "Command Tactics", tier: 4, levels: 5, bonus: "+March capacity", requires: ["g_trainer_tools", "g_bandaging"] },
];

const ECONOMY: ResearchNode[] = [
  { id: "e_meat_output", name: "Meat Output", tier: 1, levels: 5, bonus: "+Meat production", requires: [] },
  { id: "e_wood_output", name: "Wood Output", tier: 1, levels: 5, bonus: "+Wood production", requires: [] },
  { id: "e_food_gathering", name: "Food Gathering", tier: 2, levels: 5, bonus: "+Food gathering speed", requires: ["e_meat_output"] },
  { id: "e_wood_gathering", name: "Wood Gathering", tier: 2, levels: 5, bonus: "+Wood gathering speed", requires: ["e_wood_output"] },
  { id: "e_coal_output", name: "Coal Output", tier: 3, levels: 5, bonus: "+Coal production", requires: ["e_food_gathering", "e_wood_gathering"] },
  { id: "e_coal_mining", name: "Coal Mining", tier: 4, levels: 5, bonus: "+Coal gathering speed", requires: ["e_coal_output"] },
  { id: "e_iron_output", name: "Iron Output", tier: 5, levels: 5, bonus: "+Iron production", requires: ["e_coal_mining"] },
  { id: "e_iron_mining", name: "Iron Mining", tier: 6, levels: 5, bonus: "+Iron gathering speed", requires: ["e_iron_output"] },
];

const BATTLE: ResearchNode[] = [
  // Marksman / offense lane
  { id: "b_weapons_prep", name: "Weapons Prep", tier: 1, levels: 6, bonus: "+Troop attack", requires: [] },
  { id: "b_reprisal", name: "Reprisal Tactics", tier: 2, levels: 6, bonus: "+Counter-attack damage", requires: ["b_weapons_prep"] },
  { id: "b_precision", name: "Precision Targeting", tier: 3, levels: 6, bonus: "+Marksman attack", requires: ["b_reprisal"] },
  { id: "b_targeted_sniping", name: "Targeted Sniping", tier: 4, levels: 6, bonus: "+Marksman lethality", requires: ["b_precision"] },
  { id: "b_marksman_armor", name: "Marksman Armor", tier: 5, levels: 6, bonus: "+Marksman defense", requires: ["b_targeted_sniping"] },
  // Infantry / defense lane
  { id: "b_def_formation", name: "Defensive Formation", tier: 1, levels: 6, bonus: "+Troop defense", requires: [] },
  { id: "b_picket_lines", name: "Picket Lines", tier: 2, levels: 6, bonus: "+Infantry defense", requires: ["b_def_formation"] },
  { id: "b_bulwark", name: "Bulwark Formations", tier: 3, levels: 6, bonus: "+Defense vs cavalry", requires: ["b_picket_lines"] },
  { id: "b_special_def", name: "Special Defensive Training", tier: 4, levels: 6, bonus: "+Troop health", requires: ["b_bulwark"] },
  { id: "b_shield_upgrade", name: "Shield Upgrade", tier: 5, levels: 6, bonus: "+Infantry defense", requires: ["b_special_def"] },
  // Lancer / troops lane
  { id: "b_survival", name: "Survival Techniques", tier: 1, levels: 6, bonus: "+Troop health", requires: [] },
  { id: "b_assault", name: "Assault Techniques", tier: 2, levels: 6, bonus: "+Troop lethality", requires: ["b_survival"] },
  { id: "b_regimental", name: "Regimental Expansion", tier: 3, levels: 6, bonus: "+Army size", requires: ["b_assault"] },
  { id: "b_close_combat", name: "Close Combat", tier: 4, levels: 6, bonus: "+Infantry attack", requires: ["b_regimental"] },
  { id: "b_lancer_upgrade", name: "Lancer Upgrade", tier: 5, levels: 6, bonus: "+Lancer attack", requires: ["b_close_combat"] },
  { id: "b_lancer_armor", name: "Lancer Armor", tier: 6, levels: 6, bonus: "+Lancer defense", requires: ["b_lancer_upgrade"] },
  { id: "b_skirmishing", name: "Skirmishing", tier: 6, levels: 6, bonus: "+Marksman attack", requires: ["b_marksman_armor"] },
];

export const WOS_RESEARCH: ResearchGame = {
  id: "wos",
  label: "Whiteout Survival",
  sourceUrl: "https://www.whiteoutsurvival.wiki/research/",
  sourceLabel: "whiteoutsurvival.wiki/research",
  branches: [
    {
      id: "growth",
      label: "Growth",
      blurb: "Camp/ward expansion, tools, healing and training bonuses.",
      nodes: GROWTH,
    },
    {
      id: "economy",
      label: "Economy",
      blurb: "Resource output and gathering across meat, wood, coal and iron.",
      nodes: ECONOMY,
    },
    {
      id: "battle",
      label: "Battle",
      blurb: "Infantry, Lancer and Marksman attack, defense and health.",
      nodes: BATTLE,
    },
  ],
};
