<!-- docs/phase9-execution-plan.md — Phase 9 execution plan.
     Written 2026-07-12/13 (Fable planning session), from the post-Phase-8 research queue
     (docs/research/methane-detection-methods-research.md §6 + …-implementation-audit §"Prioritized
     candidate queue", items 1+2+3+5+7; item 10 was consumed and refuted by Phase 8 §7.1).
     Implements on a branch cut from main after PR #11 (Phase 8) merged — `afe4946`.

     Externally checkable facts verified 2026-07-12 in the planning session (do not re-derive):

     ── S2CH4 dataset (Gorroño et al. 2023, AMT 16, 89) ──
     - Harvard Dataverse doi:10.7910/DVN/KRNPEH, version 2 RELEASED, license **CC0 1.0**
       (public domain — fixtures ARE committable; contrast the CH4Net CC-BY-NC-ND wall).
     - 1345 netCDF4/HDF5 files, ~0.7 MB each, ~925 MB total, served by file id via
       https://dataverse.harvard.edu/api/access/datafile/{id}; ids + md5s come from the dataset
       JSON (…/api/datasets/:persistentId?persistentId=doi:10.7910/DVN/KRNPEH).
     - Exactly 3 base scenes, ALL S2A, ONE date per site:
         T32SKA 2021-07-02 (Hassi Messaoud, 455 files) · T13SGR 2021-07-04 (Permian, 435)
         · T40SBH 2021-07-18 (Korpeje, 455)
       5 plume shapes (plume0–4) × ~87–91 flux levels; filename tag QY = TRUE flux in kg/h,
       Y ∈ {0, 500 … 50000} (Q0 = plume-free version of the same scene).
     - File contents (inspected): `S2TOA` (75,75,13) float64 TOA **reflectance** in L1C band
       order B01,B02,…,B08,B8A,B09,B10,B11,B12 — band order confirmed empirically (Q50000−Q0
       diff is zero everywhere except idx 11 (B11, weak dimming) and idx 12 (B12, strong,
       max ΔR −0.20; deepest B12 transmittance 0.643 at 50 t/h); idx 10 has the B10-cirrus
       ~0.003 signature). Scalars `SZA`, `VZA` (deg → exact per-file AMF) and `U10` (the true
       10 m wind the plume was transported with; Hassi = 2.693 m s⁻¹). (75,75) `lat`/`lon`
       (Hassi crop spans ~1520 m ≈ 75 px × 20 m). `xch4` (75,75) = per-pixel TRUTH ΔXCH4 as a
       DIMENSIONLESS column-averaged mole fraction (Q50000 peak 4.23e-5 ≈ 42 ppm — consistent
       with the observed B12 absorption through our own forward model; small negative values
       are LES turbulence, keep them).
     - Consequence of one-date-per-site: MBMP in this benchmark uses the SAME-scene Q0 product
       as reference — a PERFECT-reference upper bound, stated as such. The benchmark measures
       retrieval+inversion+mask+IME fidelity; it does NOT measure reference-selection error
       (that is Phase-10 material, on live pairs).

     ── NHI (Marchese et al. 2019; rule re-confirmed via the NHI-tool pages and the ASTER
        implementation paper, PMC7926431) ──
     - NHI_SWIR = (L2.2 − L1.6)/(L2.2 + L1.6); NHI_SWNIR = (L1.6 − L0.8)/(L1.6 + L0.8),
       computed on TOA **RADIANCE** L [W m⁻² sr⁻¹ µm⁻¹]; hot pixel ⇔ NHI_SWIR > 0 OR
       NHI_SWNIR > 0. The reference implementation adds an absolute SWIR radiance floor
       (L_SWIR > 3.0) to suppress dark-pixel ratio noise.
     - Our chips are TOA reflectance. L_i = ρ_i·E_i·cos(SZA)/(π d²) with the SAME factor
       cos(SZA)/(π d²) for every band ⇒ the SIGN conditions translate exactly:
           NHI_SWIR  > 0  ⇔  ρ12·E12 > ρ11·E11   (ρ12/ρ11 > 2.881 S2A / 2.816 S2B)
           NHI_SWNIR > 0  ⇔  ρ11·E11 > ρ8A·E8A
       The absolute radiance floor does NOT translate scale-free; we replace it with a declared
       reflectance floor (see Stage 2). Typical desert background ρ12/ρ11 ≈ 0.85 — the ×2.9
       threshold is far from normal surfaces; the audit's shorthand "(B12−B11)/(B12+B11) > 0 on
       reflectance" is WRONG (it would fire on ordinary bright soil) and must not be implemented.
     - Solar irradiances (read live from COPERNICUS/S2_HARMONIZED L1C metadata this session,
       W m⁻² µm⁻¹): S2A — B8A 955.19, B11 245.59, B12 85.25; S2B — B8A 953.93, B11 247.08,
       B12 87.75.

     ── In-repo facts verified at planning time ──
     - channels.build_channels calls retrieval.mbsp/mbmp DIRECTLY → any change to mbsp's
       default behavior breaks the frozen-ChannelStats train/serve contract. New behavior must
       be opt-in kwargs; channels keeps calling defaults (guarded comment + parity golden test).
     - ee/pixels.MAX_BANDS = 6; CHIP_BANDS currently 5 → B8A fits with no cap change.
     - S2Scene carries sun_zenith_deg / view_zenith_deg; spacecraft is 'Sentinel-2A'|'Sentinel-2B'.
     - plume.robust_sigma (1.4826·MAD) exists; mbsp's refit cut uses np.nanstd (retrieval.py:137).
     - calibration_harness._baseline_path keys by LUT version ONLY → a same-LUT re-freeze needs
       an explicit new filename (v5.1), never an overwrite of calibration_baseline_v5.json.
     - noise_floor.py's own docstring mandates: any rerun writes noise_floor_v2.json + a loader
       constant bump; never mutate v1.
     - Offline tests import script modules via importlib.util.spec_from_file_location
       (test_calibration_events.py pattern).
     - services/methane._write_npz stores named arrays + flags/params in result_json → new flags
       and evidence scalars ride the existing schema; no DB migration.
     - Web QC-flag hints live in apps/web/src/lib/methane.ts (~line 194).
     - ALGO_VERSION is 6 (Phase 7). -->

# Phase 9 — measure, then change: S2CH4 truth benchmark + retrieval-robustness bundle

**Goal:** give the repo a ground-truth instrument — an **offline benchmark against the S2CH4
simulated-plume dataset** (real L1C scenes, WRF-LES plumes, known flux, CC0) — and then land the
literature-derived **retrieval-robustness bundle** (NHI flare mask, robust-σ calibration cut,
Kayrros false-positive checks, chip-validity flags) with its effect **measured by that instrument
and the existing harnesses, not asserted**. Phase 8's composite-reference refutation is the
method lesson: hypotheses about this pipeline get killed or confirmed by measurement. *Exit: the
benchmark reproduces per-site detection floors and flux-recovery curves offline from committed
fixtures + a local dataset download; the bundle is landed behind one ALGO_VERSION bump with
pre/post benchmark JSONs frozen; every existing gate (validate_events, calibration harness,
noise floor) is re-frozen with lineage intact (v5 → v5.1, floor v1 → v2); the new flags are
visible in the Lab with hints.*

**Branch:** `v2/phase9-benchmark-bundle`, cut from main at/after `afe4946`. One commit per
stage, prefixed `core:` / `api:` / `web:` / `docs:` / `scripts:`. After every stage:
`uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`; web
stages add `pnpm --dir apps/web lint && … typecheck && … test -- --run`; any API schema change
lands with its `make gen` diff in the same commit.

**Standing rules (Phases 3–8 sets still apply, plus):**

- **Anchor rule, extended.** No constant is ever tuned toward published retrieval rates.
  S2CH4's truth is WRF-LES-simulated flux — fitting against it is LES calibration in Varon's
  sense, NOT anchor-fitting, and is therefore *permitted in principle* — but adoption of any
  refit constant still goes through this plan's decision boxes, and the new evidence-flag
  thresholds (NHI floor, correlation cut) are declared constants, never tuned to calibration
  events.
- **Channel-parity law.** `build_channels` output must remain byte-identical this phase. The
  golden-fixture parity test added in Stage 2 is the enforcement; if it ever needs regeneration,
  that is by definition an ML-retrain phase, not this one.
- **License discipline.** S2CH4 is CC0 — committing fixture files is allowed and intended.
  This changes nothing for CH4Net (still CC-BY-NC-ND; nothing derived is ever committed).
- **Dependency walls.** h5py joins the **dev dependency group only** (the `lut` group pattern)
  — it must never appear in `packages/core`'s or `packages/api`'s runtime dependencies, and
  nothing under `packages/` may import it. earthaccess/torch/HAPI walls unchanged.
- **Baseline lineage is append-only.** `calibration_baseline_v5.json` and
  `noise_floor_v1.json` are never modified; Stage 3 writes `…_v5.1.json` and
  `noise_floor_v2.json` beside them.
- **Offline tests make zero EE calls and zero network calls.** The benchmark's pytest surface
  runs on committed fixtures only; full-dataset runs and all `--freeze` runs are manual,
  like the calibration harness.

---

## Load-bearing design decisions

1. **Instrument before intervention.** Stage 1 freezes `s2ch4_benchmark_v1.json` under the
   *current* pipeline (ALGO 6) before Stage 2 changes anything. The Stage 3 rerun under ALGO 7
   is the bundle's A/B. Expected honest outcome: on S2CH4 the bundle is *approximately neutral*
   (the simulated scenes contain no flares — NHI must be a no-op there, and that no-op is
   itself the false-positive regression test); the bundle's positive evidence comes from unit
   tests with synthetic hot clusters and a live flare-site spot check. Do not spin a neutral
   S2CH4 A/B as a win; report it as the regression guard it is.
2. **NHI is translated, not transplanted.** Sign rules on ρ·E per the header math, both
   spacecraft's E constants committed and cited; the reference implementation's absolute
   radiance floor becomes a declared reflectance floor. This is OUR documented adaptation of
   NHI to reflectance chips — say exactly that in methods, with the Marchese citation.
3. **`mbsp` grows opt-in kwargs; defaults stay legacy.** `detect.analyze` opts into the robust
   cut + NHI exclusion; `channels.build_channels` keeps calling defaults so the ML seam is
   untouched. One ALGO_VERSION bump (6→7) covers every result-affecting change, spent in
   Stage 2's single core commit.
4. **The benchmark recomposes `analyze`'s pure steps; it never calls `analyze`.** `analyze` is
   EE-bound (scene listing, chip fetch, wind). The benchmark builds the identical chain —
   `mbsp`/`mbmp` → reporting-LUT + frozen-mask-LUT inversion (same split as detect.py) →
   `detect_plume`/`quantify` with the same defaults (k·σ = 2, min_area 5) — from file-fed
   arrays. Any constant it uses must be imported from the core modules, never re-typed.

---

## Stage 0 — `scripts:` S2CH4 data tooling + committed fixtures

**Deliverables**

- `scripts/fetch_s2ch4.py`: downloads the dataset via the Dataverse native API (dataset JSON →
  file ids + md5s → per-file GET), into `<data_dir>/s2ch4/` (git-ignored), verifying md5 per
  file; `--site {hassi,permian,korpeje}` and `--max-files` subset flags; idempotent (skips
  verified files); prints a manifest summary. No new runtime deps (urllib is fine; this is a
  script).
- `scripts/s2ch4_benchmark.py` (reader half): `parse_product_name()` (site/tile/date/plume/Q
  from the filename — regex per the header's naming facts), `read_product(path)` → a frozen
  `Product` dataclass: `bands` dict (B11, B12, B8A, B4, B3, B2 pulled by L1C index from
  `S2TOA`; float64), `grid: GridSpec` (built from the file's lat/lon — 20 m EPSG:4326 grid
  math, same conventions as `ee.pixels`), `amf` (from SZA/VZA, same formula as `S2Scene.amf`),
  `u10_ms`, `truth_xch4` (dimensionless mole fraction), `q_true_kg_h`. h5py imported inside
  the script; h5py added to the dev group in `pyproject.toml`.
- Committed fixtures under `packages/core/tests/data/s2ch4/`: the Hassi plume0 files for
  **Q0, Q5000, Q50000** (~2.1 MB total, unmodified originals) + a `README.md` stating source
  DOI, CC0, and the retrieval date.
- Offline tests (`packages/core/tests/test_s2ch4_reader.py`, importing the script via the
  spec_from_file_location pattern): filename parsing; grid shape/scale from lat/lon; **band-order
  pin** (Q50000 vs Q0 differ only in B11/B12, B12 strongest — the empirical check from planning,
  now a regression test); **truth linearity pin** (xch4 peak ratio Q50000/Q5000 ≈ 10 within a
  few %); AMF formula equality with `S2Scene.amf` for the same angles.

**Exit:** `uv run pytest` green offline; `fetch_s2ch4.py --site hassi --max-files 20` verified
manually against md5s.

## Stage 1 — `scripts:` benchmark v1 + pre-bundle freeze

**Deliverables**

- Shared regression metrics move to core: new `openearth/methane/metrics.py` holding
  `slope_through_origin`, `median_ratio`, `log_scatter`, `theil_sen_slope`, `spearman`
  (verbatim from `calibration_harness.py`, which now imports them — no behavior change; their
  unit tests move/extend accordingly).
- `scripts/s2ch4_benchmark.py` (scoring half). Per product (site, plume, Q>0):
  - **MBSP** on the plume product; **MBMP** against the same-site Q0 product (the declared
    perfect-reference bound). Inversion mirrors detect.py exactly: reporting LUT for
    columns/IME, frozen mask LUT for the footprint; spacecraft `'Sentinel-2A'`, AMF from file.
  - Two source modes: `hinted` (source_rc = the true plume origin pixel — the site-monitoring
    scenario) and `blind` (no hint — the screening scenario). Both recorded.
  - Per-product metrics: `detected` (n_px > 0), mask IoU vs the truth mask, `q_est_kg_h` via
    `ime.emission_over_mask` with the file's true U10 (σ_u10 → 0 isolates retrieval+mask+IME
    error from wind error — state this), in-truth-mask per-pixel ΔXCH4 bias/RMS (ours via
    `delta_omega_to_xch4_ppb`, truth = `xch4 × 1e9` ppb), and the calibration-harness
    validity-fraction exclusion rule for comparability.
  - **Truth-mask convention (declared, in the JSON provenance):** truth mask = pixels with
    `xch4 ≥ 5 % of that product's peak xch4`. Scale-free across Q. If Opus finds this produces
    degenerate masks at low Q, STOP and surface it — do not silently choose another convention.
  - **MC subset:** full `quantify()` (seeded MC, n=500) on every 10th product → fraction of
    products whose ±1σ band contains Q_true (the Sherwin-style CI-coverage number).
- Aggregates per site × method × source-mode: detection-rate vs Q curve (fixed Q bins);
  **minimum detectable Q** := lowest Q bin with ≥ 50 % detection across the 5 plume shapes
  (declared convention); Q-recovery slope / median-ratio / log-scatter / Spearman via
  `metrics.py`; CI coverage.
- **α,β (F6) evidence block:** per detected product, implied `U_eff = Q_true · L / IME`;
  report the (U10, U_eff) point cloud and a fitted α̂, β̂ with CI. ⚠️ Only 3 distinct U10
  values exist (one per site) — the fit is almost certainly under-constrained.
  **Decision box (pre-declared):** adopt refit α,β into `constants.py` ONLY IF the three U10
  values span ≥ 3 m s⁻¹ AND the fit CI excludes the current Varon constants. Otherwise the
  block ships as *recorded evidence* with the "insufficient wind diversity" statement — that
  is the expected outcome and it still closes the audit's F6 question honestly (measured,
  not deadlocked).
