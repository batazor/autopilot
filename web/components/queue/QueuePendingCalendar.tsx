"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "@svar-ui/react-calendar/all.css";
import type { CalendarEvent, EventContext, StoreActions } from "@svar-ui/calendar-store";
import { CopyButton } from "@/components/CopyButton";
import {
  CooperativePill,
  HistoryOutcomePill,
  PendingSchedulePill,
  PriorityBadge,
  QueueTaskActions,
  historyDebugPayload,
  pendingDebugPayload,
} from "@/components/queue/QueueVisuals";
import { instanceHref, playerStateHref } from "@/lib/fleet-links";
import {
  overlayTestHref,
  regionFromQueueHistory,
  regionFromQueuePending,
} from "@/lib/debug-links";
import { removeQueueTasks, rescheduleQueueTask, runQueueTaskNow } from "@/lib/api";
import { QueueCreateTaskDialog } from "@/components/queue/QueueCreateTaskDialog";
import type { QueueHistoryRow, QueuePendingRow } from "@/lib/types";

const Calendar = dynamic(
  () => import("@svar-ui/react-calendar").then((m) => ({ default: m.Calendar })),
  {
    ssr: false,
    loading: () => <div className="muted">Loading calendar…</div>,
  },
);

const ContextMenu = dynamic(
  () =>
    import("@svar-ui/react-calendar").then((m) => ({ default: m.ContextMenu })),
  { ssr: false },
);

const WillowDark = dynamic(
  () => import("@svar-ui/react-calendar").then((m) => ({ default: m.WillowDark })),
  { ssr: false },
);

const DEFAULT_DURATION_S = 60;
const MIN_DURATION_S = 15;
const MAX_DURATION_S = 30 * 60;

type CalendarKey = string; // `${instance_id}|${player_id}`
type EventKind = "pending" | "history";

type QueueCalendarEvent = CalendarEvent & {
  calendar: CalendarKey;
  kind: EventKind;
  task_id: string;
  // For history events we need the finished_at to disambiguate retries of the same task_id.
  history_finished_at?: number;
};

const PALETTE = [
  "#0ea5e9", "#22c55e", "#f97316", "#a855f7", "#ec4899",
  "#eab308", "#14b8a6", "#ef4444", "#6366f1", "#84cc16",
  "#06b6d4", "#f43f5e", "#8b5cf6", "#10b981", "#f59e0b",
];

function paletteColor(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i += 1) {
    h = (h * 31 + key.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(h) % PALETTE.length];
}

function calendarKey(instanceId: string, playerId: string): CalendarKey {
  return `${instanceId}|${playerId || "_"}`;
}

