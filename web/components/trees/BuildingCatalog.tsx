import { useMemo } from "react";
import type { BuildingDef } from "@/lib/types";

export function BuildingCatalog({ buildings }: { buildings: BuildingDef[] }) {
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