- `--freeze` → `scripts/data/s2ch4_benchmark_v1.json` (provenance: git hash, ALGO_VERSION,
  LUT version, dataset DOI + version 2, conventions block). Run on the full local download,
  manually, BEFORE Stage 2 merges.
- Offline tests: aggregate math; fixture-driven end-to-end smoke (Q50000 detects in both
  modes, Q0 yields no_plume; IoU > 0 for Q50000 hinted).

**Exit:** `s2ch4_benchmark_v1.json` committed; pytest green offline; the per-site minimum
detectable Q lands in the same order of magnitude as Gorroño's published 1–2 t/h
(homogeneous) / 5–10 t/h (heterogeneous) — a sanity band, NOT a gate.

## Stage 2 — `core:` retrieval-robustness bundle (the single ALGO 7 commit)

**Deliverables**

- `openearth/methane/flare.py`: committed per-spacecraft `SOLAR_IRRADIANCE` constants (cited
  to live L1C metadata, values in the header); `nhi_hot_mask(bands, spacecraft)` implementing
  the translated sign rules (SWIR and SWNIR, OR-combined per Marchese) + a declared
  `ρ12 ≥ 0.01` reflectance floor + 1-px 8-connectivity dilation (both as documented modeling
  constants in the `constants.py` citation style). Pure NumPy.
