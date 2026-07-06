/**
 * Browse-mode transport: a date slider over the active layer's date axis. Each
 * step is a `date_window` composite, but we keep a small pool of **hidden,
 * preloaded raster sources** at ±2 steps and swap the visible one with
 * `setLayoutProperty(visibility)` — never a re-mint on the visible layer
 * (no-refetch rule). At most 2 mint-ahead requests are ever in flight.
 */
import type { Map as MapLibreMap } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { mintTiles } from "../api/queries";
import type { TilesRequest } from "../api/types";
import { evictableKeys, poolIndices } from "../lib/animation";
import type { Layer } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";

const POOL_RADIUS = 2;
const MAX_IN_FLIGHT = 2;

type FrameStatus = "minting" | "ready" | "error";

interface PoolEntry {
  status: FrameStatus;
  tileUrl?: string;
}

function sourceIdForFrame(i: number): string {
  return `oe-anim-b-${i}`;
}

export interface BrowseFrames {
  /** Per-index load status, for the UI dots. */
  status: Record<number, FrameStatus>;
}

export function useBrowseFrames(
  map: MapLibreMap | null,
  ready: boolean,
  layer: Layer | null,
  dates: string[],
  index: number,
  opts: { enabled: boolean; halfWindowDays: number; opacity: number },
): BrowseFrames {
  const roi = useRoiStore((s) => s.roi);
  const pool = useRef<Map<number, PoolEntry>>(new Map());
  const inFlight = useRef(0);
  const [status, setStatus] = useState<Record<number, FrameStatus>>({});

  const active = opts.enabled && layer !== null && dates.length > 1;

  // A stable key: any change to layer identity / roi / dates rebuilds the pool.
  const poolKey = active
    ? JSON.stringify([layer.dataset, layer.product, layer.vizOverrides, roi, dates])
    : "";

  const publishStatus = useCallback(() => {
    const next: Record<number, FrameStatus> = {};
    for (const [i, entry] of pool.current) next[i] = entry.status;
    setStatus(next);
  }, []);

  const teardown = useCallback(() => {
    if (map) {
      for (const i of pool.current.keys()) {
        const sid = sourceIdForFrame(i);
        try {
          if (map.getLayer(sid)) map.removeLayer(sid);
          if (map.getSource(sid)) map.removeSource(sid);
        } catch {
          /* map already removed */
        }
      }
    }
    pool.current.clear();
    inFlight.current = 0;
  }, [map]);

  // Swap which pooled source is visible: only the current index, and only once
  // it is ready — pure layout property changes, never a source touch.
  const swapVisible = useCallback(
    (current: number) => {
      if (!map) return;
      for (const [i, entry] of pool.current) {
        const sid = sourceIdForFrame(i);
        if (!map.getLayer(sid)) continue;
        const visible = i === current && entry.status === "ready";
        map.setLayoutProperty(sid, "visibility", visible ? "visible" : "none");
      }
    },
    [map],
  );

  const mint = useCallback(
    async (i: number, currentIndex: number) => {
      const targetDate = dates[i];
      if (!map || !layer || targetDate === undefined) return;
      if (pool.current.has(i) || inFlight.current >= MAX_IN_FLIGHT) return;
      pool.current.set(i, { status: "minting" });
      inFlight.current += 1;
      publishStatus();

      const req: TilesRequest = {
        dataset: layer.dataset,
        product: layer.product,
        roi: roi ?? null,
        viz_overrides: layer.vizOverrides ?? null,
        auto_range: layer.autoRange,
        composite: "date_window",
        target_date: targetDate,
        half_window_days: opts.halfWindowDays,
      };
      try {
        const res = await mintTiles(req);
        if (!pool.current.has(i)) return; // evicted while minting
        const sid = sourceIdForFrame(i);
        if (!map.getSource(sid)) {
          map.addSource(sid, {
            type: "raster",
            tiles: [res.tile_url],
            tileSize: 256,
            attribution: res.attribution,
          });
          map.addLayer({
            id: sid,
            type: "raster",
            source: sid,
            paint: { "raster-opacity": opts.opacity },
            layout: { visibility: "none" },
          });
        }
        pool.current.set(i, { status: "ready", tileUrl: res.tile_url });
      } catch {
        pool.current.set(i, { status: "error" });
      } finally {
        inFlight.current = Math.max(0, inFlight.current - 1);
        publishStatus();
        swapVisible(currentIndex);
        // Fill any remaining pool slots freed up by this completion.
        ensurePoolRef.current(currentIndex);
      }
    },
    [map, layer, roi, dates, opts.halfWindowDays, opts.opacity, publishStatus, swapVisible],
  );

  const ensurePoolRef = useRef<(current: number) => void>(() => {});
  const ensurePool = useCallback(
    (current: number) => {
      if (!map || !active) return;
      const keep = poolIndices(current, dates.length, POOL_RADIUS);
      for (const key of evictableKeys([...pool.current.keys()], keep)) {
        const sid = sourceIdForFrame(key);
        try {
          if (map.getLayer(sid)) map.removeLayer(sid);
          if (map.getSource(sid)) map.removeSource(sid);
        } catch {
          /* ignore */
        }
        pool.current.delete(key);
      }
      // Mint the current frame first, then its neighbours (nearest-out order).
      const ordered = [current, ...keep.filter((k) => k !== current)];
      for (const i of ordered) {
        if (!pool.current.has(i) && inFlight.current < MAX_IN_FLIGHT) void mint(i, current);
      }
      publishStatus();
    },
    [map, active, dates.length, mint, publishStatus],
  );
  ensurePoolRef.current = ensurePool;

  // Rebuild the pool when the animation source parameters change.
  useEffect(() => {
    teardown();
    setStatus({});
    if (!map || !ready || !active) return;
    ensurePoolRef.current(index);
    return () => teardown();
    // index intentionally excluded here — its own effect below drives stepping.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ready, active, poolKey, teardown]);

  // On index change: swap the visible frame and top up the pool ahead.
  useEffect(() => {
    if (!map || !ready || !active) return;
    swapVisible(index);
    ensurePoolRef.current(index);
  }, [map, ready, active, index, swapVisible]);

  return { status };
}
