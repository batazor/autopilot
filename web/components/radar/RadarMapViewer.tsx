"use client";

// Pan/zoom viewer for one run's tile pyramid: react-leaflet on CRS.Simple
// (flat pixel space, no geo projection). The Leaflet map instance is exposed
// via `onMapReady` so future GeoJSON overlay layers can attach to it without
// touching this component.

import { useEffect, useRef } from "react";
import L from "leaflet";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { radarTileUrl, type RadarTilesMeta } from "@/lib/radar-api";

type Viewport = { center: L.LatLng; zoom: number };

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

export default function RadarMapViewer({
  runId,
  meta,
  onMapReady,
}: {
  runId: string;
  meta: RadarTilesMeta;
  onMapReady?: (map: L.Map) => void;
}) {
  const mapRef = useRef<L.Map | null>(null);
  return (
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
    </MapContainer>
  );
}
