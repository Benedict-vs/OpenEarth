/**
 * Mints (and re-mints) a layer's tile URL whenever its *data* parameters
 * change: dataset/product/viz, the shared ROI, or the shared dates.
 * Opacity/order/visibility deliberately never reach this hook.
 *
 * `mintLayerNow` is the single mint path, shared with the expiry re-mint
 * (useTileRemint). Staleness guard: a response is dropped if the layer's
 * current parameters no longer match the ones it was requested with.
 */
import { useEffect } from "react";
import { mintTiles } from "../api/queries";
import type { TilesRequest } from "../api/types";
import { isoToMs, remintAtMs } from "../lib/time";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore, type Layer } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";

interface DateParams {
  mode: "range" | "single";
  start: string;
  end: string;
  targetDate: string;
  halfWindowDays: number;
}

export function buildTilesRequest(
  layer: Pick<Layer, "dataset" | "product" | "vizOverrides">,
  roi: TilesRequest["roi"],
  dates: DateParams,
): TilesRequest {
  if (dates.mode === "single") {
    return {
      dataset: layer.dataset,
      product: layer.product,
      roi: roi ?? null,
      viz_overrides: layer.vizOverrides ?? null,
      composite: "date_window",
      target_date: dates.targetDate,
      half_window_days: dates.halfWindowDays,
    };
  }
  return {
    dataset: layer.dataset,
    product: layer.product,
    roi: roi ?? null,
    viz_overrides: layer.vizOverrides ?? null,
    composite: "mean",
    dates: { start: dates.start, end: dates.end },
    half_window_days: dates.halfWindowDays,
  };
}

/** The layer's current mint parameters, serialized (stable enough here:
 *  key order is construction order, which is fixed in buildTilesRequest). */
function currentParamsKey(layerId: string): string | null {
  const layer = useLayersStore.getState().layers.find((l) => l.id === layerId);
  if (!layer) return null;
  const { mode, start, end, targetDate, halfWindowDays } = useDateStore.getState();
  const body = buildTilesRequest(layer, useRoiStore.getState().roi, {
    mode,
    start,
    end,
    targetDate,
    halfWindowDays,
  });
  return JSON.stringify(body);
}

// One request per (layer, params) at a time — dev remounts (StrictMode) and
// scheduler/param races must not stack identical mints onto the EE budget.
const inFlight = new Map<string, string>();

export async function mintLayerNow(layerId: string, options?: { force?: boolean }): Promise<void> {
  const requestKey = currentParamsKey(layerId);
  if (requestKey === null) return;
  if (inFlight.get(layerId) === requestKey) return;

  const { setMinting, setMint, setError } = useLayersStore.getState();
  const layer = useLayersStore.getState().layers.find((l) => l.id === layerId);
  // A fresh mint with identical params is still valid — skip unless forced
  // (the expiry re-mint passes force to get a new URL for the same params).
  if (
    !options?.force &&
    layer?.status === "ready" &&
    layer.mint?.paramsKey === requestKey &&
    Date.now() < remintAtMs(layer.mint.mintedAt, layer.mint.expiresAt)
  ) {
    return;
  }

  inFlight.set(layerId, requestKey);
  setMinting(layerId);
  try {
    const response = await mintTiles(JSON.parse(requestKey) as TilesRequest);
    if (currentParamsKey(layerId) !== requestKey) return; // params changed meanwhile
    setMint(layerId, {
      tileUrl: response.tile_url,
      mintedAt: Date.now(),
      expiresAt: isoToMs(response.expires_at),
      attribution: response.attribution,
      legend: response.legend,
      paramsKey: requestKey,
    });
  } catch (error: unknown) {
    if (currentParamsKey(layerId) !== requestKey) return;
    setError(layerId, error instanceof Error ? error.message : String(error));
  } finally {
    if (inFlight.get(layerId) === requestKey) inFlight.delete(layerId);
  }
}

export function useMintLayer(layer: Layer): void {
  const roi = useRoiStore((state) => state.roi);
  const mode = useDateStore((state) => state.mode);
  const start = useDateStore((state) => state.start);
  const end = useDateStore((state) => state.end);
  const targetDate = useDateStore((state) => state.targetDate);
  const halfWindowDays = useDateStore((state) => state.halfWindowDays);

  const paramsKey = JSON.stringify(
    buildTilesRequest(
      { dataset: layer.dataset, product: layer.product, vizOverrides: layer.vizOverrides },
      roi,
      { mode, start, end, targetDate, halfWindowDays },
    ),
  );

  useEffect(() => {
    void mintLayerNow(layer.id);
  }, [layer.id, paramsKey]);
}
