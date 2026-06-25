"use client";

// Pan/zoom viewer for one run's tile pyramid: react-leaflet on CRS.Simple
// (flat pixel space, no geo projection). The Leaflet map instance is exposed
// via `onMapReady` so future GeoJSON overlay layers can attach to it without
// touching this component. When the run carries a game↔canvas affine
// (`meta.coords`, global_map only), a hover readout reports the in-game X:Y.

import { memo, useEffect, useReducer, useRef, useState } from "react";
import L from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Polygon,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  canvasToGame,
  gameToCanvas,
  radarTileUrl,
  type RadarCoordsAffine,
  type RadarTerritory,
  type RadarTilesMeta,
  type RadarZone,
} from "@/lib/radar-api";

type Viewport = { center: L.LatLng; zoom: number };
type GameXY = { x: number; y: number };

// Last viewport per run — in memory only (deliberately not localStorage), so
// switching back to a run restores where you were within the session.
const viewportMemory = new Map<string, Viewport>();

function FitToRun({ runId, meta }: { runId: string; meta: RadarTilesMeta }) {
  const map = useMap();
  useEffect(() => {
    // Image-pyramid bounds: unproject the native pixel extent at max zoom.
    const sw = map.unproject([0, meta.height], meta.max_zoom);
    const ne = map.unproject([meta.width, 0], meta.max_zoom);
    const bounds = L.latLngBounds(sw, ne);
    map.setMaxBounds(bounds.pad(0.25));
    const saved = viewportMemory.get(runId);
    if (saved) {
      map.setView(saved.center, saved.zoom, { animate: false });
    } else {
      map.fitBounds(bounds, { animate: false });
    }
    const remember = () =>
      viewportMemory.set(runId, { center: map.getCenter(), zoom: map.getZoom() });
    map.on("moveend zoomend", remember);
    return () => {
      map.off("moveend zoomend", remember);
    };
  }, [map, runId, meta]);
  return null;
}

/** Tracks the cursor's in-game coordinate via the precomputed inverse affine.
 * `map.project(latlng, max_zoom)` is the inverse of the `unproject` FitToRun
 * uses for bounds, so it yields native canvas pixels — what the affine maps. */
function CoordReadout({
  coords,
  maxZoom,
  onHover,
}: {
  coords: RadarCoordsAffine;
  maxZoom: number;
  onHover: (xy: GameXY | null) => void;
}) {
  const map = useMap();
  useMapEvents({
    mousemove(e) {
      const p = map.project(e.latlng, maxZoom);
      const [gx, gy] = canvasToGame(p.x, p.y, coords);
      onHover({ x: gx, y: gy }); // unrounded; the HUD + tile highlight floor it
    },
    mouseout() {
      onHover(null);
    },
  });
  return null;
}

/** Forwards map clicks as canvas pixels (for marking kingdom corners). */
function ClickCapture({
  maxZoom,
  onMapClick,
}: {
  maxZoom: number;
  onMapClick: (canvasPx: [number, number]) => void;
}) {
  const map = useMap();
  useMapEvents({
    click(e) {
      const p = map.project(e.latlng, maxZoom);
      onMapClick([p.x, p.y]);
    },
  });
  return null;
}

/** Dots at the already-placed corner marks (canvas px → latlng via the map). */
function CornerMarks({ marks, maxZoom }: { marks: [number, number][]; maxZoom: number }) {
  const map = useMap();
  return (
    <>
      {marks.map((px, i) => (
        <CircleMarker
          key={i}
          center={map.unproject(px, maxZoom)}
          radius={7}
          pathOptions={{ color: "#06b6d4", weight: 3, fillColor: "#06b6d4", fillOpacity: 0.5 }}
        />
      ))}
    </>
  );
}

const GAME_SIZE = 1200; // WoS kingdom is a fixed 1200×1200 coordinate square.
const NICE_STEPS = [1, 2, 5, 10, 25, 50, 100, 200, 300];

