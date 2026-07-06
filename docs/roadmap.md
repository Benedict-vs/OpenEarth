# OpenEarth v2 — Roadmap

Each phase has a hard exit criterion; something is usable at the end of every phase. Scope
violations wait here, not in the codebase.

## Phase 0 — Foundations ✅

Monorepo + uv workspace; Streamlit app frozen in `legacy/`; core ported to `packages/core`
with all audited v1 defects fixed; offline test suite (111 tests, zero EE calls); ruff/mypy
strict/pre-commit/CI.
*Exit: CI green; legacy app still launches; pytest green with zero EE calls; no Streamlit
import anywhere under `packages/`.* ✅

## Phase 1 — Map platform MVP ✅ (this PR)

FastAPI (`packages/api`): catalog/tiles/thumbnail/scenes/presets/config endpoints, diskcache
tier, custom-dataset CRUD. Core: TOML dataset loader + registry user-layer + generic provider
("one new dataset = zero new code"). React shell (`apps/web`): MapLibre map (OpenFreeMap
basemap), catalog browser, layer panel (opacity/order/toggle without refetch), terra-draw
polygon + bbox ROIs, presets (methane sites apply date hints), range/single-date control,
legends, tile re-mint at 75 % TTL + on error bursts; Settings view with EE status, cache
stats, and a TOML dataset editor.
*Exit: any registered dataset browsable with polygon ROI; a brand-new GEE dataset added via
TOML with zero code changes (demo: `docs/examples/modis_lst.toml`); tiles survive >4 h
sessions via automatic re-mint.*

## Phase 2 — Analysis backbone ✅

In-process job manager + SSE progress; timeseries v2 (chunked, concurrent, coarse→fine,
parquet-bytes cache); ECharts panel with client-side stat cards; pixel inspector;
GeoTIFF (fast + windowed `computePixels`) / PNG / CSV exports for every product; wind
point + field endpoints + arrow overlay; saved AOIs + versioned workspaces (SQLite).
*Exit: 1-year S5P + S2-index series over a polygon streams progress, lands < 30 s warm;
GeoTIFF opens georeferenced in QGIS.* ✅ *(measured: warm S5P NO₂ ≈6 s, S2 NDVI ≈3 s;
cached ~instant; both GeoTIFF paths rasterio-verified; E2E golden path green.)*

## Phase 3 — Methane Lab, physics (XL) ✅

HITRAN LUT (`scripts/generate_ch4_lut.py` → committed `ch4_lut_v3.npz`); calibrated MBSP/MBMP
NumPy retrieval on computePixels chips; plume masking; IME + Monte-Carlo uncertainty;
S5P screening tier; sites/detections DB; Methane Lab UI; IMEO/SRON validation importer;
`docs/methane_methods.md`.
*Exit: reproduce ≥2 documented super-emitter events with Q within ~±50 % of published values;
synthetic-plume test suite green; every detection persisted and reviewable.*
*(Delivered: LUT v3 — layered US Std Atmosphere forward model, regression-pinned to its own
reference with the Varon 2021 anchor as a ±30 % sanity band (see `docs/methane_methods.md` §2
for why closer anchor agreement was error cancellation); synthetic golden paths green across
conversion/retrieval/plume/IME/detect; migration 3 (sites/detections/reference_events) with
overlay-PNG + npz artifacts; 3-pane Lab UI verified live (Korpezhe 2018-06-19). Reproduction
via `scripts/validate_events.py` under v3 — Korpezhe 13.7 ± 22.7 t/h (pub 11.2 ± 5.2, MBMP,
PASS; wide MC band from a mask-size cliff, documented in §8) and Hassi Messaoud 8.5 t/h mean
(pub 9.3 ± 5.5, MBSP, PASS).)*

## Phase 3.5 — Methane calibration hardening (M, parallel track)

Three sequenced stages, each its own commit with a falsifiable acceptance criterion; never
touches the EE-browsing/parity stack, so it can run alongside Phase 4.

1. **Multi-event regression harness** (prerequisite): extend `validate_events.py` over N ≥ 10
   IMEO/SRON events with cloud-free S2 coverage (importer + cross-match exist); output the
   ours-vs-published regression (slope/intercept/scatter), not per-event pass/fail; freeze the
   v3 baseline as a committed JSON. *Exit: harness green live; baseline slope in methods §8.*
2. **ΔR-space plume masking**: MBSP masks on the ΔR field, MBMP on ΔR_t − ΔR_ref (per-pass ΔΩ
   inversion still feeds IME); footprint becomes LUT-invariant. *Exit: footprint bit-identical
   under LUT swap (v2 snapshot vs v3); harness slope/scatter not degraded; Korpezhe MC band
   shrinks or the mask cliff is characterized. ALGO_VERSION bump.*
3. **LUT v4 spectroscopy**: H₂O + CO₂ interfering absorbers (per-layer HITRAN σ; CO₂ ~420 ppm
   well-mixed, H₂O from a committed US Std profile extract) + solar-irradiance band weighting
   (committed TSIS-1 HSRS extract, same pattern as the SRF csv). Expect the anchor to move
   toward Varon — do **not** gate on it. *Exit: harness slope closer to 1, scatter not
   degraded; fresh own-reference regression pin; Varon stays a sanity band.*

Out of scope here (noted in methods §7): multiple scattering/aerosols, site-elevation surface
pressure (P₀ axis), EMIT per-pixel co-location (Phase 6).

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
