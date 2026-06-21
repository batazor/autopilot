"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import "@svar-ui/react-gantt/all.css";
import type { CalendarEvent } from "@/lib/calendar-api";

// SVAR Gantt touches the DOM on import — load client-only to avoid SSR errors.
const Gantt = dynamic(
  () => import("@svar-ui/react-gantt").then((m) => ({ default: m.Gantt })),
  { ssr: false },
);

/**
 * The schedule is stored in UTC. The Gantt lays bars out by a Date's *local*
 * fields, so build a Date whose local wall-clock equals the event's UTC
 * wall-clock — otherwise a viewer's timezone would shift day boundaries.
 */
function utcWallToLocal(iso: string): Date {
  const u = new Date(iso);
  return new Date(
    u.getUTCFullYear(),
    u.getUTCMonth(),
    u.getUTCDate(),
    u.getUTCHours(),
    u.getUTCMinutes(),
  );
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function useGanttTheme(): string {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    const read = () =>
      setDark(document.documentElement.getAttribute("data-theme") !== "light");
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);
  return dark ? "wx-willow-dark-theme" : "wx-willow-theme";
}

export function CalendarGantt({
  events,
  now,
}: {
  events: CalendarEvent[];
  now: string;
}) {
  const theme = useGanttTheme();

  const { tasks, scales, start, end, highlightTime } = useMemo(() => {
    const today = utcWallToLocal(now);
    const tasks = events.map((e, i) => ({
      id: i + 1,
      text: e.name,
      start: utcWallToLocal(e.start),
      end: utcWallToLocal(e.end),
      type: "task" as const,
    }));
    const starts = tasks.map((t) => t.start.getTime());
    const ends = tasks.map((t) => t.end.getTime());
    // Pad the window by a day each side and always include today.
    const lo = new Date(Math.min(today.getTime(), ...starts));
    const hi = new Date(Math.max(today.getTime(), ...ends));
    lo.setDate(lo.getDate() - 1);
    hi.setDate(hi.getDate() + 1);
    return {
      tasks,
      scales: [
        {
          unit: "month" as const,
          step: 1,
          format: (d: Date) =>
            d.toLocaleString("en-US", { month: "long", year: "numeric" }),
        },
        {
          unit: "day" as const,
          step: 1,
          format: (d: Date) =>
            `${d.toLocaleString("en-GB", { weekday: "short" })} ${d.getDate()}`,
        },
      ],
      start: lo,
      end: hi,
      highlightTime: (date: Date, unit: string) =>
        unit === "day" && sameDay(date, today) ? "cal-gantt-today" : "",
    };
  }, [events, now]);

  if (events.length === 0) return null;

  return (
    <div className={theme} style={{ height: Math.min(80 + tasks.length * 38, 520) }}>
      <Gantt
        tasks={tasks}
        scales={scales}
        start={start}
        end={end}
        cellWidth={56}
        cellHeight={34}
        readonly
        columns={[{ id: "text", header: "Event", width: 180, flexgrow: 1 }]}
        highlightTime={highlightTime}
      />
    </div>
  );
}