- `CHIP_BANDS` gains `B8A` (6 = MAX_BANDS, no cap change; update the retrieval.py comment).
  Verify by test that RGB stacking, npz artifacts, and `build_channels` are unaffected (all
  access bands by name).
- `retrieval.mbsp(r11, r12, *, robust_cut=False, exclude=None)`: `robust_cut=True` switches
  the refit's exclusion σ from `np.nanstd` to `plume.robust_sigma`; `exclude` (bool mask)
  drops pixels from BOTH fits. **Defaults preserve current behavior bit-for-bit.**
  `channels.py` keeps calling defaults and gains the guard comment; a **parity golden test**
  (committed small chip fixture → `build_channels` output allclose against a committed golden
  npz) enforces the seam.
- `detect.analyze` opts in: NHI masks computed on target and reference chips → passed as
  `exclude` to both c-fits with `robust_cut=True` → the dilated hot pixels are NaN-ed in both
  ΔR fields before inversion (downstream is NaN-aware; IME's nansum under-counts a hot pixel
  inside a real plume — conservative, and `nan_in_mask` already reports it). New flags:
  `flare_lit_target`, `flare_lit_reference` (≥ 1 post-floor hot pixel); new
  `DetectionResult` fields with defaults: `n_hot_target`, `n_hot_reference` (ride result_json;
  no migration).
