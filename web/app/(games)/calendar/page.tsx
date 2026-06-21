"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarGantt } from "@/components/calendar/CalendarGantt";
import { PageHeader } from "@/components/PageHeader";
import {
  fetchCalendar,
  type CalendarStateView,
  type CalendarView,
} from "@/lib/calendar-api";

const REFRESH_MS = 60_000;

function fmtUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    timeZone: "UTC",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function fmtAgo(ts: number | null): string {
  if (!ts) return "never read";
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 90) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86_400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86_400)}d ago`;
}

function fmtIn(hours: number): string {
  if (hours < 1) return `in ${Math.round(hours * 60)}m`;
  if (hours < 48) return `in ${Math.round(hours)}h`;
  return `in ${Math.round(hours / 24)}d`;
}

function StateCard({ s, now }: { s: CalendarStateView; now: string }) {
  return (
    <section className="rounded-2xl border border-wos-border-subtle bg-wos-surface p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-wos-text">
          State {s.state}
        </h2>
        <span className="text-xs text-wos-text-muted">
          {s.event_count} events · read {fmtAgo(s.updated_at)}
        </span>
      </div>

      {s.active.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-wos-text-muted">
            Live now
          </div>
          <div className="flex flex-wrap gap-1.5">
            {s.active.map((e) => (
              <span key={e.name} className="pill-live" title={`ends ${fmtUtc(e.ends)} UTC`}>
                {e.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {s.upcoming.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-wos-text-muted">
            Upcoming
          </div>
          <ul className="space-y-0.5 text-sm text-wos-text-secondary">
            {s.upcoming.slice(0, 6).map((e) => (
              <li key={`${e.name}-${e.starts}`} className="flex justify-between gap-3">
                <span className="truncate">{e.name}</span>
                <span className="shrink-0 text-wos-text-muted">
                  {fmtIn(e.in_hours)} · {fmtUtc(e.starts)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-wos-text-muted">
        Timeline
      </div>
      <CalendarGantt events={s.events} now={now} />
    </section>
  );
}

export default function CalendarPage() {
  const [view, setView] = useState<CalendarView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchCalendar(7)
      .then((v) => {
        setView(v);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const states = view?.states ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="Event calendar">
        Per-state event schedule read off the in-game calendar (times in UTC).
        Refreshed by the <code>refresh_calendar</code> bot scenario.
      </PageHeader>

      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {!view && !error && (
        <div className="text-sm text-wos-text-muted">Loading…</div>
      )}

      {view && states.length === 0 && (
        <div className="rounded-2xl border border-wos-border-subtle bg-wos-surface p-6 text-sm text-wos-text-muted">
          No schedule read yet. Run the <code>refresh_calendar</code> scenario on a
          bot to read the in-game calendar for its state.
        </div>
      )}

      <div className="grid gap-4">
        {states.map((s) => (
          <StateCard key={s.state} s={s} now={view?.now ?? new Date().toISOString()} />
        ))}
      </div>
    </div>
  );
}
