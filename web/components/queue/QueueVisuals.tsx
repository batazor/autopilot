import Link from "next/link";
import { AppMenu, type AppMenuItem } from "@/components/headless";
import { CopyButton } from "@/components/CopyButton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  approvalsProbeHref,
  debugRunHref,
  overlayTestHref,
  regionFromQueueHistory,
  regionFromQueuePending,
  regionFromQueueRunning,
} from "@/lib/debug-links";
import { instanceHref, playerStateHref } from "@/lib/fleet-links";
import type { QueueHistoryRow, QueuePendingRow, QueueRunningRow, QueueView } from "@/lib/types";

function debugJson(payload: unknown): string {
  return JSON.stringify(payload, null, 2);
}

export function pendingDebugPayload(row: QueuePendingRow) {
  return debugJson({
    task_id: row.task_id,
    scenario_key: row.scenario_key,
    scenario: row.scenario,
    instance_id: row.instance_id,
    player_id: row.player_id,
    region: row.region,
    priority: row.priority,
    scheduled_at: row.scheduled_at,
    overdue: row.overdue,
    cooperative: row.cooperative,
  });
}

export function runningDebugPayload(row: QueueRunningRow) {
  return debugJson({
    task_id: row.task_id,
    scenario_key: row.scenario_key,
    scenario: row.scenario,
    active_scenario: row.active_scenario,
    instance_id: row.instance_id,
    player_id: row.player_id,
    priority: queuePriorityValue(row.priority),
    step: row.step,
    nav_target: row.nav_target,
    region: row.region,
  });
}

export function historyDebugPayload(row: QueueHistoryRow) {
  return debugJson({
    task_id: row.task_id,
    trace_id: row.trace_id || null,
    tempo_trace_url: row.tempo_trace_url || null,
    scenario_key: row.scenario_key,
    scenario: row.scenario,
    instance_id: row.instance_id,
    player_id: row.player_id,
    success: row.success,
    reason: row.reason || null,
    steps: row.steps,
    steps_trace: row.steps_trace,
    duration_s: row.duration_s,
    started_at: row.started_at,
    finished_at: row.finished_at,
    region: row.region,
    priority: row.priority,
  });
}

export function QueueCopyCell({
  text,
  title,
}: {
  text: string;
  title?: string;
}) {
  return <CopyButton text={text} label="Copy" title={title} />;
}

export function queueSummary(data: QueueView | null) {
  const pending = data?.pending_count ?? 0;
  const overdue = data?.pending.filter((r) => r.overdue).length ?? 0;
  const running = data?.running.length ?? 0;
  const history = data?.history ?? [];
  const recent = history.slice(0, 20);
  const ok = recent.filter((h) => h.success).length;
  const fail = recent.length - ok;
  return { pending, overdue, running, ok, fail, historyTotal: history.length };
}

export function QueueMetrics({ data }: { data: QueueView | null }) {
  const s = queueSummary(data);
  return (
    <div className="metrics-row queue-metrics">
      <div className="metric-card">
        <div className="label">Pending</div>
        <div className="value">{s.pending}</div>
      </div>
      <div className="metric-card">
        <div className="label">Overdue</div>
        <div className={`value ${s.overdue ? "queue-metric-warn" : ""}`}>{s.overdue}</div>
      </div>
      <div className="metric-card">
        <div className="label">Running</div>
        <div className={`value ${s.running ? "queue-metric-live" : ""}`}>{s.running}</div>
      </div>
      <div className="metric-card">
        <div className="label">Recent OK</div>
        <div className="value queue-metric-ok">{s.ok}</div>
      </div>
      <div className="metric-card">
        <div className="label">Recent failed</div>
        <div className={`value ${s.fail ? "queue-metric-fail" : ""}`}>{s.fail}</div>
      </div>
    </div>
  );
}

export function PendingSchedulePill({ row }: { row: QueuePendingRow }) {
  const cls = row.overdue ? "status-pending pulse" : "pill-stale";
  const label = row.overdue ? "Overdue" : "Scheduled";
  return (
    <span className={`status-pill ${cls}`} title={row.scheduled}>
      <span className="status-pill__dot" aria-hidden />
      {label}
    </span>
  );
}

export function CooperativePill({ cooperative }: { cooperative: boolean }) {
  if (!cooperative) return <span className="muted">—</span>;
  return <span className="status-pill pill-busy">Coop</span>;
}

function queuePriorityValue(priority: number | undefined): number {
  return typeof priority === "number" && Number.isFinite(priority) ? priority : 0;
}

