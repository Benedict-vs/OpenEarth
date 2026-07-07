<!-- docs/phase3.5-execution-plan.md — Phase 3.5 (methane calibration hardening) execution plan.
     Written 2026-07-06 against main at 7710877 (PR #4: LUT v3 layered model + review fixes).
     Expands the docs/roadmap.md "Phase 3.5" section into implementable stages; where this
     doc refines or deviates from that sketch, the "Deviations" section says so explicitly.
     Externally checkable facts were re-verified 2026-07-06: TSIS-1 HSRS coverage/format
     (LASP LISIRD; Coddington et al. 2021), IMEO Eye on Methane export formats + 30-day
     embargo + instrument list, SRON weekly list = TROPOMI-scale rates. The IMEO portal
     bot-walls non-browser fetches, so its exact export column names could NOT be verified
     remotely — see the Stage 1 pitfall. -->

# Phase 3.5 — Methane calibration hardening: execution plan

**Goal (roadmap):** replace single-anchor calibration with a multi-event regression against
published Sentinel-2 rates; make plume footprints LUT-invariant (ΔR-space masking); close the
two known spectroscopy gaps vs Varon's reference model (H₂O/CO₂ interfering absorbers +
solar-irradiance weighting) as LUT v4. Parallel track — never touches the EE-browsing /
Phase 4 parity stack.

**Branch:** `v2/phase3.5-calibration`, cut from main at `7710877`. One commit per stage
(Stage 1 gets two — see below), prefixed `core:` / `methane:` / `docs:`. After every stage:
`make check`. No API schema changes are expected in this phase; if one sneaks in, `make gen`
in the same commit.

**Standing rules (in addition to the Phase 3 set, which still applies):**
- HAPI and any LUT-generation dependency stay in the `lut` group; importing them under
  `packages/` is a defect. The generator runs manually, never in CI.
- **Never tighten a test toward the Varon anchor.** The external anchor stays a ±30 % sanity
  band forever (his reference model differs structurally; closer agreement can be error
  cancellation — that is exactly how LUT v2 went wrong). Precision pins are against our own
  generated reference only, and the pinned values must be the *actually generated* numbers,
  never estimates written before the npz exists.
- Committed data extracts (SRF pattern): small CSV under `scripts/data/` with a provenance
  header comment (source document, version, retrieval date), so regeneration never depends
  on an external URL surviving.
- Live results (harness baselines) are committed JSON artifacts that record their own
  provenance (git hash, date, seed, LUT version). CI never runs them; offline tests validate
  schema + consistency only.

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 1a | Calibration event set: curation criteria + committed `calibration_events.json` | scripts | M | — |
| 1b | Regression harness + frozen v3 baseline | scripts + docs | M | 1a |
| 2 | ΔR-space plume masking (footprints LUT-invariant) | core | M | 1b (needs the baseline to compare against) |
| 3 | LUT v4 — H₂O/CO₂ absorbers + TSIS-1 solar weighting | scripts + core | L | 1b (2 strongly recommended first: it decouples footprints from the LUT swap) |

Strictly sequential: the harness is the instrument that makes stages 2 and 3 falsifiable, so
it lands first. Do not reorder 2 after 3 without accepting that the v4 swap will then move
masks *and* columns at once (un-attributable).

---

## Pinned contracts

### Stage 1a — calibration event set

**File:** `scripts/data/calibration_events.json`

```json
{
  "version": 1,
  "curated_utc": "2026-07-…",
  "events": [
    {
      "id": "korpezhe-2018-06-19",
      "region": "Turkmenistan", "surface": "arid",
      "source": "varon2021",
      "source_ref": "doi:10.5194/amt-14-2771-2021, Sect. 4 / Fig. 6",
      "published_q_t_h": 11.2, "published_sigma_t_h": 5.2,
      "published_instrument": "Sentinel-2",
      "published_time_utc": "2018-06-19T07:06:19Z",
      "lat": 38.4939, "lon": 53.9648,
      "bbox": [53.94, 38.47, 53.99, 38.51],
      "method": "mbmp",
      "target_scene_id": "20180619T070619_20180619T071220_T39SYC",
      "reference_scene_id": "20180624T070621_20180624T071359_T39SYC",
      "source_lonlat": [53.9648, 38.4939],
      "notes": "orbit-degenerate auto reference; ref pinned (Phase 3 lesson)"
    }
  ]
}
```

