# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OpenEarth v2

Satellite-based environmental analysis. **v2 rebuild in progress** (currently Phase 1 complete):
Python core library (`packages/core`) + FastAPI backend (`packages/api`) + React/MapLibre
frontend (`apps/web`), with a physics-honest methane detection suite (Phase 3+). The v1
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
    `TileRef.expires_at` for the ~4 h getMapId lifetime).
  - `methane/wind.py` — overpass-matched ERA5 wind; `wind_to_deg`/`wind_from_deg` are distinct,
    tested conventions. Retrieval/plume/IME modules arrive in Phase 3.
  - `geometry.py` — `BBox`/`PolygonROI` validate on construction; pure-python `is_global`,
    aspect math (no EE round-trips).
- `packages/api/src/openearth_api/` — FastAPI layer (`routers/` thin, `services/` do the work).
  `create_app()` must stay EE-free at creation time — `scripts/export_openapi.py` and web CI
  rely on it. EE-touching routes depend on `deps.ensure_ee`. One diskcache tier (`cache.py`,
  sha256 canonical-JSON keys + `ALGO_VERSION`); tile URLs are never cached. Tests fake EE by
  monkeypatching the core fns imported by name into `services/*`.
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
