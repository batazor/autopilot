"use client";

import Link from "next/link";
import { useMemo } from "react";
import { wikiBuildingHref } from "@/lib/wiki-links";
import type { BuildingLevelRow } from "@/lib/types";

type Props = {
  rows: BuildingLevelRow[];
  filter: string;
};

export function BuildingLevelsTable({ rows, filter }: Props) {
  const q = filter.trim().toLowerCase();
  const visible = useMemo(() => {
    if (!q) return rows;
    return rows.filter((r) =>
      [r.id, r.building, r.category, String(r.level)]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [rows, q]);

  const numeric = visible
    .map((r) => (typeof r.level === "number" ? r.level : Number(r.level)))
    .filter((n) => !Number.isNaN(n));

  if (!visible.length) {
    return <p className="meta">No buildings matched the filter.</p>;
  }

  return (
    <>
      <div className="toolbar" style={{ gap: "1rem" }}>
        <span className="meta">Buildings tracked: {rows.length}</span>
        <span className="meta">
          Highest level: {numeric.length ? Math.max(...numeric) : "—"}
        </span>
      </div>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Building</th>
              <th>Category</th>
              <th style={{ textAlign: "right" }}>Level</th>
              <th style={{ width: "4rem", textAlign: "center" }}>Wiki</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id}>
                <td>
                  <code>{r.id}</code>
                </td>
                <td>{r.building}</td>
                <td className="meta">{r.category}</td>
                <td style={{ textAlign: "right" }}>{String(r.level)}</td>
                <td style={{ textAlign: "center" }}>
                  <Link
                    href={wikiBuildingHref(r.id)}
                    className="player-state-wiki-link"
                    title={`Wiki: ${r.building}`}
                  >
                    →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