`reference_scene_id: null` means auto (`pick_reference`); pin it explicitly wherever the auto
pick is degenerate (same-overpass adjacent tile) or was found plume-contaminated during
curation. `method: "mbsp"` requires `source_lonlat`.

**Curation criteria (the load-bearing part — these define what the regression measures):**

1. **Same-scene principle.** The published rate must derive from the *same Sentinel-2
   acquisition* we analyze (IMEO instrument = Sentinel-2 with plume datetime matching an
   `S2_HARMONIZED` scene over the source, or a per-scene S2 value from the literature).
   Intermittent sources vary by factors of several between overpasses, so cross-instrument
   or cross-day pairs would measure source variability, not our calibration. This is why the
   SRON TROPOMI weekly list is **excluded from the regression** (7 km pixels, tens-of-t/h
   detection scale, different overpass time); it may only seed site discovery.
2. **Candidate sources, in priority order:**
   (a) IMEO *Eye on Methane* plume export (`methanedata.unep.org/download-dataset`; Excel /
   GeoJSON / API; events appear 30 days after detection, so all usable events are historical
   — no constraint in practice), filtered to instrument = Sentinel-2;
   (b) per-scene S2 values from the literature — Varon et al. 2021 Korpezhe series and Hassi
   Messaoud per-scene values (digitized values get a `source_ref` saying so);
   (c) other published S2 case studies only if a per-scene rate + scene date are given.
3. **Practicality gates** (checked live during curation, per event): scene present in
   `COPERNICUS/S2_HARMONIZED`; analysis bbox ≤ 1024² px at 20 m; B11/B12 valid fraction
   ≥ 95 % over the bbox (chip-level cloud gate — scene-level `CLOUDY_PIXEL_PERCENTAGE` is
   not sufficient); for MBMP, a reference passing the `pick_reference` gates that is
   *visually plume-free* (inspect the ΔR field of the reference date; pin the id).
4. **Diversity quotas:** N ≥ 10 quantifiable events; ≤ 3 events per site; ≥ 4 distinct
   regions; published rates spanning at least ~5 to ~30 t/h; both methods represented if
   the event set allows it. A regression dominated by one Turkmenistan site is a site
   calibration, not a model calibration.

**Curation workflow:** `scripts/curate_calibration_events.py` (live, `lut`-group-free): reads
a downloaded IMEO export (path argument — never fetch in the script; the portal bot-walls
automation), filters to Sentinel-2 rows, groups by site, runs the practicality gates via
`list_scenes` + one chip fetch per candidate, and emits a candidate JSON for human review.
The human prunes to the final `calibration_events.json`. Semi-manual is fine — the committed
file with per-row provenance is the deliverable, not the curation script's output.

**Offline tests** (`packages/core/tests/test_calibration_events.py`): the committed file
parses; ids unique; every bbox constructs a valid `BBox`; `method` ∈ {mbsp, mbmp}; mbsp rows
have `source_lonlat`; published σ present; N ≥ 10; per-site cap and region quota hold. Zero EE.

Commit 1a: `docs: calibration event set — N≥10 same-scene S2 events with provenance`.

### Stage 1b — regression harness + v3 baseline

**File:** `scripts/calibration_harness.py` (live, like `validate_events.py`, which stays
untouched as the fast two-event exit gate).

- For each event: `analyze(...)` with `McParams(n=500, seed=0)`, collect
  `(q_ours_t_h, sigma_ours_t_h, flags)`. A `no_plume` or gate failure becomes a recorded
  *exclusion with reason*, not a crash; the run is green when every event yields either a
  finite Q or a documented exclusion, and ≥ 10 events quantified.