/** Game-coordinate gridlines over the map, adaptive step + clipped to the view.
 * Memoized so it only recomputes on zoom/pan, not on every hover. */
const GridLines = memo(function GridLines({
  coords,
  maxZoom,
}: {
  coords: RadarCoordsAffine;
  maxZoom: number;
}) {
  const map = useMap();
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useMapEvents({ zoomend: () => bump(), moveend: () => bump() });

  const c2ll = (gx: number, gy: number) => map.unproject(gameToCanvas(gx, gy, coords), maxZoom);
  // Step so lines sit ~50 screen px apart at the current zoom (1 canvas px =
  // 2^(zoom−maxZoom) screen px).
  const lin = coords.game_to_canvas_linear;
  const pxPerTile = (Math.hypot(lin[0][0], lin[1][0]) + Math.hypot(lin[0][1], lin[1][1])) / 2;
  const screenPerCanvas = Math.pow(2, map.getZoom() - maxZoom);
  const raw = 50 / Math.max(pxPerTile * screenPerCanvas, 1e-6);
  const step = NICE_STEPS.find((s) => s >= raw) ?? 300;

  // Visible game range from the viewport corners — don't draw the whole kingdom.
  const clamp = (v: number) => Math.max(0, Math.min(GAME_SIZE, v));
  const gxs: number[] = [];
  const gys: number[] = [];
  for (const ll of [
    map.getBounds().getNorthWest(),
    map.getBounds().getNorthEast(),
    map.getBounds().getSouthEast(),
    map.getBounds().getSouthWest(),
  ]) {
    const p = map.project(ll, maxZoom);
    const [gx, gy] = canvasToGame(p.x, p.y, coords);
    gxs.push(gx);
    gys.push(gy);
  }
  const x0 = clamp(Math.floor(Math.min(...gxs) / step) * step);
  const x1 = clamp(Math.ceil(Math.max(...gxs) / step) * step);
  const y0 = clamp(Math.floor(Math.min(...gys) / step) * step);
  const y1 = clamp(Math.ceil(Math.max(...gys) / step) * step);

  const lines: L.LatLng[][] = [];
  for (let x = x0; x <= x1; x += step) lines.push([c2ll(x, y0), c2ll(x, y1)]);
  for (let y = y0; y <= y1; y += step) lines.push([c2ll(x0, y), c2ll(x1, y)]);

  return (
    <>
      {lines.map((pts, i) => (
        <Polyline
          key={i}
          positions={pts}
          interactive={false}
          pathOptions={{ color: "#22d3ee", weight: 0.5, opacity: 0.3 }}
        />
      ))}
    </>
  );
});

/** Highlights the 1×1 game tile under the cursor. */
function TileHighlight({
  coords,
  maxZoom,
  hover,
}: {
  coords: RadarCoordsAffine;
  maxZoom: number;
  hover: GameXY | null;
}) {
  const map = useMap();
  if (!hover) return null;
  const tx = Math.floor(hover.x);
  const ty = Math.floor(hover.y);
  if (tx < 0 || ty < 0 || tx >= GAME_SIZE || ty >= GAME_SIZE) return null;
  const c2ll = (gx: number, gy: number) => map.unproject(gameToCanvas(gx, gy, coords), maxZoom);
  return (
    <Polygon
      positions={[c2ll(tx, ty), c2ll(tx + 1, ty), c2ll(tx + 1, ty + 1), c2ll(tx, ty + 1)]}
      interactive={false}
      pathOptions={{ color: "#fbbf24", weight: 2, fillColor: "#fbbf24", fillOpacity: 0.3 }}
    />
  );
}

