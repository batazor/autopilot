"use client";

import Link from "next/link";
import { HistoryOutcomePill, TraceIdCell } from "@/components/queue/QueueVisuals";
import { playerStateHref } from "@/lib/fleet-links";
import type { InstanceHistoryRow } from "@/lib/types";

const MAX_ROWS = 20;

function formatWhen(startedAt: number): { relative: string; absolute: string } {
  const d = new Date(startedAt * 1000);
  const absolute = d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return { relative: `${sec}s ago`, absolute };
  const min = Math.round(sec / 60);
  if (min < 60) return { relative: `${min}m ago`, absolute };
  const hr = Math.round(min / 60);
  if (hr < 48) return { relative: `${hr}h ago`, absolute };
  const day = Math.round(hr / 24);
  return { relative: `${day}d ago`, absolute };
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s >= 0.05 ? `${m}m ${s.toFixed(0)}s` : `${m}m`;
}

type Props = {
  rows: InstanceHistoryRow[];
  instanceId: string;
};

export function InstanceScenarioHistory({ rows, instanceId }: Props) {
  const visible = rows.slice(0, MAX_ROWS);
  const ok = visible.filter((r) => r.success).length;
  const fail = visible.length - ok;

  return (
    <div className="instance-history">
      <div className="instance-history__header">
        <div>
          <h2 className="instance-history__title">Scenario history</h2>
          <p className="instance-history__subtitle meta">
            Recent runs on this instance (newest first)
          </p>
        </div>
        <div className="instance-history__stats" aria-label="Recent outcomes">
          <span className="instance-history__stat instance-history__stat--ok">
            <span className="instance-history__stat-value">{ok}</span>
            <span className="instance-history__stat-label">OK</span>
          </span>
          <span className="instance-history__stat instance-history__stat--fail">
            <span className="instance-history__stat-value">{fail}</span>
            <span className="instance-history__stat-label">Failed</span>
          </span>
        </div>
      </div>

      <ol className="instance-history__list">
        {visible.map((h, i) => {
          const when = formatWhen(h.started_at);
          const itemClass = h.success
            ? "instance-history__item instance-history__item--ok"
            : "instance-history__item instance-history__item--fail";
          return (
            <li key={`${h.started_at}-${h.scenario}-${h.player_id}-${i}`} className={itemClass}>
              <div className="instance-history__marker" aria-hidden />
              <div className="instance-history__card">
                <div className="instance-history__card-head">
                  <HistoryOutcomePill success={h.success} />
                  <span className="instance-history__scenario">
                    {h.scenario}
                  </span>
                  <span className="instance-history__duration" title="Duration">
                    {formatDuration(h.duration_s)}
                  </span>
                </div>
                <div className="instance-history__card-meta">
                  <time dateTime={new Date(h.started_at * 1000).toISOString()} title={when.absolute}>
                    {when.relative}
                    <span className="instance-history__time-sep" aria-hidden>
                      ·
                    </span>
                    <span className="instance-history__time-abs">{when.absolute}</span>
                  </time>
                  {h.player_id ? (
                    <>
                      <span className="instance-history__meta-sep" aria-hidden>
                        ·
                      </span>
                      <Link
                        href={playerStateHref(h.player_id, { instanceId })}
                        className="instance-history__player"
                      >
                        {h.player_id}
                      </Link>
                    </>
                  ) : null}
                </div>
                {h.detail?.trim() ? (
                  <p className="instance-history__detail" title={h.detail}>
                    {h.detail}
                  </p>
                ) : null}
                {h.trace_id?.trim() ? (
                  <div className="instance-history__trace">
                    <TraceIdCell traceId={h.trace_id} />
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      {rows.length > MAX_ROWS ? (
        <p className="instance-history__more meta">
          Showing latest {MAX_ROWS} of {rows.length} entries
        </p>
      ) : null}
    </div>
  );
}