- `openearth/methane/evidence.py` (pure): `b12_dimming_ok(delta_r_target, mask)` → flag
  `not_b12_dimming` when the in-mask mean target-pass ΔR ≥ 0 (the Ehret dimming-sign check);
  `surface_correlation(mask, blind_bands, ring_px=3)` → max point-biserial |r| between the
  mask indicator and each of B4/B3/B2 over mask ∪ ring → flag `surface_correlated` above a
  declared 0.5 cut (the benchmark records the true-plume distribution of this statistic —
  S2CH4 plumes are RGB-invisible by construction, so it should sit near 0 there).
- Chip-validity diagnostics at fetch/use time: per-chip finite fraction → `sparse_chip`
  (< 0.7, declared); mean B2 brightness cloud proxy → `cloudy_chip` (declared constant).
  Flag-only — nothing is gated on them this phase.
- **ALGO_VERSION 6 → 7** in this commit, alongside every result-affecting change above.
- Offline tests: synthetic hot cluster (ρ12/ρ11 > 2.9) biases legacy `c` and is neutralized
  by exclude+robust_cut; a synthetic MBMP flare-state transition (hot in reference, cold in
  target) produces a fake positive component under the old path and none under the new;
  NHI translation math against hand-computed radiances for both spacecraft; dimming and
  correlation checks on constructed fields; parity golden test.

