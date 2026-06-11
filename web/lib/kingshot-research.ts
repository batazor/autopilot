// Kingshot Academy research tree — curated reference data (Kingshot only).
// Shared types live in research-types.ts; WoS data lives in wos-research.ts.
//
// Modeled on the public tree at https://kingshot.net/research-tree (three
// categories: Growth, Economy, Battle). That page is "under construction" and
// only exposes the Battle node names + per-category level totals, so the node
// graph below is hand-curated from the in-game Academy and community guides.
// It is intentionally the SINGLE place to correct game data — the page renders
// straight from this file, so fixing a name/level/prerequisite here updates the
// whole visualization. No prerequisite chain is authoritative; treat it as a
// best-effort map of unlock order.
//
// `tier` doubles as the column (1 = leftmost / unlocked first ... 6 = deepest).
// `requires` lists the node ids that must be completed before this one opens.

import type { ResearchGame, ResearchNode } from "@/lib/research-types";

const GROWTH: ResearchNode[] = [
  { id: "g_construction_1", name: "Construction I", tier: 1, levels: 5, bonus: "+Construction speed", requires: [] },
  { id: "g_research_1", name: "Research I", tier: 1, levels: 5, bonus: "+Research speed", requires: [] },
  { id: "g_tooling_up", name: "Tooling Up", tier: 1, levels: 3, bonus: "Unlocks tool-based bonuses", requires: [] },
  { id: "g_construction_2", name: "Construction II", tier: 2, levels: 5, bonus: "+Construction speed", requires: ["g_construction_1"] },
  { id: "g_research_2", name: "Research II", tier: 2, levels: 5, bonus: "+Research speed", requires: ["g_research_1"] },
  { id: "g_tool_enhancement", name: "Tool Enhancement", tier: 2, levels: 4, bonus: "+Speedup tool effectiveness", requires: ["g_tooling_up"] },
  { id: "g_march_queue", name: "March Queue", tier: 3, levels: 1, bonus: "+1 march queue", requires: ["g_construction_2"] },
  { id: "g_healing_speed", name: "Healing Speed", tier: 3, levels: 5, bonus: "+Troop healing speed", requires: ["g_research_2"] },
  { id: "g_command_tactics", name: "Command Tactics", tier: 3, levels: 4, bonus: "+March capacity", requires: ["g_tool_enhancement"] },
  { id: "g_march_capacity", name: "March Capacity", tier: 4, levels: 5, bonus: "+March size", requires: ["g_march_queue", "g_command_tactics"] },
  { id: "g_defense_reinforce", name: "Defense Reinforcement", tier: 4, levels: 3, bonus: "+Reinforcement capacity", requires: ["g_healing_speed"] },
];

const ECONOMY: ResearchNode[] = [
  { id: "e_meat", name: "Meat Production", tier: 1, levels: 5, bonus: "+Meat output", requires: [] },
  { id: "e_wood", name: "Wood Production", tier: 1, levels: 5, bonus: "+Wood output", requires: [] },
  { id: "e_gathering_1", name: "Gathering I", tier: 1, levels: 4, bonus: "+Gathering speed", requires: [] },
  { id: "e_coal", name: "Coal Production", tier: 2, levels: 5, bonus: "+Coal output", requires: ["e_meat", "e_wood"] },
  { id: "e_load_capacity", name: "Load Capacity", tier: 2, levels: 4, bonus: "+Gatherer load size", requires: ["e_gathering_1"] },
  { id: "e_iron", name: "Iron Production", tier: 3, levels: 5, bonus: "+Iron output", requires: ["e_coal"] },
  { id: "e_gathering_2", name: "Gathering II", tier: 3, levels: 4, bonus: "+Gathering speed", requires: ["e_load_capacity"] },
  { id: "e_resource_protect", name: "Resource Protection", tier: 4, levels: 3, bonus: "+Protected resources", requires: ["e_iron"] },
  { id: "e_construction_res", name: "Construction Resources", tier: 4, levels: 4, bonus: "-Building resource cost", requires: ["e_gathering_2"] },
];

const BATTLE: ResearchNode[] = [
  // Offense lane
  { id: "b_weapons_prep", name: "Weapons Prep", tier: 1, levels: 6, bonus: "+Troop attack", requires: [] },
  { id: "b_reprisal", name: "Reprisal Tactics", tier: 2, levels: 6, bonus: "+Counter-attack damage", requires: ["b_weapons_prep"] },
  { id: "b_precision", name: "Precision Targeting", tier: 3, levels: 6, bonus: "+Archer attack", requires: ["b_reprisal"] },
  { id: "b_cavalry_charge", name: "Cavalry Charge", tier: 4, levels: 6, bonus: "+Cavalry attack", requires: ["b_precision"] },
  { id: "b_targeted_sniping", name: "Targeted Sniping", tier: 5, levels: 6, bonus: "+Archer lethality", requires: ["b_cavalry_charge"] },
  // Defense lane
  { id: "b_def_formations", name: "Defensive Formations", tier: 1, levels: 6, bonus: "+Troop defense", requires: [] },
  { id: "b_picket_lines", name: "Picket Lines", tier: 2, levels: 6, bonus: "+Infantry defense", requires: ["b_def_formations"] },
  { id: "b_bulwark", name: "Bulwark Formations", tier: 3, levels: 6, bonus: "+Defense vs cavalry", requires: ["b_picket_lines"] },
  { id: "b_special_def", name: "Special Defensive Training", tier: 4, levels: 6, bonus: "+Troop health", requires: ["b_bulwark"] },
  { id: "b_shield_upgrade", name: "Shield Upgrade", tier: 5, levels: 6, bonus: "+Infantry defense", requires: ["b_special_def"] },
  // Troops lane
  { id: "b_survival", name: "Survival Techniques", tier: 1, levels: 6, bonus: "+Troop health", requires: [] },
  { id: "b_assault", name: "Assault Techniques", tier: 2, levels: 6, bonus: "+Troop lethality", requires: ["b_survival"] },
  { id: "b_regimental", name: "Regimental Expansion", tier: 3, levels: 6, bonus: "+Army size", requires: ["b_assault"] },
  { id: "b_close_combat", name: "Close Combat", tier: 4, levels: 6, bonus: "+Infantry attack", requires: ["b_regimental"] },
  { id: "b_lance_upgrade", name: "Lance Upgrade", tier: 5, levels: 6, bonus: "+Cavalry attack", requires: ["b_close_combat"] },
  // Deep tier
  { id: "b_leathercraft", name: "Leathercraft", tier: 6, levels: 6, bonus: "+Troop health", requires: ["b_shield_upgrade"] },
  { id: "b_fortified_mail", name: "Fortified Mail", tier: 6, levels: 6, bonus: "+Troop defense", requires: ["b_leathercraft"] },
];

export const KINGSHOT_RESEARCH: ResearchGame = {
  id: "kingshot",
  label: "Kingshot",
  sourceUrl: "https://kingshot.net/research-tree",
  sourceLabel: "kingshot.net/research-tree",
  branches: [
    {
      id: "growth",
      label: "Growth",
      blurb: "Construction, research, healing and march bonuses.",
      nodes: GROWTH,
    },
    {
      id: "economy",
      label: "Economy",
      blurb: "Resource production, gathering and protection.",
      nodes: ECONOMY,
    },
    {
      id: "battle",
      label: "Battle",
      blurb: "Troop attack, defense, health and lethality across three lanes.",
      nodes: BATTLE,
    },
  ],
};
