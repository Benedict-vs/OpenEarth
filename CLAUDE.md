# OpenEarth Explorer

Interactive Streamlit app for satellite-based environmental analysis. Users select a region (ROI) and time range to explore atmospheric trace gases (Sentinel-5P/TROPOMI), land-surface spectral indices (Sentinel-2), and SAR data (Sentinel-1) via spatial heatmaps, time-series charts, and statistics. A dedicated **Methane Detection** mode combines multi-source data for methane emission analysis.

## Tech Stack
- **Streamlit** — UI framework (entry point: `app/main.py`)
- **Google Earth Engine (EE) API** — satellite data access
- **Folium / streamlit-folium** — interactive map + ROI drawing
- **Altair** — charts
- **Pandas / NumPy** — data processing

## Architecture

Two layers:
- `src/openearth/` — pure core library (no Streamlit dependency); usable from scripts/notebooks
- `app/` — Streamlit UI layer; depends on core library

**Data flow:** Sidebar config → ROI bbox → build composite/timeseries via EE → render in tabs

## Application Modes

### Explorer Mode
General-purpose satellite data exploration. User selects a data source (S5P, S2, or S1), variables, ROI, and date range. Supports ERA5 wind overlay.

### Methane Detection Mode
Dedicated mode combining multiple data sources for methane emission analysis:
- **S2 SWIR proxies** — MBSP `(B12-B11)/B11`, B12/B11 ratio, CH4 anomaly (target vs baseline)
- **Overlay layers** — S5P CH4 (coarse ~7 km), S1 SAR context, ERA5 wind arrows, source classification, RGB composite
- **Masking** — vegetation (NDVI) and water (NDWI) masks with adjustable thresholds
- **Temporal animation** — step through dates with prev/next controls to track emission events
- **Source classification** — rule-based classifier combining S1 VV, NDVI, NDWI, and MBSP to label sources as Industrial, Biogenic, Wetland, or Geological
- **Auto-scale** — CH4 anomaly colour ramp auto-centres on image median; cached across date changes, recalculate via button

## Key Files

| Path | Purpose |
|------|---------|
| `app/main.py` | Entry point (`streamlit run app/main.py`); renders sidebar + 3 tabs |
| `app/config.py` | Sidebar UI, ROI_EXAMPLES dict, CH4_DATE_HINTS constants |
| `app/roi.py` | ROI state management; Folium draw-map widget |
| `app/analysis.py` | Cached tile/composite builders; color legend rendering; source classification tile helper |
| `app/errors.py` | EE error classification → user-friendly messages |
| `app/wind_overlay.py` | ERA5 wind arrow rendering as Folium DivIcon markers |
| `app/tabs/spatial_map.py` | Heatmap tab (date/mean toggle, image export, methane multi-layer map, temporal animation) |
| `app/tabs/time_series.py` | Daily chart, smoothing controls, CSV export |
| `app/tabs/statistics.py` | Summary metrics, distribution, seasonality, anomalies |
| `src/openearth/providers/__init__.py` | Dispatcher: `get_config()`, `get_collection()`, `_resolve_source()` (routes `"methane"` → s5p/s2/s1) |
| `src/openearth/providers/gee_session.py` | EE init & auth |
| `src/openearth/providers/gee_s5p.py` | Sentinel-5P collection builder |
| `src/openearth/providers/gee_s2.py` | Sentinel-2 collection builder (cloud masking, spectral indices, methane anomaly) |
| `src/openearth/providers/gee_s1.py` | Sentinel-1 GRD collection builder (VV, VH, VV/VH ratio, RVI) |
| `src/openearth/providers/gee_era5.py` | ERA5-Land hourly wind data provider (`sample_wind_grid()` for u/v at grid points) |
| `src/openearth/providers/s5p_registry.py` | 6 trace-gas configs (NO2, SO2, CO, O3, CH4, HCHO) |
| `src/openearth/providers/s2_registry.py` | Spectral-index configs (NDVI, NDWI, EVI, SWIR-1, SWIR-2, RGB) + methane proxies (MBSP, B12_B11, CH4_ANOMALY) |
| `src/openearth/providers/s1_registry.py` | S1 SAR band configs (VV, VH, VV_VH_RATIO, RVI) |
| `src/openearth/analytics/daily_timeseries.py` | `build_daily_timeseries()` — batched daily ROI stats → DataFrame |
| `src/openearth/analytics/smoothing.py` | `add_rolling_smooth()` |
| `src/openearth/analytics/source_classification.py` | Rule-based methane source classifier (S1 VV + NDVI + NDWI + MBSP → 5 categories) |
| `src/openearth/visualization/heatmap.py` | Composites, tile URLs, thumbnails, GeoTIFF export, multi-layer Folium map |
| `src/openearth/masking/vegetation_water.py` | NDVI/NDWI-based pixel masking for methane layers |

## Key Conventions
- **Data key**: string like `"NO2"`, `"NDVI"`, `"VV"` identifying the variable
- **Source**: `"s5p"`, `"s2"`, `"s1"`, or `"methane"` — routes to correct provider via `_resolve_source()`
- **ROI**: `(west, south, east, north)` float tuple → `ee.Geometry.Rectangle()`
- **Registry pattern**: `GasConfig` / `S2IndexConfig` / `S1BandConfig` dataclasses hold band name, palette, vis_min/max, unit
- **Date input**: accepts ISO string, Python `date`, or `datetime`; converted via `to_ee_date()`
- **Caching**: Streamlit `@cache_data` for tiles; dynamic timeout based on ROI area. CH4 anomaly scale cached in `session_state["_anomaly_scale"]`
- **Config**: `OPENEARTH_EE_PROJECT` env var for EE project ID
- **Methane mode sidebar**: grouped as Methane Proxies → Overlay Layers → Masking (no satellite labels in UI)
