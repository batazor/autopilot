"use client";

import Link from "next/link";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useOptimistic,
  useRef,
  useState,
  useTransition,
} from "react";

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
import { PageHeader } from "@/components/PageHeader";
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
import { Button } from "@/components/ui/Button";
import {
  fetchQueue,
  purgeBlockedQueueTasks,
  removeQueueTasks,
  runQueueTaskNow,
} from "@/lib/api";
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
    col: "schedule" | "player";
    dir: "asc" | "desc";
  }>({ col: "schedule", dir: "asc" });
  const [pendingPage, setPendingPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [isPending, startTransition] = useTransition();

  // Optimistic pending list: removed task ids drop out on the current frame so
  // rows vanish the moment Delete is clicked, before the server confirms. The
  // list reverts to the fetched data once refresh() lands (or on error).
  const [optimisticPending, dropPendingOptimistic] = useOptimistic(
    data?.pending ?? [],
    (rows, removedIds: string[]) =>
      rows.filter((r) => !removedIds.includes(r.task_id)),
  );

  const cycleSort = (col: "player") => {
    startTransition(() => {
      setPendingSort((prev) => {
        if (prev.col !== col) return { col, dir: "asc" };
        if (prev.dir === "asc") return { col, dir: "desc" };
        return { col: "schedule", dir: "asc" };
      });
    });
  };

  // Rows arrive grouped per instance (the pop_due execution order). Player
  // sort reorders rows *within* each instance group so the grouping headers
  // in the table stay contiguous.
  const sortedPending = useMemo(() => {
    const rows = optimisticPending;
    if (pendingSort.col === "schedule") return rows;
    const sign = pendingSort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const grp = a.instance_id.localeCompare(b.instance_id, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (grp !== 0) return grp;
      const cmp = a.player_id.localeCompare(b.player_id, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (cmp !== 0) return sign * cmp;
      return a.scheduled_at - b.scheduled_at;
    });
  }, [optimisticPending, pendingSort]);

  const sortArrow = (col: "player") => {
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

  const onPurgeBlocked = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const removed = await purgeBlockedQueueTasks();
      await refresh();
      showSuccess(
        removed === 1 ? "Purged 1 blocked task" : `Purged ${removed} blocked tasks`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = () => {
    if (!selected.size || busy) return;
    const ids = [...selected];
    startTransition(async () => {
      dropPendingOptimistic(ids);
      setBusy(true);
      try {
        await removeQueueTasks(ids);
        setSelected(new Set());
        await refresh();
        showSuccess(ids.length === 1 ? "Removed 1 task" : `Removed ${ids.length} tasks`);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    });
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
    (taskId: string) => {
      if (busy) return;
      startTransition(async () => {
        dropPendingOptimistic([taskId]);
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
      });
    },
    [busy, dropPendingOptimistic, refresh, showSuccess],
  );

  return (
    <>
      <PageHeader title="Queue" fleet>
        <p className="muted">Pending, running, and recent task outcomes across the fleet.</p>
      </PageHeader>
      <ErrorBanner message={error} />

      <div className="page-stack">
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
              <div className="data-table-wrap" data-pending={isPending ? "" : undefined}>
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
                          title="Sort by player (within each instance)"
                        >
                          Player{sortArrow("player")}
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
                    {pagedPending.map((r, i) => {
                      const prev = i > 0 ? pagedPending[i - 1] : null;
                      const newGroup = !prev || prev.instance_id !== r.instance_id;
                      return (
                        <Fragment key={r.task_id}>
                          {newGroup ? (
                            <tr className="queue-group-row">
                              <td colSpan={9}>
                                <span className="queue-group-row__inner">
                                  <Link href={instanceHref(r.instance_id)}>
                                    {r.instance_id}
                                  </Link>
                                  {r.blocked ? (
                                    <span className="status-pill pill-danger">
                                      Blocked · {r.blocked_reason || "device offline"}
                                    </span>
                                  ) : null}
                                </span>
                              </td>
                            </tr>
                          ) : null}
                          <tr
                            className={
                              r.blocked
                                ? "queue-row-blocked"
                                : r.overdue
                                  ? "queue-row-overdue"
                                  : undefined
                            }
                          >
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
                              <Link
                                href={playerStateHref(r.player_id, {
                                  instanceId: r.instance_id,
                                })}
                              >
                                {r.player_id}
                              </Link>
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
                        </Fragment>
                      );
                    })}
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
                pending={optimisticPending}
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

        {optimisticPending.length > 0 ? (
          <div className="toolbar toolbar--spaced">
            <AppListbox
              inline
              label="Task"
              value={pick}
              onChange={setPick}
              options={optimisticPending.map((r) => ({
                value: r.task_id,
                label: `${r.scenario} · ${r.instance_id}`,
              }))}
              minWidth={280}
            />
            <Button pending={busy} onClick={onRunNow}>
              Run now
            </Button>
            <Button
              pending={busy && selected.size > 0}
              disabled={!selected.size}
              onClick={onDelete}
            >
              Delete selected ({selected.size})
            </Button>
            {(data?.pending_blocked_count ?? 0) > 0 ? (
              <Button
                variant="danger"
                pending={busy}
                onClick={onPurgeBlocked}
                title="Remove all pending tasks of instances whose device is offline"
              >
                Purge blocked ({data?.pending_blocked_count})
              </Button>
            ) : null}
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
      </div>
    </>
  );
}