- **Aggregates (pure functions, offline unit-tested in `test_calibration_events.py`):**
  - slope through origin: `β = Σ(q_ours·q_pub) / Σ(q_pub²)`
  - median ratio: `median(q_ours / q_pub)`
  - robust log-scatter: `s = 1.4826 · MAD(log10(q_ours / q_pub))`
  - Theil–Sen slope as a robustness cross-check (report, don't gate on it).
  With N ≈ 10 these are engineering diagnostics, not hypothesis tests — no CI theater; the
  methods doc says so.
- **Baseline artifact:** `scripts/data/calibration_baseline_v3.json` — per-event rows
  (ours, published, σ's, flags, exclusion reasons) + aggregates + `lut_version`, MC seed,
  git hash, run date. `--compare` mode reruns and diffs against the committed baseline
  without overwriting; `--freeze` writes it.
- **Offline tests:** aggregate functions hand-checked on 4 synthetic events; the committed
  baseline parses; **`baseline.lut_version == conversion.load_lut().version`** — this
  coupling forcibly fails the suite whenever the LUT bumps until the baseline is
  regenerated (the mechanism that keeps Stage 3 honest).
- `docs/methane_methods.md` §8 gains the baseline table (per-event) + aggregate line.

*Exit (roadmap): harness green live; baseline slope in methods §8.*

Commit 1b: `docs+tests: multi-event calibration harness + frozen v3 baseline`.

### Stage 2 — ΔR-space plume masking

**Core change (`methane/detect.py` only; `plume.py` needs no logic change — it already
thresholds an arbitrary field's positive tail):**

- Masking field becomes `−ΔR` (MBSP) or `−(ΔR_target − ΔR_ref)` (MBMP) instead of the ΔΩ
  field. The sign flip is load-bearing: methane makes ΔR *negative*, `detect_plume`
  thresholds the *positive* tail. Feeding raw ΔR yields empty masks everywhere.
- `k_sigma` semantics unchanged (k · robust σ of the masking field); the 5 precomputed
  k-jitter masks in the MC are recomputed on the ΔR field.
- **IME unchanged:** per-pass ΔΩ inversion (own AMF/spacecraft, then subtract for MBMP)
  still produces the ΔΩ field; IME sums ΔΩ over the ΔR-derived mask; `xch4_max` etc. still
  come from ΔΩ. The MC retrieval-noise bootstrap still samples the *off-plume ΔΩ*
  population (it perturbs a mass, so it lives in column units). There are now two distinct
  σ's — mask σ (ΔR units) and bootstrap σ (ΔΩ units); name them `sigma_mask_delta_r` and
  `sigma_noise_delta_omega` in `result_json` so nobody conflates them later.
- `params_json`/`result_json` record `mask_domain: "delta_r"`. No API schema change.
  `ALGO_VERSION` 3 → 4 in `packages/api/.../cache.py` (per the roadmap pin — cheap, and it
  clears anything analysis-adjacent).

**The invariance fixture:** `packages/core/tests/data/ch4_lut_v2_snapshot.npz` — the historic
v2 LUT grids (commit `96b733b`), converted to the `load_lut` npz layout with
`version = "2-snapshot"` and a provenance JSON marking it *test fixture only*. The fixture
is **already committed alongside this plan** (converted 2026-07-06 from the review session's
scratchpad before it expired; verified to load via `load_lut(path)`; the root `.gitignore`'s
global `data/` rule needed `!packages/core/tests/data/` negations, also in that commit). If
it is ever lost, regenerate from commit `96b733b`'s generator (HITRAN cache at
`~/.cache/openearth/hitran` makes that a ~6 min offline run) — do not skip the A/B.

**Offline tests:**
- **Footprint invariance (the point of the stage):** run the synthetic end-to-end detect
  fixture twice with `load_lut` monkeypatched — packaged v3 vs the v2-snapshot fixture —
  and assert the plume masks are **bit-identical** while ΔΩ/IME/Q differ (proving the swap
  actually changed the LUT). This is strictly stronger than the roadmap's wording: the mask
  is now invariant under *any* LUT substitution, not just v2↔v3.
- Existing synthetic-plume recovery tests keep passing with the ΔR-domain mask (small-signal
  ΔR is near-linear in ΔΩ, so recovered footprints stay within the existing tolerances).
- MC determinism (same seed ⇒ identical estimate) and the `no_plume` path, unchanged.

**Live verification:** `calibration_harness.py --compare` — slope within ±5 % of the v3
baseline and log-scatter not increased by more than 10 % (masking is a footprint change, not
a calibration change; a big move means a bug). Korpezhe rerun: either the MC band shrinks,
or the mask-size cliff is characterized (mask px vs k table) in methods §8 — both outcomes
are acceptable, silence is not.

**Docs:** methods §3 rewritten (mask domain = ΔR, why: LUT-invariant footprints); the §7
"plume mask depends on the LUT" bullet replaced by the resolution; §8 note.

Commit: `core: ΔR-space plume masking — LUT-invariant footprints (ALGO_VERSION 4)`.

### Stage 3 — LUT v4: interfering absorbers + solar weighting

**Generator (`scripts/generate_ch4_lut.py`) additions — everything else (16 equal-mass USSA
layers, 500 m enhancement slab, ΔΩ/AMF grids, per-satellite SRFs) stays as v3:**

- **Interfering absorbers** in the background optical depth only (the enhancement slab stays
  CH₄-only):
  `τ_bg(ν) = Σᵢ [Ω_CH4,i·k_CH4,i(ν) + Ω_H2O,i·k_H2O,i(ν) + Ω_CO2,i·k_CO2,i(ν)]`
  with per-layer Voigt σ at the same layer (T, p) via HAPI (HITRAN molecules 1 = H₂O,
  2 = CO₂; main isotopologues, same policy as CH₄).
  - CO₂: well-mixed at **420 ppm** — a declared modeling constant in the provenance (NOAA
    GML global mean is ~423 ppm as of 2024; the LUT is insensitive at this precision; cite
    NOAA GML in the provenance).
  - H₂O: **USSA 1976 is a dry atmosphere — it has no water profile.** Use the AFGL
    atmospheric constituent profiles (Anderson et al. 1986, AFGL-TR-86-0110), *US Standard*
    model, committed as `scripts/data/afgl_us_standard_h2o.csv` (z, p, T, H₂O vmr) with a
    provenance header (report id + mirror + retrieval date). Per-layer H₂O columns by
    integrating the profile over each equal-mass layer's pressure span.
- **Solar weighting:** the band weight becomes `w(ν) = SRF(ν) · E_ν(ν) · e^(−AMF·τ_bg(ν))`
  (TOA radiance over a Lambertian surface ∝ solar irradiance × transmittance; normalization
  cancels in the ratio). Solar spectrum: **TSIS-1 HSRS** (Coddington et al. 2021; LASP
  LISIRD download; 202–2730 nm at 0.01–0.001 nm — covers both bands; verified 2026-07-06),
  committed as extracts `scripts/data/tsis1_hsrs_b11_b12.csv` over the two band ranges
  (same committed-extract pattern as the SRF csv).
  - **Jacobian pitfall (do not skip):** HSRS is `W m⁻² nm⁻¹` on a wavelength grid; the LUT
    integrates in wavenumber. Convert the *shape* with `E_ν ∝ E_λ · λ²` before interpolating
    onto the ν grid — λ² varies by ~20 % across B12, so dropping it visibly reweights the
    band even though constant factors cancel.
- Output: `packages/core/src/openearth/methane/data/ch4_lut_v4.npz`, `version = "4"`;
  delete `ch4_lut_v3.npz` (git history keeps it; the Stage 2 fixture covers invariance
  testing). Provenance adds: gas list, CO₂ vmr, H₂O profile source, solar reference +
  retrieval date, per-gas isotopologue ids.
- Runtime estimate: ~3 gases × 2 bands × 16 layers + 2 enhancement calls ≈ 100 HAPI calls
  at ~10 s ≈ 20 min, plus one-time HITRAN fetches for the H₂O/CO₂ line lists (scratch cache,
  never committed).

**Core:** `conversion.py` `_LUT_FILENAME` → v4 (+ the two docstrings). Nothing else changes —
Stage 2 already made footprints independent of this swap. `ALGO_VERSION` 4 → 5.

**Tests:**
- Structure/version pins updated to "4". New own-reference regression pin
  (`V4_ANCHOR_M_S2A/B` at rel = 0.01) — **from the generated npz, values pasted after
  generation**, never estimated beforehand (the v3 lesson).
- `test_varon_anchor` stays at rel = 0.30 with its existing warning comment, verbatim.
  Expected direction: |m| decreases toward Varon (H₂O/CO₂ + solar weighting are precisely
  the pieces whose absence pushed v3 ~25 % high) — assert *nothing* about that in tests;
  record the observed movement in methods §2.
- Generator unit tests: λ²-Jacobian conversion against the analytic case (flat E_λ ⇒
  E_ν ∝ λ²); AFGL H₂O extract parses and its total column lands in a loose 10–25 kg/m²
  sanity band (then pin the exact computed value ±1 % once the committed extract exists);
  top-hat analytic identity unchanged under a constant solar weight.
- The `baseline.lut_version` coupling from Stage 1b now fails ⇒ regenerate the baseline.

**Live verification:** full harness rerun → `scripts/data/calibration_baseline_v4.json`
(keep the v3 baseline committed alongside — it is the record of the improvement);
`validate_events.py` two-event gate green. *Exit gates:* `|slope − 1|` strictly smaller than
the v3 baseline's; log-scatter not increased by more than 20 %; Varon anchor still inside
±30 %. If v4 makes the slope *worse*, that is a finding, not a formatting problem — stop and
diagnose before committing the npz (candidates: Jacobian, H₂O layer integration, line-list
coverage).

**Docs:** methods §2 (gases + solar weighting + new anchor numbers + observed movement),
§7 (remaining limitations: scattering/aerosols, site-elevation P₀), §8 (v4 baseline row);
roadmap Phase 3.5 ✅ + as-built one-liner; CLAUDE.md LUT line (terse).

Commit: `methane: CH4 LUT v4 — H2O/CO2 interfering absorbers + TSIS-1 solar weighting (ALGO_VERSION 5)`.

---

## Deviations from / refinements of the roadmap sketch (deliberate)

| Decision | Rationale |
|---|---|
| Regression restricted to *same-scene* S2-derived published rates; SRON excluded from the fit | intermittent sources vary ×several between overpasses — cross-time/cross-instrument pairs measure source variability, not calibration; SRON = TROPOMI scale (7 km, tens of t/h, different overpass time) |
| Stage 1 split into two commits (event set, then harness) | the curated dataset is reviewable on its own; the harness diff stays readable |
| Invariance test = v2-snapshot fixture via monkeypatched `load_lut`, bit-identical masks | stronger than the roadmap's "v2 vs v3" wording — proves invariance under *any* LUT substitution; fixture is 38 KB and already prepared |
| H₂O profile from AFGL (Anderson et al. 1986), not "US Std" directly | USSA 1976 defines a dry atmosphere; AFGL's US Standard constituent profiles are the canonical H₂O companion (and the likely source behind Varon's "US Standard" profiles) |
| Baseline JSONs kept per LUT version (v3 and v4 both committed) | the v3→v4 improvement claim must stay checkable from the repo alone |
| Numeric stage gates (±5 % slope / +10 % scatter for Stage 2; slope strictly toward 1 / +20 % scatter cap for Stage 3) | falsifiable exits; explicitly engineering thresholds, not statistics — N ≈ 10 supports no more |
| Enhancement slab stays CH₄-only in v4 | the plume adds methane, not water; interfering gases matter through the *background* transmittance shaping the band weight |
| `validate_events.py` untouched | it is the fast Phase 3 exit gate; the harness is a different instrument (regression, not pass/fail) |

## Implementation pitfalls (read before coding)

- **The v3 lessons are standing law:** never tighten toward Varon; own-reference pins are
  pasted from the generated artifact, not estimated; agreement moving *away* from the
  external anchor after a physics fix can be correct (error cancellation removed).
- **IMEO export columns are unverified** (the portal 403s non-browser clients). Download a
  real export first, then extend `validation.py`'s alias maps if needed (with tests) — and
  check the rate *units* on the real file (t/h vs kg/h) before trusting the ×1000.
- **Published-rate wind provenance:** IMEO/Varon rates embed *their* wind source; ours is
  ERA5. That difference is part of the regression scatter — say so in methods §8, don't
  chase it per-event.
- **Mask sign flip:** `detect_plume` receives `−ΔR`; raw ΔR gives empty masks. The MBSP
  calibration refit's `|ΔR| > 1σ` exclusion is a *different* σ and stays as is.
- **Two σ's after Stage 2** (mask σ in ΔR, bootstrap σ in ΔΩ) — distinct names in
  `result_json`, distinct tests.
- **λ² Jacobian** on the HSRS extract (shape varies ~20 % across B12); interpolate *after*
  converting to per-wavenumber.
- **HAPI + line lists stay scratch/script-only;** H₂O/CO₂ HITRAN tables can be large —
  cache dir, never the repo.
- **Parallel with Phase 4:** both branches will touch `docs/roadmap.md` and `CLAUDE.md`.
  Merge Phase 3.5 stages promptly (each stage is independently green) or rebase before the
  docs commits; don't let the two branches diverge on `methane_methods.md`.
- **Baseline runs are live EE** — seeded (`seed=0`), provenance-stamped, committed by hand;
  CI must never execute the harness or the curation script.
