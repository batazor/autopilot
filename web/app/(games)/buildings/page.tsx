"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { fetchBuildings } from "@/lib/api";
import type { BuildingDef, BuildingsView } from "@/lib/types";

const SOURCE_URL = "https://www.whiteoutsurvival.wiki/buildings/";
const SOURCE_LABEL = "whiteoutsurvival.wiki/buildings";

/** Hub (Furnace) upgrade dependency spine — one row per level with a prereq. */
function HubSpine({ hub }: { hub: BuildingDef }) {
  const rows = useMemo(
    () =>
      Object.entries(hub.requirements_by_level)
        .map(([level, req]) => ({ level: Number(level), prereq: req.prerequisites }))
        .filter((r) => r.prereq)
        .sort((a, b) => a.level - b.level),
    [hub],
  );

  return (
    <section className="panel">
      <div className="fleet-section__head">
        <h2>{hub.name} upgrade requirements</h2>
        {hub.max_level ? (
          <span className="fleet-count">max Lv {hub.max_level}</span>
        ) : null}
      </div>
      <p className="muted mb-3 text-sm">
        {hub.name} level gates and caps every other building. To advance it, the
        listed building(s) must first reach the shown level.
      </p>

      <ol className="relative ml-3 border-l-2 border-[color:var(--wos-border-hover)] pl-5">
        {rows.map((r) => (
          <li key={r.level} className="relative mb-3 last:mb-0">
            <span
              className="absolute -left-[1.65rem] top-1 h-3 w-3 rounded-full border-2"
              style={{ background: "var(--wos-accent)", borderColor: "var(--wos-bg)" }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <span
                className="rounded px-2 py-0.5 text-sm font-semibold"
                style={{
                  background: "var(--wos-status-info-bg)",
                  color: "var(--wos-status-info-fg)",
                }}
              >
                {hub.name} Lv {r.level}
              </span>
              <span className="text-xs text-wos-text-muted">needs</span>
              <span className="text-sm">{r.prereq}</span>
            </div>
          </li>
        ))}
      </ol>
    </section>
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

  const hub = data?.buildings.find((b) => b.id === data.hub_id);

  return (
    <>
      <PageHeader title="Buildings">
        <p className="muted m-0">
          Building level dependencies — served from{" "}
          <code>games/&lt;game&gt;/db/buildings/*.yaml</code> (single source of
          truth), sourced from{" "}
          <a
            className="underline"
            href={SOURCE_URL}
            target="_blank"
            rel="noreferrer"
          >
            {SOURCE_LABEL}
          </a>
          .
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
          {hub ? <HubSpine hub={hub} /> : null}
          <BuildingCatalog buildings={data.buildings} />
        </div>
      ) : null}
    </>
  );
}
