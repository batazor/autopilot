import wosIcons from "@/lib/generated/wos-icons.json";
import type { ResearchResource } from "@/lib/types";

export const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

const BRANCH_ICON: Record<string, string> = {
  growth: "🌱",
  economy: "💰",
  battle: "⚔️",
  territory: "🏰",
};

const BUILDING_ICON: Record<string, string> = {
  furnace: "🔥",
  embassy: "🏛️",
  storehouse: "📦",
  clinic: "🏥",
  infirmary: "🏥",
  shelter: "🏠",
  cookhouse: "🍲",
  hero_hall: "🦸",
  infantry_camp: "🛡️",
  marksman_camp: "🏹",
  lancer_camp: "🐎",
  research_center: "🔬",
  command_center: "🎖️",
  iron_mine: "⛏️",
  sawmill: "🪵",
  coal_mine: "🪨",
  hunters_hut: "🥩",
  enlistment_office: "📜",
  barricade: "🧱",
  dawn_academy: "📚",
  beast_cage: "🐾",
  lighthouse: "🗼",
  arena: "🏟️",
  chiefs_house: "👑",
  war_academy: "⚔️",
};

const ICONS = wosIcons as { research: Record<string, string>; buildings: Record<string, string> };

export function buildingIcon(id: string): string {
  return (
    ICONS.buildings[id] ??
    BUILDING_ICON[id] ??
    BUILDING_ICON[id.replace(/^fire_crystal_/, "")] ??
    "🏗️"
  );
}

// Per-research icon by what the bonus does (more telling than one per branch).
const RESEARCH_ICON_RULES: [RegExp, string][] = [
  [/lancer|cavalry/, "🐎"],
  [/marksman|archer/, "🏹"],
  [/infantry/, "🛡️"],
  [/meat/, "🍖"],
  [/wood/, "🪵"],
  [/coal/, "⚫"],
  [/iron/, "🔩"],
  [/gather/, "⛏️"],
  [/production|output/, "🏭"],
  [/heal/, "💊"],
  [/construction|build/, "🏗️"],
  [/research/, "🔬"],
  [/march|capacity|army size/, "🚩"],
  [/attack|lethality/, "⚔️"],
  [/defense/, "🛡️"],
  [/health/, "❤️"],
  [/tool|speedup/, "🔧"],
];

export function researchIcon(
  node: { id: string; bonus: string; name: string },
  branchId: string,
): string {
  // Wiki icon by id; icon URLs were collected before the molten_* id cleanup,
  // so fall back to the bare line key (icons are shared across tiers anyway).
  const fromWiki =
    ICONS.research[node.id] ?? ICONS.research[node.id.replace(/_(i{1,3}|iv|v|vi|vii)$/, "")];
  if (fromWiki) return fromWiki;
  const hay = `${node.bonus} ${node.name}`.toLowerCase();
  for (const [re, icon] of RESEARCH_ICON_RULES) if (re.test(hay)) return icon;
  return BRANCH_ICON[branchId] ?? "🔬";
}

export const RESEARCH_RES: { key: ResearchResource; name: string; icon: string }[] = [
  { key: "meat", name: "Meat", icon: "🍖" },
  { key: "wood", name: "Wood", icon: "🪵" },
  { key: "coal", name: "Coal", icon: "⚫" },
  { key: "iron", name: "Iron", icon: "🔩" },
  { key: "steel", name: "Steel", icon: "⚙️" },
  { key: "fire_crystal", name: "Fire Crystal", icon: "🔥" },
  { key: "refined_fc", name: "Refined FC", icon: "💎" },
  { key: "fc_shards", name: "FC Shards", icon: "🔸" },
];

// In-game resource icon ids (verified vs the wiki: 103=Wood, 104=Coal, 105=Iron;
// building tables use 100011 for Meat; 100081/100082 are the Fire Crystal pair).
export const RESOURCE: Record<string, { name: string; icon: string }> = {
  item_icon_102: { name: "Meat", icon: "🍖" },
  item_icon_100011: { name: "Meat", icon: "🍖" },
  item_icon_103: { name: "Wood", icon: "🪵" },
  item_icon_104: { name: "Coal", icon: "⚫" },
  item_icon_105: { name: "Iron", icon: "🔩" },
  item_icon_100081: { name: "Fire Crystal", icon: "🔥" },
  item_icon_100082: { name: "Refined FC", icon: "💎" },
};

export function resourceLabel(item: string): string {
  const r = RESOURCE[item];
  return r ? `${r.icon} ${r.name}` : item.replace("item_icon_", "#");
}
