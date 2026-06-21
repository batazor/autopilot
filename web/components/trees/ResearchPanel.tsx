"use client";

import { useMemo } from "react";
import { AppTabs } from "@/components/headless";
import { TechTreeFlow } from "@/components/TechTreeFlow";
import type { TreeProgress } from "@/lib/api";
import type {
  ResearchBranchView,
  ResearchGameView,
  ResearchResource,
} from "@/lib/types";
import { ROMAN, RESEARCH_RES } from "@/lib/trees/icons";
import { branchTotalLevels, fmtNum, parseDuration, pathClosure } from "@/lib/trees/format";
import {
  FIRE_AGE_HUB,
  FIRE_AGE_ID,
  fireAgeFlowNodes,
  isFireBranch,
  researchFlowNodes,
} from "@/lib/trees/layout";
import { CostSummary } from "./CostSummary";
import { SourceLine } from "./SourceLine";

export function ResearchPanel({
  game,
  branchId,
  onBranch,
  progress,
  exportPrefix = "research",
}: {
  game: ResearchGameView;
  branchId: string | null;
  onBranch: (id: string) => void;
  progress?: TreeProgress;
  exportPrefix?: string;
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
        <div className="tree-detail">
          <div className="tree-detail__title">Fire Age</div>
          <div className="text-wos-text-muted">
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
      <div className="tree-detail">
        <div>
          <div className="tree-detail__title">{n.name}</div>
          <div className="tree-detail__bonus">{n.bonus}</div>
          <div className="tree-meta mt-2">
            <span className="tree-chip">
              Tier <strong>{ROMAN[n.tier] ?? n.tier}</strong>
            </span>
            <span className="tree-chip">
              <strong>{n.levels.length}</strong> levels
            </span>
            {reqText ? (
              <span className="tree-chip tree-chip--requires" title={reqText}>
                Requires <span>{reqText}</span>
              </span>
            ) : null}
          </div>
        </div>
        <CostSummary
          title={`Full path to max: ${path.size} techs, ${pathLevels} levels`}
          rows={costRows}
          totalTime={pathTime}
          note="Maxing this tech and every transitive prerequisite (no speedups)."
        />
        {n.levels.length ? (
          <div className="tree-table-wrap">
            <table className="tree-table">
              <thead>
                <tr>
                  <th>Lv</th>
                  <th>Effect</th>
                  {hasRC ? <th title="Research Center">RC</th> : null}
                  {hasGate ? <th title="War Academy Fire Crystal level">Gate</th> : null}
                  <th className="num">Time</th>
                  <th className="num" title="Power">
                    ⚡
                  </th>
                  {usedRes.map((r) => (
                    <th key={r.key} className="num" title={r.name}>
                      {r.icon}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {n.levels.map((lv) => (
                  <tr key={lv.level}>
                    <td>
                      <span className="tree-table__lv">{lv.level}</span>
                    </td>
                    <td>{lv.effect || <span className="muted-cell">—</span>}</td>
                    {hasRC ? <td>{lv.rc ?? <span className="muted-cell">—</span>}</td> : null}
                    {hasGate ? (
                      <td className="gate-cell whitespace-nowrap">
                        {lv.gate || <span className="muted-cell">—</span>}
                      </td>
                    ) : null}
                    <td className="num whitespace-nowrap">
                      {lv.time || <span className="muted-cell">—</span>}
                    </td>
                    <td className="num">
                      {lv.power ? fmtNum(lv.power) : <span className="muted-cell">—</span>}
                    </td>
                    {usedRes.map((r) => (
                      <td key={r.key} className="num">
                        {lv.cost[r.key] ? (
                          fmtNum(lv.cost[r.key]!)
                        ) : (
                          <span className="muted-cell">—</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
        exportName={`${exportPrefix}-${game.id}-${branch.id}`}
      />
    </>
  );
}