// Marker style per fixed-structure kind (castle/forts/turrets). Buff towers use
// their own per-type colour from the game data, so they're handled separately.
const STRUCT_STYLE: Record<string, { radius: number; color: string }> = {
  castle: { radius: 8, color: "#ef4444" },
  stronghold: { radius: 6, color: "#f59e0b" },
  fortress: { radius: 5, color: "#3b82f6" },
  turret: { radius: 3, color: "#e5e7eb" },
};

/** Fixed Sunfire Castle structures + buff towers + zone bands, drawn in game
 * coordinates over the anchored global_map (forts/castle as markers, towers
 * colour-coded by buff type, zones as dashed boxes). Read-only facts. */
function StructureOverlay({
  coords,
  maxZoom,
  territory,
}: {
  coords: RadarCoordsAffine;
  maxZoom: number;
  territory: RadarTerritory;
}) {
  const map = useMap();
  const c2ll = (gx: number, gy: number) =>
    map.unproject(gameToCanvas(gx, gy, coords), maxZoom);
  return (
    <>
      {territory.zones.map((z) => (
        <Polygon
          key={`zone-${z.id}`}
          positions={[
            c2ll(z.min_col, z.min_row),
            c2ll(z.max_col, z.min_row),
            c2ll(z.max_col, z.max_row),
            c2ll(z.min_col, z.max_row),
          ]}
          interactive={false}
          pathOptions={{
            color: z.color ?? "#a78bfa",
            weight: 1.5,
            fill: false,
            dashArray: "6 4",
            opacity: 0.6,
          }}
        />
      ))}
      {territory.towers.map((tw) => (
        <CircleMarker
          key={tw.tower_id}
          center={c2ll(tw.col, tw.row)}
          radius={4}
          pathOptions={{
            color: tw.color,
            weight: 1,
            fillColor: tw.color,
            fillOpacity: 0.85,
          }}
        >
          <Tooltip>
            {tw.label} {tw.booster} · L{tw.level} · {tw.col}:{tw.row}
          </Tooltip>
        </CircleMarker>
      ))}
      {territory.structures.map((s, i) => {
        const style = STRUCT_STYLE[s.kind] ?? STRUCT_STYLE.fortress;
        return (
          <CircleMarker
            key={`struct-${i}`}
            center={c2ll(s.col, s.row)}
            radius={style.radius}
            pathOptions={{
              color: style.color,
              weight: 2,
              fillColor: style.color,
              fillOpacity: 0.5,
            }}
          >
            <Tooltip>
              {s.label} · {s.col}:{s.row}
            </Tooltip>
          </CircleMarker>
        );
      })}
    </>
  );
}

/** Editable operator zones drawn as filled polygons in game coordinates (correct
 * under a rotated affine, unlike an axis-aligned Leaflet Rectangle). Clicking a
 * zone selects it; the numeric panel + draw mode live on the page. */
