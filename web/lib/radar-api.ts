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

/** The three game views the radar can map; each tab owns one. */
export type RadarTarget = "global_map" | "main_city" | "island";

export const RADAR_TARGETS: RadarTarget[] = ["global_map", "main_city", "island"];

export const RADAR_TARGET_LABELS: Record<RadarTarget, string> = {
  global_map: "Global map",
  main_city: "Main city",
  island: "Island",
};

export type RadarRunSummary = {
  run_id: string;
  target: RadarTarget;
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

/** Game↔canvas affine (verified sidecar, else stitch-derived) for coordinate
 * readout. The inverse is precomputed server-side so the browser only mat-vecs. */
export type RadarCoordsAffine = {
  game_to_canvas_linear: [[number, number], [number, number]];
  game_to_canvas_offset: [number, number];
  canvas_to_game_linear: [[number, number], [number, number]];
  canvas_to_game_offset: [number, number];
  source: "derived" | "refit" | "corners";
  residual_tiles_median: number | null;
};

export type RadarTilesMeta = {
  width: number;
  height: number;
  min_zoom: number;
  max_zoom: number;
  tile_size: number;
  // Present once the run's origin is anchored (global_map). Absent → no readout.
  coords?: RadarCoordsAffine;
};

/** Canvas pixel → in-game kingdom coordinate via the precomputed inverse affine. */
export function canvasToGame(
  px: number,
  py: number,
  c: RadarCoordsAffine,
): [number, number] {
  const [[a, b], [d, e]] = c.canvas_to_game_linear;
  const [ox, oy] = c.canvas_to_game_offset;
  return [a * px + b * py + ox, d * px + e * py + oy];
}

/** In-game kingdom coordinate → canvas pixel (forward affine; for grid overlay). */
export function gameToCanvas(
  gx: number,
  gy: number,
  c: RadarCoordsAffine,
): [number, number] {
  const [[a, b], [d, e]] = c.game_to_canvas_linear;
  const [ox, oy] = c.game_to_canvas_offset;
  return [a * gx + b * gy + ox, d * gx + e * gy + oy];
}

export type RadarActiveScan = {
  run_id: string;
  status: string;
  done: number;
  total: number;
  target?: RadarTarget;
  grid?: RadarGridCell[];
};

// Every event carries `target` (the backend stamps it alongside run_id) so the
// page can route live progress to the right map tab.
export type RadarEvent =
  | { type: "scan_active"; active: RadarActiveScan | null }
  | {
      type: "scan_started";
      run_id: string;
      target?: RadarTarget;
      total_frames: number;
      grid: RadarGridCell[];
    }
  | {
      type: "frame_done";
      run_id: string;
      target?: RadarTarget;
      ix: number;
      iy: number;
      unstable: boolean;
      done: number;
      total: number;
    }
  | { type: "scan_finished"; run_id: string; target?: RadarTarget; duration_s: number }
  | { type: "scan_failed"; run_id: string; target?: RadarTarget; error: string }
  | { type: "map_updated"; run_id: string; target?: RadarTarget; frames: number }
  | { type: "tiles_ready"; run_id: string; target?: RadarTarget };

export const RADAR_EVENTS_URL = "/api/radar/events";

export const radarTileUrl = (runId: string) =>
  `/api/radar/runs/${encodeURIComponent(runId)}/tiles/{z}/{x}/{y}`;

/** Live stitched preview; `version` busts the browser cache per map_updated. */
export const radarPreviewUrl = (runId: string, version: number | string) =>
  `/api/radar/runs/${encodeURIComponent(runId)}/preview.jpg?v=${encodeURIComponent(version)}`;

/** Run summaries, optionally filtered to a single map target. */
export function fetchRadarRuns(target?: RadarTarget): Promise<RadarRunSummary[]> {
  const qs = target ? `?target=${encodeURIComponent(target)}` : "";
  return rfetch(`/api/radar/runs${qs}`);
}

export function fetchRadarActive(): Promise<{ active: RadarActiveScan | null }> {
  return rfetch("/api/radar/active");
}

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

// --- Sunfire Castle territory (fixed global-map structures + buff towers + zones) ---

export type RadarStructure = {
  kind: "castle" | "turret" | "stronghold" | "fortress" | string;
  label: string;
  col: number;
  row: number;
  size: number;
};

export type RadarTower = {
  tower_id: string;
  buff_type: string;
  label: string;
  bonus: string;
  color: string;
  level: number;
  booster: string;
  booster_pct: number;
  heavily_injured: string;
  losses: string;
  col: number;
  row: number;
  dist_from_castle: number;
};

export type RadarZone = {
  id: string;
  label: string;
  min_col: number;
  min_row: number;
  max_col: number;
  max_row: number;
  color?: string;
};

export type RadarTerritory = {
  grid_size: number;
  structures: RadarStructure[];
  towers: RadarTower[];
  zones: RadarZone[];
};

/** The fixed structures + buff towers + zone bands (read-only game facts). */
export function fetchRadarTerritory(): Promise<RadarTerritory> {
  return rfetch("/api/radar/territory");
}

export type TerritoryLayout = {
  zones: RadarZone[];
  objects: Record<string, unknown>[];
};

/** The operator's editable zone layout (seeded from the yaml bands on first use). */
export function fetchTerritoryLayout(): Promise<TerritoryLayout> {
  return rfetch("/api/radar/territory/layout");
}

export function saveTerritoryLayout(
  layout: TerritoryLayout,
): Promise<{ status: string; zones: number; objects: number }> {
  return rfetch("/api/radar/territory/layout", jsonInit("PUT", layout));
}

export type RadarInstance = {
  instance_id: string;
  serial: string;
  game: string;
};

/** Configured emulator instances; the first is the default scan target. */
export function fetchRadarInstances(): Promise<RadarInstance[]> {
  return rfetch("/api/radar/instances");
}

export type RadarScanStart = {
  run_id: string;
  instance_id: string;
  target: RadarTarget;
  total_frames: number;
  grid: RadarGridCell[];
  resumed: boolean;
};

/** Start (or, with `resume`, continue the newest unfinished) scan of `target`. */
export function startRadarScan(
  instanceId = "",
  target: RadarTarget = "global_map",
  resume = false,
): Promise<RadarScanStart> {
  return rfetch(
    "/api/radar/scan",
    jsonInit("POST", { instance_id: instanceId, target, resume }),
  );
}

export function stopRadarScan(): Promise<{ run_id: string; status: string }> {
  return rfetch("/api/radar/scan/stop", jsonInit("POST", {}));
}

export type RadarCornerRef = {
  cross_px: [number, number];
  rect_px: [number, number] | null;
  rect_size: [number, number] | null;
  outside_lower: number;
};

/** Kingdom diamond vertices, in the click order the marking UI collects them. */
export const RADAR_CORNER_ORDER = ["top", "right", "bottom", "left"] as const;
export type RadarCorner = (typeof RADAR_CORNER_ORDER)[number];

/** Pin a run's coordinate grid to the four operator-clicked kingdom vertices
 * (canvas px). The backend bundle-adjusts the stitch onto the square game grid
 * and re-tiles; completion arrives as a `tiles_ready` SSE event. */
export function markRadarCorners(
  runId: string,
  corners: Record<RadarCorner, [number, number]>,
): Promise<{ run_id: string; corners: number; status: string }> {
  return rfetch(
    `/api/radar/runs/${encodeURIComponent(runId)}/corners`,
    jsonInit("POST", { corners }),
  );
}

/** A landmark anchor: a known structure's exact game coordinate + the canvas pixel
 * where the operator clicked it on the stitched map. */
export type RadarAnchor = {
  game_xy: [number, number];
  canvas_px: [number, number];
  label?: string;
};

/** Pin a run's coordinate grid to operator-clicked landmark structures (castle,
 * forts) at their known game coordinates — generalises corner-marking. Merges with
 * any already-marked corners/landmarks by default; re-stitches + re-tiles. */
export function markRadarAnchors(
  runId: string,
  anchors: RadarAnchor[],
  merge = true,
): Promise<{ run_id: string; anchors: number; status: string }> {
  return rfetch(
    `/api/radar/runs/${encodeURIComponent(runId)}/anchors`,
    jsonInit("POST", { anchors, merge }),
  );
}

/** Record the corner reference for `target` from the CURRENT screen (camera manually on the corner X). */
export function calibrateRadarCorner(
  instanceId = "",
  target: RadarTarget = "global_map",
): Promise<{ corner_ref: RadarCornerRef; target: RadarTarget; path: string }> {
  return rfetch(
    "/api/radar/corner-ref",
    jsonInit("POST", { instance_id: instanceId, target }),
  );
}

export function buildRadarTiles(runId: string): Promise<{ run_id: string; status: string }> {
  return rfetch(`/api/radar/runs/${encodeURIComponent(runId)}/tiles`, jsonInit("POST"));
}

export type CityMapResult = {
  chunks: number;
  dropped: number;
  size: [number, number];
  buildings: number;
  out_dir: string;
};

/** Fuse all scanned main_city chunks into the persistent navigation map. */
export function assembleCityMap(): Promise<CityMapResult> {
  return rfetch("/api/radar/city-map", jsonInit("POST"));
}

export function deleteRadarRun(runId: string): Promise<{ run_id: string; status: string }> {
  return rfetch(`/api/radar/runs/${encodeURIComponent(runId)}`, jsonInit("DELETE"));
}

export function deleteAllRadarRuns(): Promise<{
  deleted: string[];
  skipped: string[];
  status: string;
}> {
  return rfetch("/api/radar/runs", jsonInit("DELETE"));
}