function calendarLabel(key: CalendarKey): string {
  const [inst, player] = key.split("|");
  return player && player !== "_" ? `${inst} / ${player}` : `${inst}`;
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

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function pendingEventId(taskId: string): string {
  return `pending:${taskId}`;
}

function historyEventId(taskId: string, finishedAt: number): string {
  return `history:${taskId}:${finishedAt}`;
}

function parseEventId(
  id: string,
): { kind: EventKind; taskId: string; finishedAt?: number } | null {
  if (id.startsWith("pending:")) {
    return { kind: "pending", taskId: id.slice("pending:".length) };
  }
  if (id.startsWith("history:")) {
    const rest = id.slice("history:".length);
    const lastColon = rest.lastIndexOf(":");
    if (lastColon < 0) return null;
    const taskId = rest.slice(0, lastColon);
    const finishedAt = Number(rest.slice(lastColon + 1));
    if (!Number.isFinite(finishedAt)) return null;
    return { kind: "history", taskId, finishedAt };
  }
  return null;
}

function buildPendingEvents(
  pending: QueuePendingRow[],
  avg: Map<string, number>,
): QueueCalendarEvent[] {
  return pending.map((r) => {
    const startMs = r.scheduled_at * 1000;
    const durSec = clamp(
      avg.get(r.scenario_key) ?? DEFAULT_DURATION_S,
      MIN_DURATION_S,
      MAX_DURATION_S,
    );
    return {
      id: pendingEventId(r.task_id),
      task_id: r.task_id,
      kind: "pending",
      start: new Date(startMs),
      end: new Date(startMs + durSec * 1000),
      text: `${r.scenario} · ${r.player_id || "(device)"}`,
      calendar: calendarKey(r.instance_id, r.player_id),
    };
  });
}

function buildHistoryEvents(history: QueueHistoryRow[]): QueueCalendarEvent[] {
  return history
    .filter((h) => h.started_at > 0 && h.finished_at > 0)
    .map((h) => {
      const startMs = h.started_at * 1000;
      const endMs = Math.max(h.finished_at * 1000, startMs + MIN_DURATION_S * 1000);
      return {
        id: historyEventId(h.task_id, h.finished_at),
        task_id: h.task_id,
        kind: "history",
        history_finished_at: h.finished_at,
        start: new Date(startMs),
        end: new Date(endMs),
        text: `${h.scenario} · ${h.player_id || "(device)"}`,
        calendar: calendarKey(h.instance_id, h.player_id),
      };
    });
}

function collectCalendars(events: QueueCalendarEvent[]): CalendarKey[] {
  const set = new Set<CalendarKey>();
  for (const e of events) set.add(e.calendar);
  return [...set].sort();
}

type UpdateEventPayload = StoreActions["update-event"];
type SelectEventPayload = StoreActions["select-event"];

type CalendarApi = {
  exec: (action: string, payload: Record<string, unknown>) => void;
  getEvent: (id: string | number) => CalendarEvent | undefined;
  intercept?: (
    name: string,
    handler: (payload: unknown) => boolean | void | Promise<boolean>,
  ) => void;
};

// The react-calendar type defs only allow `string[]` but the underlying store
// supports the full ViewConfig object form (id + per-section ui overrides).
const VIEWS_CONFIG = [
  { id: "day", sections: { timeGrid: { ui: { nowLine: true } } } },
  { id: "week", sections: { timeGrid: { ui: { nowLine: true } } } },
  "month",
] as unknown as string[];

const MENU_OPTIONS = [
  { id: "run-now", text: "Run now" },
  { id: "shift-5m", text: "Shift +5 min" },
  { id: "shift-1h", text: "Shift +1 hour" },
  { id: "delete", text: "Delete" },
];

export function QueuePendingCalendar({
  pending,
  history,
  onReschedule,
  onRunNow,
  onDelete,
  onCreated,
  onError,
}: {
  pending: QueuePendingRow[];
  history: QueueHistoryRow[];
  onReschedule?: (taskId: string, scheduledAt: number) => void;
  onRunNow?: (taskId: string) => void;
  onDelete?: (taskId: string) => void;
  onCreated?: (task: { task_id: string; scheduled_at: number }) => void;
  onError?: (message: string) => void;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const avgDuration = useMemo(() => avgDurationByScenario(history), [history]);
  const pendingEvents = useMemo(
    () => buildPendingEvents(pending, avgDuration),
    [pending, avgDuration],
  );

  const [showHistory, setShowHistory] = useState(false);
  const historyEvents = useMemo(
    () => (showHistory ? buildHistoryEvents(history) : []),
    [showHistory, history],
  );
  const allEvents = useMemo(
    () => [...pendingEvents, ...historyEvents],
    [pendingEvents, historyEvents],
  );
  const calendars = useMemo(() => collectCalendars(allEvents), [allEvents]);

  const pendingById = useMemo(() => {
    const m = new Map<string, QueuePendingRow>();
    for (const r of pending) m.set(r.task_id, r);
    return m;
  }, [pending]);

  const historyById = useMemo(() => {
    const m = new Map<string, QueueHistoryRow>();
    for (const h of history) m.set(`${h.task_id}:${h.finished_at}`, h);
    return m;
  }, [history]);

  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const selected = useMemo(() => {
    if (!selectedEventId) return null;
    const parsed = parseEventId(selectedEventId);
    if (!parsed) return null;
    if (parsed.kind === "pending") {
      const row = pendingById.get(parsed.taskId);
      return row ? { kind: "pending" as const, row } : null;
    }
    const row = historyById.get(`${parsed.taskId}:${parsed.finishedAt}`);
    return row ? { kind: "history" as const, row } : null;
  }, [selectedEventId, pendingById, historyById]);

  // Deep-link: hydrate selection from ?task= on first load.
  const hydratedFromUrl = useRef(false);
  useEffect(() => {
    if (hydratedFromUrl.current) return;
    const id = searchParams.get("task");
    if (!id) return;
    if (pendingById.has(id)) {
      setSelectedEventId(pendingEventId(id));
      hydratedFromUrl.current = true;
    } else if (pendingById.size > 0) {
      // Pending data is loaded but the task isn't there — give up hydrating.
      hydratedFromUrl.current = true;
    }
  }, [searchParams, pendingById]);

  // Drop stale selection if the underlying row vanished.
  useEffect(() => {
    if (selectedEventId && !selected) {
      setSelectedEventId(null);
    }
  }, [selectedEventId, selected]);

  const writeUrl = useCallback(
    (taskId: string | null) => {
      const params = new URLSearchParams(window.location.search);
      const current = params.get("task");
      if (taskId) {
        if (current === taskId) return;
        params.set("task", taskId);
      } else {
        if (!current) return;
        params.delete("task");
      }
      const qs = params.toString();
      const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
      router.replace(url, { scroll: false });
    },
    [router],
  );

  const [actionBusy, setActionBusy] = useState(false);

  const [hidden, setHidden] = useState<Set<CalendarKey>>(new Set());
  useEffect(() => {
    setHidden((prev) => {
      const next = new Set<CalendarKey>();
      for (const k of prev) if (calendars.includes(k)) next.add(k);
      return next.size === prev.size ? prev : next;
    });
  }, [calendars]);

  const visibleEvents = useMemo(
    () => allEvents.filter((e) => !hidden.has(e.calendar)),
    [allEvents, hidden],
  );

  const initialDate = useMemo(() => {
    if (!allEvents.length) return new Date();
    let earliest = allEvents[0].start.getTime();
    for (const e of allEvents) {
      if (e.start.getTime() < earliest) earliest = e.start.getTime();
    }
    return new Date(earliest);
  }, [allEvents]);

  const eventCss = useCallback(
    (ctx: EventContext): string => {
      const ev = ctx.event as QueueCalendarEvent;
      const colorIdx = PALETTE.indexOf(paletteColor(ev.calendar));
      const parts: string[] = [];
      if (ev.kind === "pending") {
        parts.push(`queue-cal-c${colorIdx}`);
      } else {
        const historyRow = historyById.get(
          `${ev.task_id}:${ev.history_finished_at ?? 0}`,
        );
        parts.push(
          historyRow?.success === false
            ? "queue-cal-event--history-fail"
            : "queue-cal-event--history-ok",
        );
      }
      if (selectedEventId && ev.id === selectedEventId) {
        parts.push("queue-cal-event--selected");
      }
      return parts.join(" ");
    },
    [historyById, selectedEventId],
  );

  const callbacksRef = useRef({ onReschedule, onError });
  useEffect(() => {
    callbacksRef.current = { onReschedule, onError };
  }, [onReschedule, onError]);

  const onUpdateEvent = useCallback((ev: UpdateEventPayload) => {
    const newStart = ev.event?.start;
    if (!(newStart instanceof Date)) return;
    const parsed = parseEventId(String(ev.id));
    if (!parsed || parsed.kind !== "pending") return; // history is read-only
    const taskId = parsed.taskId;
    const scheduledAt = newStart.getTime() / 1000;
    rescheduleQueueTask(taskId, scheduledAt)
      .then(() => callbacksRef.current.onReschedule?.(taskId, scheduledAt))
      .catch((err) =>
        callbacksRef.current.onError?.(
          err instanceof Error ? err.message : String(err),
        ),
      );
  }, []);

  const onSelectEvent = useCallback(
    (payload: SelectEventPayload) => {
      const nextId = payload.id == null ? null : String(payload.id);
      setSelectedEventId(nextId);
      const parsed = nextId ? parseEventId(nextId) : null;
      writeUrl(parsed?.kind === "pending" ? parsed.taskId : null);
    },
    [writeUrl],
  );

  const handleClose = useCallback(() => {
    setSelectedEventId(null);
    writeUrl(null);
  }, [writeUrl]);

  const reportError = useCallback(
    (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      onError?.(msg);
    },
    [onError],
  );

  const runNowPending = useCallback(
    async (taskId: string) => {
      if (actionBusy) return;
      setActionBusy(true);
      try {
        await runQueueTaskNow(taskId);
        onRunNow?.(taskId);
      } catch (err) {
        reportError(err);
      } finally {
        setActionBusy(false);
      }
    },
    [actionBusy, onRunNow, reportError],
  );

  const deletePending = useCallback(
    async (taskId: string) => {
      if (actionBusy) return;
      setActionBusy(true);
      try {
        await removeQueueTasks([taskId]);
        onDelete?.(taskId);
        setSelectedEventId((prev) =>
          prev === pendingEventId(taskId) ? null : prev,
        );
        writeUrl(null);
      } catch (err) {
        reportError(err);
      } finally {
        setActionBusy(false);
      }
    },
    [actionBusy, onDelete, reportError, writeUrl],
  );

  const shiftPending = useCallback(
    async (row: QueuePendingRow, deltaSec: number) => {
      if (actionBusy) return;
      setActionBusy(true);
      try {
        const next = row.scheduled_at + deltaSec;
        await rescheduleQueueTask(row.task_id, next);
        callbacksRef.current.onReschedule?.(row.task_id, next);
      } catch (err) {
        reportError(err);
      } finally {
        setActionBusy(false);
      }
    },
    [actionBusy, reportError],
  );

  const toggleCalendar = (key: CalendarKey) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Create-task dialog state. Opened by clicking an empty calendar slot or the
  // "Add task" button. The clicked slot's start time pre-fills the dialog.
  const [createOpen, setCreateOpen] = useState(false);
  const [createStart, setCreateStart] = useState<number | null>(null);

  const openCreateAt = useCallback((startEpochSec: number | null) => {
    setCreateStart(startEpochSec);
    setCreateOpen(true);
  }, []);

  // Stash the latest opener so the intercept handler (registered once in init)
  // doesn't capture a stale closure when state updates re-render the component.
  const openCreateRef = useRef(openCreateAt);
  useEffect(() => {
    openCreateRef.current = openCreateAt;
  }, [openCreateAt]);

  // Calendar api — captured from the init callback so we can drive navigation.
  const [calendarApi, setCalendarApi] = useState<CalendarApi | null>(null);
  const onCalendarInit = useCallback((api: unknown) => {
    const typed = api as CalendarApi;
    setCalendarApi(typed);
    // Intercept the calendar's "add-event" action (fired when the user drags
    // or clicks an empty slot, and by the toolbar's "+" button). Returning
    // false stops the default add so we don't add a phantom event to the
    // store — instead we surface our own modal with the clicked time.
    typed.intercept?.("add-event", (payload) => {
      const ev = (payload as { event?: { start?: unknown } } | undefined)?.event;
      const start = ev?.start;
      const epoch =
        start instanceof Date ? start.getTime() / 1000 : Date.now() / 1000;
      openCreateRef.current(epoch);
      return false;
    });
  }, []);

  const jumpToNow = useCallback(() => {
    calendarApi?.exec("navigate-to", { date: new Date() });
  }, [calendarApi]);

  // Context-menu wiring. resolver() runs when the user right-clicks an event;
  // we stash the row so onClick can act on it.
  const ctxRowRef = useRef<QueuePendingRow | null>(null);
  const resolver = useCallback(
    (id: string | number) => {
      const parsed = parseEventId(String(id));
      if (!parsed || parsed.kind !== "pending") {
        ctxRowRef.current = null;
        return null;
      }
      const row = pendingById.get(parsed.taskId);
      ctxRowRef.current = row ?? null;
      return row ? { id } : null;
    },
    [pendingById],
  );

  const onMenuClick = useCallback(
    (payload: { action?: { id?: string } }) => {
      const actionId = payload?.action?.id;
      const row = ctxRowRef.current;
      if (!actionId || !row) return;
      switch (actionId) {
        case "run-now":
          void runNowPending(row.task_id);
          break;
        case "delete":
          void deletePending(row.task_id);
          break;
        case "shift-5m":
          void shiftPending(row, 5 * 60);
          break;
        case "shift-1h":
          void shiftPending(row, 60 * 60);
          break;
      }
    },
    [deletePending, runNowPending, shiftPending],
  );

  if (!allEvents.length && !showHistory) {
    return (
      <div className="queue-cal-empty muted">No pending tasks to chart.</div>
    );
  }

  const calendarNode = (
    <Calendar
      events={visibleEvents}
      date={initialDate}
      view="week"
      views={VIEWS_CONFIG}
      eventCss={eventCss}
      tooltip={EventTooltip}
      onUpdateEvent={onUpdateEvent}
      onSelectEvent={onSelectEvent}
      init={onCalendarInit}
    />
  );

  return (
    <div className="queue-cal">
      <div className="queue-cal__legend">
        <div className="queue-cal__legend-chips">
          {calendars.map((key) => {
            const off = hidden.has(key);
            const color = paletteColor(key);
            return (
              <button
                key={key}
                type="button"
                className={`queue-cal__chip${off ? " queue-cal__chip--off" : ""}`}
                onClick={() => toggleCalendar(key)}
                title={off ? "Show this calendar" : "Hide this calendar"}
              >
                <span
                  className="queue-cal__chip-swatch"
                  style={{
                    backgroundColor: off ? "transparent" : color,
                    borderColor: color,
                  }}
                />
                {calendarLabel(key)}
              </button>
            );
          })}
        </div>
        <div className="queue-cal__legend-controls">
          <label
            className="queue-cal__toggle"
            title="Overlay finished runs from the history table"
          >
            <input
              type="checkbox"
              checked={showHistory}
              onChange={(e) => setShowHistory(e.target.checked)}
            />
            Show history
          </label>
          <button
            type="button"
            className="queue-cal__now-btn"
            onClick={() => openCreateAt(null)}
            title="Schedule a new task"
          >
            + Add task
          </button>
          <button
            type="button"
            className="queue-cal__now-btn"
            onClick={jumpToNow}
            disabled={!calendarApi}
            title="Jump to current time"
          >
            Now
          </button>
        </div>
      </div>
      <div className="queue-cal__body">
        <div className="queue-cal__chart">
          <WillowDark>
            <ContextMenu
              api={calendarApi}
              options={MENU_OPTIONS}
              resolver={resolver}
              onClick={onMenuClick}
            >
              {calendarNode}
            </ContextMenu>
          </WillowDark>
        </div>
        {selected?.kind === "pending" ? (
          <QueueCalendarPendingCard
            row={selected.row}
            avgDuration={avgDuration.get(selected.row.scenario_key)}
            busy={actionBusy}
            onClose={handleClose}
            onRunNow={() => void runNowPending(selected.row.task_id)}
            onDelete={() => void deletePending(selected.row.task_id)}
            onShift={(deltaSec) => void shiftPending(selected.row, deltaSec)}
          />
        ) : selected?.kind === "history" ? (
          <QueueCalendarHistoryCard
            row={selected.row}
            onClose={handleClose}
          />
        ) : null}
      </div>
      <QueueCreateTaskDialog
        open={createOpen}
        defaultScheduledAt={createStart}
        onClose={() => setCreateOpen(false)}
        onCreated={(task) => {
          onCreated?.(task);
        }}
        onError={(msg) => onError?.(msg)}
      />
    </div>
  );
}

function EventTooltip({ event }: { event: CalendarEvent }) {
  const ev = event as QueueCalendarEvent;
  const startMs = ev.start.getTime();
  const endMs = ev.end.getTime();
  const durSec = Math.max(0, Math.round((endMs - startMs) / 1000));
  const time = new Date(startMs).toLocaleString();
  const kindLabel =
    ev.kind === "history" ? "History" : "Pending";
  return (
    <div className="queue-cal__tooltip">
      <div className="queue-cal__tooltip-title">{String(ev.text ?? "")}</div>
      <div className="queue-cal__tooltip-meta">
        <span className={`queue-cal__tooltip-kind queue-cal__tooltip-kind--${ev.kind}`}>
          {kindLabel}
        </span>
        <span>{time}</span>
        <span className="muted">{formatDuration(durSec)}</span>
      </div>
      <div className="muted queue-cal__tooltip-hint">
        {ev.kind === "pending"
          ? "Click for details · drag to reschedule · right-click for menu"
          : "Click for details"}
      </div>
    </div>
  );
}

function formatRelative(scheduledAt: number): string {
  const diffSec = scheduledAt - Date.now() / 1000;
  const abs = Math.abs(diffSec);
  const suffix = diffSec >= 0 ? "from now" : "ago";
  if (abs < 60) return `${Math.round(abs)}s ${suffix}`;
  if (abs < 3600) return `${Math.round(abs / 60)}m ${suffix}`;
  if (abs < 86400) {
    const h = Math.floor(abs / 3600);
    const m = Math.round((abs - h * 3600) / 60);
    return m ? `${h}h ${m}m ${suffix}` : `${h}h ${suffix}`;
  }
  const d = Math.floor(abs / 86400);
  const h = Math.round((abs - d * 86400) / 3600);
  return h ? `${d}d ${h}h ${suffix}` : `${d}d ${suffix}`;
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

function QueueCalendarPendingCard({
  row,
  avgDuration,
  busy,
  onClose,
  onRunNow,
  onDelete,
  onShift,
}: {
  row: QueuePendingRow;
  avgDuration: number | undefined;
  busy: boolean;
  onClose: () => void;
  onRunNow: () => void;
  onDelete: () => void;
  onShift: (deltaSec: number) => void;
}) {
  const scheduledDate = new Date(row.scheduled_at * 1000);
  const region = regionFromQueuePending(row);
  return (
    <aside className="queue-cal__detail" aria-label="Event details">
      <header className="queue-cal__detail-head">
        <div className="queue-cal__detail-titles">
          <p className="queue-cal__detail-scenario" title={row.scenario_key}>
            {row.scenario}
          </p>
          <p className="queue-cal__detail-key">{row.scenario_key}</p>
        </div>
        <button
          type="button"
          className="queue-cal__detail-close"
          onClick={onClose}
          aria-label="Close details"
          title="Close"
        >
          ×
        </button>
      </header>

      <div className="queue-cal__detail-pills">
        <PendingSchedulePill row={row} />
        <PriorityBadge priority={row.priority} />
        <CooperativePill cooperative={row.cooperative} />
      </div>

      <dl className="queue-cal__detail-meta">
        <div>
          <dt>When</dt>
          <dd>
            <div>{scheduledDate.toLocaleString()}</div>
            <div className="muted queue-cal__detail-rel">
              {formatRelative(row.scheduled_at)}
            </div>
          </dd>
        </div>
        <div>
          <dt>Instance</dt>
          <dd>
            <Link href={instanceHref(row.instance_id)}>{row.instance_id}</Link>
          </dd>
        </div>
        {row.player_id ? (
          <div>
            <dt>Player</dt>
            <dd>
              <Link
                href={playerStateHref(row.player_id, {
                  instanceId: row.instance_id,
                })}
              >
                {row.player_id}
              </Link>
            </dd>
          </div>
        ) : null}
        {row.region ? (
          <div>
            <dt>Region</dt>
            <dd className="muted">{row.region}</dd>
          </div>
        ) : null}
        <div>
          <dt>Est. duration</dt>
          <dd className="muted">{formatDuration(avgDuration ?? 0)}</dd>
        </div>
      </dl>

      <div className="queue-cal__detail-actions">
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
          disabled={busy}
          onClick={onDelete}
        >
          Delete
        </button>
      </div>

      <div className="queue-cal__detail-shift">
        <span className="muted">Shift:</span>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => onShift(5 * 60)}
        >
          +5m
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => onShift(60 * 60)}
        >
          +1h
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => onShift(-5 * 60)}
        >
          -5m
        </button>
      </div>

      <div className="queue-cal__detail-links">
        <QueueTaskActions
          instanceId={row.instance_id}
          playerId={row.player_id}
          scenarioKey={row.scenario_key}
          region={region}
          showOverlay={false}
        />
        <CopyButton
          text={pendingDebugPayload(row)}
          label="Copy debug"
          title="Copy task id, scenario key, instance"
        />
      </div>

      <p className="queue-cal__detail-hint muted">
        Tip: drag the event in the calendar to reschedule it.
      </p>
    </aside>
  );
}

