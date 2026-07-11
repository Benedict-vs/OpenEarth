/**
 * Binds one store layer to a MapLibre raster source + layer.
 *
 * The no-refetch rule lives here: opacity/visibility/order changes touch
 * only the *layer* (paint/layout/moveLayer); a new mint for the same layer
 * swaps URLs on the existing *source* via setTiles. The source is never
 * removed and re-added while the layer lives — that would refetch every
 * visible tile.
 */
import type { Map as MapLibreMap, RasterTileSource } from "maplibre-gl";
import { useEffect } from "react";
import type { Layer } from "../stores/layersStore";
import { useMapContext } from "./MapContext";

export function sourceIdFor(layerId: string): string {
  return `oe-${layerId}`;
}

/** The wind-particles custom layer stays above all data rasters. */
const WIND_LAYER_ID = "wind-particles";

function rasterCeiling(map: MapLibreMap): string | undefined {
  return map.getLayer(WIND_LAYER_ID) ? WIND_LAYER_ID : undefined;
}

function terraDrawLayerIds(map: MapLibreMap): string[] {
  return (map.getStyle().layers ?? []).filter((l) => l.id.startsWith("td-")).map((l) => l.id);
}

export function useRasterLayer(layer: Layer): void {
  const { map, ready } = useMapContext();
  const sid = sourceIdFor(layer.id);
  const tileUrl = layer.mint?.tileUrl ?? null;

  // Source + layer lifecycle: create on first mint, setTiles on re-mints.
  useEffect(() => {
    if (!map || !ready || !tileUrl) return;
    const existing = map.getSource(sid) as RasterTileSource | undefined;
    if (existing) {
      existing.setTiles([tileUrl]);
      return;
    }
    map.addSource(sid, {
      type: "raster",
      tiles: [tileUrl],
      tileSize: 256,
      attribution: layer.mint?.attribution ?? "",
    });
    map.addLayer(
      {
        id: sid,
        type: "raster",
        source: sid,
        paint: { "raster-opacity": layer.opacity },
        layout: { visibility: layer.visible ? "visible" : "none" },
      },
      rasterCeiling(map),
    );
    // Opacity/visibility deliberately excluded: they must not recreate the
    // source. Their own effects below handle changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ready, sid, tileUrl]);

  // Remove source + layer only when the store layer is removed (unmount).
  useEffect(() => {
    if (!map) return;
    return () => {
      // The whole map may already be destroyed (view switch unmounts
      // MapProvider); ops on a removed map throw.
      try {
        if (map.getLayer(sid)) map.removeLayer(sid);
        if (map.getSource(sid)) map.removeSource(sid);
      } catch {
        /* map already removed */
      }
    };
  }, [map, sid]);

  useEffect(() => {
    if (!map || !ready || !map.getLayer(sid)) return;
    map.setPaintProperty(sid, "raster-opacity", layer.opacity);
  }, [map, ready, sid, layer.opacity, tileUrl]);

  useEffect(() => {
    if (!map || !ready || !map.getLayer(sid)) return;
    map.setLayoutProperty(sid, "visibility", layer.visible ? "visible" : "none");
  }, [map, ready, sid, layer.visible, tileUrl]);
}

/** Re-assert store order on the map: bottom→top moveLayer sweeps, capped
 *  below the wind particles. The terra-draw ROI outline goes on top only
 *  while the user is drawing; otherwise it sits *beneath* the data rasters
 *  so it never tints the displayed data. Never touches sources. */
export function applyLayerOrder(
  map: MapLibreMap,
  orderedLayerIds: string[],
  drawActive = false,
): void {
  const ceiling = rasterCeiling(map);
  for (const layerId of orderedLayerIds) {
    const sid = sourceIdFor(layerId);
    if (map.getLayer(sid)) map.moveLayer(sid, ceiling);
  }
  // Bottom-most existing raster; undefined (→ top) when nothing is minted yet.
  const bottomRaster = orderedLayerIds.map(sourceIdFor).find((sid) => map.getLayer(sid));
  const drawTarget = drawActive ? undefined : bottomRaster;
  for (const id of terraDrawLayerIds(map)) map.moveLayer(id, drawTarget);
}
