# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OpenEarth v2

Satellite-based environmental analysis (**Phase 6 complete** — v1 Streamlit app retired):
Python core library (`packages/core`) + FastAPI backend (`packages/api`) + React/MapLibre
frontend (`apps/web`) + an offline ML training package (`packages/ml`), with a physics-honest
methane detection suite (the Methane Lab), side-by-side Compare, a Timelapse Studio, an
AlphaEarth Embeddings Explorer, and an EMIT plume tier. `docker compose up` serves the whole
stack (see `docs/deploy.md`).

## Commands

```bash
uv sync --all-packages        # whole dev env (uv workspace, Python 3.13 pinned)
uv run pytest                 # offline unit tests — no Earth Engine, run these after changes
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict on packages/core AND packages/api
make dev                      # uvicorn :8000 + vite :5173 together (scripts/dev.sh)
make api                      # FastAPI dev server only
make gen                      # regenerate apps/web/openapi.json + src/api/types.gen.ts —
                              #   run after ANY API schema change (CI diff-checks drift)
pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web test -- --run
OPENEARTH_EE_TESTS=1 uv run pytest -m ee   # live EE tests (real auth only; never CI)
docker compose up --build     # full stack → :8080 (uv api + nginx web, SSE-safe; docs/deploy.md)
```

## Architecture

