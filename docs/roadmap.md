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

## Phase 3.5 — Methane calibration hardening (M, parallel track) ✅

**As built:** multi-event regression over 17 same-scene S2 events (IMEO MARS-S2L + Korpezhe;
IMEO portal was down, so rates come from the MARS-S2L HuggingFace dataset) — the harness
immediately showed single-scene MBSP saturates the LUT over heterogeneous surfaces, so events
default to **MBMP with pinned plume-free references** (v3 baseline slope 1.03). Footprint
LUT-invariance is delivered by a **frozen mask LUT** (decoupled from the reporting LUT), *not*
the planned raw-ΔR masking — which was implemented and rejected after diagnosis (zero plume
overlap for MBMP; the raw ΔR difference doesn't cancel co-clamped structure). LUT **v4** adds
H₂O/CO₂ interfering absorbers (AFGL H₂O, 420 ppm CO₂) + TSIS-1 solar weighting, but its key
result is a **refuted hypothesis**: those gaps move the anchor only ~1.6 %, so the ~25 % Varon
offset is *not* dominated by them (scattering/aerosols/P₀ remain). v4 is empirically
indistinguishable from v3 and ships for physical completeness; both baselines stay committed.
See methods §2/§8.2.

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

## Phase 4 — Compare + Timelapse → retire Streamlit (M) ✅

maplibre-gl-compare view; frame-player animation (server-rendered frames, zero flicker);
Timelapse Studio (MP4/GIF/WebM + gallery); parity sweep.
*Exit: parity checklist ticked; `legacy/` deleted in one commit; README rewritten.* ✅
*As-built: `timelapse.py` (frame stepping + Pillow annotations + `render_frames`/`encode_movie`
via imageio-ffmpeg); migration 4 `renders` + timelapse job/gallery routes; Timelapse Studio,
Explore animation (browse + frame playback), and the Compare view; auto vis-range gap closed;
`legacy/` deleted (branch v2/phase4-compare-timelapse).*

## Phase 5 — ML segmentation (L) ✅

CH4Net masks + GEE chip-rebuild pipeline (license check first); U-Net (smp, resnet18,
physics-informed channels) with site-held-out CV; eval vs physics baseline; ONNX export;
`/methane/ml/scan`; ML candidates in the detection feed; physics/ML disagreement flags.
*Exit: site-held-out scene-level F1 ≥ physics baseline; ONNX inference < 1 s/chip.* ✅
*As-built: the gated CC-BY-NC-ND license forced a data-never-committed wall and a self-service
metadata-recovery pivot (`recover_ch4net_metadata.py`) to rebuild chips at our own 20 m; U-Net beats
the `−ΔR_MBMP` baseline on site-held-out scene F1 (0.597 vs 0.464, `ml_eval_v1.json`); ONNX (opset
18, dynamic HW) served via onnxruntime-CPU (~16 ms/chip) as a **candidate ranker** feeding the human
review feed (`source="ml"`, single-pass Q, physics/ML disagreement flag) — never an autonomous
detector; weights ship out-of-band via `data_dir`. Methods in `docs/methane_methods.md` §9; branch
v2/phase5-ml.*

## Phase 6 — EMIT + Embeddings + products v1 (M) ✅

EMIT provider (GEE ≤ Oct 2024 + earthaccess V2 fallback) with detection cross-validation;
AlphaEarth embeddings explorer (similarity/change/clusters); wind particle layer; first derived
products as catalog recipes; compose.yaml + deploy doc.
*Exit: EMIT plumes overlay a known event and cross-match a detection; similarity search works
from a clicked seed; `docker compose up` serves the full app.* ✅
*As-built: EMIT is independent evidence on existing detections (`emit_json`, migration 5), not a
detection source — a frozen GEE V001 mirror (`emit` builtin dataset + `methane/emit.py` plume
model) plus an earthaccess V002 GeoJSON fallback (lazy-imported in `services/emit.py`, CH4PLMMETA
asset only), cross-matched ≤5 km/≤3 d (live: 0.14 km on a Permian super-emitter). AlphaEarth
embeddings (`embeddings.py` + `/embeddings/*` + a sixth Explore view) do cosine similarity /
1−cosine change / seeded wekaKMeans over the unit-norm 64-D annual vectors (live: a Neckar seed
lit every waterway; k-means mapped forest/farmland/urban). Wind particles are a vendored
webgl-wind MapLibre custom layer fed by `/wind/field` (no deck.gl, no API change). Two-window
compare recipes (`needs_ref` + `pre_`/`post_` bands + `get_compare_image`): `DNBR` (Rhodes 2023
burn scar), `URBAN_HEAT` (NDBI−NDVI), `FLOOD_VV_CHANGE` (Emilia-Romagna 2023 inundation).
`compose.yaml` (uv multi-stage api + nginx web, SSE-safe proxy) + `docs/deploy.md`. Methods
§10; branch v2/phase6-emit-embeddings.*

## Phase 7 — Science-honesty pass (M) ✅

A three-tier internal review (physics / calibration / ML protocol) turned into a fix round: make
every headline number defensible or honestly qualified. No new user features — the deliverable is
trustworthiness.
*Exit: the asserted detection floor is replaced by a measured one; the ML evaluation is
protocol-valid; every claim in the methods doc is checkable from the repo alone.* ✅
*As-built: (0) three-tier findings recorded under `docs/reviews/` (license-safe aggregates only).
(1) thumbnail cache-key fix, unit-safe validation importer (explicit `rate_unit`, no guessing),
read-derived `physics_agreement` tri-state + ML-Q point-estimate marking. (2) LUT v5 (ΔΩ grid
−0.5→6.0 so MBSP blowups invert to finite columns; shared subgrid bit-identical to v4),
median-centered plume masks, clip/stability/cross-tile/contamination diagnostics, `ALGO_VERSION` 6;
frozen `calibration_baseline_v5.json` (13 quantified, slope 1.11, median ratio 1.00, Spearman ρ 0.09
— per-event ranking unsupported; korpezhe un-clamps 5.7→11.0 t/h onto its 11.2 Varon anchor).
(3) empirical per-site noise floor from identical `analyze` on plume-free pairs (`noise_floor_v1.json`:
pooled 24.6 t/h, best arid 6.6 t/h, 27/35 pairs quantify something), surfaced as feed/detail context.
(4) ML v2: site-cluster GroupKFold (23→11 clusters + cross-fold overlap guard), inner-val early-stop
+ threshold selection, net-negative-ΔΩ label gate (69/395), reflect-pad train/serve, serve reference
pool ±150 d — `ml_eval_v2.json` model scene-F1 0.571 ≥ baseline 0.416 (v1's protocol-invalid 0.597
retired; 91 % of labels sit below the noise floor). (5) methods honesty rewrite (§1/§7/§8.2/§9).
License wall intact — no CH4Net derivative (chips/masks/weights/onnx/manifest) committed. Methods
§1/§7/§8.2/§9; branch v2/phase7-science-fixes.*

## Phase 8 — Design pass: one time model, honest animation, resilient renders, composite reference (L) ✅

The review items the Phase 7 science round deferred to a design pass. Retire the app's conflated
date semantics; make timelapse renders interruptible and failure-tolerant; ship an opt-in
composite MBMP reference with its A/B recorded before any promotion.
*Exit: no view has two controls for the same time concept or one for two; a mid-render EE failure
costs one frame, not the render; a running render can be stopped and its partial kept; a
recurrent-emitter site can be analyzed against a composite reference from the Lab.* ✅
*As-built: (0) one shared **window** (center ± halfDays, "what a composite shows") + **period**
({start,end}, "a span you scan across") model — `dateStore` v2, shared `TimeWindowPicker`/
`PeriodPicker`, workspace state v2 with lossless v1 migration. The window compiles to
`composite:"mean"` over an exclusive-end range (no tiles-schema change, never rides the
`half_window_days ≤ 30` cap). (1) per-side Compare windows (width presets ARE the smoothing).
(2) Explore Preview transport is buffer-aware — play holds on an unready frame instead of lying
(`advanceFrame`, synchronous status ref, bounded prefetch); finished-render playback relocated to
the gallery's "Play on map" → a docked `PlaybackBar` (uiStore navigation). (3) timelapse
resilience: per-frame mint failures degrade to `failed` + a dead-pipeline breaker; cancel keeps
completed frames as a "partial" (`cancelled` manifest, no enum/migration change) with a movie when
≥ 2 frames; `tween` cross-fade smoothing at encode time. (4) opt-in median-composite MBMP reference
(k = 5 same-orbit/spacecraft, 50 % breakdown point, median-AMF + spread flag) — A/B vs baseline v5
recorded (methods §7.1): it does **not** rescue the persistent-emitter case (libya-sirte 1.72 →
1.76) so it ships default-off, no baseline v6. ML scan stays single-reference (channel parity).
Leftover handoffs (deliberately not in scope): **D10** Ehret et al. 2022 regression background
(the literature's recurrent-monitoring machine — co-registration + long series, methods §7.1);
**D11** ML-tier fate (`ml_eval_v2.json` now exists, scene-F1 0.571 ≥ 0.416 — the promote/retire
call is a separate decision); **D12** v5.1 event re-curation (unblocked by the §7.1 A/B —
composite mode is what a re-curated libya-sirte/korpezhe would test against). Also: revisit the
Preview transport if usage shows people still expect *smooth* playback there rather than treating
it as an honest buffer-aware scrubber (the escape hatch is "Render as timelapse…"). Branch
v2/phase8-design-pass.*

## Backlog (deliberately out of scope until their phase)

- Multi-*source* fusion products (S1+S2): deforestation change, flood mask (VV + MNDWI change),
  soil-moisture & biomass proxies, building damage — the compare recipe schema unlocked
  single-source temporal deltas (Phase 6), but true fusion needs a cross-collection pipeline.
- Other derived products: phenology SOS/EOS (Savitzky–Golay), Lyzenga bathymetry, AOD downscale.
  Rule: each must be a TOML catalog recipe, not a bespoke endpoint.
- STARCOP/AVIRIS data for the EMIT tier; detection fine-tuning on accumulated review decisions.
- URL-encoded shareable app state; workspace export.
- Public deployment (revisit GEE licensing terms first).
