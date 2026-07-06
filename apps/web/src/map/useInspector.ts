/**
 * Pixel inspector: a crosshair toggle that reads the topmost visible ready
 * layer's value under a map click and shows it in a MapLibre popup, with a
 * "Series here" shortcut into the analysis drawer.
 *
 * Cursor, click handling and the popup all live outside React's render loop
 * (imperative, mirroring useTerraDraw) so a read never round-trips a render.
 * The popup content is plain DOM — MapLibre owns the popup node, not React.
 */
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { inspectPoint, useCatalog } from "../api/queries";
import type { Dataset, InspectRequest, InspectResult } from "../api/types";
import { pointBBox } from "../lib/geo";
import { useAnalysisStore } from "../stores/analysisStore";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore, type Layer } from "../stores/layersStore";
import { boundsToBBox, useRoiStore } from "../stores/roiStore";
import { buildTilesRequest } from "./useMintLayer";
import { useMapContext } from "./MapContext";

export interface InspectorApi {
  active: boolean;
  toggle(): void;
}

type PopupRef = { current: maplibregl.Popup | null };
type CatalogRef = { current: Dataset[] | undefined };

/** Topmost (last) visible, successfully minted layer — what the user sees on top. */
function topReadyLayer(): Layer | undefined {
  const { layers } = useLayersStore.getState();
  for (let i = layers.length - 1; i >= 0; i--) {
    const layer = layers[i]!;
    if (layer.visible && layer.status === "ready") return layer;
  }
  return undefined;
}

function formatValue(x: number): string {
  return x.toLocaleString("en-US", { maximumSignificantDigits: 4 });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hintNode(text: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "inspect-popup inspect-hint muted";
  el.textContent = text;
  return el;
}

function resultNode(
  layer: Layer,
  result: InspectResult,
  lngLat: maplibregl.LngLat,
  onSeries: () => void,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "inspect-popup";

  const title = document.createElement("div");
  title.className = "inspect-title";
  title.textContent = layer.label;
  wrap.appendChild(title);

  const value = document.createElement("div");
  if (result.value === null) {
    value.className = "inspect-value muted";
    value.textContent = "No data (masked)";
  } else {
    value.className = "inspect-value";
    const scaled = result.value * result.display_scale;
    value.textContent = result.unit ? `${formatValue(scaled)} ${result.unit}` : formatValue(scaled);
  }
  wrap.appendChild(value);

  const coords = document.createElement("div");
  coords.className = "inspect-coords muted";
  coords.textContent = `${lngLat.lat.toFixed(4)}, ${lngLat.lng.toFixed(4)}`;
  wrap.appendChild(coords);

  const button = document.createElement("button");
  button.className = "inspect-series-btn";
  button.textContent = "Series here →";
  button.title = "Run a time series over a small box around this point";
  button.addEventListener("click", onSeries);
  wrap.appendChild(button);

  return wrap;
}

/** Kick off a coarse+native mini time-series over a small box around the point. */
function startMiniSeries(
  layer: Layer,
  lngLat: maplibregl.LngLat,
  scaleM: number,
  range: { start: string; end: string },
): void {
  const b = pointBBox(lngLat.lng, lngLat.lat, scaleM);
  void useAnalysisStore.getState().run({
    dataset: layer.dataset,
    product: layer.product,
    label: `${layer.label} @ ${lngLat.lat.toFixed(3)}, ${lngLat.lng.toFixed(3)}`,
    roi: boundsToBBox(b.west, b.south, b.east, b.north),
    start: range.start,
    end: range.end,
  });
}

async function sampleAt(
  map: maplibregl.Map,
  lngLat: maplibregl.LngLat,
  popupRef: PopupRef,
  catalogRef: CatalogRef,
): Promise<void> {
  const layer = topReadyLayer();

  // One popup at a time: a new click supersedes the previous read.
  popupRef.current?.remove();
  const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: false, maxWidth: "260px" })
    .setLngLat(lngLat)
    .addTo(map);
  popupRef.current = popup;

  if (!layer) {
    popup.setDOMContent(hintNode("Add a visible layer, then click to inspect."));
    return;
  }
  popup.setDOMContent(hintNode("Sampling…"));

  const { mode, start, end, targetDate, halfWindowDays } = useDateStore.getState();
  const roi = useRoiStore.getState().roi;
  const tiles = buildTilesRequest(
    {
      dataset: layer.dataset,
      product: layer.product,
      vizOverrides: layer.vizOverrides,
      autoRange: layer.autoRange,
    },
    roi,
    { mode, start, end, targetDate, halfWindowDays },
  );
  // Reuse the layer's composite params; a point value ignores viz, and the
  // server ignores the extra field — no separate request builder needed.
  const request: InspectRequest = { ...tiles, lon: lngLat.lng, lat: lngLat.lat };

  try {
    const result = await inspectPoint(request);
    if (popupRef.current !== popup) return; // superseded by a newer click
    const dataset = catalogRef.current?.find((d) => d.id === layer.dataset);
    const scaleM = dataset?.default_scale_m ?? 100;
    popup.setDOMContent(
      resultNode(layer, result, lngLat, () => {
        popup.remove();
        if (popupRef.current === popup) popupRef.current = null;
        startMiniSeries(layer, lngLat, scaleM, { start, end });
      }),
    );
  } catch (error) {
    if (popupRef.current !== popup) return;
    popup.setDOMContent(hintNode(errorMessage(error)));
  }
}

export function useInspector(): InspectorApi {
  const { map, ready } = useMapContext();
  const { data: catalog } = useCatalog();

  const [active, setActive] = useState(false);
  const activeRef = useRef(false);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const catalogRef = useRef<Dataset[] | undefined>(catalog);
  // Keep the imperative click handler's view of the catalog current without
  // re-registering it: refs are mutated in an effect, never during render.
  useEffect(() => {
    catalogRef.current = catalog;
  }, [catalog]);

  useEffect(() => {
    if (!map || !ready) return;
    const onClick = (e: maplibregl.MapMouseEvent) => {
      if (!activeRef.current) return;
      void sampleAt(map, e.lngLat, popupRef, catalogRef);
    };
    map.on("click", onClick);
    return () => {
      map.off("click", onClick);
      popupRef.current?.remove();
      popupRef.current = null;
    };
  }, [map, ready]);

  const toggle = () => {
    const next = !activeRef.current;
    activeRef.current = next;
    setActive(next);
    if (map) map.getCanvas().style.cursor = next ? "crosshair" : "";
    if (!next) {
      popupRef.current?.remove();
      popupRef.current = null;
    }
  };

  return { active, toggle };
}
