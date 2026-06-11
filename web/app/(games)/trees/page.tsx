"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { AppListbox, AppSwitch, AppTabs } from "@/components/headless";
import {
  flowLayout,
  NODE_H,
  NODE_W,
  TechTreeFlow,
  type FlowDir,
  type FlowTreeNode,
} from "@/components/TechTreeFlow";
import {
  fetchBuildings,
  fetchPlayers,
  fetchResearch,
  fetchTreeProgress,
  type TreeProgress,
} from "@/lib/api";
import wosIcons from "@/lib/generated/wos-icons.json";
import type {
  BuildingDef,
  BuildingsView,
  ResearchBranchView,
  ResearchGameView,
  ResearchResource,
  ResearchView,
} from "@/lib/types";

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

const BRANCH_ICON: Record<string, string> = {
  growth: "🌱",
  economy: "💰",
  battle: "⚔️",
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

function buildingIcon(id: string): string {
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

function researchIcon(
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

function branchTotalLevels(branch: ResearchBranchView): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels.length, 0);
}

const RESEARCH_RES: { key: ResearchResource; name: string; icon: string }[] = [
  { key: "meat", name: "Meat", icon: "🍖" },
  { key: "wood", name: "Wood", icon: "🪵" },
  { key: "coal", name: "Coal", icon: "⚫" },
  { key: "iron", name: "Iron", icon: "🔩" },
  { key: "steel", name: "Steel", icon: "⚙️" },
  { key: "fire_crystal", name: "Fire Crystal", icon: "🔥" },
  { key: "refined_fc", name: "Refined FC", icon: "💎" },
  { key: "fc_shards", name: "FC Shards", icon: "🔸" },
];

function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

// "5.3K" / "1.4M" / "23,000" / 270 → number (0 when unparsable).
function parseAmount(v: string | number): number {
  if (typeof v === "number") return v;
  const s = v.trim().replace(/,/g, "");
  const m = /^(\d+(?:\.\d+)?)\s*([KMB])?$/i.exec(s);
  if (!m) return 0;
  const mult = { K: 1e3, M: 1e6, B: 1e9 }[m[2]?.toUpperCase() as "K" | "M" | "B"] ?? 1;
  return Math.round(Number(m[1]) * mult);
}

// "00:21:30" / "90:16:40" (hours can exceed 24) / "7d" / "2d 02:00:00" → seconds.
function parseDuration(s: string | null | undefined): number {
  const t = (s ?? "").trim();
  if (!t || t === "-") return 0;
  let sec = 0;
  const d = /(\d+)\s*d/i.exec(t);
  if (d) sec += Number(d[1]) * 86400;
  const hms = /(\d+):(\d{2}):(\d{2})/.exec(t);
  if (hms) sec += Number(hms[1]) * 3600 + Number(hms[2]) * 60 + Number(hms[3]);
  return sec;
}

function fmtDuration(sec: number): string {
  if (sec <= 0) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return [d ? `${d}d` : "", h ? `${h}h` : "", m ? `${m}m` : ""].filter(Boolean).join(" ") || "<1m";
}

/** Transitive prerequisite closure (incl. `id` itself) over FlowTreeNodes. */
function pathClosure(nodes: FlowTreeNode[], id: string): Set<string> {
  const reqs = new Map(
    nodes.map((n) => [n.id, n.requires.map((r) => (typeof r === "string" ? r : r.id))]),
  );
  const seen = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    if (seen.has(cur) || !reqs.has(cur)) continue;
    seen.add(cur);
    for (const dep of reqs.get(cur)!) stack.push(dep);
  }
  return seen;
}

function CostSummary({
  title,
  rows,
  totalTime,
  note,
}: {
  title: string;
  rows: { icon: string; name: string; amount: number }[];
  totalTime: number;
  note: string;
}) {
  if (!rows.length && totalTime <= 0) return null;
  return (
    <div
      className="mt-2 rounded-md border p-2 text-xs"
      style={{ borderColor: "var(--wos-border)", background: "var(--wos-surface)" }}
    >
      <div className="font-medium">{title}</div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
        {rows.map((r) => (
          <span key={r.name} title={r.name} className="tabular-nums">
            {r.icon} {fmtNum(r.amount)}
          </span>
        ))}
        <span title="Total time">⏱ {fmtDuration(totalTime)}</span>
      </div>
      <div className="mt-1 text-wos-text-muted">{note}</div>
    </div>
  );
}

