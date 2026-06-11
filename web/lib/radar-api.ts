// Typed client for the radar API (FastAPI router at /api/radar/*).
// Single home for the shared types — manifest, run summary, tiles metadata and
// SSE events — used by the /radar page components.
import { ApiError } from "./api";

async function rfetch<T>(path: string, init?: RequestInit): Promise<T> {
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

export type RadarRunSummary = {
  run_id: string;
  started_at: number;
  frames_done: number;
  frames_total: number;
  unstable_count: number;
  duration_s: number;
  has_tiles: boolean;
  has_map: boolean;
};

export type RadarGridCell = { ix: number; iy: number };

export type RadarManifestFrame = {
  ix: number;
  iy: number;
  tap_px: [number, number];
  planned_game_xy: [number, number];
  file: string;
  unstable: boolean;
  ts: number;
};

export type RadarManifest = {
  config: Record<string, unknown>;
  grid?: { count: number; points?: RadarGridCell[] };
  frames: Record<string, RadarManifestFrame>;
};

export type RadarTilesMeta = {
  width: number;
  height: number;
  min_zoom: number;
  max_zoom: number;
  tile_size: number;
};

export type RadarActiveScan = {
  run_id: string;
  status: string;
  done: number;
  total: number;
  grid?: RadarGridCell[];
};

export type RadarEvent =
  | { type: "scan_active"; active: RadarActiveScan | null }
  | { type: "scan_started"; run_id: string; total_frames: number; grid: RadarGridCell[] }
  | {
      type: "frame_done";
      run_id: string;
      ix: number;
      iy: number;
      unstable: boolean;
      done: number;
      total: number;
    }
  | { type: "scan_finished"; run_id: string; duration_s: number }
  | { type: "scan_failed"; run_id: string; error: string }
  | { type: "tiles_ready"; run_id: string };

export const RADAR_EVENTS_URL = "/api/radar/events";

export const radarTileUrl = (runId: string) =>
  `/api/radar/runs/${encodeURIComponent(runId)}/tiles/{z}/{x}/{y}`;

export function fetchRadarRuns(): Promise<RadarRunSummary[]> {
  return rfetch("/api/radar/runs");
}

export function fetchRadarActive(): Promise<{ active: RadarActiveScan | null }> {
  return rfetch("/api/radar/active");
}

/** Whether the current license unlocks Radar (R4 feature). */
export function fetchRadarAccess(): Promise<{ licensed: boolean; tier: string }> {
  return rfetch("/api/radar/access");
}
// (imported by the radar page to gate the UI)

export function fetchRadarManifest(runId: string): Promise<RadarManifest> {
  return rfetch(`/api/radar/runs/${encodeURIComponent(runId)}/manifest`);
}

/** Tiles metadata, or null when the run has no tile pyramid yet (404). */
export async function fetchRadarTilesMeta(runId: string): Promise<RadarTilesMeta | null> {
  try {
    return await rfetch<RadarTilesMeta>(
      `/api/radar/runs/${encodeURIComponent(runId)}/tiles.json`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export type RadarScanStart = {
  run_id: string;
  instance_id: string;
  total_frames: number;
  grid: RadarGridCell[];
};

export function startRadarScan(): Promise<RadarScanStart> {
  return rfetch("/api/radar/scan", jsonInit("POST", {}));
}

export function buildRadarTiles(runId: string): Promise<{ run_id: string; status: string }> {
  return rfetch(`/api/radar/runs/${encodeURIComponent(runId)}/tiles`, jsonInit("POST"));
}

export function deleteRadarRun(runId: string): Promise<{ run_id: string; status: string }> {
  return rfetch(`/api/radar/runs/${encodeURIComponent(runId)}`, jsonInit("DELETE"));
}
