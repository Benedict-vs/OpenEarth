/**
 * Embeddings Explorer — AlphaEarth satellite-embedding similarity / change / clusters.
 *
 * Its own imperative MapLibre instance (Compare/Lab precedent). One raster source
 * holds the active layer; re-mints swap URLs via setTiles (no-refetch rule). The
 * CC-BY attribution is mandatory and shown in the footer.
 */
import maplibregl from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  mintChange,
  mintCluster,
  mintSimilarity,
  useEmbeddingYears,
} from "../../api/embeddingsQueries";
import type { EmbeddingTile } from "../../api/types";
import { BASEMAP_STYLES, DEFAULT_BASEMAP } from "../../map/basemap";

const ATTRIBUTION =
  "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind.";
const LAYER_SRC = "embed-src";

type Mode = "similarity" | "change" | "cluster";
type Seed = { lat: number; lon: number };

export function EmbeddingsView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const modeRef = useRef<Mode>("similarity");

  const { data: yearsData } = useEmbeddingYears(true);
  const years = useMemo(() => yearsData?.years ?? [], [yearsData]);

  const [mode, setMode] = useState<Mode>("similarity");
  const [year, setYear] = useState<number | null>(null);
  const [yearB, setYearB] = useState<number | null>(null);
  const [k, setK] = useState(6);
  const [seed, setSeed] = useState<Seed | null>(null);
  const [tile, setTile] = useState<EmbeddingTile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  modeRef.current = mode;

  // Default the years once the collection range arrives (newest for the single-year
  // modes; an earlier year for the change B-slot).
  useEffect(() => {
    const newest = years.at(-1);
    const oldest = years.at(0);
    if (newest !== undefined && oldest !== undefined && year === null) {
      setYear(newest);
      setYearB(oldest);
    }
  }, [years, year]);

  // Create the map once (Heidelberg — the exit-gate demo: river vs forest vs urban).
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: BASEMAP_STYLES[DEFAULT_BASEMAP],
      center: [8.68, 49.41],
      zoom: 11,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    mapRef.current = map;
    map.on("load", () => {
      readyRef.current = true;
    });
    map.on("click", (e) => {
      if (modeRef.current !== "similarity") return;
      setSeed({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    });
    return () => {
      readyRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Bind the active tile to one raster source (create once, setTiles on re-mint).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    if (!tile) return;
    const existing = map.getSource(LAYER_SRC) as maplibregl.RasterTileSource | undefined;
    if (existing) {
      existing.setTiles([tile.tile_url]);
    } else {
      map.addSource(LAYER_SRC, {
        type: "raster",
        tiles: [tile.tile_url],
        tileSize: 256,
        attribution: ATTRIBUTION,
      });
      map.addLayer({
        id: LAYER_SRC,
        type: "raster",
        source: LAYER_SRC,
        paint: { "raster-opacity": 0.85 },
      });
    }
  }, [tile]);

  // Seed marker (similarity mode only).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markerRef.current?.remove();
    markerRef.current = null;
    if (mode === "similarity" && seed) {
      markerRef.current = new maplibregl.Marker({ color: "#ef4444" })
        .setLngLat([seed.lon, seed.lat])
        .addTo(map);
    }
  }, [seed, mode]);

  const run = useCallback(async (fn: () => Promise<EmbeddingTile>) => {
    setBusy(true);
    setError(null);
    try {
      setTile(await fn());
    } catch (e) {
      setError((e as Error)?.message ?? "Request failed.");
    } finally {
      setBusy(false);
    }
  }, []);

  // Similarity: re-mint when the seed or year changes.
  useEffect(() => {
    if (mode !== "similarity" || !seed || year === null) return;
    void run(() => mintSimilarity({ lat: seed.lat, lon: seed.lon, year }));
  }, [mode, seed, year, run]);

  // Change: re-mint when either year changes.
  useEffect(() => {
    if (mode !== "change" || year === null || yearB === null) return;
    void run(() => mintChange({ year_a: yearB, year_b: year }));
  }, [mode, year, yearB, run]);

  const runCluster = () => {
    const map = mapRef.current;
    if (!map || year === null) return;
    const b = map.getBounds();
    void run(() =>
      mintCluster({
        roi: {
          kind: "bbox",
          west: b.getWest(),
          south: b.getSouth(),
          east: b.getEast(),
          north: b.getNorth(),
        },
        year,
        k,
      }),
    );
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setTile(null);
    setError(null);
    const map = mapRef.current;
    if (map?.getLayer(LAYER_SRC)) map.removeLayer(LAYER_SRC);
    if (map?.getSource(LAYER_SRC)) map.removeSource(LAYER_SRC);
  };

  return (
    <div className="embeddings-view">
      <aside className="embed-panel">
        <h3>Embeddings Explorer</h3>
        <p className="muted embed-intro">
          AlphaEarth learned 64-D annual embeddings (10 m). Unit-norm, so the dot product is cosine
          similarity.
        </p>

        <div className="embed-modes">
          {(["similarity", "change", "cluster"] as const).map((m) => (
            <button
              key={m}
              className={mode === m ? "toggle active" : "toggle"}
              onClick={() => switchMode(m)}
            >
              {m === "similarity" ? "Similarity" : m === "change" ? "Change" : "Clusters"}
            </button>
          ))}
        </div>

        {mode === "similarity" && (
          <div className="embed-controls">
            <label>
              Year
              <YearSelect years={years} value={year} onChange={setYear} />
            </label>
            <p className="muted embed-hint">
              {seed
                ? `Seed: ${seed.lat.toFixed(4)}, ${seed.lon.toFixed(4)} — find places like this.`
                : "Click the map to drop a seed; the layer lights up look-alike surfaces."}
            </p>
          </div>
        )}

        {mode === "change" && (
          <div className="embed-controls">
            <label>
              From
              <YearSelect years={years} value={yearB} onChange={setYearB} />
            </label>
            <label>
              To
              <YearSelect years={years} value={year} onChange={setYear} />
            </label>
            <p className="muted embed-hint">Bright = surface changed between the two years.</p>
          </div>
        )}

        {mode === "cluster" && (
          <div className="embed-controls">
            <label>
              Year
              <YearSelect years={years} value={year} onChange={setYear} />
            </label>
            <label className="embed-krow">
              Clusters (k): {k}
              <input
                type="range"
                min={2}
                max={12}
                value={k}
                onChange={(e) => setK(Number(e.target.value))}
              />
            </label>
            <button className="primary" onClick={runCluster} disabled={busy}>
              {busy ? "Clustering…" : "Cluster this view"}
            </button>
            <p className="muted embed-hint">k-means over the current viewport (pinned seed).</p>
          </div>
        )}

        {busy && mode !== "cluster" ? <p className="muted">Rendering…</p> : null}
        {error ? <p className="embed-error">{error}</p> : null}

        {tile ? <Legend tile={tile} mode={mode} /> : null}

        <p className="embed-attribution">{ATTRIBUTION} (CC-BY 4.0)</p>
      </aside>

      <div className="embed-map-wrap">
        <div ref={containerRef} className="embed-map" data-testid="embed-map" />
      </div>
    </div>
  );
}

