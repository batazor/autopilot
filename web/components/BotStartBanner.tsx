"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  fetchClickApprovalStatus,
  fetchAdbStatus,
  fetchBotStatus,
  fetchInstances,
  fetchOverview,
  startLocalBot,
  stopLocalBot,
  toggleInstancePause,
} from "@/lib/api";
import {
  loadFleetInstanceId,
  saveFleetInstanceId,
  subscribeFleetInstanceId,
} from "@/lib/fleet-prefs";
import {
  adbReadinessTitle,
  evaluateAdbReadiness,
  type AdbReadiness,
} from "@/lib/adb-device-ready";
import { adbSerialMatches } from "@/lib/adb-serial";
import type { AdbStatus } from "@/lib/config-pages";
import { approvalsHref, instanceHref } from "@/lib/fleet-links";
import type {
  BotStatusView,
  ClickApprovalStatus,
  FleetInstanceRow,
  OverviewView,
} from "@/lib/types";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import { Icon } from "@/components/ui/Icon";

const BOT_POLL_MS = 4000;
const BOT_STATUS_QUERY_KEY = ["botStartBanner"] as const;
const BOT_FLEET_QUERY_KEY = ["botStartBannerFleet"] as const;
const FLEET_INSTANCES_QUERY_KEY = ["fleetInstances"] as const;

function approvalStatusQueryKey(instanceId: string) {
  return ["botStartBannerApproval", instanceId] as const;
}

type BannerStatus = {
  bot: BotStatusView;
  adb: AdbStatus;
};

async function fetchBannerStatus(): Promise<BannerStatus> {
  const [bot, adb] = await Promise.all([fetchBotStatus(), fetchAdbStatus()]);
  return { bot, adb };
}

