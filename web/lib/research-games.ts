// Registry of per-game research trees rendered by /research-tree.
// Each game's data lives in its own file — see wos-research.ts and
// kingshot-research.ts. Order here drives the game tab order on the page.

import { KINGSHOT_RESEARCH } from "@/lib/kingshot-research";
import type { ResearchGame } from "@/lib/research-types";
import { WOS_RESEARCH } from "@/lib/wos-research";

export const RESEARCH_GAMES: ResearchGame[] = [WOS_RESEARCH, KINGSHOT_RESEARCH];
