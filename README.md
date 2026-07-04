# OpenEarth

Satellite-based environmental analysis, rebuilt as **OpenEarth v2**: a Python core science
library, a FastAPI backend, and a React/TypeScript/MapLibre GL frontend — with a scientifically
defensible methane detection suite at its heart (physics retrieval + ML segmentation + EMIT
confirmation).

> **Status: Phase 0 (Foundations) — the v2 core library exists; the API and web app land in
> Phases 1–2.** The original Streamlit app lives on unchanged in [`legacy/`](legacy/) and stays
> runnable until v2 reaches feature parity. The full plan (methane science spec, architecture,
> phased roadmap) is in [`docs/`](docs/).

## Data sources

| Satellite | Variables | Resolution | Revisit |
|-----------|-----------|------------|---------|
| **Sentinel-5P / TROPOMI** | NO₂, SO₂, CO, O₃, CH₄, HCHO (QA-screened L3) | ~7 km | Daily |
| **Sentinel-2 Harmonized** | 18 spectral indices + 13 raw bands + RGB + methane proxies | 10–60 m | ~5 days |
| **Sentinel-1 GRD** | VV, VH, polarization difference, RVI (orbit-pass aware) | 10 m | 6–12 days |
| **ERA5-Land** | 10 m wind, overpass-matched | ~9 km | Hourly |

All data is accessed via [Google Earth Engine](https://earthengine.google.com/) with per-user
OAuth (`earthengine authenticate`).

## Repository layout

```
openearth/
├── packages/
│   └── core/            # openearth-core: EE access, unified dataset catalog, NumPy physics
│                        #   (api/ and ml/ packages arrive in later phases)
├── legacy/              # frozen v1 Streamlit app — own pins, not a workspace member
├── docs/                # architecture, roadmap, (soon) methane methods
├── scripts/             # LUT generation, DB seeding, dev helpers (arriving with later phases)
└── pyproject.toml       # uv workspace root + ruff/mypy/pytest config
```

## Development

Requires [uv](https://docs.astral.sh/uv/) (Python 3.13 is pinned via `.python-version`).

```bash
uv sync --all-packages   # whole dev environment, one command
make test                # offline unit tests (no Earth Engine needed)
make lint typecheck      # ruff + mypy --strict
make legacy              # run the frozen v1 Streamlit app
```

Live Earth Engine integration tests are opt-in and never run in CI:

```bash
OPENEARTH_EE_TESTS=1 uv run pytest -m ee
```

Configuration is environment-based (prefix `OPENEARTH_`, see `.env.example`); set
`OPENEARTH_EE_PROJECT` to your Earth Engine cloud project.

## What Phase 0 fixed (vs the v1 app)

The v2 core is a port of the v1 library **with the audited defects fixed**:

- **Wind direction convention** — v1 mislabeled the blowing-*toward* azimuth as meteorological;
  v2 returns both conventions, explicitly named and unit-tested, and samples ERA5 matched to the
  actual satellite overpass instead of a fixed noon window.
- **`CH4_ANOMALY` vestigial expression** — the registry entry silently rendered plain B12/B11
  through the generic path; it now requires its dedicated anomaly builder.
- **L1C/L2A split** — indices and RGB render from L2A surface reflectance; the methane proxies
  deliberately stay on L1C TOA (retrieval-literature convention), documented in the catalog.
- **S5P valid-range masking**, **s2cloudless null-guard**, **single-scene missing-scene guard**,
  **S1 orbit-pass filtering + honest "polarization difference (dB)" naming**.

## Roadmap (abridged — see `docs/roadmap.md`)

- **Phase 1** — FastAPI + MapLibre map platform: catalog browser, polygon ROIs, layer stacks,
  "add any GEE dataset via TOML"
- **Phase 2** — jobs + SSE progress, time series v2, exports, wind overlay
- **Phase 3** — Methane Lab: calibrated MBSP/MBMP retrieval, plume masking, IME quantification
  with Monte-Carlo uncertainty, validation against IMEO/SRON events
- **Phase 4** — compare view + timelapse studio → retire `legacy/`
- **Phase 5** — ML plume segmentation (CH4Net, site-held-out CV, ONNX serving)
- **Phase 6** — EMIT hyperspectral tier, AlphaEarth embeddings explorer, derived products

## License

MIT — see [LICENSE](LICENSE).
