/**
 * Playback-mode overlay: binds a finished render's frame sequence to a MapLibre
 * **image source** and drives it from the shared frame transport. Each tick
 * calls `ImageSource.updateImage({ url })` — frames are preloaded first (by the
 * transport), so there is zero tile churn and zero React re-render per frame.
 *
 * The image source `coordinates` use the pinned corner order (top-left,
 * top-right, bottom-right, bottom-left); a wrong order renders mirrored.
 */
import type { ImageSource, Map as MapLibreMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo } from "react";
import { frameUrl } from "../api/timelapseQueries";
import { useFrameTransport, type FrameTransport } from "../features/timelapse/useFrameTransport";
import { imageSourceCorners } from "../lib/animation";

const SOURCE_ID = "oe-anim-frames";
const LAYER_ID = "oe-anim-frames-layer";

export interface FrameRender {
  id: string;
  frameCount: number;
  bbox: { west: number; south: number; east: number; north: number };
}

export function useImageFrames(
  map: MapLibreMap | null,
  ready: boolean,
  render: FrameRender | null,
  opts: { fps: number; loop: boolean; enabled: boolean },
): FrameTransport {
  const active = opts.enabled && render !== null;

  const frames = useMemo(
    () =>
      active && render
        ? Array.from({ length: render.frameCount }, (_, i) => frameUrl(render.id, i))
        : [],
    [active, render],
  );

  const onFrame = useCallback(
    (_index: number, img: HTMLImageElement) => {
      if (!map) return;
      const src = map.getSource(SOURCE_ID) as ImageSource | undefined;
      src?.updateImage({ url: img.src });
    },
    [map],
  );

  const transport = useFrameTransport(frames, { fps: opts.fps, loop: opts.loop, onFrame });

  // Image source + raster layer lifecycle, torn down when disabled/unmounted.
  useEffect(() => {
    if (!map || !ready || !active || !render) return undefined;
    const coordinates = imageSourceCorners(render.bbox);
    if (!map.getSource(SOURCE_ID)) {
      map.addSource(SOURCE_ID, { type: "image", url: frameUrl(render.id, 0), coordinates });
      map.addLayer({
        id: LAYER_ID,
        type: "raster",
        source: SOURCE_ID,
        paint: { "raster-opacity": 1 },
      });
    } else {
      (map.getSource(SOURCE_ID) as ImageSource).setCoordinates(coordinates);
    }
    return () => {
      try {
        if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
        if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
      } catch {
        /* map already removed on view switch */
      }
    };
  }, [map, ready, active, render]);

  return transport;
}
