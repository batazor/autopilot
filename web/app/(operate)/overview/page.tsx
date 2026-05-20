"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState, type MouseEvent } from "react";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { useFleet } from "@/components/FleetContextProvider";
import { instanceHref, playerStateHref } from "@/lib/fleet-links";
import { DataTableSkeleton } from "@/components/skeleton/DataTableSkeleton";
import { MetricsRowSkeleton } from "@/components/skeleton/MetricsRowSkeleton";
import { StatusPill } from "@/components/StatusPill";
import { fetchOverview, toggleInstancePause } from "@/lib/api";
import { usePollWhenVisible } from "@/lib/hooks";
import type { FleetInstanceRow, OverviewView } from "@/lib/types";

const POLL_MS = 2000;

export default function OverviewPage() {
  const router = useRouter();
  const { setInstanceId } = useFleet();
  const { showSuccess } = useFeedback();
  const [data, setData] = useState<OverviewView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pauseBusyId, setPauseBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const view = await fetchOverview();
      setData(view);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  usePollWhenVisible(refresh, POLL_MS);

  const openInstance = (instanceId: string) => {
    setInstanceId(instanceId);
    router.push(instanceHref(instanceId));
  };

  const onTogglePause = async (instanceId: string, e: MouseEvent) => {
    e.stopPropagation();
    if (pauseBusyId) return;
    setPauseBusyId(instanceId);
    try {
      const row = data?.fleet.find((r) => r.instance_id === instanceId);
      const willResume = row?.paused ?? false;
      await toggleInstancePause(instanceId);
      await refresh();
      showSuccess(willResume ? `${instanceId} resumed` : `${instanceId} paused`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPauseBusyId(null);
    }
  };

  const m = data?.metrics;

  return (
    <>
      <FleetPageHeader title="Overview" />
      <ErrorBanner message={error} />

      {loading && !m ? <MetricsRowSkeleton count={5} /> : null}
      {m ? (
        <div className="metrics-row">
          <Metric label="Instances" value={String(m.instances)} />
          <Metric label="Live workers" value={`${m.live_workers}/${m.instances}`} />
          <Metric label="Queue" value={String(m.queue)} />
          <Metric label="Busy" value={String(m.busy)} />
          <Metric label="Locks" value={String(m.locks)} />
        </div>
      ) : null}

      {m && m.paused > 0 ? (
        <p className="meta">{m.paused} instance(s) paused.</p>
      ) : null}

      <section className="panel">
        <h2>Fleet</h2>
        {!data?.has_devices_yaml ? (
          <p className="meta">No entries in db/devices.yaml — configure ADB first.</p>
        ) : null}
        {loading && !data ? (
          <DataTableSkeleton
            columns={[
              "Instance",
              "Status",
              "Active",
              "Node",
              "Task",
              "Uptime",
              "Alert",
              "",
            ]}
            rows={4}
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Instance</th>
                  <th>Status</th>
                  <th>Active</th>
                  <th>Node</th>
                  <th>Task</th>
                  <th>Uptime</th>
                  <th>Alert</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data?.fleet.flatMap((row) =>
                  fleetRows(row, {
                    pauseBusyId,
                    onOpen: openInstance,
                    onTogglePause,
                  }),
                )}
              </tbody>
            </table>
          </div>
        )}
        {data?.fleet.length ? (
          <p className="meta mt-3">
            Click a row to open the instance. Pause/resume runs on that instance only.
          </p>
        ) : null}
      </section>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

function fleetRowTone(row: FleetInstanceRow): string {
  const status = row.status.toLowerCase();
  if (status === "offline" || status === "stale") return "fleet-row--offline";
  if (status === "crashed" || status === "restarting") return "fleet-row--danger";
  if (row.alert.trim()) return "fleet-row--alert";
  if (row.paused || status === "paused") return "fleet-row--paused";
  return "";
}

function fleetRows(
  row: FleetInstanceRow,
  handlers: {
    pauseBusyId: string | null;
    onOpen: (instanceId: string) => void;
    onTogglePause: (instanceId: string, e: MouseEvent) => void;
  },
) {
  const tone = fleetRowTone(row);
  const rowBusy = handlers.pauseBusyId === row.instance_id;
  const anyBusy = handlers.pauseBusyId != null;

  const parent = (
    <tr
      key={row.instance_id}
      className={["fleet-row", tone].filter(Boolean).join(" ")}
      onClick={() => handlers.onOpen(row.instance_id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handlers.onOpen(row.instance_id);
        }
      }}
      tabIndex={0}
      role="link"
      aria-label={`Open instance ${row.instance_id}`}
    >
      <td>
        <strong>{row.instance_id}</strong>
      </td>
      <td>
        <StatusPill status={row.status} />
      </td>
      <td>{row.active_player || "—"}</td>
      <td>{row.node || "—"}</td>
      <td>{row.task || "—"}</td>
      <td>{row.uptime || "—"}</td>
      <td className="fleet-row__alert" title={row.alert || undefined}>
        {row.alert || "—"}
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <div className="fleet-row-actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={anyBusy}
            aria-busy={rowBusy}
            onClick={(e) => handlers.onTogglePause(row.instance_id, e)}
          >
            {rowBusy ? "…" : row.paused ? "Resume" : "Pause"}
          </button>
        </div>
      </td>
    </tr>
  );

  const subs = row.players.map((p) => (
    <tr key={`${row.instance_id}-${p.id}`} className="sub-row">
      <td>{p.who}</td>
      <td>{p.on_device ? "● on device" : ""}</td>
      <td colSpan={2}>{p.nickname}</td>
      <td>{p.in_game_id}</td>
      <td colSpan={2}>
        stove {p.stove} · kid {p.kid}
      </td>
      <td>
        <Link
          href={playerStateHref(p.who, { instanceId: row.instance_id })}
          onClick={(e) => e.stopPropagation()}
        >
          State
        </Link>
      </td>
    </tr>
  ));

  return [parent, ...subs];
}
