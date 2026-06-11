// Registry of per-game building dependency data rendered by /buildings.
// Each game's data lives in its own file — see wos-buildings.ts. Order here
// drives the game tab order on the page. Only WoS has data today.

import type { BuildingGame } from "@/lib/buildings-types";
import { WOS_BUILDINGS } from "@/lib/wos-buildings";

export const BUILDING_GAMES: BuildingGame[] = [WOS_BUILDINGS];
