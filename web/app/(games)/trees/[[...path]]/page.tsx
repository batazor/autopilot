"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
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

function researchIcon(node: { bonus: string; name: string }, branchId: string): string {
  const hay = `${node.bonus} ${node.name}`.toLowerCase();
  for (const [re, icon] of RESEARCH_ICON_RULES) if (re.test(hay)) return icon;
  return BRANCH_ICON[branchId] ?? "🔬";
}

function branchTotalLevels(branch: ResearchBranchView): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels, 0);
}

function researchLevelKey(nodeId: string, level: number): string {
  return `${nodeId}@${level}`;
}

// One node per (research, level). Edges follow the game's unlock rules:
//   • Level L of a research requires its own Level L-1 (sequential chain).
//   • Level 1 requires the FINAL level of each prerequisite research — in WoS a
//     research column only unlocks once the prior research is fully maxed.
function researchFlowNodes(branch: ResearchBranchView): FlowTreeNode[] {
  const maxLevel = new Map(branch.nodes.map((n) => [n.id, n.levels]));
  return branch.nodes.flatMap((n) =>
    Array.from({ length: n.levels }, (_, i) => {
      const level = i + 1;
      const requires =
        level > 1
          ? [researchLevelKey(n.id, level - 1)]
          : n.requires
              .filter((r) => maxLevel.has(r))
              .map((r) => researchLevelKey(r, maxLevel.get(r)!));
      return {
        id: researchLevelKey(n.id, level),
        tier: n.tier,
        title: n.name,
        subtitle: n.bonus,
        footer: `Tier ${ROMAN[n.tier] ?? n.tier}`,
        badge: `Lv ${level}`,
        icon: researchIcon(n, branch.id),
        requires,
      } satisfies FlowTreeNode;
    }),
  );
}

// In-game resource icon ids (verified vs the wiki: 103=Wood, 104=Coal, 105=Iron).
const RESOURCE: Record<string, { name: string; icon: string }> = {
  item_icon_102: { name: "Meat", icon: "🍖" },
  item_icon_103: { name: "Wood", icon: "🪵" },
  item_icon_104: { name: "Coal", icon: "⚫" },
  item_icon_105: { name: "Iron", icon: "🔩" },
};

function resourceLabel(item: string): string {
  const r = RESOURCE[item];
  return r ? `${r.icon} ${r.name}` : item.replace("item_icon_", "#");
}

function levelKey(building: string, level: number): string {
  return `${building}@${level}`;
}

// One node per (building, level). The graph follows the actual game rules,
// not the (inconsistent) free-text prerequisites:
//   • Furnace Lv N requires its support buildings at Lv N-1 (from furnace data).
//   • Any building at Lv L requires Furnace at Lv L (the level cap) — the wiki
//     lists a constant unlock level for camps/etc., which is wrong for build
//     order, so we apply the cap rule uniformly.
//   • Consecutive levels of a building chain (Lv L-1 → Lv L).
// Scoped to the Furnace build ladder (every Furnace level + what gates it).
function buildingFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const hubId = view.hub_id;
  const nameOf = new Map(view.buildings.map((b) => [b.id, b.name]));
  const furnace = view.buildings.find((b) => b.id === hubId);
  if (!furnace) return [];

  // Furnace level → its support-building requirements (correct in the data).
  const furnaceReq = new Map<number, { building: string; level: number }[]>();
  for (const [lvlStr, lvl] of Object.entries(furnace.requirements_by_level)) {
    furnaceReq.set(Number(lvlStr), lvl.requires ?? []);
  }

  // Cross/cap requirements of a (building@level), by rule.
  const ruleReqs = (key: string): string[] => {
    const [bid, lvlStr] = key.split("@");
    const level = Number(lvlStr);
    if (bid === hubId) {
      return (furnaceReq.get(level) ?? []).map((r) => levelKey(r.building, r.level));
    }
    return [levelKey(hubId, level)]; // building Lv L needs Furnace Lv L
  };

  // Closure from every Furnace level.
  const seen = new Set<string>();
  const stack = [...furnaceReq.keys()].map((l) => levelKey(hubId, l));
  while (stack.length) {
    const key = stack.pop()!;
    if (seen.has(key)) continue;
    seen.add(key);
    for (const dep of ruleReqs(key)) stack.push(dep);
  }

  // Previous included level per building → sequential chain edges.
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
    const base = ruleReqs(key).filter((k) => seen.has(k));
    const prev = prevLevel.get(key);
    const requires = prev !== undefined ? [levelKey(bid, prev), ...base] : base;
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

  // Detail for a (research@level) node id.
  const renderDetail = (key: string) => {
    const [rid, lvlStr] = key.split("@");
    const n = nodeById.get(rid);
    if (!n) return null;
    const level = Number(lvlStr);
    const reqText =
      level > 1
        ? `${n.name} Lv ${level - 1}`
        : n.requires
            .map((r) => {
              const dep = nodeById.get(r);
              return dep ? `${dep.name} Lv ${dep.levels}` : null;
            })
            .filter(Boolean)
            .join(", ");
    return (
      <div>
        <div className="font-semibold">
          {n.name} — Lv {level} / {n.levels}
        </div>
        <div className="text-wos-text-muted">{n.bonus}</div>
        <div className="mt-2 text-xs text-wos-text-secondary">
          Tier {ROMAN[n.tier] ?? n.tier}
        </div>
        <div className="mt-1 text-xs text-wos-text-secondary">
          Requires: {reqText || "—"}
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
      <TechTreeFlow
        nodes={nodes}
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
        <TechTreeFlow
          nodes={nodes}
          height={720}
          defaultDirection="LR"
          renderDetail={renderDetail}
          exportName={`buildings-${view.game}`}
        />
        <BuildingCatalog buildings={view.buildings} />
      </div>
    </>
  );
}

type DataType = "research" | "buildings";

function TreesContent() {
  const routeParams = useParams<{ path?: string[] }>();
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

  // Navigation state lives in the URL path (/trees/<game>/<type>/<branch?>) so
  // views are shareable/bookmarkable. We mirror local state to it via the
  // History API (router.replace soft-navigates and would drop the route params).
  const seg = routeParams.path ?? [];
  const [gameId, setGameId] = useState<string | null>(seg[0] ?? null);
  const [type, setType] = useState<DataType>(
    seg[1] === "buildings" ? "buildings" : "research",
  );
  const [branchId, setBranchId] = useState<string | null>(seg[2] ?? null);

  const game = games.find((g) => g.id === gameId) ?? games[0];

  const syncUrl = useCallback(
    (next: { game?: string; type?: DataType; branch?: string | null }) => {
      const g = next.game ?? gameId ?? game?.id;
      if (!g) return;
      const t = next.type ?? type;
      const b = next.branch !== undefined ? next.branch : branchId;
      const parts = ["trees", g, t];
      if (b) parts.push(b);
      window.history.replaceState(null, "", "/" + parts.join("/"));
    },
    [gameId, type, branchId, game?.id],
  );

  // Once the games load, normalize a bare /trees URL to the canonical path.
  useEffect(() => {
    if (game && seg.length < 2) syncUrl({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [game?.id]);

  // Adopt the URL on external navigation (a shared link, in-app <Link>).
  useEffect(() => {
    const s = routeParams.path ?? [];
    if (s[0]) setGameId(s[0]);
    if (s[1] === "research" || s[1] === "buildings") setType(s[1]);
    setBranchId(s[2] ?? null);
  }, [routeParams]);

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
