import Compare from "@maplibre/maplibre-gl-compare";
import "@maplibre/maplibre-gl-compare/dist/maplibre-gl-compare.css";
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { BASEMAP_STYLES, DEFAULT_BASEMAP } from "../../map/basemap";
import { MapContext } from "../../map/MapContext";
import { useCompareSide } from "../../map/useCompareSide";
import { useCompareStore } from "../../stores/compareStore";
import { CompareControls } from "./CompareControls";

/** Headless per-side controller: binds the side's layer to its own map. */
function SideController({ side }: { side: "left" | "right" }) {
  useCompareSide(side);
  return null;
}

/**
 * Side-by-side comparison. Two MapLibre maps in one wrapper, joined by
 * @maplibre/maplibre-gl-compare (which syncs movement internally via
 * mapbox-gl-sync-move — we add no sync of our own). Each side's layers bind to
 * its own map through a per-instance MapContext provider.
 */
export function CompareView() {
  const beforeRef = useRef<HTMLDivElement>(null);
  const afterRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [maps, setMaps] = useState<{ a: maplibregl.Map; b: maplibregl.Map } | null>(null);
  const [ready, setReady] = useState(false);
  const orientation = useCompareStore((s) => s.orientation);

  // Create both maps once (mirrors MapProvider's one-time init effect).
  useEffect(() => {
    const opts = {
      style: BASEMAP_STYLES[DEFAULT_BASEMAP],
      center: [8.68, 49.41] as [number, number],
      zoom: 5,
    };
    const a = new maplibregl.Map({ container: beforeRef.current!, ...opts });
    const b = new maplibregl.Map({
      container: afterRef.current!,
      ...opts,
      attributionControl: { compact: true },
    });
    let loaded = 0;
    const onLoad = () => {
      loaded += 1;
      if (loaded === 2) setReady(true);
    };
    a.on("load", onLoad);
    b.on("load", onLoad);
    setMaps({ a, b });
    return () => {
      setReady(false);
      setMaps(null);
      a.remove();
      b.remove();
    };
  }, []);

  // Construct the swiper after both maps' styles load; rebuild on orientation
  // change; .remove() on unmount so the sync handlers never leak.
  useEffect(() => {
    if (!maps || !ready || !wrapRef.current) return;
    const compare = new Compare(maps.a, maps.b, wrapRef.current, { orientation });
    return () => compare.remove();
  }, [maps, ready, orientation]);

  return (
    <div className="compare-view">
      <div className="compare-maps" ref={wrapRef}>
        <div ref={beforeRef} className="compare-map" data-testid="compare-map-a" />
        <div ref={afterRef} className="compare-map" data-testid="compare-map-b" />
      </div>
      {maps && ready ? (
        <>
          <MapContext.Provider value={{ map: maps.a, ready }}>
            <SideController side="left" />
          </MapContext.Provider>
          <MapContext.Provider value={{ map: maps.b, ready }}>
            <SideController side="right" />
          </MapContext.Provider>
        </>
      ) : null}
      <CompareControls />
    </div>
  );
}
