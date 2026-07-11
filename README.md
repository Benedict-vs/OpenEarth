# OpenEarth

Satellite-based environmental analysis: browse trace gases, spectral indices, and radar over any
region and time window, quantify methane super-emitters with a physics-honest retrieval, compare
two scenes side by side, and export polished timelapse movies — all served from Google Earth
Engine through a clean API and a fast MapLibre GL web app.

## Stack

Three layers, one workspace:

- **`packages/core`** (`openearth-core`) — the science library. Unified dataset catalog, Earth
  Engine access, GeoTIFF/pixel export, and the methane physics suite. Guiding split: *Earth
  Engine for browsing and bulk reduction; NumPy for physics* — everything science-critical runs
  on plain arrays so it is unit-testable offline, with **no UI framework** ever in the graph.
- **`packages/api`** (`openearth-api`) — a thin FastAPI layer over the core: tiles, analysis
  jobs with SSE progress, time series, exports, the Methane Lab, and the Timelapse Studio. Jobs
  persist to SQLite; tile URLs and reviewable artifacts (detections, renders) live on disk.
- **`apps/web`** — Vite + React + TypeScript + MapLibre GL (pnpm; not a uv member). A thin
  imperative map binding: layer controls only touch paint/layout, and animation never
  round-trips React renders.

## Features

- **Explore** — stack any registered dataset on the map, drawn or preset ROIs, per-layer opacity,
  legends, and an optional data-adaptive vis range; a pixel inspector, time-series charts with a
  rolling mean, PNG/GeoTIFF/CSV export, and an ERA5 wind overlay. An **animation** transport plays
  the active layer over time — either by browsing date-window composites or by overlaying an
  encoded render's frames.
- **Compare** — two synced maps joined by a swipe slider; *linked* mode compares one layer at two
  dates (the classic change view), *independent* mode gives each side its own configuration.
- **Methane Lab** — calibrated MBSP/MBMP retrieval on Sentinel-2, robust plume masking, and
  Integrated Mass Enhancement quantification with Monte-Carlo uncertainty, cross-matched against
  IMEO/SRON reference events. The methods, constants, and their citations are written up in
  [`docs/methane_methods.md`](docs/methane_methods.md).
- **ML tier (candidate ranker)** — an optional U-Net (resnet18, five physics-informed input
  channels built from the same retrieval code the physics tier uses) scans a site's scenes and
  proposes candidates into the *same* review feed as physics, tagged `ml` with a score and a
  physics/ML agreement flag. It beats the `−ΔR_MBMP` baseline on site-held-out scene-level F1
  (0.597 vs 0.464) and serves via ONNX/onnxruntime on CPU (~16 ms/chip). It is a **candidate ranker
  that requires human review, never an autonomous detector** — physics stays the load-bearing tier.
  Trained on the CC-BY-NC-ND CH4Net dataset, so the weights are never committed and ship out-of-band
  (see [`docs/methane_methods.md`](docs/methane_methods.md) §9).
- **Timelapse Studio** — step a date range into frames, render each with burned-in date label,
  scale bar, and colorbar, and encode an MP4/WebM/GIF with a live gallery and in-app player.

## Data sources

| Satellite | Variables | Resolution | Revisit |
|-----------|-----------|------------|---------|
| **Sentinel-5P / TROPOMI** | NO₂, SO₂, CO, O₃, CH₄, HCHO (QA-screened) | ~7 km | Daily |
| **Sentinel-2 Harmonized** | 18 spectral indices + 13 raw bands + RGB + methane proxies | 10–60 m | ~5 days |
| **Sentinel-1 GRD** | VV, VH, polarization difference, RVI (orbit-pass aware) | 10 m | 6–12 days |
| **ERA5-Land** | 10 m wind, overpass-matched | ~9 km | Hourly |

All data is accessed via [Google Earth Engine](https://earthengine.google.com/) with per-user
OAuth — run `earthengine authenticate` once and set `OPENEARTH_EE_PROJECT` to your EE cloud
project. New public GEE collections can be added from a TOML file with zero code changes
(demo: `docs/examples/modis_lst.toml`).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) (Python 3.13, pinned via `.python-version`) and, for the
web app, [pnpm](https://pnpm.io) with Node ≥ 22.

```bash
uv sync --all-packages         # whole Python dev environment, one command
pnpm --dir apps/web install
earthengine authenticate       # one-time EE OAuth; then set OPENEARTH_EE_PROJECT (.env.example)
make dev                       # API (uvicorn :8000) + web (vite :5173) together
```

Common tasks:

```bash
make test                      # offline unit tests (no Earth Engine needed)
make lint typecheck            # ruff + mypy --strict
make gen                       # regenerate OpenAPI schema + TS client types after API changes
OPENEARTH_EE_TESTS=1 uv run pytest -m ee   # live EE tests (opt-in; never in CI)
```

## Deploy

One command serves the whole stack — the FastAPI backend and the built SPA behind an
SSE-safe nginx proxy:

```bash
docker compose up --build      # → http://localhost:8080
```

State (SQLite + diskcache + artifacts) persists under `./data`. The app boots with no
credentials; mount Earth Engine credentials (personal or service-account) and set
`OPENEARTH_EE_PROJECT` to enable the EE routes. See [`docs/deploy.md`](docs/deploy.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — the as-built system, phase by phase.
- [`docs/roadmap.md`](docs/roadmap.md) — where it has been and where it is going.
- [`docs/deploy.md`](docs/deploy.md) — Docker Compose, EE/Earthdata auth, persistence.
- [`docs/methane_methods.md`](docs/methane_methods.md) — the methane retrieval + quantification.
- [`docs/parity-checklist.md`](docs/parity-checklist.md) — the v1 → v2 feature disposition.

## License

MIT — see [LICENSE](LICENSE).
