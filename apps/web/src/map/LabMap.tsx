/**
 * The Methane Lab's own imperative MapLibre instance (separate from Explore's).
 * Renders, for the selected detection: an S2 RGB context raster, the ΔXCH4
 * overlay image, the plume-mask outline, and a rotated wind-arrow marker.
 */
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { mintTiles } from "../api/queries";
import { useDetectionDetail } from "../api/methaneQueries";
import { overlayUrl } from "../api/methaneQueries";
import type { DetectionDetail, Site } from "../api/types";
import { toImageCoordinates } from "../lib/methane";
import { useMethaneStore } from "../stores/methaneStore";
import { BASEMAP_STYLES, DEFAULT_BASEMAP } from "./basemap";

const OVERLAY_SRC = "det-overlay";
const MASK_SRC = "det-mask";
const BOX_SRC = "site-box";
const RGB_SRC = "s2-rgb";

function boxGeoJSON(site: Site): GeoJSON.Feature {
  const { west, south, east, north } = site.bbox;
  return {
    type: "Feature",
    properties: {},
    geometry: {
      type: "LineString",
      coordinates: [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
        [west, north],
      ],
    },
  };
}

export function LabMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);
  const windMarker = useRef<maplibregl.Marker | null>(null);

  const site = useMethaneStore((s) => s.selectedSite);
  const detId = useMethaneStore((s) => s.selectedDetectionId);
  const { data: detail } = useDetectionDetail(detId);

  // Create the map once.
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: BASEMAP_STYLES[DEFAULT_BASEMAP],
      center: [54.2, 38.5],
      zoom: 8,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    mapRef.current = map;
    map.on("load", () => {
      readyRef.current = true;
      map.addSource(BOX_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "site-box-line",
        type: "line",
        source: BOX_SRC,
        paint: { "line-color": "#38bdf8", "line-width": 1.5, "line-dasharray": [2, 2] },
      });
    });
    return () => {
      readyRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Fit to the selected site and draw its box.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current || !site) return;
    const src = map.getSource(BOX_SRC) as maplibregl.GeoJSONSource | undefined;
    src?.setData({ type: "FeatureCollection", features: [boxGeoJSON(site)] });
    map.fitBounds(
      [
        [site.bbox.west, site.bbox.south],
        [site.bbox.east, site.bbox.north],
      ],
      { padding: 40, duration: 600 },
    );
  }, [site]);

  // Render the selected detection's overlay + mask + wind + RGB context.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    clearDetectionLayers(map, windMarker);
    if (!detId || !detail) return;

    const coords = toImageCoordinates(detail.overlay_bounds);
    if (coords) {
      void addRgbContext(map, detail);
      map.addSource(OVERLAY_SRC, { type: "image", url: overlayUrl(detId), coordinates: coords });
      map.addLayer({
        id: `${OVERLAY_SRC}-layer`,
        type: "raster",
        source: OVERLAY_SRC,
        paint: { "raster-opacity": 0.85 },
      });
      map.fitBounds([coords[3], coords[1]] as [[number, number], [number, number]], {
        padding: 60,
        duration: 600,
      });
    }

    if (detail.mask_geojson) {
      map.addSource(MASK_SRC, {
        type: "geojson",
        data: detail.mask_geojson as unknown as GeoJSON.GeoJSON,
      });
      map.addLayer({
        id: `${MASK_SRC}-line`,
        type: "line",
        source: MASK_SRC,
        paint: { "line-color": "#facc15", "line-width": 2 },
      });
    }

    const from = detail.wind_from_deg;
    if (coords && typeof from === "number") {
      const [tl, , br] = coords;
      const center: [number, number] = [(tl[0] + br[0]) / 2, (tl[1] + br[1]) / 2];
      windMarker.current = new maplibregl.Marker({ element: windArrowEl(from) })
        .setLngLat(center)
        .addTo(map);
    }
  }, [detId, detail]);

  return <div ref={containerRef} className="lab-map" data-testid="lab-map" />;
}

function clearDetectionLayers(
  map: maplibregl.Map,
  windMarker: React.MutableRefObject<maplibregl.Marker | null>,
) {
  for (const layer of [`${OVERLAY_SRC}-layer`, `${MASK_SRC}-line`, `${RGB_SRC}-layer`]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  for (const src of [OVERLAY_SRC, MASK_SRC, RGB_SRC]) {
    if (map.getSource(src)) map.removeSource(src);
  }
  windMarker.current?.remove();
  windMarker.current = null;
}

/** Best-effort S2 true-colour context under the overlay (silent on failure). */
async function addRgbContext(map: maplibregl.Map, detail: DetectionDetail) {
  const coords = toImageCoordinates(detail.overlay_bounds);
  if (!coords) return;
  const [tl, , br] = coords;
  const west = tl[0];
  const north = tl[1];
  const east = br[0];
  const south = br[1];
  const timestampMs = Date.parse(detail.scene_time_utc);
  try {
    const tile = await mintTiles({
      dataset: "s2",
      product: "RGB",
      roi: { kind: "bbox", west, south, east, north },
      composite: "single_scene",
      timestamp_ms: timestampMs,
    } as Parameters<typeof mintTiles>[0]);
    if (!map.getSource(RGB_SRC) && !map.getLayer(`${OVERLAY_SRC}-layer`)) return;
    if (map.getSource(RGB_SRC)) return;
    map.addSource(RGB_SRC, { type: "raster", tiles: [tile.tile_url], tileSize: 256 });
    const before = map.getLayer(`${OVERLAY_SRC}-layer`) ? `${OVERLAY_SRC}-layer` : undefined;
    map.addLayer({ id: `${RGB_SRC}-layer`, type: "raster", source: RGB_SRC }, before);
  } catch {
    // Context imagery is optional; the basemap remains beneath the overlay.
  }
}

function windArrowEl(fromDeg: number): HTMLElement {
  const el = document.createElement("div");
  el.className = "wind-arrow";
  el.title = `Wind from ${Math.round(fromDeg)}°`;
  // Arrow points in the direction the wind blows TOWARD (from + 180°).
  el.style.transform = `rotate(${fromDeg + 180}deg)`;
  el.textContent = "↑";
  return el;
}
