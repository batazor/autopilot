import type { QueueHistoryRow, QueuePendingRow, QueueRunningRow } from "@/lib/types";

type LinkSearchOpts = {
  instanceId?: string;
  playerId?: string;
  extra?: Record<string, string | undefined>;
};

function buildSearch(opts: LinkSearchOpts): string {
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

/** Open DSL editor for a module scope + scenario file/key. */
export function editDslHref(opts: {
  module?: string;
  scope?: string;
  scenario?: string;
}): string {
  const scope = opts.scope ?? opts.module;
  return `/edit-dsl${buildSearch({
    extra: {
      ...(scope ? { scope, module: scope } : {}),
      scenario: opts.scenario,
    },
  })}`;
}

export function debugRunHref(opts?: {
  instanceId?: string;
  playerId?: string;
  scenario?: string;
  scope?: string;
}): string {
  return `/debug-run${buildSearch({
    instanceId: opts?.instanceId,
    playerId: opts?.playerId,
    extra: {
      scenario: opts?.scenario,
      scope: opts?.scope,
    },
  })}`;
}

export function overlayTestHref(
  instanceId: string,
  opts?: { region?: string; highlight?: string },
): string {
  return `/overlay-test${buildSearch({
    instanceId,
    extra: {
      region: opts?.region ?? opts?.highlight,
      highlight: opts?.highlight ?? opts?.region,
    },
  })}`;
}

/** Approvals with region probe panel open and region prefilled. */
export function approvalsProbeHref(
  instanceId: string,
  region: string,
): string {
  return `/approvals${buildSearch({
    instanceId,
    extra: { region, probe: "1" },
  })}`;
}

const OK_STATUSES = new Set(["ok", "success", "skipped"]);

function normalizeRegion(value: unknown): string {
  const s = String(value ?? "").trim();
  return s && s !== "—" ? s : "";
}

/** Region name from queue row or last failing DSL trace step. */
export function regionFromQueueHistory(row: QueueHistoryRow): string {
  const direct = normalizeRegion(row.region);
  if (direct) return direct;

  const trace = row.steps_trace;
  if (!trace?.length) return "";

  for (let i = trace.length - 1; i >= 0; i--) {
    const step = trace[i];
    const status = String(step.status ?? "").trim().toLowerCase();
    if (status && !OK_STATUSES.has(status)) {
      const region = normalizeRegion(step.region);
      if (region) return region;
    }
  }

  for (let i = trace.length - 1; i >= 0; i--) {
    const region = normalizeRegion(trace[i].region);
    if (region) return region;
  }

  return "";
}

export function regionFromQueuePending(row: QueuePendingRow): string {
  return normalizeRegion(row.region);
}

export function regionFromQueueRunning(row: QueueRunningRow): string {
  return normalizeRegion(row.region);
}
