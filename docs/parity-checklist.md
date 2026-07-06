<!-- docs/parity-checklist.md — Phase 4 parity sweep. Every user-facing capability
     in the frozen legacy/app is listed with a disposition: ported (where),
     superseded (by what), or dropped (why). No silent gaps. Verified against the
     as-built v2 tree on 2026-07-06 (Phase 4). -->

# v1 → v2 parity checklist

Systematic sweep of `legacy/app` — `main.py`, `tabs/{spatial_map,statistics,time_series}.py`,
`config.py`, `roi.py`, `wind_overlay.py` — every widget/expander accounted for before
`legacy/` is deleted. Disposition is one of **ported** (re-implemented in v2, with the
location), **superseded** (a different, better v2 capability covers the need), or
**dropped** (deliberately not carried over, with the reason).

## Capabilities

| Legacy feature | Disposition | Where / why |
|---|---|---|
| Gas / index / raw-band / RGB / S1 layers | **ported** | catalog builtins (`packages/core/openearth/catalog/builtin/{s5p,s2,s1}.py`); Explore `CatalogBrowser` + `LayerPanel` |
| ROI presets | **ported** | `catalog/presets.py` → `/api/presets/rois`; Explore `RoiToolbar` |
| Drawn ROIs (rectangle/polygon) | **ported** | terra-draw (`map/useTerraDraw.ts`), `RoiToolbar` |
| Composite modes (mean / date-window / single scene) | **ported** | `composites.py`; `TilesRequest.composite`; Explore `DateControl` |
| Scale settings — manual vis min/max | **ported** | `TilesRequest.viz_overrides` → mint + legend (`services/tiles.py`, `services/legend.py`) |
| Scale settings — **auto range** | **ported (gap closed in Phase 4)** | `compute_vis_range` now wired via `TilesRequest.auto_range` → computed range flows to the mint **and** the legend; Explore per-layer **A** toggle (`LayerPanel`) |
| Temporal animation — date browse (tiles) | **superseded** | Phase 4 Explore `AnimationBar` "Browse" mode (±2 preloaded raster-source pool, visibility swap) |
| Temporal animation — frame playback | **superseded** | Phase 4 Explore `AnimationBar` "Playback" mode (MapLibre image source + frame transport) **and** the Timelapse Studio (encoded MP4/WebM/GIF) |
| Side-by-side comparison | **superseded** | Phase 4 `Compare` view (`@maplibre/maplibre-gl-compare`), linked (two dates) or independent modes — a real capability v1 only gestured at |
| Wind arrows overlay | **ported** | `methane/wind.py` → `/api/wind/field`; Explore `WindOverlay` |
| Statistics tab (min/max/mean/σ, histogram) | **ported** | `/api/inspect` + timeseries stats; Explore `StatsCards` |
| Time-series (daily + coverage) | **ported** | `timeseries.py` chunked series job → `/api/timeseries`; Explore `ChartPanel` / `SeriesChart` |
| Time-series — rolling smooth | **ported (reduced)** | `SeriesChart` draws a 7-day rolling mean, toggleable via the chart legend. The v1 adjustable window + method selector are **dropped** — a fixed, honest 7-day mean covers the intent; reopen if a use case needs a tunable window |
| CSV download | **ported** | `/api/export` CSV path; `ChartPanel` CSV link |
| Export image — PNG | **ported** | `/api/export/png` (sync); Explore `ExportDialog` |
| Export image — GeoTIFF | **ported** | `export.py` (fast + windowed) → `/api/export/geotiff` job; `ExportDialog` |
| Batch export by period | **dropped** | superseded by the Timelapse Studio (visual sequences) + timeseries CSV (numbers). Batch *GeoTIFF-per-period* is not ported; **backlog** if anyone needs a numeric raster stack |
| Methane quicklook — vegetation/water masking toggles | **dropped** | superseded by the Methane Lab physics suite (`methane/`, `docs/methane_methods.md`). Core `masking.py` is retained for future products |
| Methane quicklook — source classification layer | **dropped** | same rationale as masking — the physics retrieval + IME/MC quantification replaces the heuristic classifier. `analytics/source_classification.py` retained in core |
| Cloud masking (automatic) | **ported** | applied in the provider collection builders (`providers/`), same as v1 |

## Gaps closed in this sweep

- **Auto vis-range**: `compute_vis_range` existed in core but nothing exposed it. Added
  `auto_range` to `TilesRequest`; `services/tiles.mint_tiles` computes the range from the
  composite's percentiles (skipped for RGB and when an explicit range is pinned) and returns
  it in the legend, so the UI shows exactly what it rendered. Per-layer **A** toggle in the
  Explore layer panel.

## Explicitly dropped (recorded, not silent)

- Batch GeoTIFF export by period → **backlog**.
- Adjustable time-series smoothing window/method → fixed 7-day mean.
- Methane vegetation/water masking toggles and the source-classification overlay → replaced
  by the Methane Lab; the underlying core modules (`masking.py`,
  `analytics/source_classification.py`) are kept for potential future products.
