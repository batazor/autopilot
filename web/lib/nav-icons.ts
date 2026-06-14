import type { IconName } from "@/components/ui/Icon";
import type { NavGroupId } from "@/lib/nav-groups";

export type NavGroupIconId = NavGroupId;

export const NAV_GROUP_ICONS: Record<NavGroupId, IconName> = {
  operate: "operate",
  games: "games",
  debug: "debug",
  assets: "assets",
  config: "config",
};

/** @deprecated Use NAV_GROUP_ICONS — SVG via NavIcon */
export const NAV_SECTION_ICONS: Record<string, IconName> = {
  Operate: "operate",
  Debug: "debug",
  Assets: "assets",
  Config: "config",
  DB: "modules",
  Wiki: "wiki",
};

export const NAV_ICONS: Record<string, IconName> = {
  "/overview": "overview",
  "/instance": "instance",
  "/player-state": "player-state",
  "/player-stats": "player-stats",
  "/alliance-stats": "player-stats",
  "/approvals": "approvals",
  "/overlay-test": "overlay-test",
  "/fish-detect": "overlay-test",
  "/queue": "queue",
  "/routes": "routes",
  "/optimizer": "optimizer",
  "/gift-codes": "gift-codes",
  "/dreamscape-memory": "dreamscape-memory",
  "/notify-monitor": "notify-monitor",
  "/radar": "radar",
  "/calendar": "calendar",
  "/trees": "trees",
  "/wiki": "wiki",
  "/labeling": "labeling",
  "/gallery": "gallery",
  "/edit-dsl": "edit-dsl",
  "/modules": "modules",
  "/adb": "adb",
  "/balance": "balance",
  "/license": "license",
};