// One node per research (roman tier), mirroring the in-game tree. The tier is
// in the name; per-level costs live in the hover detail table. With player
// progress, the badge shows researched/total ("4/6" or "MAX") like the game.
function researchFlowNodes(
  branch: ResearchBranchView,
  progress?: Record<string, number>,
): FlowTreeNode[] {
  const known = new Set(branch.nodes.map((n) => n.id));
  return branch.nodes.map((n) => {
    const cur = progress?.[n.id];
    const max = n.levels.length;
    const done = cur !== undefined && max > 0 && cur >= max;
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
      requires: n.requires.filter((r) => known.has(r)),
    } satisfies FlowTreeNode;
  });
}

// ── Fire Age (T11 + T12) combined view ──────────────────────────────────────
// The six endgame branches (t11_/t12_ × class) merge into one radial graph:
// each class is laid out with dagre along its own axis (infantry grows up,
// lancer right, marksman down) and the T12 group is stacked after T11 along
// the same axis. Node ids are unique across all six branches (verified), and
// requires are still filtered per source branch, so no fake cross-class edges.
const FIRE_AGE_ID = "fire_age";
const FIRE_AGE_HUB = "__fire_age_hub";
const FIRE_CLASSES: { cls: string; dir: FlowDir }[] = [
  { cls: "infantry", dir: "BT" },
  { cls: "lancer", dir: "LR" },
  { cls: "marksman", dir: "TB" },
];
const HUB_GAP = 220; // distance from the center hub to each class strip
const TIER_GAP = 120; // gap between the T11 and T12 groups within a strip

