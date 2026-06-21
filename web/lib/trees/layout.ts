import {
  flowLayout,
  NODE_H,
  NODE_W,
  type FlowDir,
  type FlowTreeNode,
} from "@/components/TechTreeFlow";
import type { BuildingsView, ResearchBranchView } from "@/lib/types";
import { buildingIcon, researchIcon, ROMAN } from "./icons";
import { levelKey } from "./format";

// One node per research (roman tier), mirroring the in-game tree. The tier is
// in the name; per-level costs live in the hover detail table. With player
// progress, the badge shows researched/total ("4/6" or "MAX") like the game.
//
// The in-game tree is leveled (Tier I → II → III), but the source `requires`
// lists only the cross-line arrows. We derive the same-line tier ladder from
// line+tier and add it as an edge so the graph lays out as clean depth levels
// instead of a flat cloud — the same "ladder" reading as the buildings tree.
export function researchFlowNodes(
  branch: ResearchBranchView,
  progress?: Record<string, number>,
): FlowTreeNode[] {
  const known = new Set(branch.nodes.map((n) => n.id));
  const prevTier = new Map<string, string>();
  for (const n of branch.nodes) {
    const pred = branch.nodes.find((m) => m.line === n.line && m.tier === n.tier - 1);
    if (pred) prevTier.set(n.id, pred.id);
  }
  return branch.nodes.map((n) => {
    const cur = progress?.[n.id];
    const max = n.levels.length;
    const done = cur !== undefined && max > 0 && cur >= max;
    const ladder = prevTier.get(n.id);
    return {
      id: n.id,
      tier: n.tier,
      title: n.name,
      subtitle: n.bonus,
      footer: `Tier ${ROMAN[n.tier] ?? n.tier}`,
      badge:
        progress && cur !== undefined ? (done ? "MAX" : `${cur}/${max}`) : undefined,
      done,
      icon: researchIcon(n, branch.id),
      requires: [
        ...(ladder ? [ladder] : []),
        ...n.requires.filter((r) => known.has(r)),
      ],
    } satisfies FlowTreeNode;
  });
}

// ── Fire Age (T11 + T12) combined view ──────────────────────────────────────
// The six endgame branches (t11_/t12_ × class) merge into one radial graph:
// each class is laid out with dagre along its own axis (infantry grows up,
// lancer right, marksman down) and the T12 group is stacked after T11 along
// the same axis. Node ids are unique across all six branches (verified), and
// requires are still filtered per source branch, so no fake cross-class edges.
export const FIRE_AGE_ID = "fire_age";
export const FIRE_AGE_HUB = "__fire_age_hub";
const FIRE_CLASSES: { cls: string; dir: FlowDir }[] = [
  { cls: "infantry", dir: "BT" },
  { cls: "lancer", dir: "LR" },
  { cls: "marksman", dir: "TB" },
];
const HUB_GAP = 220; // distance from the center hub to each class strip
const TIER_GAP = 120; // gap between the T11 and T12 groups within a strip

export function isFireBranch(id: string): boolean {
  return /^t1[12]_/.test(id);
}

function bboxOf(layout: Map<string, { x: number; y: number }>) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const { x, y } of layout.values()) {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + NODE_W);
    maxY = Math.max(maxY, y + NODE_H);
  }
  return { minX, minY, maxX, maxY };
}

export function fireAgeFlowNodes(
  branches: ResearchBranchView[],
  progress?: Record<string, number>,
): FlowTreeNode[] {
  const out: FlowTreeNode[] = [];
  for (const { cls, dir } of FIRE_CLASSES) {
    let depth = HUB_GAP;
    for (const tier of ["t11", "t12"]) {
      const branch = branches.find((b) => b.id === `${tier}_${cls}`);
      if (!branch) continue;
      const nodes = researchFlowNodes(branch, progress);
      const layout = flowLayout(nodes, dir);
      const bb = bboxOf(layout);
      let dx = 0;
      let dy = 0;
      let extent = 0;
      if (dir === "TB") {
        dx = -(bb.minX + bb.maxX) / 2;
        dy = depth - bb.minY;
        extent = bb.maxY - bb.minY;
      } else if (dir === "BT") {
        dx = -(bb.minX + bb.maxX) / 2;
        dy = -depth - bb.maxY;
        extent = bb.maxY - bb.minY;
      } else {
        dy = -(bb.minY + bb.maxY) / 2;
        dx = depth - bb.minX;
        extent = bb.maxX - bb.minX;
      }
      for (const n of nodes) {
        const p = layout.get(n.id)!;
        out.push({ ...n, dir, position: { x: p.x + dx, y: p.y + dy } });
      }
      depth += extent + TIER_GAP;
    }
  }
  out.push({
    id: FIRE_AGE_HUB,
    tier: 0,
    title: "Fire Age",
    subtitle: "T11 → T12",
    icon: "🔥",
    width: 150,
    requires: [],
    position: { x: -75, y: -NODE_H / 2 },
  });
  return out;
}

