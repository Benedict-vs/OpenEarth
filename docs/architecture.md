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

- **uv workspace** (root `pyproject.toml`; single lockfile; Python 3.13 pinned; `legacy/`
  excluded with its own pins).
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

## Earth Engine ground rules (design defensively)

| Mechanic | Assumption | Defense |
|---|---|---|
| `getMapId` tile URLs | valid ~4 h (undocumented) | `TileRef.expires_at`; clients re-mint at 75 % TTL |
| Concurrent requests | ~40/user across tiles + compute | global semaphore (8) in `ee/client.py` |
| `getInfo` | 0.5–5 s, occasional 429/5xx | tenacity retry + backoff + jitter on classified transients |
| `computePixels` | ≤ ~48 MB/response | (Phase 3) self-limit 1024² px × ≤6 float32 bands, tile locally |
| `getThumbURL` | practical ~2048 px/side | cosine-corrected `geo_dimensions` |
| `getDownloadURL` | small areas only | fast path only; large exports assemble computePixels tiles (Phase 2) |

## Rules that keep the build honest

- Core has **no web or UI dependencies** (enforced by `test_no_ui_deps.py`).
- All blocking EE round-trips go through `ee_call()` — no bare `getInfo()` calls.
- Science-critical math runs on NumPy arrays (offline-testable); EE only browses and reduces.
- One new dataset = zero new code (TOML catalog loader, Phase 1 exit criterion).
- Every methane product ships with a methodology note + limitations (Phase 3).
