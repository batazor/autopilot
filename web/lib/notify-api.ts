// Typed client for the notification-monitor API (FastAPI router at /api/notify/*).
// Kept separate from lib/api.ts so the notify page stays self-contained.
import { ApiError } from "./api";

async function nfetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(path, res.status, text);
  }
  return res.json() as Promise<T>;
}

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export type NotifyGame = { id: string; name: string; packages: string[] };

export type NotifyRedisStatus = {
  url: string;
  connected: boolean;
  last_error: string | null;
  published_count: number;
  last_publish_ts: string | null;
};

export type NotifyMonitorStatus = {
  running: boolean;
  cycles: number;
  last_poll_ts: number | null;
  last_poll_human: string | null;
  last_cycle_count: number;
  last_error: string | null;
  poll_interval: number;
  seen_cache: number;
  redis: NotifyRedisStatus;
};

export type NotifyCounts = {
  players: number;
  active_players: number;
  patterns: number;
  events: number;
  unrecognized: number;
};

export type NotifyStatus = {
  monitor: NotifyMonitorStatus;
  counts: NotifyCounts;
  adb_devices: string[];
};

export type NotifyEvent = {
  id: number;
  game: string;
  player: string;
  event_type: string;
  raw_text: string;
  timestamp: string;
};

export type NotifyPlayer = {
  id: number;
  nickname: string;
  game: string;
  active: number;
  created_at: string;
};

export type NotifyPattern = {
  id: number;
  game: string;
  pattern_regex: string;
  event_type: string;
  description: string;
  active: number;
};

export type NotifyUnrecognized = {
  id: number;
  game: string;
  raw_text: string;
  timestamp: string;
  reviewed: number;
};

export type NotifySettings = {
  poll_interval?: string;
  adb_serial?: string;
  adb_path?: string;
  monitor_enabled?: string;
};

export type PatternTestResult = {
  ok: boolean;
  matched?: boolean;
  match?: string | null;
  groups?: Record<string, string>;
  error?: string;
};

// --- meta / status ---
export const fetchNotifyGames = () => nfetch<NotifyGame[]>("/api/notify/games");
export const fetchNotifyStatus = () => nfetch<NotifyStatus>("/api/notify/status");
export const notifyPollNow = () =>
  nfetch<{ ok: boolean; summary: Record<string, number> }>("/api/notify/poll", jsonInit("POST"));
export const notifySetMonitor = (action: "start" | "stop") =>
  nfetch<{ ok: boolean; running: boolean }>(`/api/notify/monitor/${action}`, jsonInit("POST"));

// --- events ---
export const fetchNotifyEvents = (game?: string) =>
  nfetch<NotifyEvent[]>(`/api/notify/events?limit=100${game ? `&game=${encodeURIComponent(game)}` : ""}`);

// --- players ---
export const fetchNotifyPlayers = () => nfetch<NotifyPlayer[]>("/api/notify/players");
export const addNotifyPlayer = (nickname: string, game: string) =>
  nfetch<{ ok: boolean; id: number }>("/api/notify/players", jsonInit("POST", { nickname, game }));
export const setNotifyPlayerActive = (id: number, active: boolean) =>
  nfetch<{ ok: boolean }>(`/api/notify/players/${id}`, jsonInit("PATCH", { active }));
export const deleteNotifyPlayer = (id: number) =>
  nfetch<{ ok: boolean }>(`/api/notify/players/${id}`, jsonInit("DELETE"));

// --- patterns ---
export const fetchNotifyPatterns = (game?: string) =>
  nfetch<NotifyPattern[]>(`/api/notify/patterns${game ? `?game=${encodeURIComponent(game)}` : ""}`);
export const addNotifyPattern = (p: {
  game: string;
  event_type: string;
  pattern_regex: string;
  description?: string;
}) => nfetch<{ ok: boolean; id: number }>("/api/notify/patterns", jsonInit("POST", p));
export const updateNotifyPattern = (
  id: number,
  fields: Partial<Pick<NotifyPattern, "game" | "event_type" | "pattern_regex" | "description">> & {
    active?: boolean;
  },
) => nfetch<{ ok: boolean }>(`/api/notify/patterns/${id}`, jsonInit("PATCH", fields));
export const deleteNotifyPattern = (id: number) =>
  nfetch<{ ok: boolean }>(`/api/notify/patterns/${id}`, jsonInit("DELETE"));
export const testNotifyPattern = (pattern_regex: string, sample_text: string) =>
  nfetch<PatternTestResult>("/api/notify/patterns/test", jsonInit("POST", { pattern_regex, sample_text }));

// --- unrecognized ---
export const fetchNotifyUnrecognized = (includeReviewed: boolean) =>
  nfetch<NotifyUnrecognized[]>(`/api/notify/unrecognized?include_reviewed=${includeReviewed}`);
export const reviewNotifyUnrecognized = (id: number) =>
  nfetch<{ ok: boolean }>(`/api/notify/unrecognized/${id}/review`, jsonInit("POST"));
export const promoteNotifyUnrecognized = (
  id: number,
  body: { event_type: string; pattern_regex: string; description?: string },
) => nfetch<{ ok: boolean; pattern_id: number }>(`/api/notify/unrecognized/${id}/promote`, jsonInit("POST", body));

// --- settings ---
export const fetchNotifySettings = () => nfetch<NotifySettings>("/api/notify/settings");
export const updateNotifySettings = (body: {
  poll_interval?: number;
  adb_serial?: string;
  adb_path?: string;
  monitor_enabled?: boolean;
}) => nfetch<{ ok: boolean; settings: NotifySettings }>("/api/notify/settings", jsonInit("PUT", body));
