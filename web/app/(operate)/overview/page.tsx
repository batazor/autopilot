"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useOptimistic,
  useTransition,
  type MouseEvent,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { AttentionPanel } from "@/components/attention/AttentionPanel";
import { ATTENTION_KEY } from "@/components/attention/useAttention";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { FleetStatusGrid } from "@/components/FleetStatusGrid";
import { LiveIndicator } from "@/components/LiveIndicator";
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
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { DailyTasksButton } from "@/components/quests/DailyTasksButton";
import { MetricCard, MetricGrid } from "@/components/ui";
import { fetchOverview, toggleInstancePause } from "@/lib/api";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type { FleetInstanceRow } from "@/lib/types";

const OVERVIEW_KEY = ["overview"] as const;

export default function OverviewPage() {
  const router = useRouter();
  const { setInstanceId } = useFleet();
  const { showSuccess } = useFeedback();
  const queryClient = useQueryClient();

  const overview = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: fetchOverview,
  });
  const data = overview.data;

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
    void queryClient.invalidateQueries({ queryKey: ATTENTION_KEY });
  }, [queryClient]);

  const streamStatus = useDashboardEventStream({
    topics: ["fleet", "queue"],
    enabled: true,
    onEvent: invalidate,
    onFallbackPoll: invalidate,
  });

  const pauseMutation = useMutation({
    mutationFn: toggleInstancePause,
    onSuccess: async (_res, instanceId) => {
      const willResume =
        data?.fleet.find((r) => r.instance_id === instanceId)?.paused ?? false;
      await queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
      showSuccess(willResume ? `${instanceId} resumed` : `${instanceId} paused`);
    },
  });

  // Optimistic fleet: flip the toggled instance's paused flag on the current
  // frame so the row reflects the click before the server confirms. Reverts to
  // the real value once the query refetch lands (or on error).
  const [optimisticFleet, flipPaused] = useOptimistic(
    data?.fleet ?? [],
    (fleet: FleetInstanceRow[], instanceId: string) =>
      fleet.map((r) =>
        r.instance_id === instanceId ? { ...r, paused: !r.paused } : r,
      ),
  );
  const [, startPauseTransition] = useTransition();
  const pauseBusyId = pauseMutation.isPending
    ? (pauseMutation.variables ?? null)
    : null;

  const openInstance = (instanceId: string) => {
    setInstanceId(instanceId);
    router.push(instanceHref(instanceId));
  };

  const onTogglePause = (instanceId: string, e: MouseEvent) => {
    e.stopPropagation();
    if (pauseMutation.isPending) return;
    startPauseTransition(async () => {
      flipPaused(instanceId);
      try {
        await pauseMutation.mutateAsync(instanceId);
      } catch {
        // Surfaced via pauseMutation.error → ErrorBanner; optimistic state reverts.
      }
    });
  };

  const errorMessage = overview.isError
    ? overview.error instanceof Error
      ? overview.error.message
      : String(overview.error)
    : pauseMutation.isError
      ? pauseMutation.error instanceof Error
        ? pauseMutation.error.message
        : String(pauseMutation.error)
      : null;

  const loading = overview.isLoading;
  const m = data?.metrics;

  return (
    <>
      <PageHeader title="Overview" fleet />
      <ErrorBanner
        message={errorMessage}
        onRetry={() => void overview.refetch()}
        retrying={overview.isFetching}
      />

      <AttentionPanel />

      {loading && !m ? <MetricsRowSkeleton count={5} /> : null}
      {m ? (
        <MetricGrid>
          <MetricCard label="Instances" value={String(m.instances)} />
          <MetricCard
            label="Live workers"
            value={`${m.live_workers}/${m.instances}`}
            tone={
              m.instances === 0
                ? "neutral"
                : m.live_workers === 0
                  ? "danger"
                  : m.live_workers < m.instances
                    ? "warn"
                    : "ok"
            }
            hint={
              m.instances > 0 && m.live_workers < m.instances
                ? `${m.instances - m.live_workers} down`
                : undefined
            }
          />
          <MetricCard
            label="Queue"
            value={String(m.queue)}
            href={queueHref()}
            tone={m.queue > 0 ? "accent" : "neutral"}
          />
          <MetricCard
            label="Busy"
            value={String(m.busy)}
            href={queueHref()}
            tone={m.busy > 0 ? "accent" : "neutral"}
          />
          <MetricCard
            label="Locks"
            value={String(m.locks)}
            tone={m.locks > 0 ? "warn" : "neutral"}
          />
        </MetricGrid>
      ) : null}

      {data?.has_devices && optimisticFleet.length ? (
        <FleetStatusGrid fleet={optimisticFleet} onOpen={openInstance} />
      ) : null}

      <section className="panel panel--spaced">
        <div className="fleet-section__head">
          <h2>Fleet</h2>
          {data?.has_devices && optimisticFleet.length ? (
            <span className="fleet-count">{optimisticFleet.length}</span>
          ) : null}
          <LiveIndicator status={streamStatus} />
        </div>
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
                {optimisticFleet.flatMap((row) =>
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
          <Button
            pending={rowBusy}
            disabled={anyBusy && !rowBusy}
            onClick={(e) => handlers.onTogglePause(row.instance_id, e)}
          >
            {row.paused ? "Resume" : "Pause"}
          </Button>
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
        <span className="inline-flex items-center gap-2">
          <Link
            href={playerStateHref(p.who, { instanceId: row.instance_id })}
            onClick={(e) => e.stopPropagation()}
          >
            State
          </Link>
          <DailyTasksButton playerId={p.who} nickname={p.nickname} />
        </span>
      </td>
    </tr>
  ));

  return [parent, ...subs];
}
