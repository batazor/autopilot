"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Card } from "@/components/ui";
import type { AllianceMemberRow } from "@/lib/types";
import { fmtPower, lastActiveLabel } from "./format";

type SortKey = "name" | "rank" | "power" | "level" | "last";

function lastSortValue(m: AllianceMemberRow): number {
  // online → most-recent (0); unknown last-seen sinks to the bottom.
  if (m.online) return 0;
  return m.last_online_seconds ?? Number.POSITIVE_INFINITY;
}

export function MembersTable({
  members,
  inactiveDays,
}: {
  members: AllianceMemberRow[];
  inactiveDays: number;
}) {
  const [sort, setSort] = useState<SortKey>("power");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const threshold = inactiveDays * 86_400;

  const sorted = useMemo(() => {
    const arr = [...members];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sort) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "rank":
          cmp = a.rank - b.rank;
          break;
        case "power":
          cmp = a.power - b.power;
          break;
        case "level":
          cmp = a.level - b.level;
          break;
        case "last":
          cmp = lastSortValue(a) - lastSortValue(b);
          break;
      }
      return dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [members, sort, dir]);

  function toggle(key: SortKey) {
    if (sort === key) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(key);
      setDir(key === "name" ? "asc" : "desc");
    }
  }

  function isInactive(m: AllianceMemberRow): boolean {
    return !m.online && m.last_online_seconds != null && m.last_online_seconds >= threshold;
  }

  const arrow = (key: SortKey) => (sort === key ? (dir === "asc" ? " ▲" : " ▼") : "");

  return (
    <Card title={`Members (${members.length})`}>
      <div className="overflow-x-auto rounded-lg border border-wos-border-subtle">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-wos-panel-raised text-left text-xs uppercase tracking-wide text-wos-text-muted">
              <Th onClick={() => toggle("name")}>Name{arrow("name")}</Th>
              <Th onClick={() => toggle("rank")}>Rank{arrow("rank")}</Th>
              <Th onClick={() => toggle("power")} className="text-right">
                Power{arrow("power")}
              </Th>
              <Th onClick={() => toggle("level")} className="text-right">
                Lv{arrow("level")}
              </Th>
              <Th onClick={() => toggle("last")}>Last active{arrow("last")}</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <tr
                key={m.member_key ?? m.name}
                className={[
                  "border-t border-wos-border-subtle",
                  isInactive(m) ? "bg-amber-500/[0.07]" : "",
                ].join(" ")}
              >
                <td className="px-3 py-2 font-medium">
                  <span className="inline-flex items-center gap-2">
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{
                        background: m.online
                          ? "var(--wos-status-ok-fg)"
                          : "var(--wos-border)",
                      }}
                      aria-hidden
                    />
                    {m.name || "—"}
                  </span>
                </td>
                <td className="px-3 py-2">R{m.rank}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPower(m.power)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{m.level || "—"}</td>
                <td className="px-3 py-2 text-wos-text-muted">{lastActiveLabel(m)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Th({
  children,
  onClick,
  className,
}: {
  children: ReactNode;
  onClick: () => void;
  className?: string;
}) {
  return (
    <th
      className={[
        "cursor-pointer select-none px-3 py-2 font-medium hover:text-wos-text",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onClick}
    >
      {children}
    </th>
  );
}