function formatProcessAge(startedAt: number | null): string {
  if (!startedAt) return "—";
  const ageSec = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  if (ageSec < 60) return `${ageSec}s`;
  const m = Math.floor(ageSec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60 ? `${m % 60}m` : ""}`;
}

function formatMode(mode: BotStatusView["mode"]): string {
  if (!mode) return "unknown";
  return mode === "embedded" ? "embedded" : "supervisor";
}

function deviceChipLabel(adb: AdbStatus | null): string {
  if (!adb) return "ADB checking";
  const configured = adb.configured.length;
  const live = adb.live_devices.length;
  if (adb.scan_error?.trim()) return "ADB scan error";
  if (configured === 0 && live === 0) return "No devices";
  if (configured === 0) return `${live} live`;
  return `${live}/${configured} live`;
}

// Switch the dashboard's active device from Bot control. The banner lives in
// the sidebar, *outside* FleetContextProvider, so it can't read that context —
// instead it shares the same source of truth the provider uses: the persisted
// ``wos.fleet.instanceId`` (localStorage) plus the ``?instance_id=`` URL param.
// Writing both keeps page selectors and this carousel in lockstep, and the
// same-tab event from ``saveFleetInstanceId`` lets the banner reflect changes
// made from a page dropdown.
function useDeviceSwitcher() {
  const router = useRouter();
  const pathname = usePathname();
  const instancesQuery = useQuery<string[]>({
    queryKey: FLEET_INSTANCES_QUERY_KEY,
    queryFn: fetchInstances,
    refetchInterval: BOT_POLL_MS,
  });
  const instances = instancesQuery.data ?? [];
  const [selected, setSelected] = useState("");

  useEffect(() => {
    setSelected(loadFleetInstanceId());
    return subscribeFleetInstanceId(setSelected);
  }, []);

  // Fall back to the first device until a valid selection is persisted, so the
  // carousel always shows *something* coherent with the list.
  const current =
    selected && instances.includes(selected) ? selected : (instances[0] ?? "");
  const index = current ? instances.indexOf(current) : -1;

  const select = useCallback(
    (id: string) => {
      if (!id) return;
      saveFleetInstanceId(id); // persists + fires the same-tab sync event
      setSelected(id);
      // Push ``?instance_id=`` so a mounted FleetContextProvider (operate /
      // debug pages) reacts live. Read the existing query off the URL rather
      // than useSearchParams to avoid forcing a Suspense boundary in the
      // always-mounted sidebar.
      const params = new URLSearchParams(
        typeof window !== "undefined" ? window.location.search : "",
      );
      params.set("instance_id", id);
      const q = params.toString();
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  const step = useCallback(
    (delta: number) => {
      if (instances.length < 2 || index < 0) return;
      const next = (index + delta + instances.length) % instances.length;
      select(instances[next]);
    },
    [instances, index, select],
  );

  return { instances, current, index, step };
}

type DeviceSwitcherState = ReturnType<typeof useDeviceSwitcher>;

function DeviceCarousel({ switcher }: { switcher: DeviceSwitcherState }) {
  const { instances, current, index, step } = switcher;
  if (instances.length === 0) return null;
  const multi = instances.length > 1;
  return (
    <div
      className="nav-bot-banner__devnav"
      role="group"
      aria-label="Active device"
    >
      <button
        type="button"
        className="nav-bot-banner__action"
        onClick={() => step(-1)}
        disabled={!multi}
        aria-label="Previous device"
        title="Previous device"
      >
        <Icon name="chevron-left" size="sm" />
      </button>
      <span className="nav-bot-banner__devnav-label" title={current}>
        <span className="nav-bot-banner__devnav-name">{current || "—"}</span>
        {multi ? (
          <span className="nav-bot-banner__badge">
            {index + 1}/{instances.length}
          </span>
        ) : null}
      </span>
      <button
        type="button"
        className="nav-bot-banner__action"
        onClick={() => step(1)}
        disabled={!multi}
        aria-label="Next device"
        title="Next device"
      >
        <Icon name="chevron-right" size="sm" />
      </button>
    </div>
  );
}

function shortStatus(status: string): string {
  const s = (status || "").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unknown";
}

function rowStatusChipClass(row: FleetInstanceRow | null): string {
  if (!row) return "nav-bot-banner__chip--device";
  const status = row.status.toLowerCase();
  if (row.paused || status === "paused") return "nav-bot-banner__chip--warn";
  if (status === "live") return "nav-bot-banner__chip--ok";
  if (status === "crashed" || status === "offline") {
    return "nav-bot-banner__chip--danger";
  }
  return "nav-bot-banner__chip--device";
}

function ApprovalStatusChip({
  instanceId,
  status,
}: {
  instanceId: string;
  status: ClickApprovalStatus | null;
}) {
  if (!instanceId) return null;
  if (!status) {
    return (
      <Link
        href={approvalsHref(instanceId)}
        className="nav-bot-banner__chip nav-bot-banner__chip--device"
        title="Checking approval status"
      >
        Approvals…
      </Link>
    );
  }
  const pending = !!status?.has_pending;
  const enabled = status.approval_enabled;
  const label = pending
    ? "Approval pending"
    : enabled
      ? "Approvals on"
      : "Approvals off";
  const chipClass = [
    "nav-bot-banner__chip",
    pending
      ? "nav-bot-banner__chip--pending"
      : enabled
        ? "nav-bot-banner__chip--ok"
        : "nav-bot-banner__chip--warn",
  ].join(" ");
  const title = pending
    ? [
        status?.scenario_label || status?.scenario_key || "Pending approval",
        status?.region_label ? `region ${status.region_label}` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : enabled
      ? "Approval mode is enabled for this instance"
      : "Approval mode is disabled for this instance";
  return (
    <Link href={approvalsHref(instanceId)} className={chipClass} title={title}>
      {label}
    </Link>
  );
}

function InstanceStatusChip({
  instanceId,
  row,
}: {
  instanceId: string;
  row: FleetInstanceRow | null;
}) {
  if (!instanceId) return null;
  const status = row ? shortStatus(row.status) : "Status";
  return (
    <Link
      href={instanceHref(instanceId)}
      className={[
        "nav-bot-banner__chip",
        rowStatusChipClass(row),
      ].join(" ")}
      title={row?.alert || `Open ${instanceId}`}
    >
      {instanceId}: {status}
    </Link>
  );
}

function InstanceStatusLine({
  row,
  approval,
}: {
  row: FleetInstanceRow | null;
  approval: ClickApprovalStatus | null;
}) {
  if (!row && !approval) return null;
  const node = row?.node && row.node !== "—" ? row.node : approval?.current_screen;
  const player =
    row?.active_player && row.active_player !== "—"
      ? row.active_player
      : approval?.active_player_in_game_id || approval?.active_player || "";
  const task = row?.task && row.task !== "—" ? row.task : "";
  const parts = [
    node ? `Node ${node}` : "",
    player ? `Player ${player}` : "",
    task ? `Task ${task}` : "",
  ].filter(Boolean);
  if (!parts.length) return null;
  return <p className="nav-bot-banner__desc">{parts.join(" · ")}</p>;
}

export function BotStartBanner() {
  const qc = useQueryClient();
  const deviceSwitcher = useDeviceSwitcher();
  const currentInstance = deviceSwitcher.current;
  const [localError, setLocalError] = useState<string | null>(null);
  // Which supervisor process the operator is currently looking at when
  // more than one is alive (dev rotation, stuck terminate, etc.).
  const [carouselIdx, setCarouselIdx] = useState(0);

  const query = useQuery<BannerStatus>({
    queryKey: BOT_STATUS_QUERY_KEY,
    queryFn: fetchBannerStatus,
    refetchInterval: BOT_POLL_MS,
  });

  const fleetQuery = useQuery<OverviewView>({
    queryKey: BOT_FLEET_QUERY_KEY,
    queryFn: fetchOverview,
    refetchInterval: BOT_POLL_MS,
  });

  const approvalQuery = useQuery<ClickApprovalStatus>({
    queryKey: approvalStatusQueryKey(currentInstance),
    queryFn: () => fetchClickApprovalStatus(currentInstance),
    enabled: Boolean(currentInstance),
  });

  const startMutation = useMutation({
    mutationFn: startLocalBot,
    onSuccess: (view) => {
      qc.setQueryData<BannerStatus>(BOT_STATUS_QUERY_KEY, (prev) =>
        prev ? { ...prev, bot: view } : prev,
      );
      void qc.invalidateQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      setLocalError(null);
    },
    onError: (e) => {
      setLocalError(e instanceof Error ? e.message : "Failed to start bot");
    },
  });
  const stopMutation = useMutation({
    mutationFn: stopLocalBot,
    onSuccess: (view) => {
      qc.setQueryData<BannerStatus>(BOT_STATUS_QUERY_KEY, (prev) =>
        prev ? { ...prev, bot: view } : prev,
      );
      void qc.invalidateQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      if (currentInstance) {
        void qc.invalidateQueries({
          queryKey: approvalStatusQueryKey(currentInstance),
        });
      }
      setLocalError(null);
    },
    onError: (e) => {
      setLocalError(e instanceof Error ? e.message : "Failed to stop bot");
    },
  });

  const pauseMutation = useMutation({
    mutationFn: toggleInstancePause,
    onMutate: async (instanceId) => {
      setLocalError(null);
      await qc.cancelQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      const previous = qc.getQueryData<OverviewView>(BOT_FLEET_QUERY_KEY);
      qc.setQueryData<OverviewView>(BOT_FLEET_QUERY_KEY, (prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          fleet: prev.fleet.map((row) => {
            if (row.instance_id !== instanceId) return row;
            const paused = !row.paused;
            return {
              ...row,
              paused,
              status: paused
                ? "paused"
                : row.status.toLowerCase() === "paused"
                  ? "live"
                  : row.status,
            };
          }),
        };
      });
      return { previous };
    },
    onError: (e, _instanceId, ctx) => {
      if (ctx?.previous) qc.setQueryData(BOT_FLEET_QUERY_KEY, ctx.previous);
      setLocalError(e instanceof Error ? e.message : "Failed to toggle pause");
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      if (currentInstance) {
        void qc.invalidateQueries({
          queryKey: approvalStatusQueryKey(currentInstance),
        });
      }
    },
  });

  const botStatus = query.data?.bot ?? null;
  const adbStatus = query.data?.adb ?? null;
  const refreshing = query.isFetching;
  const loaded = query.isFetched;
  const processes = botStatus?.processes ?? [];
  const safeIdx = processes.length > 0
    ? ((carouselIdx % processes.length) + processes.length) % processes.length
    : 0;
  const currentProc = processes[safeIdx] ?? null;
  const currentPid = currentProc?.pid ?? botStatus?.pid ?? null;
  const currentRow =
    fleetQuery.data?.fleet.find((r) => r.instance_id === currentInstance) ?? null;
  const approvalStatus = approvalQuery.data ?? null;

  const bannerTopics = currentInstance
    ? ["fleet", "queue", "instance", "approval"]
    : ["fleet", "queue"];
  useDashboardEventStream({
    topics: bannerTopics,
    instanceId: currentInstance || undefined,
    enabled: true,
    onEvent: (topic) => {
      if (topic === "fleet" || topic === "queue" || topic === "instance") {
        void qc.invalidateQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      }
      if ((topic === "approval" || topic === "instance") && currentInstance) {
        void qc.invalidateQueries({
          queryKey: approvalStatusQueryKey(currentInstance),
        });
      }
      if (topic === "fleet") {
        void qc.invalidateQueries({ queryKey: FLEET_INSTANCES_QUERY_KEY });
      }
    },
    onFallbackPoll: () => {
      void qc.invalidateQueries({ queryKey: BOT_FLEET_QUERY_KEY });
      void qc.invalidateQueries({ queryKey: FLEET_INSTANCES_QUERY_KEY });
      if (currentInstance) {
        void qc.invalidateQueries({
          queryKey: approvalStatusQueryKey(currentInstance),
        });
      }
    },
  });

  // Clamp the index back into range whenever a process disappears (Stop was
  // pressed, dev tool killed it, etc.) — otherwise we'd index past the array
  // and show empty PID / mode.
  useEffect(() => {
    if (processes.length > 0 && carouselIdx >= processes.length) {
      setCarouselIdx(0);
    }
  }, [processes.length, carouselIdx]);

  const adbReadiness: AdbReadiness | null = adbStatus
    ? evaluateAdbReadiness(adbStatus)
    : null;

  // Live ADB devices that aren't in the fleet registry get no worker and never
  // show on Overview — surface a one-tap path to /adb to register them.
  const unregisteredCount = adbStatus
    ? adbStatus.live_devices.filter(
        (d) =>
          !adbStatus.configured.some((c) =>
            adbSerialMatches(c.adb_serial, d.serial, d.canonical_serial),
          ),
      ).length
    : 0;
  const unregisteredChip =
    unregisteredCount > 0 ? (
      <Link
        href="/adb"
        className="nav-bot-banner__chip nav-bot-banner__chip--warn"
        title="Live ADB devices not in the fleet registry — register them to run the bot, then restart"
      >
        {unregisteredCount} unregistered →
      </Link>
    ) : null;

  const queryError =
    query.isError && query.error instanceof Error ? query.error.message : null;
  const fleetError =
    fleetQuery.isError && fleetQuery.error instanceof Error
      ? fleetQuery.error.message
      : null;
  const approvalError =
    approvalQuery.isError && approvalQuery.error instanceof Error
      ? approvalQuery.error.message
      : null;
  const error = localError ?? queryError ?? fleetError ?? approvalError;

  if (!loaded && refreshing && !query.data) {
    return null;
  }

  if (query.isError && !query.data) {
    return (
      <div
        className="nav-bot-banner nav-bot-banner--offline"
        role="region"
        aria-label="Bot worker"
      >
        <div className="nav-bot-banner__top">
          <div className="nav-bot-banner__identity">
            <span className="nav-bot-banner__icon" aria-hidden>
              <Icon name="warning" size="sm" />
            </span>
            <span className="nav-bot-banner__body">
              <span className="nav-bot-banner__eyebrow">Bot control</span>
              <span className="nav-bot-banner__title">API offline</span>
            </span>
          </div>
          <span className="nav-bot-banner__chip nav-bot-banner__chip--danger">
            Offline
          </span>
        </div>
        <p className="nav-bot-banner__desc">
          {queryError ?? "Failed to reach API"}
        </p>
      </div>
    );
  }

  if (botStatus?.running) {
    const multi = processes.length > 1;
    const devicesLabel = deviceChipLabel(adbStatus);
    const selectedPaused = !!currentRow?.paused;
    const pauseBusy = pauseMutation.isPending;
    const pauseDisabled =
      !currentInstance ||
      !currentRow ||
      pauseBusy ||
      stopMutation.isPending;
    const pauseLabel = pauseBusy
      ? "Updating selected instance"
      : selectedPaused
        ? "Resume selected instance"
        : "Pause selected instance";
    const pauseTitle = pauseBusy
      ? "Updating..."
      : !currentInstance
        ? "No active device selected"
        : !currentRow
          ? "Waiting for selected device status"
          : selectedPaused
            ? `Resume ${currentInstance}`
            : `Pause ${currentInstance}`;
    return (
      <div
        className="nav-bot-banner nav-bot-banner--running"
        role="region"
        aria-label="Bot worker"
      >
        <div className="nav-bot-banner__top">
          <div className="nav-bot-banner__identity">
            <button
              type="button"
              className={[
                "nav-bot-banner__icon",
                "nav-bot-banner__control",
                selectedPaused ? "" : "nav-bot-banner__control--warn",
              ]
                .filter(Boolean)
                .join(" ")}
              disabled={pauseDisabled}
              onClick={() => {
                if (!currentInstance || !currentRow) return;
                pauseMutation.mutate(currentInstance);
              }}
              aria-label={pauseLabel}
              title={pauseTitle}
            >
              <Icon name={selectedPaused ? "play" : "pause"} size="sm" />
            </button>
            <span className="nav-bot-banner__body">
              <span className="nav-bot-banner__eyebrow">Bot control</span>
              <span className="nav-bot-banner__title">
                <span className="nav-bot-banner__live" aria-hidden />
                {selectedPaused ? "Running · paused" : "Running"}
                {multi ? (
                  <span
                    className="nav-bot-banner__badge"
                    aria-label={`${safeIdx + 1} of ${processes.length} supervisors`}
                  >
                    {safeIdx + 1}/{processes.length}
                  </span>
                ) : null}
              </span>
            </span>
          </div>
          <div className="nav-bot-banner__actions">
            {multi ? (
              <button
                type="button"
                className="nav-bot-banner__action"
                onClick={() => setCarouselIdx((i) => (i + 1) % processes.length)}
                aria-label="Show next supervisor"
                title={`Next supervisor (${safeIdx + 1}/${processes.length})`}
              >
                <Icon name="chevron-right" size="sm" />
              </button>
            ) : null}
            <button
              type="button"
              className="nav-bot-banner__action"
              disabled={stopMutation.isPending || pauseBusy}
              onClick={() => stopMutation.mutate()}
              aria-label={stopMutation.isPending ? "Stopping bot" : "Stop bot"}
              title={
                stopMutation.isPending
                  ? "Stopping..."
                  : multi
                    ? `Stop bot (terminates all ${processes.length} supervisors)`
                    : "Stop bot"
              }
            >
              <Icon name="stop" size="sm" />
            </button>
          </div>
        </div>
        <DeviceCarousel switcher={deviceSwitcher} />
        <InstanceStatusLine row={currentRow} approval={approvalStatus} />
        <div className="nav-bot-banner__chips" aria-label="Bot details">
          <span className="nav-bot-banner__chip">
            Mode {formatMode(botStatus.mode)}
          </span>
          {currentPid ? (
            <span className="nav-bot-banner__chip">PID {currentPid}</span>
          ) : null}
          {currentProc?.started_at ? (
            <span className="nav-bot-banner__chip">
              Up {formatProcessAge(currentProc.started_at)}
            </span>
          ) : null}
          <span className="nav-bot-banner__chip nav-bot-banner__chip--device">
            {devicesLabel}
          </span>
          {currentInstance ? (
            <InstanceStatusChip instanceId={currentInstance} row={currentRow} />
          ) : null}
          {currentInstance ? (
            <ApprovalStatusChip
              instanceId={currentInstance}
              status={approvalStatus}
            />
          ) : null}
          {approvalStatus?.heartbeat_active ? (
            <span className="nav-bot-banner__chip nav-bot-banner__chip--ok">
              Approval page open
            </span>
          ) : null}
          {unregisteredChip}
        </div>
        {error ? (
          <p className="nav-bot-banner__error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  // Bot is stopped (or never started). Always render the Start button so the
  // operator has an obvious action right after pressing Stop — previously an
  // ADB hiccup at this exact moment swallowed the Play affordance and there
  // was no way back to a running bot from this banner.
  const adbProblem = adbReadiness && !adbReadiness.ok ? adbReadiness : null;
  const startDisabled = startMutation.isPending || Boolean(adbProblem);
  const startTitle = startMutation.isPending
    ? "Starting..."
    : adbProblem
      ? `${adbReadinessTitle(adbProblem.kind)} - ${adbProblem.message}`
      : "Start bot";
  const ready = !adbProblem;
  return (
    <div
      className={
        adbProblem
          ? "nav-bot-banner nav-bot-banner--devices"
          : "nav-bot-banner"
      }
      role="region"
      aria-label="Bot worker"
    >
      <div className="nav-bot-banner__top">
        <div className="nav-bot-banner__identity">
          <button
            type="button"
            className="nav-bot-banner__icon nav-bot-banner__control"
            disabled={startDisabled}
            onClick={() => startMutation.mutate()}
            aria-label={startMutation.isPending ? "Starting bot" : "Start bot"}
            title={startTitle}
          >
            <Icon name={ready ? "play" : "warning"} size="sm" />
          </button>
          <span className="nav-bot-banner__body">
            <span className="nav-bot-banner__eyebrow">Bot control</span>
            <span className="nav-bot-banner__title">
              {adbProblem ? adbReadinessTitle(adbProblem.kind) : "Stopped"}
            </span>
          </span>
        </div>
      </div>
      <DeviceCarousel switcher={deviceSwitcher} />
      <p className="nav-bot-banner__desc">
        {adbProblem ? (
          <>
            {adbProblem.message}{" "}
            <Link href="/adb" className="nav-bot-banner__link">
              Open ADB
            </Link>
          </>
        ) : (
          "ADB online. Start workers when you are ready."
        )}
      </p>
      <div className="nav-bot-banner__chips" aria-label="Bot readiness">
        <span
          className={[
            "nav-bot-banner__chip",
            adbProblem ? "nav-bot-banner__chip--warn" : "nav-bot-banner__chip--ok",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {deviceChipLabel(adbStatus)}
        </span>
        <span className="nav-bot-banner__chip">Mode local</span>
        {currentInstance ? (
          <InstanceStatusChip instanceId={currentInstance} row={currentRow} />
        ) : null}
        {currentInstance ? (
          <ApprovalStatusChip
            instanceId={currentInstance}
            status={approvalStatus}
          />
        ) : null}
        {unregisteredChip}
      </div>
      {error ? (
        <p className="nav-bot-banner__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
