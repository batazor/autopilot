"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState, type MouseEvent } from "react";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { useFleet } from "@/components/FleetContextProvider";
import {
  approvalsHref,
  instanceHref,
  playerStateHref,
  playerStatsHref,
  queueHref,
} from "@/lib/fleet-links";
import { DataTableSkeleton } from "@/components/skeleton/DataTableSkeleton";
import { MetricsRowSkeleton } from "@/components/skeleton/MetricsRowSkeleton";
import { StatusPill } from "@/components/StatusPill";
import { fetchOverview, toggleInstancePause } from "@/lib/api";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type { FleetInstanceRow, OverviewView } from "@/lib/types";

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

  useDashboardEventStream({
    topics: ["fleet", "queue"],
    enabled: true,
    onEvent: () => {
      void refresh();
    },
    onFallbackPoll: refresh,
  });

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
          <Metric label="Queue" value={String(m.queue)} href={queueHref()} />
          <Metric label="Busy" value={String(m.busy)} href={queueHref()} />
          <Metric label="Locks" value={String(m.locks)} />
        </div>
      ) : null}

      {m && m.paused > 0 ? (
        <p className="meta">{m.paused} instance(s) paused.</p>
      ) : null}

      <section className="panel">
        <h2>Fleet</h2>
        {data && !data.has_devices ? (
          <div className="ui-empty">
            <h3 className="ui-empty__title">No devices configured yet</h3>
            <p className="ui-empty__desc">
              Connect an Android emulator or physical device via ADB, then add it to the
              fleet. The bot has nothing to do until at least one device is configured.
            </p>
            <div className="ui-empty__action flex gap-2">
              <Link href="/adb" className="btn-primary">
                Add device
              </Link>
              <a
                href="https://github.com/batazor/autopilot#emulator-requirements"
                target="_blank"
                rel="noreferrer"
                className="btn-secondary"
              >
                Setup docs
              </a>
            </div>
          </div>
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
        ) : data?.has_devices ? (
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
        ) : null}
        {data?.fleet.length ? (
          <p className="meta mt-3">
            Click a row to open the instance. Pause/resume runs on that instance only.
          </p>
        ) : null}
      </section>
    </>
  );
}

function Metric({
  label,
  value,
  href,
}: {
  label: string;
  value: string;
  href?: string;
}) {
  const inner = (
    <>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </>
  );
  if (href) {
    return (
      <Link
        href={href}
        className="metric-card no-underline transition hover:border-wos-border hover:bg-wos-panel-raised/80"
      >
        {inner}
      </Link>
    );
  }
  return <div className="metric-card">{inner}</div>;
}

function GameIcon({ game }: { game: string }) {
  const slug = (game || "").toLowerCase();
  if (slug !== "wos" && slug !== "kingshot") return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/games/${slug}.webp`}
      alt={slug}
      width={16}
      height={16}
      className="inline-block h-4 w-4 shrink-0 rounded"
      title={slug}
    />
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
      <td>
        {row.active_player && row.active_player !== "—" ? (
          <Link
            href={playerStateHref(row.active_player, {
              instanceId: row.instance_id,
            })}
            onClick={(e) => e.stopPropagation()}
          >
            {row.active_player}
          </Link>
        ) : (
          "—"
        )}
      </td>
      <td>
        {row.node && row.node !== "—" ? (
          <Link
            href={`/routes?focus=${encodeURIComponent(row.node)}`}
            onClick={(e) => e.stopPropagation()}
          >
            {row.node}
          </Link>
        ) : (
          "—"
        )}
      </td>
      <td>
        {row.task && row.task !== "—" ? (
          <Link
            href={queueHref({ instanceId: row.instance_id })}
            onClick={(e) => e.stopPropagation()}
          >
            {row.task}
          </Link>
        ) : (
          "—"
        )}
      </td>
      <td>{row.uptime || "—"}</td>
      <td className="fleet-row__alert" title={row.alert || undefined}>
        {row.alert ? (
          <Link
            href={approvalsHref(row.instance_id)}
            onClick={(e) => e.stopPropagation()}
          >
            {row.alert}
          </Link>
        ) : (
          "—"
        )}
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
      <td>
        <span className="inline-flex items-center gap-1.5">
          <GameIcon game={p.game} />
          <Link
            href={playerStateHref(p.who, { instanceId: row.instance_id })}
            onClick={(e) => e.stopPropagation()}
          >
            {p.who}
          </Link>
        </span>
      </td>
      <td>{p.on_device ? "● on device" : ""}</td>
      <td colSpan={2}>
        <Link
          href={playerStateHref(p.who, { instanceId: row.instance_id })}
          onClick={(e) => e.stopPropagation()}
        >
          {p.nickname}
        </Link>
      </td>
      <td>
        {p.in_game_id && p.in_game_id !== "—" ? (
          <Link
            href={playerStatsHref(p.who, { instanceId: row.instance_id })}
            onClick={(e) => e.stopPropagation()}
          >
            {p.in_game_id}
          </Link>
        ) : (
          "—"
        )}
      </td>
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
