# OpenEarth Explorer

Interactive Streamlit web app for satellite-based environmental analysis. Select any region on Earth and explore atmospheric trace gases and land-surface spectral indices over custom time ranges.

## Data Sources

| Satellite | Variables | Resolution | Revisit |
|-----------|-----------|------------|---------|
| **Sentinel-5P / TROPOMI** | NO₂, SO₂, CO, O₃, CH₄, HCHO | ~7 km | Daily |
| **Sentinel-2 Harmonized** | NDVI, NDWI, EVI, SWIR-1 (B11), SWIR-2 (B12) | 10–20 m | ~5 days |

All data is accessed via [Google Earth Engine](https://earthengine.google.com/).

## Features

- **Interactive ROI selection** — draw a polygon/rectangle on the map, pick from predefined regions, or enter coordinates manually
- **Spatial heatmaps** — date composite or full-period mean, with adjustable colour scale (auto or manual) and background map toggle
- **Time-series analysis** — daily values with configurable rolling smoothing (mean/median)
- **Statistics dashboard** — summary metrics, percentiles, distribution histogram, seasonal box plots, σ-anomaly detection, year-over-year comparison
- **Image export** — PNG, JPEG, or GeoTIFF download of any displayed heatmap
- **CSV export** — full daily time series with coverage fractions
- **Intelligent caching** — LRU analysis cache + Streamlit `@cache_data` for tiles and thumbnails
- **Batch processing** — automatic batch-size fallback on Earth Engine concurrency limits

## Architecture

```
OpenEarth/
├── app/                          # Streamlit UI
│   ├── main.py                   # Entry point
│   ├── config.py                 # Sidebar configuration & constants
│   ├── roi.py                    # ROI state management & draw-map widget
│   ├── errors.py                 # EE error classification & display
│   ├── analysis.py               # Cached helpers & analysis orchestration
│   └── tabs/
│       ├── spatial_map.py        # Heatmap tab with image export
│       ├── time_series.py        # Time-series chart + CSV export
│       └── statistics.py         # Stats dashboard
│
├── src/openearth/                # Reusable core library
│   ├── providers/                # Data access layer
│   │   ├── __init__.py           # Shared get_config() / get_collection()
│   │   ├── gee_session.py        # EE initialization & auth
│   │   ├── gee_s5p.py            # Sentinel-5P collection builder
│   │   ├── gee_s2.py             # Sentinel-2 with cloud masking
│   │   ├── s5p_registry.py       # 6 trace-gas configurations
│   │   └── s2_registry.py        # 5 spectral-index configurations
│   ├── analytics/
│   │   ├── conversions.py        # Date → ee.Date conversion
│   │   ├── smoothing.py          # Rolling-window smoothing
│   │   └── daily_timeseries.py   # Batched daily ROI time series
│   └── visualization/
│       └── heatmap.py            # Composites, tile URLs, thumbnails, GeoTIFF
│
├── pyproject.toml                # Package metadata (src-layout)
├── requirements.txt              # Pinned dependencies
└── README.md
```

**Design principles:**
- Core library (`src/openearth/`) has no Streamlit dependency — it can be used from scripts, notebooks, or a future CLI
- Duplicated helpers (config lookup, collection fetching) are centralised in `openearth.providers`
- Error handling is separated from business logic (`app/errors.py`)

## Quickstart

### Prerequisites

- Python 3.10+
- A [Google Earth Engine](https://earthengine.google.com/) account with a cloud project

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
streamlit run app/main.py
```

On first run, the app will prompt you to authenticate with Earth Engine.

## Usage

1. Enter your Earth Engine project ID in the sidebar (or set `OPENEARTH_EE_PROJECT` env var)
2. Choose a data source: **Sentinel-5P** (trace gases) or **Sentinel-2** (spectral indices)
3. Select a variable (e.g. NO₂, NDVI)
4. Define a region of interest — draw on the map, pick from examples, or type coordinates
5. Set the date range and click **Run analysis**
6. Explore results across three tabs:
   - **Spatial Map** — interactive heatmap with date slider and image export
   - **Time Series** — daily chart with smoothing controls and CSV download
   - **Statistics** — summary metrics, distribution, seasonality, anomalies

## Roadmap

- [x] Interactive ROI drawing + predefined regions
- [x] Sentinel-5P time series (6 trace gases)
- [x] Sentinel-2 spectral indices with cloud masking
- [x] Spatial heatmap with date/mean composite toggle
- [x] Statistical dashboard (distribution, anomalies, YoY)
- [x] Image export (PNG, JPEG, GeoTIFF)
- [x] CSV export
- [x] Analysis caching with LRU eviction
- [ ] Unit tests for core analytics
- [ ] CLI for scriptable/reproducible runs
- [ ] Additional data sources (e.g. ERA5 climate reanalysis)

## License

MIT — see [LICENSE](LICENSE).
