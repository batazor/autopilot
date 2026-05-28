"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const PENDING_PAGE_SIZE = 25;
const HISTORY_PAGE_SIZE = 20;

function Pager({
  page,
  pageCount,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onChange: (next: number) => void;
}) {
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return (
    <div className="toolbar toolbar--spaced queue-pager">
      <span className="meta">
        {start}–{end} of {total}
      </span>
      <button
        type="button"
        className="btn-secondary"
        disabled={page <= 1}
        onClick={() => onChange(Math.max(1, page - 1))}
      >
        ← Prev
      </button>
      <span className="meta">
        Page {page} / {pageCount}
      </span>
      <button
        type="button"
        className="btn-secondary"
        disabled={page >= pageCount}
        onClick={() => onChange(Math.min(pageCount, page + 1))}
      >
        Next →
      </button>
    </div>
  );
}
import { AppCheckbox, AppListbox, AppTabs } from "@/components/headless";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { EmptyState } from "@/components/ui/EmptyState";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { instanceHref, playerStateHref } from "@/lib/fleet-links";
import { MetricsRowSkeleton } from "@/components/skeleton/MetricsRowSkeleton";
import {
  CooperativePill,
  HistoryOutcomePill,
  HistoryStepsCell,
  PendingSchedulePill,
  PriorityBadge,
  QueueHistoryActions,
  QueueMetrics,
  QueuePendingActions,
  RunningCards,
  ScenarioCell,
} from "@/components/queue/QueueVisuals";
import { QueuePendingCalendar } from "@/components/queue/QueuePendingCalendar";
import { overlayTestHref, regionFromQueueHistory } from "@/lib/debug-links";
import { fetchQueue, removeQueueTasks, runQueueTaskNow } from "@/lib/api";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type { QueueView } from "@/lib/types";

