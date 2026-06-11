"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { TechTreeFlow, type FlowTreeNode } from "@/components/TechTreeFlow";
import { fetchBuildings } from "@/lib/api";
import type { BuildingDef, BuildingsView } from "@/lib/types";

const SOURCE_URL = "https://www.whiteoutsurvival.wiki/buildings/";
const SOURCE_LABEL = "whiteoutsurvival.wiki/buildings";

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

function toFlowNodes(view: BuildingsView): FlowTreeNode[] {
  const hubId = view.hub_id;

  // Only graph buildings that participate in a dependency (have a prerequisite
  // or are one), plus the hub — keeps isolated entries out of the canvas.
  const connected = new Set<string>([hubId]);
  for (const b of view.buildings) {
    for (const r of b.requires) {
      connected.add(b.id);
      connected.add(r.building);
    }
  }

  return view.buildings
    .filter((b) => connected.has(b.id))
    .map((b) => {
      const furnaceReq = b.requires.find((r) => r.building === hubId);
      // Column by the Furnace level that gates the building (hub = column 1).
      const lvl = furnaceReq?.level ?? null;
      const tier =
        b.id === hubId ? 1 : lvl ? 2 + Math.floor((lvl - 1) / 4) : 2;
      return {
        id: b.id,
        tier,
        title: b.name,
        icon: buildingIcon(b.id),
        subtitle:
          b.id === hubId ? "Gates & caps every building" : undefined,
        footer: b.max_level ? `max Lv ${b.max_level}` : undefined,
        requires: b.requires.map((r) => ({
          id: r.building,
          label: `Lv ${r.level}`,
        })),
      };
    });
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
