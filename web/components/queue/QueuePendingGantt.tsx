"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "gantt-task-react/dist/index.css";
import { ViewMode, type Task } from "gantt-task-react";
import { rescheduleQueueTask } from "@/lib/api";
import type { QueueHistoryRow, QueuePendingRow } from "@/lib/types";

const Gantt = dynamic(() => import("gantt-task-react").then((m) => m.Gantt), {
  ssr: false,
  loading: () => <div className="muted">Loading timeline…</div>,
});

const DEFAULT_DURATION_S = 60;
const MIN_DURATION_S = 15;
const MAX_DURATION_S = 30 * 60;

const VIEW_MODE_OPTIONS: { mode: ViewMode; label: string }[] = [
  { mode: ViewMode.Hour, label: "Hour" },
  { mode: ViewMode.QuarterDay, label: "6h" },
  { mode: ViewMode.HalfDay, label: "12h" },
  { mode: ViewMode.Day, label: "Day" },
  { mode: ViewMode.Week, label: "Week" },
];

const COLUMN_WIDTH: Record<ViewMode, number> = {
  [ViewMode.Hour]: 50,
  [ViewMode.QuarterDay]: 60,
  [ViewMode.HalfDay]: 70,
  [ViewMode.Day]: 70,
  [ViewMode.Week]: 90,
  [ViewMode.Month]: 110,
  [ViewMode.Year]: 130,
};

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function avgDurationByScenario(history: QueueHistoryRow[]): Map<string, number> {
  const sums = new Map<string, { total: number; count: number }>();
  for (const h of history) {
    if (!h.success) continue;
    const key = h.scenario_key || h.scenario;
    if (!key) continue;
    const acc = sums.get(key) ?? { total: 0, count: 0 };
    acc.total += h.duration_s;
    acc.count += 1;
    sums.set(key, acc);
  }
  const out = new Map<string, number>();
  for (const [k, v] of sums) {
    if (v.count > 0) out.set(k, v.total / v.count);
  }
  return out;
}

function buildTasks(
  pending: QueuePendingRow[],
  history: QueueHistoryRow[],
): Task[] {
  if (!pending.length) return [];
  const avg = avgDurationByScenario(history);

  const byInstance = new Map<string, QueuePendingRow[]>();
  for (const r of pending) {
    const arr = byInstance.get(r.instance_id) ?? [];
    arr.push(r);
    byInstance.set(r.instance_id, arr);
  }

  const result: Task[] = [];
  const sortedInstances = [...byInstance.keys()].sort();

  for (const instanceId of sortedInstances) {
    const rows = (byInstance.get(instanceId) ?? []).slice();
    rows.sort((a, b) => a.scheduled_at - b.scheduled_at);

    const projectId = `proj::${instanceId}`;
    const children: Task[] = rows.map((r) => {
      const startMs = r.scheduled_at * 1000;
      const durSec = clamp(
        avg.get(r.scenario_key) ?? DEFAULT_DURATION_S,
        MIN_DURATION_S,
        MAX_DURATION_S,
      );
      const endMs = startMs + durSec * 1000;
      const overdueStyle = r.overdue
        ? {
            backgroundColor: "#dc2626",
            backgroundSelectedColor: "#ef4444",
            progressColor: "#fca5a5",
            progressSelectedColor: "#fecaca",
          }
        : {
            backgroundColor: "#0284c7",
            backgroundSelectedColor: "#0ea5e9",
            progressColor: "#7dd3fc",
            progressSelectedColor: "#bae6fd",
          };
      return {
        id: r.task_id,
        type: "task",
        name: r.scenario,
        start: new Date(startMs),
        end: new Date(endMs),
        progress: 0,
        project: projectId,
        styles: overdueStyle,
      };
    });

    if (!children.length) continue;
    const minStart = Math.min(...children.map((t) => t.start.getTime()));
    const maxEnd = Math.max(...children.map((t) => t.end.getTime()));
    result.push({
      id: projectId,
      type: "project",
      name: instanceId,
      start: new Date(minStart),
      end: new Date(maxEnd),
      progress: 0,
      hideChildren: false,
      isDisabled: true,
      styles: {
        backgroundColor: "#1e293b",
        backgroundSelectedColor: "#334155",
        progressColor: "#475569",
        progressSelectedColor: "#64748b",
      },
    });
    result.push(...children);
  }

  return result;
}

export function QueuePendingGantt({
  pending,
  history,
  onReschedule,
  onError,
}: {
  pending: QueuePendingRow[];
  history: QueueHistoryRow[];
  onReschedule?: (taskId: string, scheduledAt: number) => void;
  onError?: (message: string) => void;
}) {
  const [viewMode, setViewMode] = useState<ViewMode>(ViewMode.Hour);
  const computedTasks = useMemo(
    () => buildTasks(pending, history),
    [pending, history],
  );
  const [tasks, setTasks] = useState<Task[]>(computedTasks);
  const draggingRef = useRef(false);

  // Resync from props when the server-side queue changes, unless the user is
  // actively dragging (which would clobber the in-flight move).
  useEffect(() => {
    if (draggingRef.current) return;
    setTasks(computedTasks);
  }, [computedTasks]);

  const handleDateChange = useCallback(
    async (task: Task): Promise<boolean> => {
      if (task.type !== "task") return false;
      const scheduledAt = task.start.getTime() / 1000;
      draggingRef.current = true;
      // Optimistic: keep the bar at the dragged location while we hit the API.
      setTasks((prev) =>
        prev.map((t) =>
          t.id === task.id ? { ...t, start: task.start, end: task.end } : t,
        ),
      );
      try {
        await rescheduleQueueTask(task.id, scheduledAt);
        onReschedule?.(task.id, scheduledAt);
        return true;
      } catch (err) {
        // Revert local state to the last known server value.
        setTasks(computedTasks);
        onError?.(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        draggingRef.current = false;
      }
    },
    [computedTasks, onReschedule, onError],
  );

  if (!tasks.length) {
    return (
      <div className="queue-gantt-empty muted">
        No pending tasks to chart.
      </div>
    );
  }

  const rowCount = tasks.length;
  const rowHeight = 34;
  const headerHeight = 50;
  const ganttHeight = Math.min(560, rowCount * rowHeight + 20);

  return (
    <div className="queue-gantt">
      <div className="queue-gantt__toolbar">
        <span className="queue-gantt__zoom-label">Zoom:</span>
        {VIEW_MODE_OPTIONS.map((opt) => (
          <button
            key={opt.mode}
            type="button"
            className={`queue-gantt__zoom${
              viewMode === opt.mode ? " queue-gantt__zoom--active" : ""
            }`}
            onClick={() => setViewMode(opt.mode)}
          >
            {opt.label}
          </button>
        ))}
        <span className="queue-gantt__hint">
          Drag a bar to reschedule.
        </span>
        <span className="queue-gantt__legend">
          <span className="queue-gantt__legend-swatch queue-gantt__legend-swatch--scheduled" />
          Scheduled
          <span className="queue-gantt__legend-swatch queue-gantt__legend-swatch--overdue" />
          Overdue
        </span>
      </div>
      <div className="queue-gantt__chart">
        <Gantt
          tasks={tasks}
          viewMode={viewMode}
          columnWidth={COLUMN_WIDTH[viewMode]}
          rowHeight={rowHeight}
          headerHeight={headerHeight}
          ganttHeight={ganttHeight}
          listCellWidth="220px"
          barFill={70}
          fontFamily="inherit"
          fontSize="12px"
          todayColor="rgba(56, 189, 248, 0.12)"
          onDateChange={handleDateChange}
        />
      </div>
    </div>
  );
}