export default function QueuePage() {
  const { showSuccess } = useFeedback();
  const [data, setData] = useState<QueueView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [pendingView, setPendingView] = useState<"table" | "timeline">("table");
  const [pendingSort, setPendingSort] = useState<{
    col: "schedule" | "instance" | "player";
    dir: "asc" | "desc";
  }>({ col: "schedule", dir: "asc" });
  const [pendingPage, setPendingPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);

  const cycleSort = (col: "instance" | "player") => {
    setPendingSort((prev) => {
      if (prev.col !== col) return { col, dir: "asc" };
      if (prev.dir === "asc") return { col, dir: "desc" };
      return { col: "schedule", dir: "asc" };
    });
  };

  const sortedPending = useMemo(() => {
    const rows = data?.pending ?? [];
    if (pendingSort.col === "schedule") return rows;
    const sign = pendingSort.dir === "asc" ? 1 : -1;
    const key = pendingSort.col === "instance" ? "instance_id" : "player_id";
    return [...rows].sort((a, b) => {
      const cmp = a[key].localeCompare(b[key], undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (cmp !== 0) return sign * cmp;
      return a.scheduled_at - b.scheduled_at;
    });
  }, [data?.pending, pendingSort]);

  const sortArrow = (col: "instance" | "player") => {
    if (pendingSort.col !== col) return "";
    return pendingSort.dir === "asc" ? " ↑" : " ↓";
  };

  const pendingTotal = sortedPending.length;
  const pendingNeedsFullRows = pendingView === "timeline" || pendingSort.col !== "schedule";
  const pendingTotalRows = data?.pending_count ?? pendingTotal;
  const pendingPageCount = Math.max(1, Math.ceil(pendingTotal / PENDING_PAGE_SIZE));
  const pendingServerPageCount = Math.max(1, Math.ceil(pendingTotalRows / PENDING_PAGE_SIZE));
  const pendingPageSafe = Math.min(
    pendingPage,
    pendingNeedsFullRows ? pendingPageCount : pendingServerPageCount,
  );
  const pagedPending = useMemo(() => {
    if (!pendingNeedsFullRows && sortedPending.length <= PENDING_PAGE_SIZE) return sortedPending;
    const start = (pendingPageSafe - 1) * PENDING_PAGE_SIZE;
    return sortedPending.slice(start, start + PENDING_PAGE_SIZE);
  }, [pendingNeedsFullRows, sortedPending, pendingPageSafe]);

  const historyRows = data?.history ?? [];
  const historyNeedsFullRows = pendingView === "timeline";
  const historyTotal = data?.history_count ?? historyRows.length;
  const historyPageCount = Math.max(1, Math.ceil(historyTotal / HISTORY_PAGE_SIZE));
  const historyPageSafe = Math.min(historyPage, historyPageCount);
  const pagedHistory = useMemo(() => {
    if (!historyNeedsFullRows && historyRows.length <= HISTORY_PAGE_SIZE) return historyRows;
    const start = (historyPageSafe - 1) * HISTORY_PAGE_SIZE;
    return historyRows.slice(start, start + HISTORY_PAGE_SIZE);
  }, [historyNeedsFullRows, historyRows, historyPageSafe]);

  useEffect(() => {
    const maxPage = pendingNeedsFullRows ? pendingPageCount : pendingServerPageCount;
    if (pendingPage > maxPage) setPendingPage(maxPage);
  }, [pendingNeedsFullRows, pendingPage, pendingPageCount, pendingServerPageCount]);
  useEffect(() => {
    if (historyPage > historyPageCount) setHistoryPage(historyPageCount);
  }, [historyPage, historyPageCount]);
  useEffect(() => {
    setPendingPage(1);
  }, [pendingSort.col, pendingSort.dir]);
  const pickRef = useRef(pick);
  pickRef.current = pick;
  const revisionRef = useRef<string | undefined>(undefined);
  const queryKeyRef = useRef<string | undefined>(undefined);

  const refresh = useCallback(async () => {
    const full = pendingNeedsFullRows;
    const queryKey = JSON.stringify({
      pendingPage: full ? 1 : pendingPageSafe,
      pendingPageSize: PENDING_PAGE_SIZE,
      historyPage: full ? 1 : historyPageSafe,
      historyPageSize: HISTORY_PAGE_SIZE,
      full,
    });
    try {
      const result = await fetchQueue({
        ifRevision: queryKeyRef.current === queryKey ? revisionRef.current : undefined,
        pendingPage: full ? 1 : pendingPageSafe,
        pendingPageSize: PENDING_PAGE_SIZE,
        historyPage: full ? 1 : historyPageSafe,
        historyPageSize: HISTORY_PAGE_SIZE,
        full,
      });
      if ("unchanged" in result) {
        setError(null);
        return;
      }
      revisionRef.current = result.revision;
      queryKeyRef.current = queryKey;
      setData(result);
      setError(null);
      if (!pickRef.current && result.pending.length) {
        setPick(result.pending[0].task_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [historyPageSafe, pendingNeedsFullRows, pendingPageSafe]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useDashboardEventStream({
    topics: ["queue"],
    enabled: true,
    onEvent: () => {
      void refresh();
    },
    onFallbackPoll: refresh,
  });

  const toggleSelect = (taskId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  const onRunNow = async () => {
    if (!pick || busy) return;
    setBusy(true);
    try {
      await runQueueTaskNow(pick);
      await refresh();
      showSuccess("Task moved to front of queue");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!selected.size || busy) return;
    setBusy(true);
    try {
      const n = selected.size;
      await removeQueueTasks([...selected]);
      setSelected(new Set());
      await refresh();
      showSuccess(n === 1 ? "Removed 1 task" : `Removed ${n} tasks`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onRunRow = useCallback(
    async (taskId: string) => {
      if (busy) return;
      setBusy(true);
      try {
        await runQueueTaskNow(taskId);
        await refresh();
        showSuccess("Task moved to front of queue");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [busy, refresh, showSuccess],
  );

  const onDeleteRow = useCallback(
    async (taskId: string) => {
      if (busy) return;
      setBusy(true);
      try {
        await removeQueueTasks([taskId]);
        setSelected((prev) => {
          if (!prev.has(taskId)) return prev;
          const next = new Set(prev);
          next.delete(taskId);
          return next;
        });
        await refresh();
        showSuccess("Removed 1 task");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [busy, refresh, showSuccess],
  );

  return (
    <>
      <FleetPageHeader title="Queue">
        <p className="muted">Pending, running, and recent task outcomes across the fleet.</p>
      </FleetPageHeader>
      <ErrorBanner message={error} />

      {loading && !data ? (
        <MetricsRowSkeleton count={5} className="queue-metrics" />
      ) : (
        <QueueMetrics data={data} />
      )}

      <section className="panel queue-panel">
        <h2>Running</h2>
        <RunningCards rows={data?.running ?? []} />
      </section>

      <section className="panel queue-panel">
        <h2>Pending ({data?.pending_count ?? 0})</h2>
        <p className="meta queue-pending-order-hint">
          {pendingSort.col === "schedule" ? (
            <>
              Sorted per instance in execution order (same ranking as the worker&apos;s{" "}
              <code>pop_due</code>): due tasks first, then scheduled later by time.
            </>
          ) : (
            <>
              Sorted by <strong>{pendingSort.col}</strong> ({pendingSort.dir}). Click the
              column header again to toggle direction or reset to schedule order.
            </>
          )}
        </p>
        {pendingTotalRows ? (
          <>
            <AppTabs
              selectedKey={pendingView}
              onChange={(k) => setPendingView(k as "table" | "timeline")}
              renderPanels={false}
              tabs={[
                { key: "table", label: "Table" },
                { key: "timeline", label: "Timeline" },
              ]}
            />
            {pendingView === "table" ? (
              <div className="data-table-wrap">
                <table className="data-table queue-table">
                  <thead>
                    <tr>
                      <th />
                      <th>Status</th>
                      <th>When</th>
                      <th
                        aria-sort={
                          pendingSort.col === "player"
                            ? pendingSort.dir === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                        }
                      >
                        <button
                          type="button"
                          className="queue-sort-btn"
                          onClick={() => cycleSort("player")}
                          title="Sort by player"
                        >
                          Player{sortArrow("player")}
                        </button>
                      </th>
                      <th
                        aria-sort={
                          pendingSort.col === "instance"
                            ? pendingSort.dir === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                        }
                      >
                        <button
                          type="button"
                          className="queue-sort-btn"
                          onClick={() => cycleSort("instance")}
                          title="Sort by instance"
                        >
                          Instance{sortArrow("instance")}
                        </button>
                      </th>
                      <th>Scenario</th>
                      <th>Region</th>
                      <th>Coop</th>
                      <th>Pri</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {pagedPending.map((r) => (
                      <tr key={r.task_id} className={r.overdue ? "queue-row-overdue" : undefined}>
                        <td>
                          <AppCheckbox
                            checked={selected.has(r.task_id)}
                            onChange={() => toggleSelect(r.task_id)}
                            aria-label={`Select ${r.scenario}`}
                          />
                        </td>
                        <td>
                          <PendingSchedulePill row={r} />
                        </td>
                        <td className="queue-when">{r.scheduled}</td>
                        <td>
                          <Link href={playerStateHref(r.player_id, { instanceId: r.instance_id })}>
                            {r.player_id}
                          </Link>
                        </td>
                        <td>
                          <Link href={instanceHref(r.instance_id)}>{r.instance_id}</Link>
                        </td>
                        <td>
                          <ScenarioCell
                            label={r.scenario}
                            scenarioKey={r.scenario_key}
                            instanceId={r.instance_id}
                            playerId={r.player_id}
                          />
                        </td>
                        <td className="muted">{r.region}</td>
                        <td>
                          <CooperativePill cooperative={r.cooperative} />
                        </td>
                        <td>
                          <PriorityBadge priority={r.priority} />
                        </td>
                        <td>
                          <QueuePendingActions
                            row={r}
                            onRunNow={onRunRow}
                            onDelete={onDeleteRow}
                            disabled={busy}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(pendingNeedsFullRows ? pendingPageCount : pendingServerPageCount) > 1 ? (
                  <Pager
                    page={pendingPageSafe}
                    pageCount={pendingNeedsFullRows ? pendingPageCount : pendingServerPageCount}
                    total={pendingNeedsFullRows ? pendingTotal : pendingTotalRows}
                    pageSize={PENDING_PAGE_SIZE}
                    onChange={setPendingPage}
                  />
                ) : null}
              </div>
            ) : (
              <QueuePendingCalendar
                pending={data?.pending ?? []}
                history={data?.history ?? []}
                onReschedule={() => {
                  showSuccess("Task rescheduled");
                  void refresh();
                }}
                onRunNow={() => {
                  showSuccess("Task moved to front of queue");
                  void refresh();
                }}
                onDelete={(taskId) => {
                  showSuccess("Removed 1 task");
                  setSelected((prev) => {
                    if (!prev.has(taskId)) return prev;
                    const next = new Set(prev);
                    next.delete(taskId);
                    return next;
                  });
                  void refresh();
                }}
                onCreated={() => {
                  showSuccess("Task scheduled");
                  void refresh();
                }}
                onError={(msg) => setError(msg)}
              />
            )}
          </>
        ) : (
          <EmptyState
            icon="inbox-empty"
            title="Queue is empty"
            description="Scheduler will enqueue cron tasks when due."
          />
        )}

        {data && data.pending.length > 0 ? (
          <div className="toolbar toolbar--spaced">
            <AppListbox
              inline
              label="Task"
              value={pick}
              onChange={setPick}
              options={data.pending.map((r) => ({
                value: r.task_id,
                label: `${r.scenario} · ${r.instance_id}`,
              }))}
              minWidth={280}
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={onRunNow}
            >
              Run now
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !selected.size}
              onClick={onDelete}
            >
              Delete selected ({selected.size})
            </button>
          </div>
        ) : null}
      </section>

      <section className="panel queue-panel">
        <h2>History</h2>
        {historyTotal ? (
          <div className="data-table-wrap">
            <table className="data-table queue-table">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Finished</th>
                  <th>Instance</th>
                  <th>Scenario</th>
                  <th>Player</th>
                  <th>Duration</th>
                  <th>Steps</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {pagedHistory.map((h) => (
                  <tr
                    key={`${h.task_id}-${h.finished_at}`}
                    className={h.success ? "queue-row-ok" : "queue-row-fail"}
                  >
                    <td>
                      <HistoryOutcomePill success={h.success} />
                    </td>
                    <td className="queue-when">
                      {new Date(h.finished_at * 1000).toLocaleString()}
                    </td>
                    <td>
                      <Link href={instanceHref(h.instance_id)}>{h.instance_id}</Link>
                    </td>
                    <td>
                      <ScenarioCell
                        label={h.scenario}
                        scenarioKey={h.scenario_key}
                        instanceId={h.instance_id}
                        playerId={h.player_id}
                      />
                    </td>
                    <td>
                      <Link
                        href={playerStateHref(h.player_id, {
                          instanceId: h.instance_id,
                        })}
                      >
                        {h.player_id}
                      </Link>
                    </td>
                    <td>{h.duration_s.toFixed(1)}s</td>
                    <td>
                      <HistoryStepsCell
                        steps={h.steps}
                        failedRegionHref={
                          !h.success && regionFromQueueHistory(h)
                            ? overlayTestHref(h.instance_id, {
                                region: regionFromQueueHistory(h),
                              })
                            : null
                        }
                      />
                    </td>
                    <td className="queue-reason" title={h.reason || undefined}>
                      {h.success ? (
                        <span className="muted">—</span>
                      ) : (
                        h.reason || <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <QueueHistoryActions row={h} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {historyPageCount > 1 ? (
              <Pager
                page={historyPageSafe}
                pageCount={historyPageCount}
                total={historyTotal}
                pageSize={HISTORY_PAGE_SIZE}
                onChange={setHistoryPage}
              />
            ) : null}
          </div>
        ) : (
          <EmptyState
            icon="list-empty"
            title="No recent history"
            description="Completed tasks from the last runs will show up here."
          />
        )}
      </section>
    </>
  );
}