export function PriorityBadge({ priority }: { priority?: number }) {
  const p = queuePriorityValue(priority);
  const hot = p >= 50_000;
  return (
    <span className={`queue-priority ${hot ? "queue-priority-hot" : ""}`} title={`Priority ${p}`}>
      {p.toLocaleString()}
    </span>
  );
}

export function RunningCards({ rows }: { rows: QueueRunningRow[] }) {
  if (!rows.length) {
    return (
      <EmptyState
        icon="list-empty"
        title="No tasks running"
        description="When a worker picks up a task, it will appear here."
      />
    );
  }
  return (
    <div className="queue-running-grid">
      {rows.map((r) => {
        const priority = queuePriorityValue(r.priority);
        return (
        <article key={r.task_id} className="queue-running-card">
          <header className="queue-running-card__head">
            <span className="status-pill pill-live pulse">
              <span className="status-pill__dot" aria-hidden />
              Running
            </span>
            <PriorityBadge priority={priority} />
            <Link href={instanceHref(r.instance_id)}>
              <strong>{r.instance_id}</strong>
            </Link>
          </header>
          <p className="queue-running-card__scenario">{r.scenario}</p>
          {r.active_scenario_label && r.active_scenario_label !== r.scenario ? (
            <p className="meta">Active: {r.active_scenario_label}</p>
          ) : null}
          <div className="queue-running-card__actions">
            <QueueTaskActions
              instanceId={r.instance_id}
              playerId={r.player_id}
              scenarioKey={r.scenario_key}
              region={regionFromQueueRunning(r)}
            />
            <CopyButton
              text={runningDebugPayload(r)}
              label="Copy debug"
              title="Copy task id, scenario, step, nav target"
            />
          </div>
          <dl className="queue-running-card__meta">
            {r.step > 0 ? (
              <div>
                <dt>Step</dt>
                <dd>{r.step}</dd>
              </div>
            ) : null}
            {r.nav_target ? (
              <div>
                <dt>Nav</dt>
                <dd>{r.nav_target}</dd>
              </div>
            ) : null}
            <div>
              <dt>Priority</dt>
              <dd>{priority.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{r.started}</dd>
            </div>
            <div>
              <dt>Player</dt>
              <dd>
                <Link
                  href={playerStateHref(r.player_id, {
                    instanceId: r.instance_id,
                  })}
                >
                  {r.player_id}
                </Link>
              </dd>
            </div>
          </dl>
        </article>
        );
      })}
    </div>
  );
}

export function TraceIdCell({
  traceId,
  tempoTraceUrl,
}: {
  traceId: string;
  tempoTraceUrl?: string;
}) {
  const tid = traceId.trim();
  if (!tid) {
    return <span className="muted">—</span>;
  }
  const short = tid.length > 12 ? `${tid.slice(0, 12)}…` : tid;
  const tempo = tempoTraceUrl?.trim() || "";
  return (
    <span className="queue-trace" title={tid}>
      <code className="queue-trace__id">{short}</code>
      <CopyButton text={tid} label="Copy" title="Copy trace ID (Grafana / Tempo)" />
      {tempo ? (
        <a
          href={tempo}
          target="_blank"
          rel="noopener noreferrer"
          className="queue-task-actions__link"
          title="Open trace in Tempo"
        >
          Tempo
        </a>
      ) : null}
    </span>
  );
}

export function HistoryOutcomePill({ success }: { success: boolean }) {
  return success ? (
    <span className="status-pill pill-live">OK</span>
  ) : (
    <span className="status-pill pill-danger">Failed</span>
  );
}

export function HistoryStepsCell({
  steps,
  failedRegionHref,
}: {
  steps: string;
  /** When set, failed/partial steps link to overlay-test with region prefilled. */
  failedRegionHref?: string | null;
}) {
  if (!steps || steps === "—") {
    return <span className="muted">—</span>;
  }
  if (steps.includes("complete")) {
    return <span className="status-pill pill-live">{steps}</span>;
  }
  if (steps.includes("partial") || failedRegionHref) {
    const inner = (
      <span className={steps.includes("partial") ? "status-pill pill-paused" : "queue-steps"}>
        {steps}
      </span>
    );
    if (failedRegionHref) {
      return (
        <Link href={failedRegionHref} className="queue-steps-link" title="Open in overlay test">
          {inner}
        </Link>
      );
    }
    return inner;
  }
  return <span className="queue-steps">{steps}</span>;
}

export function QueueTaskActions({
  instanceId,
  playerId,
  scenarioKey,
  region,
  showOverlay = true,
}: {
  instanceId: string;
  playerId?: string;
  scenarioKey: string;
  region?: string;
  showOverlay?: boolean;
}) {
  const probeRegion = region?.trim();
  return (
    <nav className="queue-task-actions" aria-label="Debug actions">
      <Link
        href={debugRunHref({ instanceId, playerId, scenario: scenarioKey })}
        className="queue-task-actions__link"
      >
        DSL runner
      </Link>
      {probeRegion ? (
        <>
          <Link
            href={approvalsProbeHref(instanceId, probeRegion)}
            className="queue-task-actions__link"
          >
            Probe
          </Link>
          {showOverlay ? (
            <Link
              href={overlayTestHref(instanceId, { region: probeRegion })}
              className="queue-task-actions__link"
            >
              Overlay
            </Link>
          ) : null}
        </>
      ) : null}
    </nav>
  );
}

export function QueueHistoryActions({ row }: { row: QueueHistoryRow }) {
  const region = regionFromQueueHistory(row);
  const traceId = row.trace_id?.trim();
  const tempoUrl = row.tempo_trace_url?.trim();
  const items: AppMenuItem[] = [];

  items.push({
    kind: "link",
    label: "Open in DSL runner",
    href: debugRunHref({
      instanceId: row.instance_id,
      playerId: row.player_id,
      scenario: row.scenario_key,
    }),
  });

  if (region) {
    items.push({
      kind: "link",
      label: "Probe region",
      href: approvalsProbeHref(row.instance_id, region),
      title: `Probe approvals for ${region}`,
    });
    items.push({
      kind: "link",
      label: "Open in overlay test",
      href: overlayTestHref(row.instance_id, { region }),
      title: `Inspect ${region} in overlay test`,
    });
  }

  if (tempoUrl) {
    items.push({
      kind: "link",
      label: "Open trace in Tempo",
      href: tempoUrl,
      title: traceId || "Open distributed trace",
    });
  }

  if (traceId) {
    items.push({
      label: "Copy trace ID",
      onClick: () => {
        void navigator.clipboard?.writeText(traceId);
      },
      title: "Copy trace ID (Grafana / Tempo)",
    });
  }

  items.push({
    label: "Copy debug JSON",
    onClick: () => {
      void navigator.clipboard?.writeText(historyDebugPayload(row));
    },
    title: "Copy task id, trace id, steps_trace (for debugging)",
  });

  return (
    <div className="queue-row-actions">
      <AppMenu
        items={items}
        anchor="bottom end"
        buttonTitle="History actions"
        ariaLabel={`Actions for ${row.scenario}`}
      />
    </div>
  );
}

export function QueuePendingActions({
  row,
  onRunNow,
  onDelete,
  disabled = false,
}: {
  row: QueuePendingRow;
  onRunNow?: (taskId: string) => void;
  onDelete?: (taskId: string) => void;
  disabled?: boolean;
}) {
  const region = regionFromQueuePending(row);
  const items: AppMenuItem[] = [];

  if (onRunNow) {
    items.push({
      label: "Run now",
      onClick: () => onRunNow(row.task_id),
      title: "Move task to the front of the queue",
    });
  }

  items.push({
    kind: "link",
    label: "Open in DSL runner",
    href: debugRunHref({
      instanceId: row.instance_id,
      playerId: row.player_id,
      scenario: row.scenario_key,
    }),
  });

  if (region) {
    items.push({
      kind: "link",
      label: "Probe region",
      href: approvalsProbeHref(row.instance_id, region),
      title: `Probe approvals for ${region}`,
    });
  }

  items.push({
    label: "Copy debug JSON",
    onClick: () => {
      void navigator.clipboard?.writeText(pendingDebugPayload(row));
    },
    title: "Copy task id, scenario key, instance",
  });

  if (onDelete) {
    items.push({ kind: "separator" });
    items.push({
      label: "Delete task",
      onClick: () => onDelete(row.task_id),
      danger: true,
      title: "Remove this task from the queue",
    });
  }

  return (
    <div className="queue-row-actions">
      <AppMenu
        items={items}
        anchor="bottom end"
        buttonTitle="Task actions"
        ariaLabel={`Actions for ${row.scenario}`}
        disabled={disabled}
      />
    </div>
  );
}

export function ScenarioCell({
  label,
  scenarioKey,
  instanceId,
  playerId,
}: {
  label: string;
  scenarioKey: string;
  instanceId?: string;
  playerId?: string;
}) {
  return (
    <span className="queue-scenario" title={scenarioKey}>
      {label}
    </span>
  );
}
