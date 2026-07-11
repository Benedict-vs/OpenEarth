/**
 * The Methane Lab's own imperative MapLibre instance (separate from Explore's).
 * Renders, for the selected detection: an S2 RGB context raster, the ΔXCH4
 * overlay image, the plume-mask outline, and a rotated wind-arrow marker.
 */
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { mintTiles } from "../api/queries";
import { useDetectionDetail, useEmitPlumes } from "../api/methaneQueries";
import { overlayUrl } from "../api/methaneQueries";
import type { BBoxIn, EmitPlume, EmitPlumes, Site } from "../api/types";
import { analysisAreaToBBox, toImageCoordinates } from "../lib/methane";
import { useMethaneStore } from "../stores/methaneStore";
import { BASEMAP_STYLES, DEFAULT_BASEMAP } from "./basemap";

const OVERLAY_SRC = "det-overlay";
const MASK_SRC = "det-mask";
const BOX_SRC = "site-box";
const AREA_SRC = "analysis-area";
const SCENE_RGB_SRC = "scene-rgb";
const PLUME_SRC = "emit-plumes";

const AREA_COLOR = "#f472b6";

// Provenance styling: amber = frozen GEE V001 mirror, emerald = live LP DAAC V002.
const PLUME_COLOR_V001 = "#fbbf24";
const PLUME_COLOR_V002 = "#34d399";

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

