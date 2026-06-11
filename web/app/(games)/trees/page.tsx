"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
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

function levelKey(building: string, level: number): string {
  return `${building}@${level}`;
}

// One node per (building, level) wired by per-level prerequisites — at level
// granularity the graph is an acyclic tree (laid out by dagre). Scoped to the
// Furnace build ladder: every Furnace level plus its transitive prerequisites.
function buildingFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const hubId = view.hub_id;
  const nameOf = new Map(view.buildings.map((b) => [b.id, b.name]));

  const reqOf = new Map<string, { building: string; level: number }[]>();
  for (const b of view.buildings) {
    for (const [lvlStr, lvl] of Object.entries(b.requirements_by_level)) {
      reqOf.set(levelKey(b.id, Number(lvlStr)), lvl.requires ?? []);
    }
  }

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

  // Previous included level of the same building, so each building forms a
  // sequential chain (Furnace Lv2 → Lv3 → …) in addition to cross-building deps.
  const byBuilding = new Map<string, number[]>();
  for (const key of seen) {
    const [bid, lvl] = key.split("@");
    byBuilding.set(bid, [...(byBuilding.get(bid) ?? []), Number(lvl)]);
  }
  const prevLevel = new Map<string, number>();
  for (const [bid, levels] of byBuilding) {
    levels.sort((a, b) => a - b);
    for (let i = 1; i < levels.length; i++) {
      prevLevel.set(levelKey(bid, levels[i]), levels[i - 1]);
    }
  }

  return [...seen].map((key) => {
    const [bid, lvlStr] = key.split("@");
    const level = Number(lvlStr);
    const crossReqs = (reqOf.get(key) ?? [])
      .map((r) => levelKey(r.building, r.level))
      .filter((k) => seen.has(k));
    const prev = prevLevel.get(key);
    const requires = prev !== undefined ? [levelKey(bid, prev), ...crossReqs] : crossReqs;
    return {
      id: key,
      tier: 0,
      title: nameOf.get(bid) ?? bid,
      badge: `Lv ${level}`,
      icon: buildingIcon(bid),
      requires,
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
}: {
  game: ResearchGameView;
  branchId: string | null;
  onBranch: (id: string) => void;
}) {
  const branch =
    game.branches.find((b) => b.id === branchId) ?? game.branches[0];
  const nodes = useMemo(
    () => (branch ? researchFlowNodes(branch) : []),
    [branch],
  );
  const nodeById = useMemo(
    () => new Map((branch?.nodes ?? []).map((n) => [n.id, n])),
    [branch],
  );
  if (!branch) return <p className="muted">No research data.</p>;

  const renderDetail = (id: string) => {
    const n = nodeById.get(id);
    if (!n) return null;
    const reqNames = n.requires
      .map((r) => nodeById.get(r)?.name)
      .filter(Boolean)
      .join(", ");
    return (
      <div>
        <div className="font-semibold">{n.name}</div>
        <div className="text-wos-text-muted">{n.bonus}</div>
        <div className="mt-2 text-xs text-wos-text-secondary">
          Tier {ROMAN[n.tier] ?? n.tier} · {n.levels} levels
        </div>
        <div className="mt-1 text-xs text-wos-text-secondary">
          Requires: {reqNames || "—"}
        </div>
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
        tabs={game.branches.map((b) => ({
          key: b.id,
          label: `${b.label} (${branchTotalLevels(b)})`,
          title: b.blurb,
        }))}
      />
      <p className="muted mb-3 mt-1 text-sm">{branch.blurb}</p>
      <TechTreeFlow nodes={nodes} defaultDirection="TB" renderDetail={renderDetail} />
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
  const byId = useMemo(
    () => new Map(view.buildings.map((b) => [b.id, b])),
    [view],
  );

  const renderDetail = (key: string) => {
    const [bid, lvlStr] = key.split("@");
    const b = byId.get(bid);
    const lvl = b?.requirements_by_level[lvlStr];
    if (!b) return null;
    return (
      <div>
        <div className="font-semibold">
          {b.name} — Lv {lvlStr}
        </div>
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
                  {c.amount} · {c.item.replace("item_icon_", "#")}
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
        <TechTreeFlow
          nodes={nodes}
          height={720}
          defaultDirection="LR"
          renderDetail={renderDetail}
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

  // Navigation state lives in the URL (?game=&type=&branch=) so views are
  // shareable/bookmarkable. We mirror local state to it via the History API
  // (router.replace soft-navigates and would drop useSearchParams updates).
  const [gameId, setGameId] = useState<string | null>(params.get("game"));
  const [type, setType] = useState<DataType>(
    params.get("type") === "buildings" ? "buildings" : "research",
  );
  const [branchId, setBranchId] = useState<string | null>(params.get("branch"));

  const game = games.find((g) => g.id === gameId) ?? games[0];

  const syncUrl = useCallback(
    (next: { game?: string; type?: DataType; branch?: string | null }) => {
      const url = new URL(window.location.href);
      if (next.game !== undefined) url.searchParams.set("game", next.game);
      if (next.type !== undefined) url.searchParams.set("type", next.type);
      if (next.branch !== undefined) {
        if (next.branch) url.searchParams.set("branch", next.branch);
        else url.searchParams.delete("branch");
      }
      window.history.replaceState(null, "", url.pathname + url.search);
    },
    [],
  );

  // Adopt the URL on external navigation (back/forward, a shared link).
  useEffect(() => {
    const g = params.get("game");
    const t = params.get("type");
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
    syncUrl({ type: next });
  };
  const onBranchChange = (next: string) => {
    setBranchId(next);
    syncUrl({ branch: next });
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
            variant="section"
            renderPanels={false}
            selectedKey={type}
            onChange={(k) => onTypeChange(k as DataType)}
            tabs={[
              { key: "research", label: "Research" },
              { key: "buildings", label: "Buildings" },
            ]}
          />

          <div className="mt-3">
            {type === "research" ? (
              <ResearchPanel
                key={game.id}
                game={game}
                branchId={branchId}
                onBranch={onBranchChange}
              />
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

export default function TreesPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <TreesContent />
    </Suspense>
  );
}
