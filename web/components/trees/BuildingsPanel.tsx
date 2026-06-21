"use client";

import { useMemo, useState } from "react";
import { AppSwitch, AppTabs } from "@/components/headless";
import { TechTreeFlow } from "@/components/TechTreeFlow";
import type { TreeProgress } from "@/lib/api";
import type { BuildingsView } from "@/lib/types";
import { RESOURCE, resourceLabel } from "@/lib/trees/icons";
import { parseAmount, parseDuration, pathClosure } from "@/lib/trees/format";
import { buildingFlowNodes, buildingLadderLayout } from "@/lib/trees/layout";
import { BuildingCatalog } from "./BuildingCatalog";
import { BuildScheduleView } from "./BuildScheduleView";
import { CostSummary } from "./CostSummary";
import { SourceLine } from "./SourceLine";

type BuildView = "tree" | "schedule";

export function BuildingsPanel({
  view,
  progress,
  playerId,
}: {
  view: BuildingsView;
  progress?: TreeProgress;
  playerId: string;
}) {
  // Tree (dependency graph) ⇄ Schedule (furnace-first Gantt). Kept in ?bmode=.
  const [mode, setMode] = useState<BuildView>(() =>
    typeof window !== "undefined" &&
    new URL(window.location.href).searchParams.get("bmode") === "schedule"
      ? "schedule"
      : "tree",
  );
  const onMode = (next: string) => {
    const m = next === "schedule" ? "schedule" : "tree";
    setMode(m);
    const url = new URL(window.location.href);
    if (m === "schedule") url.searchParams.set("bmode", "schedule");
    else url.searchParams.delete("bmode");
    window.history.replaceState(null, "", url.pathname + url.search);
  };
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
      <div className="tree-detail">
        <div>
          <div className="tree-detail__title">{b.name}</div>
          <div className="tree-meta mt-2">
            <span className="tree-chip">
              Level <strong>{lvlStr}</strong>
            </span>
            {lvl?.construction_time && lvl.construction_time !== "-" ? (
              <span className="tree-chip">⏱ {lvl.construction_time}</span>
            ) : null}
            {lvl?.building_power ? (
              <span className="tree-chip">
                ⚡ <strong>{lvl.building_power}</strong>
              </span>
            ) : null}
          </div>
        </div>
        <CostSummary
          title={`Full build path: ${path.size} steps`}
          rows={costRows}
          totalTime={pathTime}
          note="Every building level required up to this point (no speedups)."
        />
        {lvl?.prerequisites ? (
          <div className="tree-meta">
            <span className="tree-chip tree-chip--requires" title={lvl.prerequisites}>
              Requires <span>{lvl.prerequisites}</span>
            </span>
          </div>
        ) : null}
        {lvl?.build_cost?.length ? (
          <div className="tree-table-wrap">
            <table className="tree-table">
              <thead>
                <tr>
                  <th>Resource</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {lvl.build_cost.map((c, i) => (
                  <tr key={i}>
                    <td>{resourceLabel(c.item)}</td>
                    <td className="num">{c.amount}</td>
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
      <AppTabs
        variant="toolbar"
        renderPanels={false}
        selectedKey={mode}
        onChange={onMode}
        tabs={[
          { key: "tree", label: "Tree" },
          { key: "schedule", label: "Schedule (Gantt)" },
        ]}
      />
      <div className="mt-3">
        {mode === "schedule" ? (
          <BuildScheduleView playerId={playerId} />
        ) : (
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
        )}
      </div>
    </>
  );
}
