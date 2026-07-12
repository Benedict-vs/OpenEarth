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
import { windowMeanDates, type TimeWindow } from "../lib/timeWindow";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore, type Layer } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";

// The layer window compiles to a `composite: "mean"` request over the window's
// exclusive-end dates (windowMeanDates) — never `date_window`, whose
// `half_window_days` is capped at 30 and would 422 a ±45/custom window.
// `half_window_days` is vestigial for a mean composite (the server reads it only
// for `date_window`); the generated type still requires the field, so it is
// pinned to 0 and the width rides `dates`.
const MEAN_HALF_WINDOW_DAYS = 0;

export function buildTilesRequest(
  layer: Pick<Layer, "dataset" | "product" | "vizOverrides" | "autoRange" | "ref">,
  roi: TilesRequest["roi"],
  window: TimeWindow,
): TilesRequest {
  const dates = windowMeanDates(window);
  // Two-window compare product: post = the layer window, pre = the layer's ref.
  if (layer.ref) {
    return {
      dataset: layer.dataset,
      product: layer.product,
      roi: roi ?? null,
      viz_overrides: layer.vizOverrides ?? null,
      auto_range: layer.autoRange,
      composite: "mean",
      dates,
      ref: { start: layer.ref.start, end: layer.ref.end },
      half_window_days: MEAN_HALF_WINDOW_DAYS,
    };
  }
  return {
    dataset: layer.dataset,
    product: layer.product,
    roi: roi ?? null,
    viz_overrides: layer.vizOverrides ?? null,
    auto_range: layer.autoRange,
    composite: "mean",
    dates,
    half_window_days: MEAN_HALF_WINDOW_DAYS,
  };
}

/** The layer's current mint parameters, serialized (stable enough here:
 *  key order is construction order, which is fixed in buildTilesRequest). */
function currentParamsKey(layerId: string): string | null {
  const layer = useLayersStore.getState().layers.find((l) => l.id === layerId);
  if (!layer) return null;
  const body = buildTilesRequest(layer, useRoiStore.getState().roi, useDateStore.getState().window);
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
  const window = useDateStore((state) => state.window);

  const paramsKey = JSON.stringify(
    buildTilesRequest(
      {
        dataset: layer.dataset,
        product: layer.product,
        vizOverrides: layer.vizOverrides,
        autoRange: layer.autoRange,
        ref: layer.ref,
      },
      roi,
      window,
    ),
  );

  useEffect(() => {
    void mintLayerNow(layer.id);
  }, [layer.id, paramsKey]);
}
