# OpenEarth Explorer

Interactive Streamlit web app for global Earth observation (EO) analysis. Draw an area anywhere on Earth and explore:

- **NO₂** (Sentinel-5P/TROPOMI)
- **NDVI** (vegetation index)
- **Cloud fraction**

For any selected time range, the app generates:
- A **map layer** (current/selected time composite)
- An **ROI time series** (daily/weekly/monthly aggregation)
- **Coverage metrics** (valid observations vs. missing/cloudy)

## Why this project

This is a portfolio project aimed at demonstrating:
- Geospatial data handling (ROI selection, reprojection/resampling)
- Time-series analysis and aggregation
- Clear visual communication (maps + plots)
- A reusable core library shared by UI and CLI

## Planned data sources (open)

- **NO₂:** Sentinel-5P/TROPOMI (tropospheric NO₂ column)
- **NDVI + cloud fraction:** selectable backend (see below)
  - fast global option: MODIS/VIIRS products
  - higher resolution option: Sentinel-2 (heavier)

## Architecture

- **Streamlit UI** for interactive exploration
- **Core processing library** (`src/`) used by both the UI and the CLI
- Optional **CLI** for reproducible, scriptable runs (export CSV/GeoTIFF)

## Project status

Early stage / in development.

## Quickstart (local)

### 1) Create environment

Using conda:

```bash
conda env create -f environment.yml
conda activate openearth-explorer
```

Or using pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Run the app

```bash
streamlit run app/main.py
```

## Usage (MVP)

1. Open the app in your browser.
2. Draw a polygon / bounding box to define the ROI.
3. Choose a variable: **NO₂**, **NDVI**, or **Cloud fraction**.
4. Choose a date or date range and an aggregation interval.
5. View:
   - Map composite layer for the selected time
   - ROI mean time series (+ coverage)

## Exports (planned)

- CSV: ROI time series
- GeoTIFF: composite map for the selected period and ROI

## Repo layout (suggested)

```
.
├── app/                 # Streamlit UI
│   └── main.py
├── src/                 # reusable core
│   ├── providers/       # data access (gee, stac, etc.)
│   ├── analytics/       # compositing, aggregation, trends
│   └── viz/             # map + plotting helpers
├── cli.py               # optional CLI wrapper (planned)
├── tests/               # unit tests for core reducers
├── environment.yml
└── README.md
```

## Backends (not decided yet)

### Option A — Google Earth Engine (fastest global app)
- Quick to prototype global NDVI/cloud + NO₂
- Minimal data engineering
- Requires an Earth Engine account

### Option B — STAC + COG + xarray/dask (fully open pipeline)
- Fully transparent processing
- More engineering (performance/caching)

## Roadmap (minimal)

- [ ] ROI drawing + map preview layer
- [ ] NO₂ time series from Sentinel-5P
- [ ] NDVI + cloud fraction time series
- [ ] Caching for responsiveness
- [ ] CSV export
- [ ] Basic tests for aggregation/coverage

## License
MIT - see LICENSE.