function isFireBranch(id: string): boolean {
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

function fireAgeFlowNodes(
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

// In-game resource icon ids (verified vs the wiki: 103=Wood, 104=Coal, 105=Iron;
// building tables use 100011 for Meat; 100081/100082 are the Fire Crystal pair).
const RESOURCE: Record<string, { name: string; icon: string }> = {
  item_icon_102: { name: "Meat", icon: "🍖" },
  item_icon_100011: { name: "Meat", icon: "🍖" },
  item_icon_103: { name: "Wood", icon: "🪵" },
  item_icon_104: { name: "Coal", icon: "⚫" },
  item_icon_105: { name: "Iron", icon: "🔩" },
  item_icon_100081: { name: "Fire Crystal", icon: "🔥" },
  item_icon_100082: { name: "Refined FC", icon: "💎" },
};

function resourceLabel(item: string): string {
  const r = RESOURCE[item];
  return r ? `${r.icon} ${r.name}` : item.replace("item_icon_", "#");
}

function levelKey(building: string, level: number | string): string {
  return `${building}@${level}`;
}

// One node per (building, level), edges straight from the wiki data:
//   • the level's parsed cross-building prerequisites (lvl.requires) — e.g.
//     Barricade Lv 2 ← Furnace Lv 7, Command Center Lv 5 ← Furnace 10 + Embassy 5;
//   • the building's previous level (sequential chain; the API emits levels
//     already sorted: "1".."30", then "30-1".., then the "FC …" ladder);
//   • a fire_crystal_* ladder chains onto its base building's last numeric
//     level (Furnace 30 → Fire Crystal Furnace 30-1 → … → FC 10).
// Layout is dagre (auto).
function buildingFlowNodes(view: BuildingsView): FlowTreeNode[] {
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

function buildingLadderLayout(
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

function SourceLine({ url, label }: { url: string; label: string }) {
  return (
    <p className="muted mb-3 text-sm">
      Sourced from{" "}
      <a className="underline" href={url} target="_blank" rel="noreferrer">
        {label}
      </a>
      .
    </p>
  );
}

function ResearchPanel({
  game,
  branchId,
  onBranch,
  progress,
}: {
  game: ResearchGameView;
  branchId: string | null;
  onBranch: (id: string) => void;
  progress?: TreeProgress;
}) {
  // The six t11_/t12_ source branches collapse into one "Fire Age" tab.
  const { branches, fireSources } = useMemo(() => {
    const fireSources = game.branches.filter((b) => isFireBranch(b.id));
    const base = game.branches.filter((b) => !isFireBranch(b.id));
    const branches: ResearchBranchView[] = fireSources.length
      ? [
          ...base,
          {
            id: FIRE_AGE_ID,
            label: "Fire Age",
            blurb:
              "T11 (Helios) and T12 (Molten/Exalted) endgame research, all three classes in one graph: infantry grows up, lancer right, marksman down.",
            nodes: fireSources.flatMap((b) => b.nodes),
          },
        ]
      : base;
    return { branches, fireSources };
  }, [game]);
  const wantId = branchId && isFireBranch(branchId) ? FIRE_AGE_ID : branchId;
  const branch = branches.find((b) => b.id === wantId) ?? branches[0];
  const isFire = branch?.id === FIRE_AGE_ID;
  const nodes = useMemo(() => {
    if (!branch) return [];
    return isFire
      ? fireAgeFlowNodes(fireSources, progress?.research)
      : researchFlowNodes(branch, progress?.research);
  }, [branch, isFire, fireSources, progress]);
  const nodeById = useMemo(
    () => new Map((branch?.nodes ?? []).map((n) => [n.id, n])),
    [branch],
  );
  if (!branch) return <p className="muted">No research data.</p>;

  // Detail for a research node id — full per-level table (cost / time / power).
  const renderDetail = (rid: string) => {
    if (rid === FIRE_AGE_HUB)
      return (
        <div>
          <div className="font-semibold">Fire Age</div>
          <div className="mt-1 text-wos-text-muted">
            T11 (Helios) and T12 (Molten/Exalted) research for all three troop
            classes. Infantry grows up, lancer right, marksman down; each
            class chains T11 → T12 outward from this hub.
          </div>
        </div>
      );
    const n = nodeById.get(rid);
    if (!n) return null;
    const reqText = n.requires
      .map((r) => nodeById.get(r)?.name)
      .filter(Boolean)
      .join(", ");
    const usedRes = RESEARCH_RES.filter((r) =>
      n.levels.some((lv) => (lv.cost[r.key] ?? 0) > 0),
    );
    const hasRC = n.levels.some((lv) => lv.rc != null);
    const hasGate = n.levels.some((lv) => lv.gate);
    // Cost planner: max out this tech + every transitive prerequisite.
    const path = pathClosure(nodes, rid);
    const totals = new Map<ResearchResource, number>();
    let pathTime = 0;
    let pathLevels = 0;
    for (const pid of path) {
      const pn = nodeById.get(pid);
      if (!pn) continue;
      pathLevels += pn.levels.length;
      for (const lv of pn.levels) {
        pathTime += parseDuration(lv.time);
        for (const r of RESEARCH_RES)
          if (lv.cost[r.key]) totals.set(r.key, (totals.get(r.key) ?? 0) + lv.cost[r.key]!);
      }
    }
    const costRows = RESEARCH_RES.filter((r) => totals.get(r.key)).map((r) => ({
      icon: r.icon,
      name: r.name,
      amount: totals.get(r.key)!,
    }));
    return (
      <div>
        <div className="font-semibold">{n.name}</div>
        <div className="text-wos-text-muted">{n.bonus}</div>
        <div className="mt-1 text-xs text-wos-text-secondary">
          Tier {ROMAN[n.tier] ?? n.tier} · {n.levels.length} lvls · Requires:{" "}
          {reqText || "—"}
        </div>
        <CostSummary
          title={`Full path to max: ${path.size} techs, ${pathLevels} levels`}
          rows={costRows}
          totalTime={pathTime}
          note="Maxing this tech and every transitive prerequisite (no speedups)."
        />
        {n.levels.length ? (
          <table className="mt-2 w-full border-collapse text-[11px]">
            <thead className="text-wos-text-secondary">
              <tr className="text-left">
                <th className="pr-2 font-medium">Lv</th>
                <th className="pr-2 font-medium">Effect</th>
                {hasRC ? (
                  <th className="pr-2 font-medium" title="Research Center">
                    RC
                  </th>
                ) : null}
                {hasGate ? (
                  <th className="pr-2 font-medium" title="War Academy Fire Crystal level">
                    Gate
                  </th>
                ) : null}
                <th className="pr-2 font-medium">Time</th>
                <th className="pr-2 font-medium" title="Power">
                  ⚡
                </th>
                {usedRes.map((r) => (
                  <th key={r.key} className="pr-2 text-right font-medium" title={r.name}>
                    {r.icon}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {n.levels.map((lv) => (
                <tr key={lv.level} className="border-t border-[color:var(--wos-border)]">
                  <td className="pr-2 py-0.5">{lv.level}</td>
                  <td className="pr-2">{lv.effect || "—"}</td>
                  {hasRC ? <td className="pr-2">{lv.rc ?? "—"}</td> : null}
                  {hasGate ? (
                    <td className="pr-2 whitespace-nowrap">{lv.gate || "—"}</td>
                  ) : null}
                  <td className="pr-2 whitespace-nowrap">
                    {lv.time || "—"}
                  </td>
                  <td className="pr-2">{lv.power ? fmtNum(lv.power) : "—"}</td>
                  {usedRes.map((r) => (
                    <td key={r.key} className="pr-2 text-right tabular-nums">
                      {lv.cost[r.key] ? fmtNum(lv.cost[r.key]!) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    );
  };

  return (
    <>
      <SourceLine url={game.source_url} label={game.source_label} />
      <AppTabs
        variant="section"
        renderPanels={false}
        selectedKey={branch.id}
        onChange={onBranch}
        tabs={branches.map((b) => ({
          key: b.id,
          label: `${b.label} (${branchTotalLevels(b)})`,
          title: b.blurb,
        }))}
      />
      <p className="muted mb-3 mt-1 text-sm">{branch.blurb}</p>
      <TechTreeFlow
        nodes={nodes}
        height={isFire ? 760 : 600}
        defaultDirection="TB"
        renderDetail={renderDetail}
        exportName={`research-${game.id}-${branch.id}`}
      />
    </>
  );
}

function BuildingCatalog({ buildings }: { buildings: BuildingDef[] }) {
  const rows = useMemo(
    () => [...buildings].sort((a, b) => a.name.localeCompare(b.name)),
    [buildings],
  );
  return (
    <section className="panel panel--spaced">
      <h2>Buildings ({rows.length})</h2>
      <div className="data-table-wrap mt-3">
        <table className="data-table">
          <thead>
            <tr>
              <th>Building</th>
              <th>Max level</th>
              <th>Unlocks at</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id}>
                <td className="font-medium">{b.name}</td>
                <td>{b.max_level ?? "—"}</td>
                <td className="text-wos-text-muted">
                  {b.requirements_by_level["1"]?.prerequisites || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BuildingsPanel({
  view,
  progress,
}: {
  view: BuildingsView;
  progress?: TreeProgress;
}) {
  // Toggle: full graph ⇄ the Furnace ladder (Furnace/FC-Furnace levels plus
  // everything transitively required to advance them). Kept in ?bview=ladder.
  const [ladder, setLadder] = useState<boolean>(
    () =>
      typeof window !== "undefined" &&
      new URL(window.location.href).searchParams.get("bview") === "ladder",
  );
  const onLadder = (next: boolean) => {
    setLadder(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("bview", "ladder");
    else url.searchParams.delete("bview");
    window.history.replaceState(null, "", url.pathname + url.search);
  };

  const allNodes = useMemo(() => {
    const base = buildingLadderLayout(buildingFlowNodes(view), view.hub_id);
    const lvls = progress?.buildings;
    if (!lvls) return base;
    // Player overlay: a (building, numeric level) node is done when the
    // player's recorded level reaches it.
    return base.map((n) => {
      const [bid, lvlStr] = n.id.split("@");
      const have = lvls[bid];
      const done =
        have !== undefined && /^\d+$/.test(lvlStr) && have >= Number(lvlStr);
      return done ? { ...n, done } : n;
    });
  }, [view, progress]);
  const nodes = useMemo(() => {
    if (!ladder) return allNodes;
    const keep = new Set<string>();
    for (const n of allNodes) {
      const bid = n.id.split("@")[0];
      if (bid === view.hub_id || bid === `fire_crystal_${view.hub_id}`) {
        for (const k of pathClosure(allNodes, n.id)) keep.add(k);
      }
    }
    return allNodes.filter((n) => keep.has(n.id));
  }, [allNodes, ladder, view.hub_id]);
  const byId = useMemo(
    () => new Map(view.buildings.map((b) => [b.id, b])),
    [view],
  );

  const renderDetail = (key: string) => {
    const [bid, lvlStr] = key.split("@");
    const b = byId.get(bid);
    const lvl = b?.requirements_by_level[lvlStr];
    if (!b) return null;
    // Cost planner: build everything this (building, level) depends on.
    const path = pathClosure(nodes, key);
    const totals = new Map<string, number>();
    let pathTime = 0;
    for (const pid of path) {
      const [pb, plv] = pid.split("@");
      const pl = byId.get(pb)?.requirements_by_level[plv];
      if (!pl) continue;
      pathTime += parseDuration(pl.construction_time);
      for (const c of pl.build_cost ?? []) {
        totals.set(c.item, (totals.get(c.item) ?? 0) + parseAmount(c.amount));
      }
    }
    const costRows = [...totals.entries()]
      .filter(([, amt]) => amt > 0)
      .map(([item, amount]) => {
        const r = RESOURCE[item];
        return {
          icon: r?.icon ?? "❔",
          name: r?.name ?? item.replace("item_icon_", "#"),
          amount,
        };
      });
    return (
      <div>
        <div className="font-semibold">
          {b.name} — Lv {lvlStr}
        </div>
        <CostSummary
          title={`Full build path: ${path.size} steps`}
          rows={costRows}
          totalTime={pathTime}
          note="Every building level required up to this point (no speedups)."
        />
        {lvl?.prerequisites ? (
          <div className="mt-1 text-xs text-wos-text-muted">
            Requires: {lvl.prerequisites}
          </div>
        ) : null}
        <div className="mt-2 space-y-0.5 text-xs text-wos-text-secondary">
          {lvl?.construction_time && lvl.construction_time !== "-" ? (
            <div>⏱ {lvl.construction_time}</div>
          ) : null}
          {lvl?.building_power ? <div>⚡ {lvl.building_power} power</div> : null}
        </div>
        {lvl?.build_cost?.length ? (
          <div className="mt-2">
            <div className="text-xs font-medium">Cost</div>
            <ul className="text-xs text-wos-text-muted">
              {lvl.build_cost.map((c, i) => (
                <li key={i}>
                  {resourceLabel(c.item)} · {c.amount}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <>
      <SourceLine
        url="https://www.whiteoutsurvival.wiki/buildings/"
        label="whiteoutsurvival.wiki/buildings"
      />
      <div className="flex flex-col gap-4">
        <AppSwitch
          inline
          checked={ladder}
          onChange={onLadder}
          label="Furnace ladder"
          title="Show only the Furnace chain (incl. FC) and everything required to advance it"
        />
        <TechTreeFlow
          key={ladder ? "ladder" : "full"}
          nodes={nodes}
          height={720}
          defaultDirection="TB"
          renderDetail={renderDetail}
          exportName={`buildings-${view.game}${ladder ? "-ladder" : ""}`}
        />
        <BuildingCatalog buildings={view.buildings} />
      </div>
    </>
  );
}

type DataType = "research" | "buildings";

function TreesContent() {
  const params = useSearchParams();
  const research = useQuery<ResearchView>({
    queryKey: ["research"],
    queryFn: fetchResearch,
  });
  const buildings = useQuery<BuildingsView>({
    queryKey: ["buildings"],
    queryFn: fetchBuildings,
  });

  const games = research.data?.games ?? [];
  const buildingsGameId = buildings.data?.game ?? "wos";

  // Navigation state lives in the URL query (?game=&tab=&branch=) so views are
  // shareable/bookmarkable. We mirror local state to it via the History API
  // (router.replace soft-navigates and would drop the useSearchParams updates).
  const [gameId, setGameId] = useState<string | null>(params.get("game"));
  const [type, setType] = useState<DataType>(
    params.get("tab") === "buildings" ? "buildings" : "research",
  );
  const [branchId, setBranchId] = useState<string | null>(params.get("branch"));
  const [playerId, setPlayerId] = useState<string>(params.get("player") ?? "");

  const players = useQuery<string[]>({
    queryKey: ["players"],
    queryFn: () => fetchPlayers(),
  });
  const progress = useQuery<TreeProgress>({
    queryKey: ["tree-progress", playerId],
    queryFn: () => fetchTreeProgress(playerId),
    enabled: Boolean(playerId),
  });

  const game = games.find((g) => g.id === gameId) ?? games[0];

  const syncUrl = useCallback(
    (next: {
      game?: string;
      tab?: DataType;
      branch?: string | null;
      player?: string | null;
    }) => {
      const url = new URL(window.location.href);
      if (next.game !== undefined) url.searchParams.set("game", next.game);
      if (next.tab !== undefined) url.searchParams.set("tab", next.tab);
      if (next.branch !== undefined) {
        if (next.branch) url.searchParams.set("branch", next.branch);
        else url.searchParams.delete("branch");
      }
      if (next.player !== undefined) {
        if (next.player) url.searchParams.set("player", next.player);
        else url.searchParams.delete("player");
      }
      window.history.replaceState(null, "", url.pathname + url.search);
    },
    [],
  );

  // Adopt the URL on external navigation (back/forward, a shared link).
  useEffect(() => {
    const g = params.get("game");
    const t = params.get("tab");
    const b = params.get("branch");
    if (g) setGameId(g);
    if (t === "research" || t === "buildings") setType(t);
    setBranchId(b);
  }, [params]);

  const onGameChange = (next: string) => {
    setGameId(next);
    syncUrl({ game: next });
  };
  const onTypeChange = (next: DataType) => {
    setType(next);
    syncUrl({ tab: next });
  };
  const onBranchChange = (next: string) => {
    setBranchId(next);
    syncUrl({ branch: next });
  };
  const onPlayerChange = (next: string) => {
    setPlayerId(next);
    syncUrl({ player: next || null });
  };

  const isLoading = research.isLoading || buildings.isLoading;
  const error = research.error ?? buildings.error;

  return (
    <>
      <PageHeader title="Game trees">
        <p className="muted m-0">
          Research &amp; building dependency trees per game — served from{" "}
          <code>games/&lt;game&gt;/db/</code> (single source of truth).
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner">
          {error instanceof Error ? error.message : String(error)}
        </div>
      ) : null}
      {isLoading ? <p className="muted">Loading…</p> : null}

      {game ? (
        <>
          {games.length > 1 ? (
            <AppTabs
              renderPanels={false}
              selectedKey={game.id}
              onChange={onGameChange}
              tabs={games.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
            />
          ) : null}

          <AppTabs
            variant="toolbar"
            renderPanels={false}
            selectedKey={type}
            onChange={(k) => onTypeChange(k as DataType)}
            tabs={[
              { key: "research", label: "Research" },
              { key: "buildings", label: "Buildings" },
            ]}
            afterTabs={
              <AppListbox
                inline
                label="Player"
                value={playerId}
                onChange={onPlayerChange}
                loading={players.isLoading}
                minWidth={160}
                options={[
                  { value: "", label: "— no progress —" },
                  ...(players.data ?? []).map((p) => ({ value: p, label: p })),
                ]}
              />
            }
          />

          <div className="mt-3">
            {type === "research" ? (
              <ResearchPanel
                key={game.id}
                game={game}
                branchId={branchId}
                onBranch={onBranchChange}
                progress={progress.data}
              />
            ) : buildings.data && game.id === buildingsGameId ? (
              <BuildingsPanel view={buildings.data} progress={progress.data} />
            ) : (
              <p className="muted">No building data for {game.label} yet.</p>
            )}
          </div>
        </>
      ) : null}
    </>
  );
}

export default function TreesPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <TreesContent />
    </Suspense>
  );
}