**Exit:** full offline suite green including parity; mypy strict clean.

## Stage 3 — instruments re-run: the A/B and the re-freezes (manual, live where noted)

1. `s2ch4_benchmark.py --freeze` rerun under ALGO 7 → `scripts/data/s2ch4_benchmark_v2.json`
   (v1 kept). Expected: NHI fires on **zero** simulated pixels (report the count — it is the
   false-positive regression); Q-recovery and detection curves shift only marginally from the
   robust cut. **Decision box:** if the bundle *degrades* Q-recovery slope or log-scatter
   beyond noise, STOP — the bundle does not merge on faith; diagnose or descope the offending
   piece.
2. Calibration harness rerun (live EE) → freeze as `scripts/data/calibration_baseline_v5.1.json`
   (add an explicit output-name flag; v5 untouched; harness docstring updated to describe the
   ALGO-7 lineage). `validate_events.py` 2-event gate must stay green.
   **D12 rider (optional):** if the v5.1 run shows libya-sirte/korpezhe exclusions shifting in
   the direction the Phase 8 §7.1 A/B predicted, re-curate those two events per the roadmap's
   D12 note — otherwise leave D12 parked.
3. `noise_floor.py --freeze` rerun (live EE) → `noise_floor_v1.json` untouched,
   new `noise_floor_v2.json` + loader bump in `services/noise_floor.py` (the script's own
   v2 discipline). Floors move where flare sites had σ inflation — report per-site deltas.