function YearSelect({
  years,
  value,
  onChange,
}: {
  years: number[];
  value: number | null;
  onChange: (y: number) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(Number(e.target.value))}
      disabled={!years.length}
    >
      {years.map((y) => (
        <option key={y} value={y}>
          {y}
        </option>
      ))}
    </select>
  );
}

function Legend({ tile, mode }: { tile: EmbeddingTile; mode: Mode }) {
  const { legend } = tile;
  if (mode === "cluster") {
    return (
      <div className="embed-legend">
        <div className="embed-legend-title">{tile.n_clusters} clusters (arbitrary labels)</div>
        <div className="embed-swatches">
          {legend.palette.map((c, i) => (
            <span
              key={i}
              className="embed-swatch"
              style={{ background: c }}
              title={`cluster ${i}`}
            />
          ))}
        </div>
      </div>
    );
  }
  const gradient = `linear-gradient(to right, ${legend.palette.join(", ")})`;
  return (
    <div className="embed-legend">
      <div className="embed-legend-title">
        {legend.unit}
        {mode === "similarity" && tile.seed_norm != null ? (
          <span className="embed-norm" title="‖seed‖ — unit-norm sanity check">
            {" "}
            ‖seed‖ = {tile.seed_norm.toFixed(3)}
          </span>
        ) : null}
      </div>
      <div className="embed-ramp" style={{ background: gradient }} />
      <div className="embed-ramp-labels">
        <span>{legend.min}</span>
        <span>{legend.max}</span>
      </div>
      <p className="muted embed-legend-desc">{legend.description}</p>
    </div>
  );
}