function areaGeoJSON(bbox: BBoxIn): GeoJSON.Feature {
  const { west, south, east, north } = bbox;
  return {
    type: "Feature",
    properties: {},
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [west, north],
          [east, north],
          [east, south],
          [west, south],
          [west, north],
        ],
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
  const area = useMethaneStore((s) => s.analysisArea);
  const placing = useMethaneStore((s) => s.placingArea);
  const setAnalysisArea = useMethaneStore((s) => s.setAnalysisArea);
  const setPlacingArea = useMethaneStore((s) => s.setPlacingArea);
  const targetSceneTime = useMethaneStore((s) => s.targetSceneTime);
  const rgbEnabled = useMethaneStore((s) => s.rgbPreviewEnabled);
  const setRgbPreview = useMethaneStore((s) => s.setRgbPreview);
  const overlayVisible = useMethaneStore((s) => s.overlayVisible);
  const overlayOpacity = useMethaneStore((s) => s.overlayOpacity);
  const setOverlayVisible = useMethaneStore((s) => s.setOverlayVisible);
  const setOverlayOpacity = useMethaneStore((s) => s.setOverlayOpacity);
  const { data: detail } = useDetectionDetail(detId);

  const emitEnabled = useMethaneStore((s) => s.emitPlumesEnabled);
  const setEmitPlumes = useMethaneStore((s) => s.setEmitPlumes);
  const dates = useMethaneStore((s) => s.dates);
  const { data: plumes, isFetching: plumesFetching } = useEmitPlumes(
    site,
    dates.start,
    dates.end,
    emitEnabled,
  );

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
      map.addSource(AREA_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: `${AREA_SRC}-fill`,
        type: "fill",
        source: AREA_SRC,
        paint: { "fill-color": AREA_COLOR, "fill-opacity": 0.08 },
      });
      map.addLayer({
        id: `${AREA_SRC}-line`,
        type: "line",
        source: AREA_SRC,
        paint: { "line-color": AREA_COLOR, "line-width": 2 },
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

  // Keep the analysis-area box (the chip actually analyzed) in sync.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const src = map.getSource(AREA_SRC) as maplibregl.GeoJSONSource | undefined;
    src?.setData({
      type: "FeatureCollection",
      features: area ? [areaGeoJSON(analysisAreaToBBox(area))] : [],
    });
  }, [area]);

  // ΔXCH4 overlay visibility/opacity — paint/layout only, never a re-mint.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current || !map.getLayer(`${OVERLAY_SRC}-layer`)) return;
    map.setLayoutProperty(
      `${OVERLAY_SRC}-layer`,
      "visibility",
      overlayVisible ? "visible" : "none",
    );
    map.setPaintProperty(`${OVERLAY_SRC}-layer`, "raster-opacity", overlayOpacity);
  }, [overlayVisible, overlayOpacity]);

  // True-colour preview of the scene under review, beneath the boxes — surface
  // context for placing the analysis area and the bare-eye false-positive check.
  // A selected detection supplies its own scene time, so feed review keeps the
  // RGB context through this same single layer.
  const rgbSceneTime = targetSceneTime ?? detail?.scene_time_utc ?? null;
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const remove = () => {
      if (map.getLayer(`${SCENE_RGB_SRC}-layer`)) map.removeLayer(`${SCENE_RGB_SRC}-layer`);
      if (map.getSource(SCENE_RGB_SRC)) map.removeSource(SCENE_RGB_SRC);
    };
    remove();
    if (!rgbEnabled || !site || !rgbSceneTime) return;

    let stale = false;
    void mintTiles({
      dataset: "s2",
      product: "RGB",
      roi: site.bbox,
      composite: "single_scene",
      timestamp_ms: Date.parse(rgbSceneTime),
    } as Parameters<typeof mintTiles>[0])
      .then((tile) => {
        if (stale || map.getSource(SCENE_RGB_SRC)) return;
        map.addSource(SCENE_RGB_SRC, { type: "raster", tiles: [tile.tile_url], tileSize: 256 });
        map.addLayer(
          { id: `${SCENE_RGB_SRC}-layer`, type: "raster", source: SCENE_RGB_SRC },
          "site-box-line", // beneath the site/area boxes and all detection layers
        );
      })
      .catch(() => {
        // Preview is best-effort; the basemap stays.
      });
    return () => {
      stale = true; // re-runs start with remove(); unmount destroys the map anyway
    };
  }, [rgbEnabled, site, rgbSceneTime]);

  // "Place on map": the next click recentres the analysis area.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !placing) return;
    const canvas = map.getCanvas();
    canvas.style.cursor = "crosshair";
    const onClick = (e: maplibregl.MapMouseEvent) => {
      setAnalysisArea({ lon: e.lngLat.lng, lat: e.lngLat.lat });
      setPlacingArea(false);
    };
    map.once("click", onClick);
    return () => {
      map.off("click", onClick);
      canvas.style.cursor = "";
    };
  }, [placing, setAnalysisArea, setPlacingArea]);

  // Render the selected detection's overlay + mask + wind + RGB context.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    clearDetectionLayers(map, windMarker);
    if (!detId || !detail) return;

    const coords = toImageCoordinates(detail.overlay_bounds);
    if (coords) {
      const { overlayVisible, overlayOpacity } = useMethaneStore.getState();
      map.addSource(OVERLAY_SRC, { type: "image", url: overlayUrl(detId), coordinates: coords });
      map.addLayer({
        id: `${OVERLAY_SRC}-layer`,
        type: "raster",
        source: OVERLAY_SRC,
        paint: { "raster-opacity": overlayOpacity },
        layout: { visibility: overlayVisible ? "visible" : "none" },
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

  // EMIT plume overlay: outlines styled by provenance, click for q ± σ.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    if (!emitEnabled || !plumes) {
      removePlumeLayers(map);
      return;
    }
    const fc = plumesFeatureCollection(plumes);
    const existing = map.getSource(PLUME_SRC) as maplibregl.GeoJSONSource | undefined;
    if (existing) {
      existing.setData(fc);
    } else {
      addPlumeLayers(map, fc);
    }
  }, [emitEnabled, plumes]);

  const plumeCount = emitEnabled ? (plumes?.plumes.length ?? null) : null;

  return (
    <div ref={containerRef} className="lab-map" data-testid="lab-map">
      <div className="lab-toggles">
        {detId && detail?.overlay_bounds ? (
          <label
            className="lab-emit-toggle lab-overlay-control"
            title="ΔXCH4 overlay visibility and opacity — hide it to check the RGB below"
          >
            <input
              type="checkbox"
              checked={overlayVisible}
              onChange={(e) => setOverlayVisible(e.target.checked)}
            />
            <span>ΔXCH4</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={overlayOpacity}
              disabled={!overlayVisible}
              onChange={(e) => setOverlayOpacity(Number(e.target.value))}
            />
          </label>
        ) : null}
        <label
          className="lab-emit-toggle lab-rgb-toggle"
          title={
            rgbSceneTime
              ? "True-colour view of the scene under review (context for placing the analysis area). Cloud-masked: s2cloudless-flagged pixels render transparent."
              : "Select a scene or detection to preview its true-colour image"
          }
        >
          <input
            type="checkbox"
            checked={rgbEnabled}
            disabled={!rgbSceneTime}
            onChange={(e) => setRgbPreview(e.target.checked)}
          />
          <span>S2 RGB</span>
        </label>
        <label
          className="lab-emit-toggle"
          title="EMIT methane plume complexes (independent evidence)"
        >
          <input
            type="checkbox"
            checked={emitEnabled}
            onChange={(e) => setEmitPlumes(e.target.checked)}
          />
          <span>EMIT plumes</span>
          {emitEnabled ? (
            <span className="lab-emit-count">
              {plumesFetching ? "…" : plumeCount != null ? plumeCount : "—"}
            </span>
          ) : null}
        </label>
      </div>
      {emitEnabled ? (
        <div className="lab-emit-legend">
          <span>
            <i style={{ background: PLUME_COLOR_V002 }} /> V002 (live)
          </span>
          <span>
            <i style={{ background: PLUME_COLOR_V001 }} /> V001 (GEE, ≤ Oct 2024)
          </span>
        </div>
      ) : null}
    </div>
  );
}

