/**
 * Mints (and re-mints) a layer's tile URL whenever its *data* parameters
 * change: dataset/product/viz, the shared ROI, or the shared dates.
 * Opacity/order/visibility deliberately never reach this hook.
 */
import { useEffect, useRef } from "react";
import { mintTiles } from "../api/queries";
import type { TilesRequest } from "../api/types";
import { useDateStore } from "../stores/dateStore";
import { useLayersStore, type Layer } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { isoToMs } from "../lib/time";

export function buildTilesRequest(
  layer: Pick<Layer, "dataset" | "product" | "vizOverrides">,
  roi: TilesRequest["roi"],
  dates: {
    mode: "range" | "single";
    start: string;
    end: string;
    targetDate: string;
    halfWindowDays: number;
  },
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

export function useMintLayer(layer: Layer): void {
  const roi = useRoiStore((state) => state.roi);
  const mode = useDateStore((state) => state.mode);
  const start = useDateStore((state) => state.start);
  const end = useDateStore((state) => state.end);
  const targetDate = useDateStore((state) => state.targetDate);
  const halfWindowDays = useDateStore((state) => state.halfWindowDays);
  const { setMinting, setMint, setError } = useLayersStore.getState();

  // Guards a stale response landing after newer params were requested.
  const requestToken = useRef(0);

  const paramsKey = JSON.stringify(
    buildTilesRequest(
      { dataset: layer.dataset, product: layer.product, vizOverrides: layer.vizOverrides },
      roi,
      { mode, start, end, targetDate, halfWindowDays },
    ),
  );

  useEffect(() => {
    const token = ++requestToken.current;
    const body = JSON.parse(paramsKey) as TilesRequest;
    setMinting(layer.id);
    mintTiles(body)
      .then((response) => {
        if (requestToken.current !== token) return;
        setMint(layer.id, {
          tileUrl: response.tile_url,
          mintedAt: Date.now(),
          expiresAt: isoToMs(response.expires_at),
          attribution: response.attribution,
          legend: response.legend,
        });
      })
      .catch((error: unknown) => {
        if (requestToken.current !== token) return;
        setError(layer.id, error instanceof Error ? error.message : String(error));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layer.id, paramsKey]);
}
