"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { TechTreeFlow, type FlowTreeNode } from "@/components/TechTreeFlow";
import { fetchBuildings, fetchResearch } from "@/lib/api";
import type {
  BuildingDef,
  BuildingsView,
  ResearchBranchView,
  ResearchGameView,
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

function buildingIcon(id: string): string {
  return BUILDING_ICON[id] ?? BUILDING_ICON[id.replace(/^fire_crystal_/, "")] ?? "🏗️";
}

function branchTotalLevels(branch: ResearchBranchView): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels, 0);
}

function researchFlowNodes(branch: ResearchBranchView): FlowTreeNode[] {
  const icon = BRANCH_ICON[branch.id] ?? "🔬";
  return branch.nodes.map((n) => ({
    id: n.id,
    tier: n.tier,
    title: n.name,
    subtitle: n.bonus,
    footer: `0 / ${n.levels} levels`,
    badge: ROMAN[n.tier] ?? String(n.tier),
    icon,
    requires: n.requires,
  }));
}

// Per-(building, level) swimlane layout: one node per building level, laid out
// with x = level and y = building row. Scoped to the Furnace build ladder —
// every Furnace level plus the transitive build/upgrade prerequisites it needs
// — so the graph is the real, acyclic build order instead of a cyclic blob.
const LANE_LABEL_X = 0;
const LANE_LABEL_W = 150;
const LEVEL_X0 = 180;
const LEVEL_STEP = 86;
const LANE_H = 84;
const LEVEL_W = 66;

function levelKey(building: string, level: number): string {
  return `${building}@${level}`;
}

function buildingFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const hubId = view.hub_id;
  const nameOf = new Map(view.buildings.map((b) => [b.id, b.name]));

  // (building@level) -> its prerequisite (building@level) keys.
  const reqOf = new Map<string, { building: string; level: number }[]>();
  for (const b of view.buildings) {
    for (const [lvlStr, lvl] of Object.entries(b.requirements_by_level)) {
      reqOf.set(levelKey(b.id, Number(lvlStr)), lvl.requires ?? []);
    }
  }

  // Closure: seed with every Furnace level, pull in transitive prerequisites.
  const seen = new Set<string>();
  const stack: { building: string; level: number }[] = view.buildings
    .filter((b) => b.id === hubId)
    .flatMap((b) =>
      Object.keys(b.requirements_by_level).map((l) => ({
        building: b.id,
        level: Number(l),
      })),
    );
  while (stack.length) {
    const n = stack.pop()!;
    const key = levelKey(n.building, n.level);
    if (seen.has(key)) continue;
    seen.add(key);
    for (const r of reqOf.get(key) ?? []) stack.push(r);
  }

  // Group included levels by building; order rows by earliest level (hub first).
  const byBuilding = new Map<string, number[]>();
  for (const key of seen) {
    const [bid, lvl] = key.split("@");
    byBuilding.set(bid, [...(byBuilding.get(bid) ?? []), Number(lvl)]);
  }
  const rows = [...byBuilding.entries()].sort((a, b) => {
    if (a[0] === hubId) return -1;
    if (b[0] === hubId) return 1;
    return Math.min(...a[1]) - Math.min(...b[1]) || a[0].localeCompare(b[0]);
  });

  const nodes: FlowTreeNode[] = [];
  rows.forEach(([bid, levels], rowIdx) => {
    const y = rowIdx * LANE_H;
    // Lane label (building name + icon) at the left.
    nodes.push({
      id: `lane:${bid}`,
      tier: 0,
      title: nameOf.get(bid) ?? bid,
      icon: buildingIcon(bid),
      position: { x: LANE_LABEL_X, y },
      width: LANE_LABEL_W,
      requires: [],
    });
    for (const level of levels) {
      nodes.push({
        id: levelKey(bid, level),
        tier: 0,
        title: `Lv ${level}`,
        position: { x: LEVEL_X0 + (level - 1) * LEVEL_STEP, y },
        width: LEVEL_W,
        requires: (reqOf.get(levelKey(bid, level)) ?? [])
          .map((r) => levelKey(r.building, r.level))
          .filter((k) => seen.has(k)),
      });
    }
  });
  return nodes;
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

function ResearchPanel({ game }: { game: ResearchGameView }) {
  const [branchId, setBranchId] = useState(game.branches[0]?.id ?? "");
  const branch = game.branches.find((b) => b.id === branchId) ?? game.branches[0];
  const nodes = useMemo(
    () => (branch ? researchFlowNodes(branch) : []),
    [branch],
  );
  if (!branch) return <p className="muted">No research data.</p>;

  return (
    <>
      <SourceLine url={game.source_url} label={game.source_label} />
      <AppTabs
        variant="section"
        renderPanels={false}
        selectedKey={branch.id}
        onChange={setBranchId}
        tabs={game.branches.map((b) => ({
          key: b.id,
          label: `${b.label} (${branchTotalLevels(b)})`,
          title: b.blurb,
        }))}
      />
      <p className="muted mb-3 mt-1 text-sm">{branch.blurb}</p>
      <TechTreeFlow nodes={nodes} />
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

function BuildingsPanel({ view }: { view: BuildingsView }) {
  const nodes = useMemo(() => buildingFlowNodes(view), [view]);
  return (
    <>
      <SourceLine
        url="https://www.whiteoutsurvival.wiki/buildings/"
        label="whiteoutsurvival.wiki/buildings"
      />
      <div className="flex flex-col gap-4">
        <TechTreeFlow nodes={nodes} height={720} />
        <BuildingCatalog buildings={view.buildings} />
      </div>
    </>
  );
}

export default function TreesPage() {
  const research = useQuery<ResearchView>({
    queryKey: ["research"],
    queryFn: fetchResearch,
  });
  const buildings = useQuery<BuildingsView>({
    queryKey: ["buildings"],
    queryFn: fetchBuildings,
  });

  // Game list = every game with research; buildings exist for a subset.
  const games = research.data?.games ?? [];
  const buildingsGameId = buildings.data?.game ?? "wos";

  const [gameId, setGameId] = useState<string | null>(null);
  const [type, setType] = useState<"research" | "buildings">("research");

  const game = games.find((g) => g.id === gameId) ?? games[0];
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
              onChange={setGameId}
              tabs={games.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
            />
          ) : null}

          <AppTabs
            variant="section"
            renderPanels={false}
            selectedKey={type}
            onChange={(k) => setType(k as "research" | "buildings")}
            tabs={[
              { key: "research", label: "Research" },
              { key: "buildings", label: "Buildings" },
            ]}
          />

          <div className="mt-3">
            {type === "research" ? (
              <ResearchPanel key={game.id} game={game} />
            ) : buildings.data && game.id === buildingsGameId ? (
              <BuildingsPanel view={buildings.data} />
            ) : (
              <p className="muted">No building data for {game.label} yet.</p>
            )}
          </div>
        </>
      ) : null}
    </>
  );
}
