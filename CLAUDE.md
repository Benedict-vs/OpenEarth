# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OpenEarth v2

Satellite-based environmental analysis. **v2 rebuild in progress** (currently Phase 3 complete):
Python core library (`packages/core`) + FastAPI backend (`packages/api`) + React/MapLibre
frontend (`apps/web`), with a physics-honest methane detection suite (the Methane Lab). The v1
Streamlit app is frozen in `legacy/` until v2 reaches parity (end of Phase 4).

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
make legacy                   # run the frozen v1 Streamlit app (own pins, separate resolution)
OPENEARTH_EE_TESTS=1 uv run pytest -m ee   # live EE tests (real auth only; never CI)
```

## Architecture

- `packages/core/src/openearth/` — the science library. **No UI frameworks, ever**
  (`tests/test_no_ui_deps.py` enforces it). Guiding split: *Earth Engine for browsing and bulk
  reduction; NumPy for physics* — everything science-critical runs on plain arrays so it is
  unit-testable offline.
  - `catalog/` — unified dataset catalog. `models.py` (frozen `DatasetSpec`/`ProductSpec`),
    `builtin/{s5p,s2,s1}.py` (ported v1 registries), `presets.py` (ROI presets + 7 methane sites),
    `loader.py` (TOML user datasets → registry user-layer; builtin `DATASETS` never mutates).
  - `providers/` — EE collection builders per source; `__init__.py` is the key/source dispatcher
    (routes the `"methane"` sentinel; non-builtin dataset ids → `generic.py`).
  - `ee/` — `client.py` (`ee_call()` = global semaphore + tenacity retry on quota/timeout;
    ALL blocking EE round-trips go through it), `render.py` (tile/thumb/GeoTIFF URL minting;
    `TileRef.expires_at` for the ~4 h getMapId lifetime), `pixels.py` (`computePixels` chip
    fetch: pure EPSG:4326 grid math + tiling, offline-tested; used by export, reused Phase 3).
  - `export.py` — GeoTIFF writer: fast `getDownloadURL` path < 32 MB, windowed `computePixels`
    assembly above.
  - `methane/` — the physics suite (theory in `docs/methane_methods.md`). `wind.py` (ERA5;
    `wind_to_deg`/`wind_from_deg` distinct tested conventions; `sample_wind_at` +
    `sample_wind_field`). `constants.py` (cited literature + declared modeling constants),
    `conversion.py` (loads committed `data/ch4_lut_v2.npz`; ΔR→ΔΩ→ΔXCH4 — pure, strict mypy),
    `scenes.py` (S2 L1C search + `pick_reference`, which excludes the same-overpass tile),
    `retrieval.py` (calibrated MBSP/MBMP on `computePixels` chips; bands are unpadded B4/B3/B2),
    `plume.py` (robust-σ threshold + components + outline), `ime.py` (IME + seeded joint MC),
    `detect.py` (7-step cancellable orchestrator), `tropomi.py` (S5P screening),
    `validation.py` (IMEO/SRON parse + cross-match). The LUT is generated **offline** by
    `scripts/generate_ch4_lut.py` (`uv run --group lut …`, HITRAN+SRFs); HAPI must never be
    imported under `packages/`. Reproduce events with `scripts/validate_events.py`.
  - `geometry.py` — `BBox`/`PolygonROI` validate on construction; pure-python `is_global`,
    aspect math (no EE round-trips).
- `packages/api/src/openearth_api/` — FastAPI layer (`routers/` thin, `services/` do the work).
  `create_app()` must stay EE-free AND DB-free at creation time — `scripts/export_openapi.py`
  and web CI rely on it; the DB engine + EE init happen in the lifespan. EE-touching routes
  depend on `deps.ensure_ee`. One diskcache tier (`cache.py`, sha256 canonical-JSON keys +
  `ALGO_VERSION`); tile URLs are never cached. Tests fake EE by monkeypatching the core fns
  imported by name into `services/*`.
  - **Jobs + SSE** (`jobs.py`): in-process `JobManager` over SQLite (WAL; one event-loop
    writer), runners off-loop via `asyncio.to_thread`; `points` events are live previews, the
    result is refetched on `done`. `db.py` migrations are `PRAGMA user_version` DDL batches —
    append, never edit (migration 1 = `jobs`; migration 2 = `aois`/`workspaces`; migration 3 =
    `sites`/`detections`/`reference_events`, plus a per-connection `busy_timeout` so the analyze
    runner inserts its own detection row off-loop).
  - **Analysis routes**: `timeseries` (chunked coarse→fine series job → parquet-bytes cache),
    `export` (GeoTIFF job / sync PNG / CSV), `inspect` (point sample), `wind` (point + field),
    `aois` + `workspaces` (plain CRUD, 409 on duplicate name; versioned `WorkspaceState`).
  - **Methane routes** (`routers/methane.py`, `services/methane.py`): sites CRUD (7 seeded in
    the lifespan), scene search, the `methane_analyze` job (SSE progress → `{detection_id}`;
    runner writes the detection row + npz artifact off-loop), detection feed/detail, overlay
    PNG (`services/methane_render.py`), `array.npz`, the `methane_screening` job, and the
    validation importer/cross-match. `POST /tiles` `methane_ref` unlocks the `CH4_ANOMALY`
    quicklook (builder products still 422 without it).
- `apps/web/` — Vite + React + TS (pnpm, NOT a uv member). Thin imperative MapLibre binding
  (no react-map-gl). **No-refetch rule**: layer controls only touch paint/layout/moveLayer;
  re-mints go through `setTiles` on the existing source. API types are generated
  (`src/api/types.gen.ts` — never edit; run `make gen` after API schema changes).
- `legacy/` — frozen v1. Do not add features; fix defects in `packages/core` instead.

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
