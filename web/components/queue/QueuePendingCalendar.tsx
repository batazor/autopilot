"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "@svar-ui/react-calendar/all.css";
import type { CalendarEvent, EventContext, StoreActions } from "@svar-ui/calendar-store";
import { rescheduleQueueTask } from "@/lib/api";
import type { QueueHistoryRow, QueuePendingRow } from "@/lib/types";

const Calendar = dynamic(
  () => import("@svar-ui/react-calendar").then((m) => ({ default: m.Calendar })),
  {
    ssr: false,
    loading: () => <div className="muted">Loading calendar…</div>,
  },
);

const WillowDark = dynamic(
  () => import("@svar-ui/react-calendar").then((m) => ({ default: m.WillowDark })),
  { ssr: false },
);

const DEFAULT_DURATION_S = 60;
const MIN_DURATION_S = 15;
const MAX_DURATION_S = 30 * 60;

type CalendarKey = string; // `${instance_id}|${player_id}`

type QueueCalendarEvent = CalendarEvent & {
  calendar: CalendarKey;
  task_id: string;
};

// Palette is keyed by string hash so the same (instance, player) keeps its
// colour across renders. Picked from Tailwind 500-shades; readable on dark bg.
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

function buildEvents(
  pending: QueuePendingRow[],
  history: QueueHistoryRow[],
): QueueCalendarEvent[] {
  const avg = avgDurationByScenario(history);
  return pending.map((r) => {
    const startMs = r.scheduled_at * 1000;
    const durSec = clamp(
      avg.get(r.scenario_key) ?? DEFAULT_DURATION_S,
      MIN_DURATION_S,
      MAX_DURATION_S,
    );
    const key = calendarKey(r.instance_id, r.player_id);
    return {
      id: r.task_id,
      task_id: r.task_id,
      start: new Date(startMs),
      end: new Date(startMs + durSec * 1000),
      text: `${r.scenario} · ${r.player_id || "(device)"}`,
      calendar: key,
    };
  });
}

function collectCalendars(events: QueueCalendarEvent[]): CalendarKey[] {
  const set = new Set<CalendarKey>();
  for (const e of events) set.add(e.calendar);
  return [...set].sort();
}

type UpdateEventPayload = StoreActions["update-event"];

export function QueuePendingCalendar({
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
  const allEvents = useMemo(() => buildEvents(pending, history), [pending, history]);
  const calendars = useMemo(() => collectCalendars(allEvents), [allEvents]);

  const [hidden, setHidden] = useState<Set<CalendarKey>>(new Set());
  useEffect(() => {
    // Drop hidden ids that no longer exist in the latest payload.
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

  const eventCss = useCallback((ctx: EventContext): string => {
    const ev = ctx.event as QueueCalendarEvent;
    const colorIdx = PALETTE.indexOf(paletteColor(ev.calendar));
    return `queue-cal-c${colorIdx}`;
  }, []);

  const callbacksRef = useRef({ onReschedule, onError });
  useEffect(() => {
    callbacksRef.current = { onReschedule, onError };
  }, [onReschedule, onError]);

  const onUpdateEvent = useCallback((ev: UpdateEventPayload) => {
    const newStart = ev.event?.start;
    if (!(newStart instanceof Date)) return;
    const taskId = String(ev.id);
    const scheduledAt = newStart.getTime() / 1000;
    rescheduleQueueTask(taskId, scheduledAt)
      .then(() => callbacksRef.current.onReschedule?.(taskId, scheduledAt))
      .catch((err) =>
        callbacksRef.current.onError?.(
          err instanceof Error ? err.message : String(err),
        ),
      );
  }, []);

  const toggleCalendar = (key: CalendarKey) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!allEvents.length) {
    return (
      <div className="queue-cal-empty muted">No pending tasks to chart.</div>
    );
  }

  return (
    <div className="queue-cal">
      <div className="queue-cal__legend">
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
                style={{ backgroundColor: off ? "transparent" : color, borderColor: color }}
              />
              {calendarLabel(key)}
            </button>
          );
        })}
      </div>
      <div className="queue-cal__chart">
        <WillowDark>
          <Calendar
            events={visibleEvents}
            date={initialDate}
            view="week"
            views={["day", "week", "month"]}
            eventCss={eventCss}
            onUpdateEvent={onUpdateEvent}
          />
        </WillowDark>
      </div>
    </div>
  );
}
