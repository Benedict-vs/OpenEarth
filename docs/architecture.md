# OpenEarth v2 — Architecture

Full design rationale lives in the v2 overhaul plan; this file tracks what is *built* and the
ground rules that shape it. Updated per phase.

## Target shape (end state)

```
Python core library (packages/core)  ← the heart: EE access, catalog, NumPy physics
        ↑
FastAPI backend (packages/api)       ← Phase 1: tiles/thumbnails/catalog; Phase 2: jobs+SSE
        ↑
React/TS/MapLibre GL (apps/web)      ← Phase 1+
ML training (packages/ml)            ← Phase 5 (torch; the API serves ONNX only)
```

## Built in Phase 0

- **uv workspace** (root `pyproject.toml`; single lockfile; Python 3.14 pinned).
- **`openearth-core`** with the v1 library ported and all audited defects fixed:
  - unified **catalog** (`DatasetSpec`/`ProductSpec` generalize the three v1 registries;
    46 products across s5p/s2/s1; ROI presets + 7 methane sites with date hints),
  - **providers** for S2 (L2A default, TOA-pinned methane proxies, s2cloudless null-guard,
    single-scene guard), S1 (orbit-pass/relative-orbit filters, optional speckle reduction),
    S5P (valid-range masking), ERA5 (fixed direction conventions),
  - **`ee.client.ee_call()`** — one global semaphore (default 8) + tenacity retry with
    exponential backoff/jitter on quota/timeout, classified via the ported error taxonomy,
  - **`ee.render`** — tile URL minting with `expires_at` (~4 h getMapId assumption),
    cosine-corrected thumbnail dimensions (pure function), GeoTIFF fast path,
  - **`methane.wind`** — overpass-matched ERA5 sampling; `wind_to_deg`/`wind_from_deg`
    explicit and unit-tested (v1 mislabeled the convention),
  - composites, vegetation/water masking, source classification, smoothing.

## Built in Phase 1

- **TOML custom datasets** (`catalog/loader.py` + registry user-layer + `providers/generic.py`):
  any public GEE ImageCollection becomes a first-class catalog dataset from a TOML file —
  strict message-first validation, persisted to `data/catalog.d/`, generic provider handles
  select/expression/RGB + valid-range masking. Built-in `DATASETS` stays import-time frozen.
- **`openearth-api`** (FastAPI): `/api/catalog` (+custom CRUD), `/api/tiles`
  (mean | date_window | single_scene composites → direct GEE XYZ URLs + `expires_at` +
  legend), `/api/thumbnail` (server-fetched PNG, diskcached), `/api/scenes`,
  `/api/presets/rois`, `/api/health`, `/api/config`. One diskcache tier
  (sha256 canonical-JSON keys, ALGO_VERSION, ROI rounded 5 dp; closed historical ranges
  cached forever, open-ended 6 h; tile URLs never cached). Core error taxonomy → HTTP
  (422/404/429/503/504). `create_app()` does no EE work at creation — the OpenAPI export
  (and web CI) depends on that; EE init is a non-fatal lifespan attempt + lazy `ensure_ee`.
- **`apps/web`** (Vite + React + TS, pnpm): thin imperative MapLibre binding (no react-map-gl);
  zustand stores + TanStack Query; types generated from the committed `openapi.json`
  (`make gen`, drift-checked in CI). The no-refetch rule: layer controls touch only
  paint/layout/moveLayer; re-mints swap URLs via `setTiles` on the surviving source.
  Tile re-mint: pure scheduler fires at 75 % TTL and on ≥3 tile errors/10 s (30 s min-gap,
  one-in-flight latch). terra-draw rectangle→bbox / polygon ROIs; presets; range ⇄
  single-date control; Settings (EE status, cache stats, TOML editor).

## Built in Phase 2

- **In-process job manager + SSE** (`jobs.py`): one event-loop DB writer, a per-job consumer
  coroutine, cooperative cancellation; `GET /jobs`, `/jobs/{id}`, `DELETE /jobs/{id}`,
  `/jobs/{id}/events` (sse-starlette, 15 s ping). Runners execute off-loop via
  `asyncio.to_thread`; progressive `points` events are live previews, the full result is
  refetched on `done`. `MAX_RUNNING_JOBS = 4` is the only brake beyond the core EE semaphore.
  SQLite is WAL, schema owned by `PRAGMA user_version` DDL batches in `db.py` (never edited,
  only appended).