/** GeoJSON FeatureCollection from a plume list; properties drive styling + popups. */
function plumesFeatureCollection(plumes: EmitPlumes): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: plumes.plumes.map((p: EmitPlume) => ({
      type: "Feature",
      geometry: p.outline as unknown as GeoJSON.Geometry,
      properties: {
        plume_id: p.plume_id,
        provenance: p.provenance,
        time_utc: p.time_utc,
        max_enh_ppm_m: p.max_enh_ppm_m,
        q_kg_h: p.q_kg_h,
        q_sigma_kg_h: p.q_sigma_kg_h,
      },
    })),
  };
}

function addPlumeLayers(map: maplibregl.Map, fc: GeoJSON.FeatureCollection) {
  map.addSource(PLUME_SRC, { type: "geojson", data: fc });
  const colorByProvenance: maplibregl.ExpressionSpecification = [
    "match",
    ["get", "provenance"],
    "lpdaac_v002",
    PLUME_COLOR_V002,
    PLUME_COLOR_V001,
  ];
  map.addLayer({
    id: `${PLUME_SRC}-fill`,
    type: "fill",
    source: PLUME_SRC,
    paint: { "fill-color": colorByProvenance, "fill-opacity": 0.18 },
  });
  // line-dasharray is not data-driven, so split solid (V002) from dashed (V001).
  map.addLayer({
    id: `${PLUME_SRC}-line-v002`,
    type: "line",
    source: PLUME_SRC,
    filter: ["==", ["get", "provenance"], "lpdaac_v002"],
    paint: { "line-color": PLUME_COLOR_V002, "line-width": 2 },
  });
  map.addLayer({
    id: `${PLUME_SRC}-line-v001`,
    type: "line",
    source: PLUME_SRC,
    filter: ["==", ["get", "provenance"], "gee_v001"],
    paint: { "line-color": PLUME_COLOR_V001, "line-width": 2, "line-dasharray": [2, 2] },
  });

  const popup = new maplibregl.Popup({ closeButton: false, maxWidth: "260px" });
  for (const layer of [`${PLUME_SRC}-line-v002`, `${PLUME_SRC}-line-v001`, `${PLUME_SRC}-fill`]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    map.on("click", layer, (e) => {
      const f = e.features?.[0];
      if (!f) return;
      popup
        .setLngLat(e.lngLat)
        .setHTML(plumePopupHtml(f.properties ?? {}))
        .addTo(map);
    });
  }
}

function removePlumeLayers(map: maplibregl.Map) {
  for (const layer of [`${PLUME_SRC}-fill`, `${PLUME_SRC}-line-v002`, `${PLUME_SRC}-line-v001`]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  if (map.getSource(PLUME_SRC)) map.removeSource(PLUME_SRC);
}

function plumePopupHtml(props: Record<string, unknown>): string {
  const provenance =
    props.provenance === "lpdaac_v002" ? "V002 (LP DAAC, live)" : "V001 (GEE mirror)";
  const time = String(props.time_utc ?? "")
    .slice(0, 16)
    .replace("T", " ");
  const enh = num(props.max_enh_ppm_m);
  const q = num(props.q_kg_h);
  const qSigma = num(props.q_sigma_kg_h);
  const rows: string[] = [
    `<strong>EMIT plume</strong> · ${provenance}`,
    `<div class="pp-time">${escapeHtml(time)} UTC</div>`,
  ];
  if (enh != null) rows.push(`<div>Max enhancement: ${enh.toFixed(0)} ppm·m</div>`);
  if (q != null) {
    const sig = qSigma != null ? ` ± ${qSigma.toFixed(0)}` : "";
    rows.push(`<div>Emission rate: ${q.toFixed(0)}${sig} kg/h</div>`);
  }
  return `<div class="emit-popup">${rows.join("")}</div>`;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

function clearDetectionLayers(
  map: maplibregl.Map,
  windMarker: React.MutableRefObject<maplibregl.Marker | null>,
) {
  for (const layer of [`${OVERLAY_SRC}-layer`, `${MASK_SRC}-line`]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  for (const src of [OVERLAY_SRC, MASK_SRC]) {
    if (map.getSource(src)) map.removeSource(src);
  }
  windMarker.current?.remove();
  windMarker.current = null;
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
