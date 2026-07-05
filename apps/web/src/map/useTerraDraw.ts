/**
 * ROI drawing via terra-draw (maintained MapLibre support, unlike
 * mapbox-gl-draw). Rectangle finishes become bbox ROIs, polygons become
 * polygon ROIs; the drawn shape stays on the map as the ROI outline.
 */
import { useEffect, useRef, useState } from "react";
import { TerraDraw, TerraDrawPolygonMode, TerraDrawRectangleMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import { ringToBBox, ringToPolygon, useRoiStore } from "../stores/roiStore";
import { useMapContext } from "./MapContext";

export type DrawMode = "static" | "polygon" | "rectangle";

export interface DrawApi {
  mode: DrawMode;
  setMode(mode: DrawMode): void;
  clear(): void;
}

export function useTerraDraw(): DrawApi {
  const { map, ready } = useMapContext();
  const drawRef = useRef<TerraDraw | null>(null);
  const [mode, setModeState] = useState<DrawMode>("static");

  useEffect(() => {
    if (!map || !ready) return;
    const draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map }),
      modes: [new TerraDrawPolygonMode(), new TerraDrawRectangleMode()],
    });
    draw.start();
    draw.setMode("static");

    draw.on("finish", (id, context) => {
      if (context.action !== "draw") return;
      const feature = draw.getSnapshot().find((f) => f.id === id);
      if (!feature || feature.geometry.type !== "Polygon") return;
      const ring = feature.geometry.coordinates[0] as [number, number][];
      const roi = context.mode === "rectangle" ? ringToBBox(ring) : ringToPolygon(ring);
      // Keep only the finished shape as the visible ROI outline.
      for (const other of draw.getSnapshot()) {
        if (other.id !== id && other.id !== undefined) draw.removeFeatures([other.id]);
      }
      useRoiStore.getState().setRoi(roi);
      draw.setMode("static");
      setModeState("static");
    });

    drawRef.current = draw;
    return () => {
      try {
        draw.stop();
      } catch {
        /* map already removed (view switch) */
      }
      drawRef.current = null;
    };
  }, [map, ready]);

  return {
    mode,
    setMode: (next) => {
      const draw = drawRef.current;
      if (!draw) return;
      if (next !== "static") draw.clear();
      draw.setMode(next);
      setModeState(next);
    },
    clear: () => {
      drawRef.current?.clear();
      drawRef.current?.setMode("static");
      setModeState("static");
    },
  };
}
