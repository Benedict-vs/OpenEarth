# OpenEarth v2 — Roadmap

Each phase has a hard exit criterion; something is usable at the end of every phase. Scope
violations wait here, not in the codebase.

## Phase 0 — Foundations ✅ (this PR)

Monorepo + uv workspace; Streamlit app frozen in `legacy/`; core ported to `packages/core`
with all audited v1 defects fixed; offline test suite (111 tests, zero EE calls); ruff/mypy
strict/pre-commit/CI.
*Exit: CI green; legacy app still launches; pytest green with zero EE calls; no Streamlit
import anywhere under `packages/`.* ✅

## Phase 1 — Map platform MVP (L)

FastAPI (`packages/api`): catalog/tiles/thumbnail/config endpoints, diskcache tier.
React shell (`apps/web`): MapLibre map, catalog browser, layer panel (opacity/order/toggle
without refetch), terra-draw polygon + bbox ROIs, presets, date control, legends, tile re-mint
on expiry. TOML custom-dataset loader.
*Exit: any registered dataset browsable with polygon ROI; a brand-new GEE dataset added via
TOML with zero code changes; tiles survive >4 h sessions.*

## Phase 2 — Analysis backbone (L)

In-process job manager + SSE progress; timeseries v2 (chunked, concurrent, coarse→fine,
parquet cache); ECharts panel; pixel inspector; GeoTIFF/PNG/CSV exports for every product;
wind endpoint + arrow overlay; saved workspaces (SQLite).
*Exit: 1-year S5P + S2-index series over a polygon streams progress, lands < 30 s warm;
GeoTIFF opens georeferenced in QGIS.*

## Phase 3 — Methane Lab, physics (XL)

HITRAN LUT (`scripts/generate_ch4_lut.py` → committed `ch4_lut_v1.npz`); calibrated MBSP/MBMP
NumPy retrieval on computePixels chips; plume masking; IME + Monte-Carlo uncertainty;
S5P screening tier; sites/detections DB; Methane Lab UI; IMEO/SRON validation importer;
`docs/methane_methods.md`.
*Exit: reproduce ≥2 documented super-emitter events with Q within ~±50 % of published values;
synthetic-plume test suite green; every detection persisted and reviewable.*

## Phase 4 — Compare + Timelapse → retire Streamlit (M)

maplibre-gl-compare view; frame-player animation (server-rendered frames, zero flicker);
Timelapse Studio (MP4/GIF/WebM + gallery); parity sweep.
*Exit: parity checklist ticked; `legacy/` deleted in one commit; README rewritten.*

## Phase 5 — ML segmentation (L)

CH4Net masks + GEE chip-rebuild pipeline (license check first); U-Net (smp, resnet18,
physics-informed channels) with site-held-out CV; eval vs physics baseline; ONNX export;
`/methane/ml/scan`; ML candidates in the detection feed; physics/ML disagreement flags.
*Exit: site-held-out scene-level F1 ≥ physics baseline; ONNX inference < 1 s/chip.*

## Phase 6 — EMIT + Embeddings + products v1 (M)

EMIT provider (GEE ≤ Oct 2024 + earthaccess V2 fallback) with detection cross-validation;
AlphaEarth embeddings explorer (similarity/change/clusters); wind particle layer; first derived
products as catalog recipes; compose.yaml + deploy doc.
*Exit: EMIT plumes overlay a known event and cross-match a detection; similarity search works
from a clicked seed; `docker compose up` serves the full app.*

## Backlog (deliberately out of scope until their phase)

- Derived products beyond the Phase 6 trio: deforestation change, urban heat proxy
  (NDBI−NDVI), phenology SOS/EOS (Savitzky–Golay), soil-moisture & biomass proxies,
  building damage. Rule: each must be a TOML catalog recipe, not a bespoke endpoint.
- STARCOP/AVIRIS data for the EMIT tier; detection fine-tuning on accumulated review decisions.
- URL-encoded shareable app state; workspace export.
- Public deployment (revisit GEE licensing terms first).
