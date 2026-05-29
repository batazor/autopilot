"use client";

import type { KeyboardEvent } from "react";
import { StatusPill } from "@/components/StatusPill";
import type { FleetInstanceRow } from "@/lib/types";

function tone(row: FleetInstanceRow): string {
  const status = row.status.toLowerCase();
  if (status === "crashed" || status === "restarting") return "tile--danger";
  if (status === "offline" || status === "stale") return "tile--offline";
  if (row.alert.trim()) return "tile--alert";
  if (row.paused || status === "paused") return "tile--paused";
  return "tile--live";
}

/**
 * Compact one-tile-per-instance health grid. Reads at a glance on any screen
 * width and doubles as the mobile-friendly view of the fleet table below it.
 */
export function FleetStatusGrid({
  fleet,
  onOpen,
}: {
  fleet: FleetInstanceRow[];
  onOpen: (instanceId: string) => void;
}) {
  if (fleet.length === 0) return null;

  return (
    <div className="fleet-grid" role="list" aria-label="Fleet status">
      {fleet.map((row) => {
        const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen(row.instance_id);
          }
        };
        return (
          <div
            key={row.instance_id}
            className={`fleet-tile ${tone(row)}`}
            role="listitem"
            tabIndex={0}
            onClick={() => onOpen(row.instance_id)}
            onKeyDown={onKey}
            aria-label={`Open instance ${row.instance_id}, status ${row.status}`}
            title={row.alert || undefined}
          >
            <div className="fleet-tile__head">
              <span className="fleet-tile__name">{row.instance_id}</span>
              <StatusPill status={row.status} />
            </div>
            <div className="fleet-tile__meta">
              {row.active_player && row.active_player !== "—"
                ? row.active_player
                : "no active player"}
            </div>
            <div className="fleet-tile__meta">
              {row.task && row.task !== "—" ? row.task : "idle"}
            </div>
            {row.alert.trim() ? (
              <div className="fleet-tile__alert">{row.alert}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
