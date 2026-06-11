"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { TechTreeFlow, type FlowTreeNode } from "@/components/TechTreeFlow";
import { fetchBuildings } from "@/lib/api";
import type { BuildingDef, BuildingsView } from "@/lib/types";

const SOURCE_URL = "https://www.whiteoutsurvival.wiki/buildings/";
const SOURCE_LABEL = "whiteoutsurvival.wiki/buildings";

/** Furnace level that unlocks a building, parsed from its level-1 prereq text. */
function unlockFurnaceLevel(b: BuildingDef): number | null {
  const text = b.requirements_by_level["1"]?.prerequisites ?? "";
  const m = /furnace[^0-9]*(\d+)/i.exec(text);
  return m ? Number(m[1]) : null;
}

function toFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const hub = view.buildings.find((b) => b.id === view.hub_id);
  const nodes: FlowTreeNode[] = [];

  if (hub) {
    nodes.push({
      id: hub.id,
      tier: 1,
      title: hub.name,
      subtitle: "Gates & caps every building",
      footer: hub.max_level ? `max Lv ${hub.max_level}` : undefined,
      requires: [],
    });
  }

  for (const b of view.buildings) {
    if (b.id === view.hub_id) continue;
    const lvl = unlockFurnaceLevel(b);
    // Bucket the unlock level into a column (1-5 → col 2, 6-10 → col 3, …).
    const tier = lvl ? 2 + Math.floor((lvl - 1) / 5) : 2;
    nodes.push({
      id: b.id,
      tier,
      title: b.name,
      subtitle: lvl ? `Unlocks at Furnace Lv ${lvl}` : undefined,
      footer: b.max_level ? `max Lv ${b.max_level}` : undefined,
      requires: hub ? [hub.id] : [],
    });
  }

  return nodes;
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
            {rows.map((b) => {
              const unlock = b.requirements_by_level["1"]?.prerequisites;
              return (
                <tr key={b.id}>
                  <td className="font-medium">{b.name}</td>
                  <td>{b.max_level ?? "—"}</td>
                  <td className="text-wos-text-muted">{unlock || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function BuildingsPage() {
  const { data, error, isLoading } = useQuery<BuildingsView>({
    queryKey: ["buildings"],
    queryFn: fetchBuildings,
  });

  const flowNodes = useMemo(() => (data ? toFlowNodes(data) : []), [data]);

  return (
    <>
      <PageHeader title="Buildings">
        <p className="muted m-0">
          Building level dependencies — served from{" "}
          <code>games/&lt;game&gt;/db/buildings/*.yaml</code> (single source of
          truth), sourced from{" "}
          <a className="underline" href={SOURCE_URL} target="_blank" rel="noreferrer">
            {SOURCE_LABEL}
          </a>
          . Furnace gates and caps every other building.
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner">
          {error instanceof Error ? error.message : String(error)}
        </div>
      ) : null}
      {isLoading ? <p className="muted">Loading…</p> : null}

      {data ? (
        <div className="flex flex-col gap-4">
          <TechTreeFlow nodes={flowNodes} />
          <BuildingCatalog buildings={data.buildings} />
        </div>
      ) : null}
    </>
  );
}
