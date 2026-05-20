export type FleetLinkOptions = {
  instanceId?: string;
  playerId?: string;
  /** Extra query params (e.g. tab, scenario). */
  extra?: Record<string, string | undefined>;
};

function buildSearch(opts: FleetLinkOptions): string {
  const params = new URLSearchParams();
  if (opts.instanceId) params.set("instance_id", opts.instanceId);
  if (opts.playerId) params.set("player_id", opts.playerId);
  if (opts.extra) {
    for (const [k, v] of Object.entries(opts.extra)) {
      if (v != null && v !== "") params.set(k, v);
    }
  }
  const q = params.toString();
  return q ? `?${q}` : "";
}

export function instanceHref(instanceId: string, extra?: Record<string, string | undefined>) {
  return `/instance${buildSearch({ instanceId, extra })}`;
}

export function playerStateHref(
  playerId: string,
  opts?: { instanceId?: string; tab?: string },
) {
  return `/player-state${buildSearch({
    playerId,
    instanceId: opts?.instanceId,
    extra: opts?.tab ? { tab: opts.tab } : undefined,
  })}`;
}

export function approvalsHref(
  instanceId: string,
  extra?: Record<string, string | undefined>,
) {
  return `/approvals${buildSearch({ instanceId, extra })}`;
}

export function queueHref(opts?: { instanceId?: string; playerId?: string }) {
  return `/queue${buildSearch(opts ?? {})}`;
}

export {
  approvalsProbeHref,
  debugRunHref,
  editDslHref,
  overlayTestHref,
  regionFromQueueHistory,
  regionFromQueuePending,
  regionFromQueueRunning,
} from "@/lib/debug-links";
