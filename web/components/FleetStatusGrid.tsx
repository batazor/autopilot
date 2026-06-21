"use client";

import type { KeyboardEvent } from "react";
import { StatusPill } from "@/components/ui/StatusPill";
import type { FleetInstanceRow } from "@/lib/types";

/** Status → accent color (CSS var) for the rail + dot. */
function accentVar(row: FleetInstanceRow): string {
  const s = row.status.toLowerCase();
  if (s === "crashed" || s === "restarting") return "var(--wos-status-danger-fg)";
  if (s === "offline" || s === "stale") return "var(--wos-border)";
  if (row.alert.trim() || row.paused || s === "paused")
    return "var(--wos-status-warn-fg)";
  return "var(--wos-status-ok-fg)";
}

function isLive(row: FleetInstanceRow): boolean {
  const s = row.status.toLowerCase();
  return (
    !row.paused &&
    !row.alert.trim() &&
    !["offline", "stale", "crashed", "restarting", "paused"].includes(s)
  );
}

function Field({ label, value }: { label: string; value: string }) {
  const clean = value && value !== "—" ? value : null;
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-semibold uppercase tracking-wide text-wos-text-muted">
        {label}
      </dt>
      <dd className="m-0 truncate text-wos-text">
        {clean ?? <span className="text-wos-text-muted">—</span>}
      </dd>
    </div>
  );
}

/**
 * One card per bot instance — status accent rail, a live-pulsing dot, the
 * key telemetry (player / task / screen / uptime), and players + paused chips.
 * Click or Enter opens the instance. Reads at a glance, scales down to mobile.
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
    <div
      className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(16rem,1fr))]"
      role="list"
      aria-label="Fleet status"
    >
      {fleet.map((row) => {
        const accent = accentVar(row);
        const live = isLive(row);
        const playerCount = row.players?.length ?? 0;
        const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen(row.instance_id);
          }
        };
        return (
          <div
            key={row.instance_id}
            role="listitem"
            tabIndex={0}
            onClick={() => onOpen(row.instance_id)}
            onKeyDown={onKey}
            title={row.alert || undefined}
            aria-label={`Open instance ${row.instance_id}, status ${row.status}`}
            style={{ borderLeftWidth: "3px", borderLeftColor: accent }}
            className="group relative flex cursor-pointer flex-col gap-3 rounded-xl border border-wos-border-subtle bg-wos-panel-raised p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-wos-border-hover hover:shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-wos-border-hover motion-reduce:transition-none motion-reduce:hover:translate-y-0"
          >
            <div className="flex items-center gap-2">
              <span
                className="relative inline-flex h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: accent }}
                aria-hidden
              >
                {live && (
                  <span
                    className="absolute inset-0 rounded-full opacity-60 motion-safe:animate-ping"
                    style={{ backgroundColor: accent }}
                  />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate font-semibold text-wos-text">
                {row.instance_id}
              </span>
              <StatusPill status={row.status} />
            </div>

            <dl className="m-0 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              <Field label="Player" value={row.active_player} />
              <Field label="Task" value={row.task} />
              <Field label="Screen" value={row.node} />
              <Field label="Uptime" value={row.uptime} />
            </dl>

            {(playerCount > 0 || row.paused) && (
              <div className="flex flex-wrap items-center gap-1.5">
                {playerCount > 0 && (
                  <span className="inline-flex items-center rounded-full border border-wos-border-subtle bg-wos-panel px-2 py-0.5 text-[11px] font-medium tabular-nums text-wos-text-secondary">
                    {playerCount} player{playerCount === 1 ? "" : "s"}
                  </span>
                )}
                {row.paused && (
                  <span
                    className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold"
                    style={{
                      color: "var(--wos-status-warn-fg)",
                      background: "var(--wos-status-warn-bg)",
                    }}
                  >
                    Paused
                  </span>
                )}
              </div>
            )}

            {row.alert.trim() ? (
              <div
                className="rounded-md px-2 py-1.5 text-xs font-medium"
                style={{
                  color: "var(--wos-status-warn-fg)",
                  background: "var(--wos-status-warn-bg)",
                }}
              >
                {row.alert}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