function ZoneLayer({
  coords,
  maxZoom,
  zones,
  selectedId,
  onSelect,
}: {
  coords: RadarCoordsAffine;
  maxZoom: number;
  zones: RadarZone[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const map = useMap();
  const c2ll = (gx: number, gy: number) =>
    map.unproject(gameToCanvas(gx, gy, coords), maxZoom);
  return (
    <>
      {zones.map((z) => {
        const selected = z.id === selectedId;
        const color = z.color ?? "#22d3ee";
        return (
          <Polygon
            key={z.id}
            positions={[
              c2ll(z.min_col, z.min_row),
              c2ll(z.max_col, z.min_row),
              c2ll(z.max_col, z.max_row),
              c2ll(z.min_col, z.max_row),
            ]}
            eventHandlers={onSelect ? { click: () => onSelect(z.id) } : undefined}
            pathOptions={{
              color,
              weight: selected ? 3 : 1.5,
              fillColor: color,
              fillOpacity: selected ? 0.25 : 0.1,
            }}
          />
        );
      })}
    </>
  );
}

export default function RadarMapViewer({
  runId,
  meta,
  onMapReady,
  onMapClick,
  cornerMarkers,
  territory,
  showTerritory,
  zones,
  selectedZoneId,
  onSelectZone,
}: {
  runId: string;
  meta: RadarTilesMeta;
  onMapReady?: (map: L.Map) => void;
  // When set, clicking the map reports the clicked canvas pixel (corner marking).
  onMapClick?: (canvasPx: [number, number]) => void;
  // Canvas-pixel positions of already-placed corner marks, drawn as dots.
  cornerMarkers?: [number, number][];
  // Fixed Sunfire Castle structures to overlay (global_map, anchored runs only).
  territory?: RadarTerritory | null;
  showTerritory?: boolean;
  // Editable operator zones (game coords); rendered + selectable when provided.
  zones?: RadarZone[] | null;
  selectedZoneId?: string | null;
  onSelectZone?: (id: string) => void;
}) {
  const mapRef = useRef<L.Map | null>(null);
  const [hover, setHover] = useState<GameXY | null>(null);
  const coords = meta.coords;
  return (
    <div className="relative">
      <MapContainer
        // Fresh map per run: zoom range and bounds are per-pyramid.
        key={runId}
        ref={(instance) => {
          mapRef.current = instance;
          if (instance && onMapReady) onMapReady(instance);
        }}
        crs={L.CRS.Simple}
        center={[0, 0]}
        zoom={Math.max(meta.min_zoom, meta.max_zoom - 2)}
        minZoom={meta.min_zoom}
        maxZoom={meta.max_zoom}
        zoomControl
        attributionControl={false}
        className="h-[560px] w-full rounded-lg"
      >
        <TileLayer
          url={radarTileUrl(runId)}
          tileSize={meta.tile_size}
          minZoom={meta.min_zoom}
          maxZoom={meta.max_zoom}
          noWrap
        />
        <FitToRun runId={runId} meta={meta} />
        {coords ? (
          <>
            <GridLines coords={coords} maxZoom={meta.max_zoom} />
            <TileHighlight coords={coords} maxZoom={meta.max_zoom} hover={hover} />
            <CoordReadout coords={coords} maxZoom={meta.max_zoom} onHover={setHover} />
          </>
        ) : null}
        {coords && showTerritory && territory ? (
          <StructureOverlay coords={coords} maxZoom={meta.max_zoom} territory={territory} />
        ) : null}
        {coords && zones && zones.length ? (
          <ZoneLayer
            coords={coords}
            maxZoom={meta.max_zoom}
            zones={zones}
            selectedId={selectedZoneId}
            onSelect={onSelectZone}
          />
        ) : null}
        {onMapClick ? (
          <ClickCapture maxZoom={meta.max_zoom} onMapClick={onMapClick} />
        ) : null}
        {cornerMarkers && cornerMarkers.length ? (
          <CornerMarks marks={cornerMarkers} maxZoom={meta.max_zoom} />
        ) : null}
      </MapContainer>
      {coords ? (
        <div className="pointer-events-none absolute left-2 top-2 z-[1100] flex items-center gap-2 rounded-md bg-black/55 px-2 py-1 text-xs font-medium text-white backdrop-blur-sm">
          {hover ? (
            <span className="tabular-nums">
              X:{Math.floor(hover.x)} Y:{Math.floor(hover.y)}
            </span>
          ) : (
            <span className="opacity-70">move over the map…</span>
          )}
          <span
            className="opacity-60"
            title={
              coords.source === "corners"
                ? "Grid pinned to the operator-marked kingdom corners (drift removed)"
                : coords.source === "refit"
                  ? "Affine refit from operator coordinate samples"
                  : "Affine derived from the minimap (no ground-truth yet — coordinates may be off)"
            }
          >
            {(coords.source === "corners" || coords.source === "refit") &&
            coords.residual_tiles_median != null
              ? `±${coords.residual_tiles_median} tiles`
              : "≈ derived"}
          </span>
        </div>
      ) : null}
    </div>
  );
}
