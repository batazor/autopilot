// Shared types for the per-game research trees. Data lives in one file per
// game (kingshot-research.ts, wos-research.ts) — never mixed here.
//
// `tier` doubles as the column (1 = leftmost / unlocked first ... N = deepest).
// `requires` lists the node ids that must be completed before this one opens.

export type ResearchBranchId = "growth" | "economy" | "battle";

export type ResearchNode = {
  id: string;
  name: string;
  /** Column / unlock depth, 1..N (rendered as tier I..N). */
  tier: number;
  /** Number of upgrade levels this research has. */
  levels: number;
  /** What maxing the research grants. */
  bonus: string;
  /** Prerequisite node ids (same branch). */
  requires: string[];
};

export type ResearchBranch = {
  id: ResearchBranchId;
  label: string;
  blurb: string;
  nodes: ResearchNode[];
};

export type ResearchGame = {
  id: string;
  label: string;
  /** Where the data was sourced from (shown on the page). */
  sourceUrl: string;
  sourceLabel: string;
  branches: ResearchBranch[];
};

export function branchTotalLevels(branch: ResearchBranch): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels, 0);
}