4. **Live NHI spot check** (manual, Lab): one analysis at a known flaring site with a lit
   flare in the target or reference; verify `flare_lit_*` fires, the hot cluster is excluded
   from the mask, and record the scene id + before/after σ in the stage notes.

**Exit:** three frozen JSONs committed (benchmark v2, baseline v5.1, floor v2), gate green,
spot-check scene documented.

## Stage 4 — `api:`/`web:`/`docs:` surface

- API: no schema change expected (flags are already `list[str]`; evidence scalars ride
  result_json). If any schema change does prove necessary, it lands with `make gen` in the
  same commit.
- Web: hint strings in `lib/methane.ts` for the six new flags (`flare_lit_target`,
  `flare_lit_reference`, `not_b12_dimming`, `surface_correlated`, `sparse_chip`,
  `cloudy_chip`) in the existing Phase-7 QC-chip pattern; DetectionDetail shows a flare-state
  line when either flare flag is present ("flare lit in reference — a lit→unlit transition
  can mimic a plume at the stack" phrasing per methods).
- Docs (`docs/methane_methods.md`): new section **"Synthetic-truth benchmark (S2CH4)"**
  (instrument description, conventions, v1→v2 numbers, CI coverage, the α,β evidence block
  and its decision-box outcome, the perfect-reference caveat); flare-physics paragraph in §7
  (lit flares combust ~91 % of CH4 — Plant 2022; unlit flares dominate venting — Irakulis
  2022; NHI mask + our reflectance translation, Marchese 2019); confirm the Phase-7/8 §7 text
  already carries the attribution caveat and add the aliasing note ("B11/B12 are aliased and
  ratios amplify it — Ehret Fig. 6; the σ=0.7 pre-blur is deliberately deferred to an ML
  retrain phase") if absent. Roadmap: Phase 9 entry + tick; note the instrument's role for
  Phase 10 candidates.

**Exit:** web gates green; methods section reads standalone; roadmap updated.

---

## Explicitly out of scope (parked with reasons)

- **Anti-alias blur σ=0.7** — passes through the channels seam; co-lands with an ML retrain +
  its own ALGO bump + noise_floor v3. The benchmark built here is the tool that will measure
  it. (Audit §8.)
- **Data-driven reference selection / σ-scoring, multi-reference beyond Phase 8** — Phase 10
  candidate; needs live pairs; the instrument is now ready for it.
- **Ehret regression background (D10)** — its own phase; strategic successor motivated by the
  Phase 8 composite refutation.
- **S5P NO2/CO combustion panel + EMIT CO2 cross-match** — product tier, unblocked, separate
  phase; the `flare_lit_*` flags land the physics hook it will build on.
- **D11 (ML-tier fate)** — a decision, not an implementation; unchanged by this phase except
  that the parity law keeps the current model servable.
