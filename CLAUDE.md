# OpenEarth Explorer

Interactive Streamlit app for satellite-based environmental analysis. Users select a region (ROI) and time range to explore atmospheric trace gases (Sentinel-5P/TROPOMI) and land-surface spectral indices (Sentinel-2) via spatial heatmaps, time-series charts, and statistics.

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

## Key Files

| Path | Purpose |
|------|---------|
| `app/main.py` | Entry point (`streamlit run app/main.py`); renders sidebar + 3 tabs |
| `app/config.py` | Sidebar UI, ROI_EXAMPLES dict, CH4_DATE_HINTS constants |
| `app/roi.py` | ROI state management; Folium draw-map widget |
| `app/analysis.py` | Cached tile/composite builders; color legend rendering |
| `app/errors.py` | EE error classification → user-friendly messages |
| `app/tabs/spatial_map.py` | Heatmap tab (date/mean toggle, image export) |
| `app/tabs/time_series.py` | Daily chart, smoothing controls, CSV export |
| `app/tabs/statistics.py` | Summary metrics, distribution, seasonality, anomalies |
| `src/openearth/providers/__init__.py` | Dispatcher: `get_config()`, `get_collection()` |
| `src/openearth/providers/gee_session.py` | EE init & auth |
| `src/openearth/providers/gee_s5p.py` | Sentinel-5P collection builder |
| `src/openearth/providers/gee_s2.py` | Sentinel-2 collection builder (cloud masking, spectral indices) |
| `src/openearth/providers/s5p_registry.py` | 6 trace-gas configs (NO₂, SO₂, CO, O₃, CH₄, HCHO) |
| `src/openearth/providers/s2_registry.py` | Spectral-index configs (NDVI, NDWI, EVI, SWIR-1, SWIR-2, RGB) |
| `src/openearth/analytics/daily_timeseries.py` | `build_daily_timeseries()` — batched daily ROI stats → DataFrame |
| `src/openearth/analytics/smoothing.py` | `add_rolling_smooth()` |
| `src/openearth/visualization/heatmap.py` | Composites, tile URLs, thumbnails, GeoTIFF export |

## Key Conventions
- **Data key**: string like `"NO2"`, `"NDVI"` identifying the variable
- **Source**: `"s5p"` or `"s2"` — routes to correct provider
- **ROI**: `(west, south, east, north)` float tuple → `ee.Geometry.Rectangle()`
- **Registry pattern**: `GasConfig` / `S2IndexConfig` dataclasses hold band name, palette, vis_min/max, unit
- **Date input**: accepts ISO string, Python `date`, or `datetime`; converted via `to_ee_date()`
- **Caching**: Streamlit `@cache_data` for tiles; dynamic timeout based on ROI area
- **Config**: `OPENEARTH_EE_PROJECT` env var for EE project ID
