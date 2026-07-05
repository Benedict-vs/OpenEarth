/**
 * Thin custom MapLibre binding (deliberately not react-map-gl): one Map
 * instance created imperatively, exposed via context. Layer hooks mutate the
 * map directly — animation and layer controls must never round-trip React
 * renders or touch raster sources.
 */
import maplibregl from "maplibre-gl";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { BASEMAP_STYLES, DEFAULT_BASEMAP } from "./basemap";

interface MapContextValue {
  map: maplibregl.Map | null;
  /** True once the style has loaded — sources/layers may only be added then. */
  ready: boolean;
}

const MapContext = createContext<MapContextValue>({ map: null, ready: false });

export function MapProvider({ children, south }: { children: ReactNode; south?: ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [value, setValue] = useState<MapContextValue>({ map: null, ready: false });

  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: BASEMAP_STYLES[DEFAULT_BASEMAP],
      center: [8.68, 49.41], // Heidelberg
      zoom: 5,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl(), "bottom-left");

    setValue({ map, ready: false });
    map.on("load", () => setValue({ map, ready: true }));

    return () => {
      setValue({ map: null, ready: false });
      map.remove();
    };
  }, []);

  return (
    <MapContext.Provider value={value}>
      {/* The map and an optional region below it (the analysis drawer) share a
          column so the map flexes as the drawer opens and closes. */}
      <div className="map-column">
        <div ref={containerRef} className="map-container" data-testid="map" />
        {south}
      </div>
      {children}
    </MapContext.Provider>
  );
}

export function useMapContext(): MapContextValue {
  return useContext(MapContext);
}
