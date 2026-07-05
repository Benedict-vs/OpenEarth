/**
 * Wires one layer to the re-mint scheduler: map error events for this
 * layer's source count toward the error burst; successful mints re-arm the
 * 75 % timer; firing re-runs the shared mint path (setTiles happens in
 * useRasterLayer when the new mint lands in the store — the source object
 * survives, so there is no flicker and no z-order change).
 */
import { useEffect, useRef } from "react";
import { useMapContext } from "./MapContext";
import { createRemintScheduler, type RemintScheduler } from "./remintScheduler";
import { mintLayerNow } from "./useMintLayer";
import { sourceIdFor } from "./useRasterLayer";
import type { Layer } from "../stores/layersStore";

export function useTileRemint(layer: Layer): void {
  const { map, ready } = useMapContext();
  const schedulerRef = useRef<RemintScheduler | null>(null);

  useEffect(() => {
    if (!map || !ready) return;
    const scheduler = createRemintScheduler({
      onRemint: () => void mintLayerNow(layer.id),
    });
    schedulerRef.current = scheduler;

    const sid = sourceIdFor(layer.id);
    const onError = (event: unknown) => {
      const sourceId = (event as { sourceId?: string }).sourceId;
      if (sourceId === sid) scheduler.noteTileError();
    };
    map.on("error", onError);

    return () => {
      map.off("error", onError);
      scheduler.dispose();
      schedulerRef.current = null;
    };
  }, [map, ready, layer.id]);

  const mintedAt = layer.mint?.mintedAt ?? null;
  const expiresAt = layer.mint?.expiresAt ?? null;
  useEffect(() => {
    if (mintedAt !== null && expiresAt !== null) {
      schedulerRef.current?.noteMint(mintedAt, expiresAt);
    }
  }, [mintedAt, expiresAt]);
}