// One node per (building, level), edges straight from the wiki data:
//   • the level's parsed cross-building prerequisites (lvl.requires) — e.g.
//     Barricade Lv 2 ← Furnace Lv 7, Command Center Lv 5 ← Furnace 10 + Embassy 5;
//   • the building's previous level (sequential chain; the API emits levels
//     already sorted: "1".."30", then "30-1".., then the "FC …" ladder);
//   • a fire_crystal_* ladder chains onto its base building's last numeric
//     level (Furnace 30 → Fire Crystal Furnace 30-1 → … → FC 10).
// Layout is dagre (auto).
export function buildingFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const byId = new Map(view.buildings.map((b) => [b.id, b]));
  const out: FlowTreeNode[] = [];
  for (const b of view.buildings) {
    const levels = Object.keys(b.requirements_by_level);
    const base = b.id.startsWith("fire_crystal_")
      ? byId.get(b.id.slice("fire_crystal_".length))
      : undefined;
    const baseTop = base
      ? Object.keys(base.requirements_by_level)
          .filter((k) => /^\d+$/.test(k))
          .map(Number)
          .sort((a, z) => z - a)[0]
      : undefined;
    levels.forEach((level, i) => {
      const lvl = b.requirements_by_level[level];
      const requires: string[] = [];
      if (i > 0) requires.push(levelKey(b.id, levels[i - 1]));
      else if (base && baseTop !== undefined)
        requires.push(levelKey(base.id, baseTop));
      for (const r of lvl?.requires ?? []) {
        const key = levelKey(r.building, r.level);
        if (!requires.includes(key)) requires.push(key);
      }
      out.push({
        id: levelKey(b.id, level),
        tier: 0,
        title: b.name,
        badge: /^\d+$/.test(level) ? `Lv ${level}` : level,
        icon: buildingIcon(b.id),
        requires,
      });
    });
  }
  return out;
}

// ── Buildings ladder layout ─────────────────────────────────────────────────
// Furnace (and its FC ladder) runs down the center as a vertical spine; every
// other building gets a side lane, alternating left/right. Rows come from
// longest-path layering over the prerequisite DAG, so every edge points
// strictly downward — the same "ladder" reading as the research trees. Lanes
// are greedily packed: a building whose level span starts after another's
// ends (plus a gap row) reuses its lane, keeping the graph narrow.
const B_ROW = 96; // px per layer row
const B_LANE = 240; // px per lane

export function buildingLadderLayout(
  nodes: FlowTreeNode[],
  hubId: string,
): FlowTreeNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const layer = new Map<string, number>();
  const depth = (id: string): number => {
    const got = layer.get(id);
    if (got !== undefined) return got;
    layer.set(id, 0); // cycle guard
    const n = byId.get(id);
    const v =
      n && n.requires.length
        ? Math.max(
            ...n.requires.map(
              (r) => depth(typeof r === "string" ? r : r.id) + 1,
            ),
          )
        : 0;
    layer.set(id, v);
    return v;
  };
  for (const n of nodes) depth(n.id);

  // fire_crystal_X continues X's chain, so they share a lane.
  const laneKey = (bid: string) => bid.replace(/^fire_crystal_/, "");
  const span = new Map<string, { start: number; end: number }>();
  for (const n of nodes) {
    const k = laneKey(n.id.split("@")[0]);
    const l = layer.get(n.id)!;
    const s = span.get(k);
    if (!s) span.set(k, { start: l, end: l });
    else {
      s.start = Math.min(s.start, l);
      s.end = Math.max(s.end, l);
    }
  }
  const hubKey = laneKey(hubId);
  const groups = [...span.entries()]
    .filter(([k]) => k !== hubKey)
    .sort((a, b) => a[1].start - b[1].start || a[0].localeCompare(b[0]));
  const laneX = new Map<string, number>([[hubKey, 0]]);
  const sides: { end: number }[][] = [[], []]; // 0 = left of spine, 1 = right
  groups.forEach(([k, s], i) => {
    const lanes = sides[i % 2];
    let li = lanes.findIndex((l) => l.end + 2 <= s.start);
    if (li === -1) {
      lanes.push({ end: s.end });
      li = lanes.length - 1;
    } else lanes[li].end = s.end;
    laneX.set(k, (i % 2 === 0 ? -1 : 1) * (li + 1) * B_LANE);
  });

  return nodes.map((n) => {
    const x = laneX.get(laneKey(n.id.split("@")[0])) ?? 0;
    return {
      ...n,
      dir: "TB" as FlowDir,
      position: { x: x - NODE_W / 2, y: layer.get(n.id)! * B_ROW },
    };
  });
}