- **Timeseries v2** (`services/timeseries.py`): chunked, concurrent per-period `reduceRegion`
  reductions streamed as progressive `points` previews (coarse→fine fill), then a parquet
  result cached as bytes in the one diskcache tier. `POST /timeseries` (job) +
  `GET /timeseries/{id}/result?format=json|csv|parquet`. ROI required (a global series is
  unbounded compute); `scale=coarse` reduces at 4× native for a fast preview. Stats
  (mean/σ/min/max/trend/coverage) are computed **client-side** from the delivered points — no
  `/stats` endpoint.
- **`ee/pixels.py`** — `computePixels` chip fetch on an explicit EPSG:4326 grid with
  self-limited tiling (pure grid math offline-tested; only `fetch_window` touches EE). Pulled
  forward from Phase 3 because exports need it; the retrieval chips reuse it unchanged.
- **Exports for every product**: `POST /export/geotiff` (job → single-shot `getDownloadURL`
  below 32 MB, windowed `computePixels` assembly above; `GET /export/{id}/download`),
  synchronous `POST /export/png`, and CSV via the timeseries result. GeoTIFFs are EPSG:4326,
  georeferenced (verified in QGIS over a basemap).
- **Pixel inspector** (`POST /inspect`): one masked-safe point sample of the current
  composite; the panel mini-series reuses `/timeseries` with a small bbox (one engine, one
  cache — no bespoke endpoint).
