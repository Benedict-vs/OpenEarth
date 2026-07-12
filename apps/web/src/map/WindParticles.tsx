/**
 * Binds the {@link WindParticleLayer} custom layer into the Explore map.
 *
 * Shares the arrow overlay's data source (the same /wind/field query key + active
 * date), so the two renderings show the *same* field. A new field rebuilds the wind
 * texture in place (setWind — no layer churn); the layer is added/removed with the
 * toggle and cleaned up on unmount. Particle count adapts to zoom.
 */
import { useQuery } from "@tanstack/react-query";
import type maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { fetchWindField } from "../api/queries";
import { useDateStore } from "../stores/dateStore";
import { useWindStore } from "../stores/windStore";
import { useMapContext } from "./MapContext";
import { WindParticleLayer } from "./wind/WindParticleLayer";
import { buildWindTexture } from "./wind/windTexture";

interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

function viewportBounds(map: maplibregl.Map): Bounds | null {
  const b = map.getBounds();
  const r2 = (x: number) => Math.round(x * 100) / 100;
  const west = r2(Math.max(-180, b.getWest()));
  const south = r2(Math.max(-90, b.getSouth()));
  const east = r2(Math.min(180, b.getEast()));
  const north = r2(Math.min(90, b.getNorth()));
  if (east <= west || north <= south) return null;
  return { west, south, east, north };
}

function sameBounds(a: Bounds | null, b: Bounds): boolean {
  return (
    a !== null &&
    a.west === b.west &&
    a.south === b.south &&
    a.east === b.east &&
    a.north === b.north
  );
}

function particleCountForZoom(zoom: number): number {
  return Math.round(Math.min(12000, Math.max(2048, 3000 + (zoom - 6) * 900)));
}

export function WindParticles() {
  const { map, ready } = useMapContext();
  const enabled = useWindStore((s) => s.particlesEnabled);
  const center = useDateStore((s) => s.window.center);
  const timeIso = `${center}T12:00:00Z`;

  const [bounds, setBounds] = useState<Bounds | null>(null);
  const layerRef = useRef<WindParticleLayer | null>(null);

  // Follow the viewport (2 dp) — same keying as the arrow overlay so fetches dedupe.
  useEffect(() => {
    if (!map || !ready) return;
    const update = () => {
      const next = viewportBounds(map);
      if (next) setBounds((prev) => (sameBounds(prev, next) ? prev : next));
    };
    update();
    map.on("moveend", update);
    return () => {
      map.off("moveend", update);
    };
  }, [map, ready]);

  const query = useQuery({
    queryKey: ["wind-field", bounds, timeIso],
    queryFn: () => fetchWindField({ ...bounds!, time: timeIso, nx: 24 }),
    enabled: enabled && bounds !== null,
    staleTime: 5 * 60_000,
  });
  const field = enabled ? (query.data ?? null) : null;

  // Add/remove the custom layer with the toggle.
  useEffect(() => {
    if (!map || !ready) return;
    if (!enabled) return;
    const layer = new WindParticleLayer(particleCountForZoom(map.getZoom()));
    map.addLayer(layer);
    layerRef.current = layer;
    const onZoom = () => layer.setNumParticles(particleCountForZoom(map.getZoom()));
    map.on("zoomend", onZoom);
    return () => {
      map.off("zoomend", onZoom);
      try {
        if (map.getLayer(layer.id)) map.removeLayer(layer.id);
      } catch {
        /* map already torn down on view switch */
      }
      layerRef.current = null;
    };
  }, [map, ready, enabled]);

  // Feed the current field to the layer (rebuild the wind texture in place).
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer || !field) return;
    layer.setWind(buildWindTexture(field), [
      field.bbox.west,
      field.bbox.south,
      field.bbox.east,
      field.bbox.north,
    ]);
    map?.triggerRepaint();
  }, [field, map]);

  return null;
}