- `packages/core/src/openearth/` — the science library. **No UI frameworks, ever**
  (`tests/test_no_ui_deps.py` enforces it). Guiding split: *Earth Engine for browsing and bulk
  reduction; NumPy for physics* — everything science-critical runs on plain arrays so it is
  unit-testable offline.
  - `catalog/` — unified dataset catalog. `models.py` (frozen `DatasetSpec`/`ProductSpec`),
    `builtin/{s5p,s2,s1,emit}.py` (`emit` = frozen GEE V001 CH4ENH mirror, flows through the
    generic pipeline — no provider code; Phase 6 compare recipes `DNBR`/`URBAN_HEAT` in s2,
    `FLOOD_VV_CHANGE` in s1 use `ProductSpec.needs_ref`), `presets.py` (ROI presets + 7 methane sites),
    `loader.py` (TOML user datasets → registry user-layer; builtin `DATASETS` never mutates).
  - `providers/` — EE collection builders per source; `__init__.py` is the key/source dispatcher
    (routes the `"methane"` sentinel; non-builtin dataset ids → `generic.py`). `get_compare_image`
    renders a `needs_ref` two-window recipe: masked mean composites of the raw bands over a
    reference (`pre_`) + request (`post_`) window, then the product's `pre_`/`post_` expression;
    the single-window `get_collection` refuses `needs_ref` loudly.
  - `ee/` — `client.py` (`ee_call()` = global semaphore + tenacity retry on quota/timeout;
    ALL blocking EE round-trips go through it), `render.py` (tile/thumb/GeoTIFF URL minting;
    `TileRef.expires_at` for the ~4 h getMapId lifetime), `pixels.py` (`computePixels` chip
    fetch: pure EPSG:4326 grid math + tiling, offline-tested; used by export, reused Phase 3).
  - `export.py` — GeoTIFF writer: fast `getDownloadURL` path < 32 MB, windowed `computePixels`
    assembly above.
  - `methane/` — the physics suite (theory in `docs/methane_methods.md`). `wind.py` (ERA5;
    `wind_to_deg`/`wind_from_deg` distinct tested conventions; `sample_wind_at` +
    `sample_wind_field`). `constants.py` (cited literature + declared modeling constants),
    `conversion.py` (loads reporting `data/ch4_lut_v4.npz`; ΔR→ΔΩ→ΔXCH4 — pure, strict mypy;
    `load_mask_lut` = frozen `ch4_lut_mask.npz` used ONLY to build footprints, decoupled from
    the reporting LUT so masks are invariant to LUT recalibration — Phase 3.5 Stage 2),
    `scenes.py` (S2 L1C search + `pick_reference`, which excludes the same-overpass tile),
    `retrieval.py` (calibrated MBSP/MBMP on `computePixels` chips; bands are unpadded B4/B3/B2),
    `plume.py` (robust-σ threshold + components + outline), `ime.py` (IME + seeded joint MC;
    `quantify(mask_field=…)` thresholds the frozen-LUT ΔΩ, IME uses reporting ΔΩ),
    `detect.py` (7-step cancellable orchestrator), `tropomi.py` (S5P screening),
    `validation.py` (IMEO/SRON parse + cross-match), `channels.py` (Phase 5 ML input stack:
    5 physics channels — MBMP/MBSP ΔR + B12/B11 ratio + SWIR — via `build_channels`/`normalize`/
    `pad_to_multiple`/`candidates_from_prob`; pure NumPy, byte-identical for training and serving),
    `ime.emission_over_mask` (single-pass IME over a given mask, no MC — used by the ML scan's Q).
    LUT v4 = layered US-Std background +
    H₂O/CO₂ interfering absorbers + TSIS-1 solar weighting, generated **offline** by
    `scripts/generate_ch4_lut.py` (`uv run --group lut …`, HITRAN+SRFs+committed data extracts);
    HAPI must never be imported under `packages/`. Reproduce events with
    `scripts/validate_events.py` (2-event gate) or `scripts/calibration_harness.py` (regression).
    `emit.py` (Phase 6 EMIT plume tier, `wind.py` pure+EE split): `parse_v002_geojson` (tolerant
    LP DAAC/portal parser, `"NA"`→None), `list_plumes_gee` (frozen V001 CH4PLM; outline =
    `reduceToVectors` over the `gt(0)` mask — integral-band requirement), `cross_match` (≤5 km/≤3 d),
    `dedup_plumes`, `gee_available` (cutoff 2024-10-26). No earthaccess here — see the API rule.
  - `embeddings.py` — **top-level, not methane science.** AlphaEarth 64-band unit-norm annual
    embeddings: `similarity_image` (dot = cosine), `change_image` (1−cos), `cluster_image` (seeded
    wekaKMeans), `seed_vector`, `available_years` (probes the live collection). CC-BY `ATTRIBUTION`
    is mandatory wherever a layer shows.
  - `geometry.py` — `BBox`/`PolygonROI` validate on construction; pure-python `is_global`,
    aspect math (no EE round-trips).
  - `timelapse.py` — Phase 4. Pure layer: `frame_windows` (interval/monthly/quarterly stepping)
    + Pillow annotations (`scale_bar_spec`/`render_colorbar`/`annotate_frame`), offline-tested.
    EE + encoding layer: `render_frames` (one geometry + one vis range per render, mean composite
    → `thumb_url` → PNG fetch → burn-in, dense re-index, empty-vs-failed status, atomic manifest)
    and `encode_movie` (mp4/webm via imageio-ffmpeg, gif via Pillow). Frames fetched with an
    injectable `urllib` `FetchFn` — no HTTP dep in core.
- `packages/api/src/openearth_api/` — FastAPI layer (`routers/` thin, `services/` do the work).
  `create_app()` must stay EE-free AND DB-free at creation time — `scripts/export_openapi.py`
  and web CI rely on it; the DB engine + EE init happen in the lifespan. EE-touching routes
  depend on `deps.ensure_ee`. One diskcache tier (`cache.py`, sha256 canonical-JSON keys +
  `ALGO_VERSION`); tile URLs are never cached. Tests fake EE by monkeypatching the core fns
  imported by name into `services/*`. **earthaccess never appears under `packages/core`** — it
  is an `api` dependency, **lazy-imported inside `services/emit.py`** so `create_app()` stays
  credential-free; Earthdata auth is env-only (`EARTHDATA_TOKEN` / `~/.netrc`, never committed).
  - **Jobs + SSE** (`jobs.py`): in-process `JobManager` over SQLite (WAL; one event-loop
    writer), runners off-loop via `asyncio.to_thread`; `points` events are live previews, the
    result is refetched on `done`. `db.py` migrations are `PRAGMA user_version` DDL batches —
    append, never edit (migration 1 = `jobs`; migration 2 = `aois`/`workspaces`; migration 3 =
    `sites`/`detections`/`reference_events`, plus a per-connection `busy_timeout` so the analyze
    runner inserts its own detection row off-loop; migration 4 = `renders`, written off-loop by
    the timelapse runner too; migration 5 = `detections.emit_json` ALTER-ADD — EMIT cross-match
    evidence, NULL = "never checked").
  - **Analysis routes**: `timeseries` (chunked coarse→fine series job → parquet-bytes cache),
    `export` (GeoTIFF job / sync PNG / CSV), `inspect` (point sample), `wind` (point + field),
    `aois` + `workspaces` (plain CRUD, 409 on duplicate name; versioned `WorkspaceState`).
  - **Methane routes** (`routers/methane.py`, `services/methane.py`): sites CRUD (7 seeded in
    the lifespan), scene search, the `methane_analyze` job (SSE progress → `{detection_id}`;
    runner writes the detection row + npz artifact off-loop), detection feed/detail (`source`
    filter param), overlay PNG (`services/methane_render.py`), `array.npz`, the
    `methane_screening` job, and the validation importer/cross-match. `POST /tiles` `methane_ref`
    unlocks the `CH4_ANOMALY` quicklook (builder products still 422 without it);
    `TilesRequest.auto_range` derives the vis range from `compute_vis_range` into the mint + legend.
    `TilesRequest.ref` (a `DateRangeIn`, distinct from `methane_ref`) drives `needs_ref` compare
    products through `get_compare_image` (422 without it); `build_image` is shared, so
    tiles/thumbnail/export all get the compare path.
  - **EMIT tier** (`routers/methane.py`, `services/emit.py`): `GET /methane/emit/plumes`
    (w/s/e/n + window → GEE V001 and/or earthaccess V002, de-duplicated, cached ~1 day) and
    `POST /methane/detections/{id}/emit-match` (cross-match → writes `emit_json`; feed rows carry
    `emit_matches`). EMIT is **independent evidence on a detection, not a `source`** — decoupled
    from the physics/ML feed. V002 fetch downloads only the `CH4PLMMETA` asset; missing Earthdata
    creds → 502.
  - **Embeddings** (`routers/embeddings.py`, `services/embeddings.py`): `POST /embeddings/{similarity,
    change,cluster}` → `TileRef` (+ `seed_norm` / `n_clusters`) and `GET /embeddings/years`. All
    `ensure_ee`; seed vectors cached, tile URLs not; years validated against the live collection.
  - **ML tier** (`routers/methane.py`, `services/ml.py`): `POST /methane/ml/scan` (`methane_ml_scan`
    job → `{detection_ids}`; each hit a `source="ml"` detection row with single-pass Q + a
    `disagreement` flag, written off-loop; npz adds a `prob` map so the overlay/`array.npz` routes
    serve it unchanged) and `GET /methane/ml/status` (Settings). Lazy `ort.InferenceSession` (CPU) +
    manifest — missing model = 503 at submit, `create_app()` stays model-free. **onnxruntime only,
    never torch**; the model is a candidate ranker requiring human review, never autonomous.
  - **Timelapse routes** (`routers/timelapse.py`, `services/timelapse.py`): the `timelapse`
    render job (SSE `frame` events → `{render_id}`; runner writes the `renders` row + frames +
    manifest + movie off-loop), gallery list, detail (row + manifest), immutable frame PNGs,
    movie download, delete (409 while running). Artifacts at `data_dir/timelapse/{render_id}/`.
- `packages/ml/` (dist `openearth-ml`) — **offline** U-Net training/eval/export; **torch + smp live
  here only, never in core/api** (`test_no_ml_deps.py` enforces it). `data.py` (npz chip dataset,
  GroupKFold-by-site), `models.py` (resnet18 U-Net, in_channels=5), `train.py`/`eval.py` (typer CLIs,
  TOML configs; scene-level F1 vs the `−ΔR_MBMP` baseline → frozen `scripts/data/ml_eval_v1.json`),
  `export.py` (ONNX opset 18, dynamic HW + torch↔ORT parity test). Imports channel-building from core
  so training and serving share it. **License wall**: CH4Net is CC-BY-NC-ND 4.0 & gated — nothing
  derived (chips/masks/weights/onnx/manifest) is ever committed; it all lives under git-ignored
  `data_dir/ml/`, and the ND term blocks publishing the weights. CI trains nothing / makes no EE
  calls. See `docs/methane_methods.md` §9.
- `apps/web/` — Vite + React + TS (pnpm, NOT a uv member). Thin imperative MapLibre binding
  (no react-map-gl). **No-refetch rule**: layer controls only touch paint/layout/moveLayer;
  re-mints go through `setTiles` on the existing source. API types are generated
  (`src/api/types.gen.ts` — never edit; run `make gen` after API schema changes).
  Views (state switcher in `App.tsx`): Explore, Compare (`@maplibre/maplibre-gl-compare`,
  two per-instance maps), Methane Lab (EMIT plume overlay + match chip; compare products get a
  reference-window picker in the LayerPanel), Timelapse Studio, Embeddings Explorer (own map;
  similarity/change/cluster; CC-BY footer), Settings. Explore's **wind particle layer**
  (`src/map/wind/`) is a vendored webgl-wind MapLibre custom layer (ISC; GPU state texture,
  streaks projected through the map matrix) fed by `/wind/field` — no deck.gl, no API change.

## Conventions

- **Data key + source**: `get_product("s2", "NDVI")` or v1-style `resolve_product("MBSP", "methane")`.
- **ROI**: pass `BBox`/`PolygonROI` models, not raw tuples or `ee.Geometry` (convert at the EE
  boundary via `.to_ee_geometry()`).
- **S2 collections**: L2A SR by default; methane proxies pin L1C TOA via catalog `collection_id`
  (deliberate — retrieval literature). Don't "fix" that.
- **Products needing dedicated builders** (e.g. `CH4_ANOMALY`) carry `builder=` in the catalog;
  the generic pipeline refuses them by design.
- **Config**: pydantic-settings, env prefix `OPENEARTH_` (`.env.example`).
- Typographic characters (−, –, ×) in catalog description strings are intentional UI text
  (RUF001-003 disabled).

## Testing & verification

- Unit tests live in `packages/core/tests` and MUST pass with zero EE calls / no credentials.
- Live EE tests are `@pytest.mark.ee` (deselected by default via `addopts`).
- mypy is strict; modules wrapping EE chains have `warn_return_any` scoped off in root
  `pyproject.toml` (ee's methods return Any — that's the library, not us).