- **Wind**: `GET /wind` (ROI-mean 10 m ERA5) and `GET /wind/field` (nx×ny lattice, one
  `reduceRegions`, ERA5-Land with global-ERA5 water fallback); a canvas arrow overlay pinned
  to geography, labeled with its fixed sampled instant (noon UTC of the active date — browsing
  context, not overpass-matched; Phase 3's Methane Lab does that properly).
- **Saved AOIs + versioned workspaces** (migration 2: `aois`, `workspaces`): plain-CRUD
  routers (409 on duplicate name). A workspace `state` is a versioned pydantic schema
  (`WorkspaceState {v, layers, roi, date, wind}`) persisted as a validated JSON blob, so an
  unknown version is rejected on the way in rather than misread at load. Web: "Save AOI…" +
  a saved group in the ROI presets; a header workspace menu (save/update/load/delete) whose
  `applyWorkspace` seeds the stores and re-adds layers through the existing mint reaction — no
  second mint path.
- **Web analysis UI**: ECharts time-series panel (coarse→fine fill + client-side stat cards),
  pixel inspector, GeoTIFF/PNG/CSV export controls, wind arrow overlay.

## Built in Phase 3

Physics-honest methane detection. Full theory in `docs/methane_methods.md`; the science lives
in `packages/core/src/openearth/methane/`, all offline unit-tested.

- **CH4 absorption LUT** — `scripts/generate_ch4_lut.py` (HITRAN via HAPI + ESA Sentinel-2
  SRFs, script-only `lut` dependency group) generates the committed
  `methane/data/ch4_lut_v3.npz` (layered US Std Atmosphere background + 500 m enhancement
  slab per Varon 2021); `conversion.py` loads it and does ΔR→ΔΩ→ΔXCH4. Regression-pinned to
  its own layered reference; the Varon 2021 anchor is a ±30 % sanity band (methods §2).
- **Retrieval** — `scenes.py` (S2 L1C metadata search + reference auto-select),
  `retrieval.py` (calibrated MBSP/MBMP on `computePixels` chips, refit calibration),
  `plume.py` (robust-σ threshold + connected components + GeoJSON outline),
  `ime.py` (IME mass balance + seeded joint Monte-Carlo uncertainty),
  `detect.py` (7-step cancellable orchestrator → `DetectionResult`).
- **Screening** — `tropomi.py` (weekly S5P XCH4 enhancement lattice + persistence ranking).
- **Validation** — `validation.py` (IMEO/SRON CSV/GeoJSON parse + haversine/time cross-match).
- **API** (migration 3: `sites`, `detections`, `reference_events`): `routers/methane.py` +
  `services/methane.py` — sites CRUD (7 seeded), scene search, the `methane_analyze` job
  (runner writes its own detection row via WAL + `busy_timeout`), detection feed/detail,
  overlay PNG (`services/methane_render.py`, diskcached) and npz artifacts, the
  `methane_screening` job, and the validation importer/cross-match. `POST /tiles` gains
  `methane_ref` to unlock the `CH4_ANOMALY` quicklook.
- **Methane Lab UI** (`apps/web/src/features/methane/`, third view, no router): 3-pane
  sites | own MapLibre `LabMap` (ΔXCH4 overlay + mask outline + wind arrow + S2 RGB context) |
  detection feed + detail (numbers, MC histogram, accept/reject, validation). Verified live on
  Korpezhe 2018-06-19.
- **Reproduction** — `scripts/validate_events.py` reproduces the Hassi Messaoud blowout within
  ±50 % (near-exact) and Korpezhe within σ (its point estimate is MARGINAL under the v2 LUT — see
  `docs/methane_methods.md` §8). Phase 3 exit gate.

## Built in Phase 4

Compare + Timelapse, then the v1 Streamlit app retired (parity reached — see
`docs/parity-checklist.md`).

- **Timelapse core** (`packages/core/src/openearth/timelapse.py`) — pure layer: `frame_windows`
  (interval/monthly/quarterly stepping, budget-guarded) and Pillow annotation helpers
  (`scale_bar_spec`, `render_colorbar`, `annotate_frame`), all offline-tested. EE + encoding
  layer: `render_frames` (one geometry + one vis range for the whole render, per-window mean
  composite → `thumb_url` → PNG fetch → burned-in annotations, dense re-indexing, empty-vs-failed
  status taxonomy, atomic `manifest.json`) and `encode_movie` (mp4 libx264 / webm libvpx-vp9 via
  imageio-ffmpeg / gif via Pillow, atomic temp+replace). Frames fetched with an injectable
  `urllib` `FetchFn` (no HTTP dep in core).
- **Timelapse API** (migration 4: `renders`): `routers/timelapse.py` + `services/timelapse.py` —
  the `timelapse` render job (runner writes its own `renders` row off-loop, publishes SSE `frame`
  events, encodes the movie), gallery list, detail (row + manifest), immutable frame PNGs, movie
  download, and delete. Render artifacts live at `data_dir/timelapse/{render_id}/`.
- **Auto vis-range** — `TilesRequest.auto_range` computes the scale from the composite's
  percentiles (`compute_vis_range`) into both the mint and the legend (closing the last v1 scale
  gap).
- **Web** (`apps/web/`, views as an `App.tsx` switcher, no router): **Timelapse Studio**
  (`features/timelapse/` — form → live SSE frame strip → preload-gated `useFrameTransport` player
  → gallery), **Explore animation** (`features/explore/AnimationBar` — browse a ±2 preloaded tile
  pool, or play a render's frames through a MapLibre image source via `map/useImageFrames`), and
  **Compare** (`features/compare/` — two per-instance maps joined by
  `@maplibre/maplibre-gl-compare`, linked/independent modes; `MapContext` is per-instance).

## Earth Engine ground rules (design defensively)

| Mechanic | Assumption | Defense |
|---|---|---|
| `getMapId` tile URLs | valid ~4 h (undocumented) | `TileRef.expires_at`; clients re-mint at 75 % TTL |
| Concurrent requests | ~40/user across tiles + compute | global semaphore (8) in `ee/client.py` |
| `getInfo` | 0.5–5 s, occasional 429/5xx | tenacity retry + backoff + jitter on classified transients |
| `computePixels` | ≤ ~48 MB/response | self-limit 1024² px × ≤6 float32 bands, tile locally (`ee/pixels.py`) |
| `getThumbURL` | practical ~2048 px/side | cosine-corrected `geo_dimensions` |
| `getDownloadURL` | small areas only | fast path < 32 MB; larger exports assemble `computePixels` windows |

## Rules that keep the build honest

- Core has **no web or UI dependencies** (enforced by `test_no_ui_deps.py`).
- All blocking EE round-trips go through `ee_call()` — no bare `getInfo()` calls.
- Science-critical math runs on NumPy arrays (offline-testable); EE only browses and reduces.
- One new dataset = zero new code (TOML catalog loader, Phase 1 exit criterion).
- Every methane product ships with a methodology note + limitations (Phase 3).
