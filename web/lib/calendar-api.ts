// Typed client for the calendar schedule API (FastAPI router at /api/calendar).

export interface CalendarEvent {
  name: string;
  state_flag: string;
  start: string; // ISO-8601 UTC
  end: string;
  active_now: boolean;
}

export interface CalendarActive {
  name: string;
  state_flag: string;
  ends: string;
}

export interface CalendarUpcoming {
  name: string;
  starts: string;
  in_hours: number;
}

export interface CalendarStateView {
  state: string;
  updated_at: number | null;
  event_count: number;
  active: CalendarActive[];
  upcoming: CalendarUpcoming[];
  events: CalendarEvent[];
}

export interface CalendarView {
  game: string;
  now: string;
  days: number;
  states: CalendarStateView[];
}

export async function fetchCalendar(days = 7): Promise<CalendarView> {
  const res = await fetch(`/api/calendar?days=${days}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`calendar: ${res.status}`);
  }
  return res.json() as Promise<CalendarView>;
}