function QueueCalendarHistoryCard({
  row,
  onClose,
}: {
  row: QueueHistoryRow;
  onClose: () => void;
}) {
  const finished = new Date(row.finished_at * 1000);
  const region = regionFromQueueHistory(row);
  const failedRegionHref =
    !row.success && region
      ? overlayTestHref(row.instance_id, { region })
      : null;
  return (
    <aside className="queue-cal__detail" aria-label="History details">
      <header className="queue-cal__detail-head">
        <div className="queue-cal__detail-titles">
          <p className="queue-cal__detail-scenario" title={row.scenario_key}>
            {row.scenario}
          </p>
          <p className="queue-cal__detail-key">{row.scenario_key}</p>
        </div>
        <button
          type="button"
          className="queue-cal__detail-close"
          onClick={onClose}
          aria-label="Close details"
          title="Close"
        >
          ×
        </button>
      </header>

      <div className="queue-cal__detail-pills">
        <HistoryOutcomePill success={row.success} />
        <PriorityBadge priority={row.priority} />
      </div>

      <dl className="queue-cal__detail-meta">
        <div>
          <dt>Finished</dt>
          <dd>
            <div>{finished.toLocaleString()}</div>
            <div className="muted queue-cal__detail-rel">
              {formatRelative(row.finished_at)}
            </div>
          </dd>
        </div>
        <div>
          <dt>Instance</dt>
          <dd>
            <Link href={instanceHref(row.instance_id)}>{row.instance_id}</Link>
          </dd>
        </div>
        {row.player_id ? (
          <div>
            <dt>Player</dt>
            <dd>
              <Link
                href={playerStateHref(row.player_id, {
                  instanceId: row.instance_id,
                })}
              >
                {row.player_id}
              </Link>
            </dd>
          </div>
        ) : null}
        {row.region ? (
          <div>
            <dt>Region</dt>
            <dd className="muted">{row.region}</dd>
          </div>
        ) : null}
        <div>
          <dt>Duration</dt>
          <dd className="muted">{row.duration_s.toFixed(1)}s</dd>
        </div>
        <div>
          <dt>Steps</dt>
          <dd className="muted">{row.steps || "—"}</dd>
        </div>
        {row.reason ? (
          <div>
            <dt>Reason</dt>
            <dd className="queue-cal__detail-reason">{row.reason}</dd>
          </div>
        ) : null}
      </dl>

      <div className="queue-cal__detail-links">
        {failedRegionHref ? (
          <Link
            href={failedRegionHref}
            className="queue-task-actions__link"
          >
            Open in overlay test
          </Link>
        ) : null}
        <CopyButton
          text={historyDebugPayload(row)}
          label="Copy debug"
          title="Copy task id, trace id, steps_trace"
        />
      </div>
    </aside>
  );
}
