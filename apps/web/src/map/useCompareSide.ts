/**
 * One Compare side: mints a `date_window` composite for the side's config and
 * binds it to *this side's* map via the shared `useRasterLayer` (which reads
 * the per-side `MapContext`). Minting is local to the side (not the global
 * layers store), and the expiry re-mint is per-instance — nothing here is
 * module-scoped, so the two maps never contend.
 */
import { useEffect, useMemo, useState } from "react";
import { mintTiles } from "../api/queries";
import type { Legend, TilesRequest } from "../api/types";
import { isoToMs, remintAtMs } from "../lib/time";
import { useCompareStore } from "../stores/compareStore";
import type { Layer, LayerMint, LayerStatus } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";
import { useRasterLayer } from "./useRasterLayer";

export interface CompareSideState {
  status: LayerStatus;
  error: string | null;
  legend: Legend | null;
}

export function useCompareSide(side: "left" | "right"): CompareSideState {
  const cfg = useCompareStore((s) => s[side]);
  const roi = useRoiStore((s) => s.roi);

  const [mint, setMint] = useState<LayerMint | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const key = useMemo(
    () =>
      JSON.stringify({
        dataset: cfg.dataset,
        product: cfg.product,
        roi: roi ?? null,
        viz_overrides: cfg.viz ?? null,
        auto_range: false,
        composite: "date_window",
        target_date: cfg.date,
        half_window_days: 3,
      } satisfies TilesRequest),
    [cfg, roi],
  );

  // Mint on param change or expiry bump; keep the old tiles until the new mint
  // lands (useRasterLayer.setTiles swaps them) — no blank flash.
  useEffect(() => {
    let cancelled = false;
    mintTiles(JSON.parse(key) as TilesRequest)
      .then((res) => {
        if (cancelled) return;
        setMint({
          tileUrl: res.tile_url,
          mintedAt: Date.now(),
          expiresAt: isoToMs(res.expires_at),
          attribution: res.attribution,
          legend: res.legend,
          paramsKey: key,
        });
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [key, nonce]);

  // Per-instance expiry re-mint at 75 % of the tile URL's lifetime.
  useEffect(() => {
    if (!mint) return;
    const delay = Math.max(0, remintAtMs(mint.mintedAt, mint.expiresAt) - Date.now());
    const timer = setTimeout(() => setNonce((n) => n + 1), delay);
    return () => clearTimeout(timer);
  }, [mint]);

  const status: LayerStatus = error ? "error" : mint ? "ready" : "minting";
  const layer: Layer = {
    id: `cmp-${side}`,
    dataset: cfg.dataset,
    product: cfg.product,
    label: `${cfg.dataset} · ${cfg.product}`,
    opacity: 1,
    visible: true,
    vizOverrides: cfg.viz,
    autoRange: false,
    mint,
    status,
    error,
  };
  useRasterLayer(layer);

  return { status, error, legend: mint?.legend ?? null };
}
