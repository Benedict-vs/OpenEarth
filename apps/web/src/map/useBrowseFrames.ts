/**
 * Browse-mode transport: a date slider over the active layer's date axis. Each
 * step is a `date_window` composite, but we keep a small pool of **hidden,
 * preloaded raster sources** at ±2 steps and swap the visible one with
 * `setLayoutProperty(visibility)` — never a re-mint on the visible layer
 * (no-refetch rule). At most 2 mint-ahead requests are ever in flight.
 */
import type { Map as MapLibreMap } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { mintTiles } from "../api/queries";
import type { TilesRequest } from "../api/types";
import { evictableKeys, poolIndices, type FrameStatus } from "../lib/animation";
import { windowMeanDates } from "../lib/timeWindow";
import type { Layer } from "../stores/layersStore";
import { useRoiStore } from "../stores/roiStore";

const POOL_RADIUS = 2;
// EE-budget bound: at most this many mint-ahead requests are ever in flight,
// even under "Prefetch all" (which widens the pool radius, not this cap).
const MAX_IN_FLIGHT = 2;

interface PoolEntry {
  status: FrameStatus;
  tileUrl?: string;
}

function sourceIdForFrame(i: number): string {
  return `oe-anim-b-${i}`;
}

export interface BrowseFrames {
  /** Per-index load status, for the UI dots (React state; one render behind). */
  status: Record<number, FrameStatus>;
  /** The same status, updated *synchronously* with the pool — the play timer
   *  reads this so it never advances past a frame that is not yet ready. */
  statusRef: MutableRefObject<Record<number, FrameStatus>>;
}

export function useBrowseFrames(
  map: MapLibreMap | null,
  ready: boolean,
  layer: Layer | null,
  dates: string[],
  index: number,
  opts: { enabled: boolean; halfDays: number; opacity: number; poolRadius?: number },
): BrowseFrames {
  const roi = useRoiStore((s) => s.roi);
  const pool = useRef<Map<number, PoolEntry>>(new Map());
  const inFlight = useRef(0);
  const [status, setStatus] = useState<Record<number, FrameStatus>>({});
  const statusRef = useRef<Record<number, FrameStatus>>({});
  // The *live* index, so a mint's follow-up fill always targets the current
  // frame — never a stale captured index (competing ensurePool windows evict
  // each other's edge frames into an infinite re-mint loop when play holds).
  const indexRef = useRef(index);

  const active = opts.enabled && layer !== null && dates.length > 1;
  const radius = opts.poolRadius ?? POOL_RADIUS;

  // A stable key: any change to layer identity / roi / dates / window width
  // rebuilds the pool (each frame is a window, so a width change is new data).
  const poolKey = active
    ? JSON.stringify([layer.dataset, layer.product, layer.vizOverrides, roi, dates, opts.halfDays])
    : "";

  const publishStatus = useCallback(() => {
    const next: Record<number, FrameStatus> = {};
    for (const [i, entry] of pool.current) next[i] = entry.status;
    statusRef.current = next; // synchronous — read by the play timer
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
    async (i: number) => {
      const frameDate = dates[i];
      if (!map || !layer || frameDate === undefined) return;
      if (pool.current.has(i) || inFlight.current >= MAX_IN_FLIGHT) return;
      pool.current.set(i, { status: "minting" });
      inFlight.current += 1;
      publishStatus();

      // Each frame is a window centered on its date; compile it to a mean
      // composite (windowMeanDates) so a wide window never rides the tiles
      // `half_window_days` cap. `half_window_days` is vestigial for mean.
      const req: TilesRequest = {
        dataset: layer.dataset,
        product: layer.product,
        roi: roi ?? null,
        viz_overrides: layer.vizOverrides ?? null,
        auto_range: layer.autoRange,
        composite: "mean",
        dates: windowMeanDates({ center: frameDate, halfDays: opts.halfDays }),
        half_window_days: 0,
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
        // Follow-up work targets the *live* index, so all in-flight mints agree
        // on one keep-window (no competing eviction loop).
        swapVisible(indexRef.current);
        ensurePoolRef.current(indexRef.current);
      }
    },
    [map, layer, roi, dates, opts.halfDays, opts.opacity, publishStatus, swapVisible],
  );

  const ensurePoolRef = useRef<(current: number) => void>(() => {});
  const ensurePool = useCallback(
    (current: number) => {
      if (!map || !active) return;
      const keep = poolIndices(current, dates.length, radius);
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
        if (!pool.current.has(i) && inFlight.current < MAX_IN_FLIGHT) void mint(i);
      }
      publishStatus();
    },
    [map, active, dates.length, radius, mint, publishStatus],
  );
  ensurePoolRef.current = ensurePool;

  // Rebuild the pool when the animation source parameters change.
  useEffect(() => {
    teardown();
    statusRef.current = {};
    setStatus({});
    if (!map || !ready || !active) return;
    ensurePoolRef.current(index);
    return () => teardown();
    // index intentionally excluded here — its own effect below drives stepping.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, ready, active, poolKey, teardown]);

  // On index change (or when "Prefetch all" widens the radius): swap the
  // visible frame and top up the pool ahead.
  useEffect(() => {
    indexRef.current = index;
    if (!map || !ready || !active) return;
    swapVisible(index);
    ensurePoolRef.current(index);
  }, [map, ready, active, index, radius, swapVisible]);

  return { status, statusRef };
}
