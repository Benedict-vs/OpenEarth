<!-- docs/methane_methods.md — Phase 3 methane retrieval + quantification methods.
     The physics is implemented in packages/core/src/openearth/methane/. -->

# Methane retrieval and quantification — methods

OpenEarth's Methane Lab detects and quantifies anomalous methane point sources from
multispectral Sentinel-2 imagery, following the multi-band methods of **Varon et al. 2021**
(*Atmos. Meas. Tech.* 14:2771, "High-frequency monitoring of anomalous methane point sources
with multispectral Sentinel-2 satellite observations"). Everything science-critical runs on
plain NumPy arrays (offline unit-tested); Earth Engine only browses metadata, fetches chips,
and does bulk reductions.

## 1. Retrieval — MBSP and MBMP fractional signal

Methane absorbs in Sentinel-2's SWIR band **B12** (~2100–2280 nm) and, weakly, in **B11**
(~1560–1660 nm). The **Multi-Band Single-Pass** signal for one scene is

```
ΔR = (c · R12 − R11) / R11
```

where `R11`, `R12` are TOA reflectances and `c` is the zero-intercept least-squares slope of
R11 on R12, `c = Σ(R11·R12) / Σ(R12²)`, fit over the valid pixels. A methane plume depresses
R12, driving ΔR negative. `c` is **refit once** excluding pixels with `|ΔR| > 1σ`, so a real
plume cannot bias its own calibration (a plumeless fit is measurably biased — see
`test_mbsp_refit_recovers_c_that_plumeless_fit_biases`). A degenerate flat field keeps the
initial `c` rather than discarding every pixel.

MBSP alone cannot separate methane absorption from static surface structure (bright/dark
terrain shifts B12/B11 too). The **Multi-Band Multi-Pass** signal subtracts a reference
scene's ΔR to cancel that structure:

```
ΔR_MBMP = ΔR_target − ΔR_reference
```

Both chips are fetched on the **same** EPSG:4326 `GridSpec` (same bbox + scale ⇒ Earth Engine
resamples each scene onto the identical grid), so the subtraction is element-wise.

`retrieval.py` implements `mbsp`/`mbmp`; `fetch_chip` fetches B11/B12 plus B4/B3/B2 (RGB
context) via `computePixels`, refusing grids > 1024×1024 (a ~20 km cap at 20 m) and mapping
Earth Engine's fill (`-9999`, set with `.unmask`) to NaN. Sentinel-2 band ids are unpadded
(`B4`, not `B04`); the methane proxies pin L1C TOA (`COPERNICUS/S2_HARMONIZED`) per the
retrieval literature.

## 2. Column conversion — the CH4 absorption LUT

The fractional signal ΔR is mapped to a methane **column enhancement** ΔΩ (mol/m²) through a
precomputed lookup table, `methane/data/ch4_lut_v4.npz`.

**Physics (`scripts/generate_ch4_lut.py`, run once with network):** layered Beer–Lambert band
transmittance, no scattering.

- The background column is **vertically resolved**: the US Standard Atmosphere 1976 split into
  16 equal-mass layers (well-mixed CH4 ⇒ absorber fraction = pressure fraction), each with its
  own absorber-weighted `(T_i, p_i)` and its own **HITRAN** Voigt cross section σ_i(ν) (HAPI
  `absorptionCoefficient_Voigt`, 0.005 cm⁻¹ grids spanning B11 ≈ 5946–6497 cm⁻¹ and
  B12 ≈ 4310–4812 cm⁻¹, ± 50 cm⁻¹).
- **Interfering absorbers (v4).** The background optical depth adds **H2O** and **CO2**:
  `τ_bg(ν) = Σ_i [Ω_CH4,i k_CH4,i + Ω_H2O,i k_H2O,i + Ω_CO2,i k_CO2,i]` (`k = N_A·1e−4·σ`), with
  CH4 background `Ω₀ = 0.65 mol/m²` (1875 ppb) well-mixed, CO2 well-mixed at **420 ppm** (a
  declared modeling constant; NOAA GML global mean ~423 ppm in 2024), and H2O from the **AFGL US
  Standard** profile (Anderson et al. 1986 — USSA 1976 is a *dry* atmosphere) integrated per
  layer (total column ≈ 14.2 kg/m² precipitable water). H2O/CO2 are HITRAN molecules 1/2, main
  isotopologues (same policy as CH4). The enhancement slab stays **CH4-only** — a plume adds
  methane, not water.
- **Solar weighting (v4).** The band weight is `w(ν) = SRF_b(ν) · E_ν(ν) · e^{−AMF·τ_bg}`, with
  `E_ν` the **TSIS-1 HSRS** solar irradiance (Coddington et al. 2021, via LASP LISIRD). The HSRS
  is per-wavelength, so its shape is converted to per-wavenumber with the **λ² Jacobian**
  (`E_ν ∝ E_λ·λ²`) *before* interpolation — λ² varies ~27 % across B12, so dropping it visibly
  reweights the band. The B11/B12 extracts are committed at `scripts/data/tsis1_hsrs_b11_b12.csv`
  and `scripts/data/afgl_us_standard_h2o.csv`.
- The plume **enhancement ΔΩ sits in the lowest 500 m** at that slab's absorber-weighted
  conditions (0.971 atm, 286.5 K) — the vertical placement Varon et al. 2021 assume in their
  100-layer reference model (the enhancement only produces signal where the background hasn't
  saturated the band). Both terms are slanted by the same geometric
  `AMF = 1/cos θ_sun + 1/cos θ_view`. Band signal
  `m_b(ΔΩ) = ∫ w e^{−AMF·ΔΩ·k_enh} dν / ∫ w dν − 1` with `w` above, using the ESA Sentinel-2
  spectral response functions (COPE-GSEG-EOPG-TN-15-0007 issue 3.2; `scripts/data/s2_srf_b11_b12.csv`),
  combined to `m_MBSP = (1 + m_B12)/(1 + m_B11) − 1`, computed **separately for Sentinel-2A
  and Sentinel-2B** (their B12 SRFs differ enough to matter).

The LUT tabulates `m_MBSP` over ΔΩ ∈ [−0.5, 3.0] (351 points; the top end raised from v2's 2.0
so saturated super-emitter cores don't clip at the grid end) × AMF ∈ [2.0, 4.0] (9 points).
`conversion.py` loads it (cached), interpolates the forward curve along AMF, and inverts it
(monotonic `np.interp`, clamping ΔR outside the tabulated range to the grid ends). ΔXCH4 in
ppb is `ΔΩ / Ω_air · 1e9` with the dry-air column `Ω_air = 3.567e5 mol/m²`.

**Anchor (sanity-checked in `test_varon_anchor`, regression-pinned in
`test_v4_regression_pin`):** at AMF = 1/cos 40° + 1 ≈ 2.305 and a doubled background
(ΔΩ = 0.65 mol/m²), the v4 LUT gives `m_MBSP ≈ −0.0357` (S2A) / `−0.0268` (S2B) — still ~23 %
above Varon's published −0.029 / −0.022 in magnitude, with the correct S2A/S2B ordering. The
Varon anchor is kept only as a loose ±30 % sanity band; correctness is pinned against our own
generated reference (`V4_ANCHOR_*`, pasted from the npz, not estimated).

**Key result of Phase 3.5 Stage 3 (a refuted hypothesis).** The two spectroscopy gaps the
roadmap blamed for the ~25 % offset — interfering **H2O/CO2** and **solar-spectrum weighting** —
were added in v4 and turn out to be **minor**: they shrink |m| by only **~1.6 %** (v3 −0.0363 →
v4 −0.0357), leaving ~23 % of the Varon discrepancy **unexplained**. So the offset is *not*
dominated by those omissions; the remaining candidates are multiple scattering / aerosols
(entirely absent from our Beer–Lambert model), the site-elevation surface pressure P₀ baked in
at sea level, and deeper reference-model structural differences. v4 ships for physical
completeness and to make this finding checkable from the repo — **not** because it improves the
calibration (see §8.2: it is empirically indistinguishable from v3). Earlier LUT history: the
interim v2 (single Curtis–Godson layer) agreed with Varon to ~8 % by *error cancellation*
(evaluating the enhancement at half surface pressure understated its absorption, cancelling the
then-missing interfering-gas/solar effects); v3 removed that, and v4 confirms those effects were
small to begin with.

**MBMP inversion** is per-pass: `ΔΩ_MBMP = invert(ΔR_target; AMF_t, sat_t) −
invert(ΔR_ref; AMF_r, sat_r)`. Inverting each pass with its own AMF and spacecraft (then
subtracting the columns) is Varon's definition and handles mixed S2A/S2B pairs correctly.

## 3. Plume masking

The plume footprint is thresholded on the **ΔΩ field from a frozen canonical inversion**
(`ch4_lut_mask.npz`), *decoupled from the reporting LUT*. This makes the footprint **invariant
to a reporting-LUT recalibration** — v3→v4 changes the retrieved columns and IME but never which
pixels are called plume (see §8 and the invariance test `test_footprint_invariant_under_lut_swap`).
The mask LUT is a pinned snapshot and is bumped only to *deliberately* move masks, never
alongside the reporting LUT.

Why a frozen inversion rather than the LUT-independent ΔR field (the natural first idea):
masking on raw `−ΔR` was tried and rejected — for MBMP it reintroduces surface-structure
sensitivity (the raw ΔR difference does not cancel co-clamped structure the way the per-pass
ΔΩ difference does) and *displaces* the mask off the source (§8). The per-pass inversion is what
actually localises the plume, so the mask keeps it — just with a fixed inversion.

`plume.py` thresholds the **positive** enhancement tail of that ΔΩ field at `k·σ`, where σ is a
robust background estimate (`1.4826 · MAD`, NaN-aware). It optionally applies a 1-px
`binary_opening` (removes speckle), labels connected components with 8-connectivity, drops
components below `min_area_px`, and keeps the component(s) intersecting a 7×7 window around a
supplied source pixel — or, failing that, the component holding the peak enhancement. No plume
above threshold is a valid, empty result (not an error). The mask is vectorised to an EPSG:4326
MultiPolygon outline (`rasterio.features.shapes`; pixel-cornered, unsmoothed).

The **mass** (IME) and the retrieval-noise bootstrap use the *reporting* ΔΩ (mol/m²):
`ime.quantify` sums the reporting ΔΩ over the mask, and the bootstrap samples the off-plume
reporting-ΔΩ population. There are therefore two distinct σ's — the mask-threshold σ on the
frozen-LUT field (`sigma_mask`) and the retrieval-noise σ on the reporting field
(`sigma_noise_delta_omega`); `result_json` names them separately, and
`mask_domain: "frozen_lut_delta_omega"` records the choice.

## 4. Quantification — IME + Monte-Carlo uncertainty

Emission rate follows the Integrated Mass Enhancement mass balance (Varon et al. 2021):

```
Q = U_eff / L · IME
```

- `IME = Σ_mask ΔΩ · A_pix · M_CH4` (kg), the integrated methane mass over the plume mask.
- `L = √(n_px · A_pix)` (m), the characteristic plume length.
- `U_eff = 0.33 · U10 + 0.45` (m/s), the LES-calibrated effective wind (Varon et al. 2021,
  Sect. 3). U10 is the ERA5 10 m wind sampled at the scene's overpass time (`sample_wind_at`
  at t and t ± 1 h, ERA5-Land with a global-ERA5 water fallback).

Uncertainty is propagated by a **seeded Monte Carlo** (n = 500, `np.random.default_rng`),
jointly perturbing four terms per draw:

1. **Mask/threshold jitter** — `k` drawn uniformly from `{1.5, 1.75, 2.0, 2.25, 2.5}`; the
   mask/IME/L for each k are precomputed once (5 labelings, not 500).
2. **Wind** — `U10 ~ Normal(U10, σ_u10)`, truncated at ≥ 0.1 m/s.
3. **Retrieval noise** — a bootstrap of the off-plume ΔΩ population (σ recomputed excluding the
   mask, so the plume can't inflate its own error), summed into the IME.
4. **Mass-balance model error** — a multiplicative `Normal(1, 0.15)` factor on Q.

The reported Q is the MC median; the band is the MC standard deviation; percentiles and a
histogram feed the UI. Same seed ⇒ bit-for-bit identical estimate.

### Error budget — literature vs modeling choices

| Term | Value | Source |
|---|---|---|
| `U_eff = 0.33·U10 + 0.45` | — | Varon et al. 2021, LES calibration (literature) |
| `SIGMA_U10_FLOOR_MS` | 1.5 m/s | **our modeling choice** — a reanalysis 10 m wind error floor (Varon's GEOS-FP-vs-mesonet residuals aren't reproducible here) |
| `IME_MODEL_SIGMA_FRAC` | 0.15 | **our modeling choice** — multiplicative mass-balance model error (Varon's LES hold-out isn't reproducible here) |

Honest, documented constants beat fake rigor; both are declared in `methane/constants.py`.

## 5. Tier-1 screening (S5P/TROPOMI)

`tropomi.py` screens a region for persistent XCH4 enhancement before spending Sentinel-2
retrievals: a per-pixel median background over `[start − background_days, start)`, then each
ISO week's mean minus that background reduced over a `cell_deg` cell lattice (one `reduceRegions`
per week). A cell-week is *flagged* when its enhancement exceeds `sigma_thresh × robustσ` of all
cell-weeks; cells are scored `mean_enh / σ` and the top-N returned with persistence counts.

## 6. Validation — reference cross-match

`validation.py` ingests public inventories (IMEO, SRON) from a tolerant CSV or a GeoJSON of
Point features (alias-mapped columns, t/h → kg/h). A detection is cross-matched by haversine
distance and time:

- **confirmed** — a reference event within 15 km **and** ± 14 days,
- **plausible** — within 15 km **and** ± 60 days,
- **unvalidated** — otherwise.

`contradicted` is never assigned automatically (event lists prove presence, not absence — that
is a human PATCH only).

## 7. Limitations

- **No scattering / single-scene surface heterogeneity** — MBSP has no reference to cancel
  static surface structure, so bright/dark terrain produces false positives; MBMP needs a
  genuinely plume-free reference.
- **Reference selection is sensitive** — for scenes sharing one orbit, the nearest candidate is
  the *same overpass* (adjacent UTM tile, Δt ≈ 0) and images the same plume; `pick_reference`
  excludes it (`min_days`), but the best different-date reference still varies with surface
  conditions (see the reproduction table). A *continuous* source (e.g. the Hassi Messaoud
  blowout) has no in-period plume-free reference at all, so single-scene MBSP is used there.
- **Detection floor** — roughly 1–5 t/h for favourable surfaces/wind; weaker plumes fall into
  the retrieval noise.
- **ERA5 vs local wind** — reanalysis 10 m wind is coarse (~11 km, hourly); U_eff error
  dominates the budget for slow, well-defined plumes.
- **LUT physics** — CH4-only Beer–Lambert (no multiple scattering, aerosols, interfering
  H2O/CO2, or solar-spectrum radiance weighting). The vertical structure is resolved (layered
  US Std Atmosphere background, 500 m enhancement slab), so the remaining ~25 % anchor offset
  vs Varon is attributable to the named spectral omissions, not to a guessed effective (T, p).
  The LUT also bakes in sea-level surface pressure — sites at significant elevation are biased.
- **Plume mask is now invariant to reporting-LUT recalibration** *(resolved in Phase 3.5)* — the
  `k·σ` threshold operates on the ΔΩ field of a *frozen* canonical inversion (§3), so a change to
  the reporting LUT (v3→v4) no longer moves the footprint; only the reported ΔΩ *columns* (and
  hence IME/Q) change. Enforced bit-identically by `test_footprint_invariant_under_lut_swap`.
- **Source localisation partly rides on the S2A/S2B inversion difference** — for mixed-spacecraft
  MBMP pairs (S2A reference, S2B target), the per-pass inversion maps near-equal ΔR (ΔR_t ≈ ΔR_r)
  to a non-zero ΔΩ difference because the S2A and S2B LUT curves differ, contributing to *where*
  the ΔΩ-domain mask places the plume. This affects the shipping Phase 3 masks as well; it is why
  raw-ΔR masking (which lacks this signal) displaces the mask off-source, and one reason the
  calibration scatter (§8.2) is wide.

## 8. Reproduction and calibration results

Two live-EE instruments validate the pipeline. They are complementary: the first is a fast
pass/fail gate on two hand-checked events; the second is a multi-event regression that makes
the LUT / masking changes of Phase 3.5 falsifiable.

### 8.1 Two-event exit gate (Phase 3)

`OPENEARTH_EE_TESTS=1 uv run python scripts/validate_events.py` reproduces two documented
super-emitter events against live Earth Engine (values verified against Varon et al. 2021),
using the v3 (layered) LUT:

| Event | Method | Published | Ours | Verdict |
|---|---|---|---|---|
| Korpezhe, Turkmenistan, 2018-06-19 | MBMP (ref 2018-06-24) | 11.2 ± 5.2 t/h | 13.7 ± 22.7 t/h | ✅ within ±50 %, σ overlaps (wide MC band — see note) |
| Hassi Messaoud blowout (Nov 2019 – Jan 2020) | MBSP, mean of 3 scenes | mean 9.3 ± 5.5 t/h | 8.5 t/h (6.8, 13.2, 5.4) | ✅ within ±50 % |

Korpezhe's reference is pinned (its auto pick is the unusable same overpass); Hassi Messaoud is
a continuous blowout, so single-scene MBSP at the well is averaged over three cloud-free scenes.

### 8.2 Multi-event calibration regression (Phase 3.5)

`OPENEARTH_EE_TESTS=1 uv run python scripts/calibration_harness.py` runs `analyze` over the
17 same-scene Sentinel-2 events in `scripts/data/calibration_events.json` — IMEO's
per-scene MARS-S2L quantifications plus the Korpezhe (Varon et al. 2021) anchor, spanning
13 regions and ~5–25 t/h — and regresses our retrieved rate against the published rate.
Every event's published value derives from the *same* S2 acquisition we analyze (the
same-scene principle); the SRON TROPOMI weekly list is excluded (7 km pixels, tens-of-t/h
scale, different overpass — that measures source variability, not our calibration).

**MBSP applicability (a genuine finding, not a nuisance).** *Method is our per-event analysis
choice, not a property of the published event.* Single-scene **MBSP** has no reference to
cancel static surface structure, so over heterogeneous terrain a coherent dark/bright region
inverts to the **clamped LUT ΔΩ grid edge** and the connected-component step engulfs it into a
multi-thousand-pixel false plume of hundreds of t/h (turkmenistan-caspian: 473 t/h, a
9 561-pixel mask that is 76 % LUT-saturated). This is exactly why Varon et al. 2021 prefer
MBMP: **MBSP is reliable only over spectrally homogeneous (arid) surfaces** (Hassi Messaoud).
We therefore **default every event to MBMP with a pinned, plume-free reference** — the
reference pass carries the same static surface structure, so co-located saturation cancels in
the ΔΩ difference (turkmenistan-caspian → 11.9 t/h vs 12.3 published) — and fall back to MBSP
only where no clean reference exists and the retrieval is itself valid. A retrieval whose plume
mask exceeds 20 % LUT-saturated fraction is a **documented exclusion** (`excluded_lut_saturated`,
with the fraction recorded), published-value-blind — never a silent drop and never a crash;
`no_plume` is likewise recorded. `scripts/curate_calibration_events.py --recurate` resolves the
per-event method + reference live.

Baseline (LUT v3, MC seed 0, n = 500; committed at `scripts/data/calibration_baseline_v3.json`):

| Event | Method | Published (t/h) | Ours (t/h) | Note |
|---|---|---|---|---|
| hassi-messaoud-2020-01-19 | MBMP | 7.0 | 7.5 ± 3.7 | |
| algeria-ghardaia-2020-08-27 | MBMP | 6.5 | 5.4 ± 2.0 | |
| neuquen-2022-06-11 | MBMP | 14.9 | 11.8 ± 3.4 | |
| libya-sirte-2020-01-21 | MBMP | 14.7 | 1.7 ± 0.8 | reference likely plume-contaminated (recurrent) |
| campeche-2024-09-13 | MBSP | 25.4 | — | *excluded:* LUT-saturated (offshore water) |
| ahvaz-2023-12-08 | MBSP | 7.5 | 10.9 ± 10.0 | homogeneous surface; no clean reference |
| gulf-of-thailand-2023-10-05 | MBMP | 15.3 | 17.1 ± 6.2 | |
| turkmenistan-caspian-2017-11-26 | MBMP | 12.3 | 11.9 ± 7.1 | 473 t/h under MBSP (76 % saturated) |
| permian-2023-09-27 | MBMP | 6.9 | 13.2 ± 4.5 | |
| maturin-2024-02-20 | MBSP | 25.0 | — | *excluded:* no plume above threshold |
| marib-2024-11-02 | MBMP | 7.1 | 2.3 ± 1.3 | |
| gulf-of-suez-2023-09-20 | MBMP | 20.0 | 51.0 ± 17.8 | |
| rub-al-khali-2023-12-09 | MBMP | 5.2 | 12.7 ± 4.3 | 3 343-px MBSP blowup fixed by MBMP |
| kazakhstan-almaty-2019-09-18 | MBMP | 9.6 | 23.8 ± 7.5 | |
| amudarya-2024-05-29 | MBMP | 11.3 | 5.7 ± 1.7 | |
| turkmenistan-south-2018-10-02 | MBMP | 25.1 | 8.3 ± 4.4 | |
| korpezhe-2018-06-19 | MBMP | 11.2 | 5.6 ± 5.0 | pinned plume-free reference |

**Aggregates (15 quantified, LUT v3):** through-origin slope **β = 1.03**, median ratio
**0.97**, robust log-scatter **s = 0.42** (≈ a factor of 2.6). With N ≈ 15 these are
engineering diagnostics, not hypothesis tests. The central calibration is essentially
unbiased; the wide scatter is honest and has known causes we do **not** chase per-event:
(i) *reference quality* — recurrent emitters may have no in-period plume-free reference, so a
contaminated reference over-subtracts (libya-sirte 1.7 vs 14.7); (ii) IMEO/Varon rates embed
*their* wind source while ours is ERA5; (iii) single-scene surface heterogeneity. This baseline
is the reference against which Stage 2 (frozen-mask-LUT footprints) and Stage 3 (LUT v4) are measured;
the harness `--compare` reruns and diffs a fresh run without overwriting it.

**v4 vs v3 — an explicit exit-gate deviation (documented honestly).** The Stage 3 exit gate as
written required `|slope − 1|` to shrink. It **did not**: swapping v3→v4 (masks held fixed by the
frozen mask LUT, so this isolates the column change) moves the four aggregates by ≤ 0.015 each —
*smaller than a rounding wobble against the s ≈ 0.41 scatter* — but in **opposite directions**:

| Aggregate | v3 | v4 | |
|---|---|---|---|
| through-origin slope | 1.027 | 1.042 | away from 1 |
| Theil–Sen slope | 0.200 | 0.190 | away from 1 |
| median ratio | 0.967 | 0.972 | toward 1 |
| log-scatter | 0.424 | 0.413 | tighter |

Both slope-like estimators drift *away* from 1 while both robust estimators improve. This split
is **structurally forced, not evidence against v4**: the residual distribution straddles 1 (median
0.97 < 1 < leverage-weighted slope 1.04), so v4's uniform ~1.6 % lift of every retrieved Q *cannot*
help both sides at once — it pulls the (below-1) median up toward 1 and pushes the (above-1) slope
further past it. We therefore ship v4 as **empirically indistinguishable from v3** on this set, for
physical completeness and the repo-checkable refuted-hypothesis result (§2) — **not** as a
calibration improvement. Both baselines stay committed (`calibration_baseline_v3.json`,
`calibration_baseline_v4.json`) so the comparison is checkable from the repo alone.

**Open finding — a rate-dependent skew.** That the median sits below 1 while the slope sits above
it means we tend to *over*-estimate the largest emitters and *under*-estimate typical ones (e.g.
gulf-of-suez 51 vs 20, kazakhstan 24 vs 10; against marib 2.3 vs 7.1, amudarya 5.7 vs 11.3). This
is a genuine open question, not tuned away: candidate mechanisms are mask-size saturation at high
IME (the k·σ footprint grows super-linearly for a bright plume), U_eff wind-error scaling, and the
IME's heavy upper tail. Left for a future phase.

### LUT history note

**LUT history at Korpezhe (v1 → v2 → v3).** Korpezhe's point estimate moved
9.6 → 5.4 → 13.7 t/h across the three LUTs while the retrieved ΔR field never changed —
*because v1–v3 thresholded the mask in ΔΩ space*, it was invariant under *linear* rescaling of
the inversion but shifted whenever the curve changed shape (v2's single-effective-layer curve
was nonlinearly shallower, collapsing the mask 50 → 12 px). **Phase 3.5 Stage 2 removed this
sensitivity**: the mask is thresholded on the ΔΩ of a *frozen* canonical inversion decoupled from
the reporting LUT (§3), so a reporting-LUT swap changes only the columns and IME, never the
footprint (`test_footprint_invariant_under_lut_swap`).

Masking on the LUT-*independent* raw `−ΔR` field was tried first (the obvious way to get
invariance) and **rejected after diagnosis**: for the MBMP-heavy calibration set it gave masks
with *zero* overlap with the true plume. Two causes — (i) the raw ΔR difference does not cancel
co-clamped surface structure the way the per-pass ΔΩ difference does, so its tail is dominated by
surface edges; (ii) the mask component displaces off the seeded source (the offsets are not
systematically downwind, so not simple advection). The per-pass inversion is what localises the
plume, so the frozen-mask-LUT keeps it while still decoupling the footprint from calibration. The
remaining Korpezhe MC width is genuine plume-footprint ambiguity for this intermittent,
different-date-reference event — we report the wide band rather than tuning k to shrink it.

## 9. ML tier — a candidate ranker over the physics pipeline (Phase 5)

The ML tier is a **candidate ranker that feeds the human-review detection feed, never an
autonomous detector.** Physics (§1–§4) stays the load-bearing tier; the U-Net only proposes
scenes worth a reviewer's attention and does not change any reported column or flux.

### 9.1 Training dataset and the license wall

The model is trained on **CH4Net** (Vaughan et al. 2024, *Atmos. Meas. Tech.* 17, 2583–2593,
[doi:10.5194/amt-17-2583-2024](https://doi.org/10.5194/amt-17-2583-2024)): 925 hand-annotated
plume masks drawn from 10,046 Sentinel-2 images over 23 super-emitter sites — **all Turkmenistan
oil-and-gas** — with a 2017–2020 train / 2021 test split. Tiles are ~200×200 px with all bands
interpolated to 10 m; the masks were **annotated with MBMP guidance**, so the labels inherit
MBMP's blind spots (a model that beats an MBMP baseline on these labels ranks candidates better,
it does not necessarily see plumes MBMP cannot — see §9.2 once populated). The published dataset
lives on Hugging Face as `av555/ch4net`
([doi:10.57967/hf/2117](https://doi.org/10.57967/hf/2117)), ~9.8 GB, **CC-BY-NC-ND 4.0 and
gated**.

**License wall (non-negotiable).** CC-BY-NC-ND forbids redistributing derivatives and commercial
use. Consequently **nothing derived from CH4Net is ever committed to this repo or published** — no
imagery, masks, rebuilt chips, per-file manifests, or trained weights. Everything derived lives
under the git-ignored `data_dir/ml/`; the repo keeps only code, configs, and aggregate
metrics/provenance JSON. The trained model ships out-of-band (a `data_dir` path + settings), and
the ND term is recorded in the model manifest and blocks any future *public* deployment of the
weights. NC is satisfied by private research use.

**Train/serve consistency.** We never train on CH4Net's own imagery: their tiles are Sentinel-Hub
L1C interpolated to 10 m, whereas our scan pipeline sees GEE L1C at 20 m. Training on theirs would
deploy a distribution shift, so chips are **rebuilt through our own `fetch_chip` at 20 m** (the
identical code path used at scan time), and the CH4Net masks are regridded onto our grid. Because
all 23 sites are Turkmenistan O&G, site-held-out cross-validation controls intra-region leakage
but *not* geography — expect degraded performance on other surfaces, stated wherever the scan UI
or docs could imply generality.

### 9.2 Recovering the stripped scene metadata

Rebuilding chips at 20 m needs each tile's **date + footprint**, but the published HF release names
every tile by an opaque integer index — it carries no date, site, scene id, or georeferencing (the
preprint-era Zenodo record that did is dead). We recover that mapping self-service in
`scripts/recover_ch4net_metadata.py` (offline clustering + Earth-Engine matching, all round-trips
through `ee_call`), keyed only on the 23 published site coordinates (Vaughan et al. 2024, Table 2 —
from the CC-BY *paper*, not the gated dataset):

1. **Cluster** (offline). The 10,983 tiles fall into 7 pixel-shapes (latitude bands); within each,
   content-correlation on a plume-invariant NIR band groups tiles by ground footprint. A site's
   appearance drifts over 2017–2021 (active O&G — new pads, spoil), so clustering *over*-segments
   a site into several clusters, which is safe (no cluster spans two sites) — it just yields clean
   single-footprint cores.
2. **Geolocate** (EE). A median-composite GEE reference is built at each site coordinate; each
   reliable cluster's median tile is matched by normalised cross-correlation, and the **NCC peak
   *location*** gives the footprint centre → bbox + nearest published site. Pilots recovered centres
   a median **~10 m** from the published coordinates.
3. **Dates** (EE). Per site, one coarse chip per Sentinel-2 overpass (2017–2021); each tile is
   matched by correlation, with a hard **split-year prior** (train/val ≤ 2020, test = 2021) and a
   confidence flag.

**Results (aggregate — the per-tile mapping is a CH4Net derivative and is never committed).** All
10,983 tiles are sited across all 23 sites; the site labels are cross-validated *independently* by
the paper — recovered positives-per-site rank matches Table 2's plume percentages (the 39/38/37 %
sites get the most recovered positives; the 0 % sites almost none). Date recovery is **42 %
confident** (median correlation 0.65) at **100 % split-year consistency**, but uneven: isolated
sites are near-perfect (T18/T20/T23 ≈ 0.9 correlation) while the northern, high-activity,
low-contrast cluster (T6/T3/T7) sits near 0.45. A tile is marked **usable** under an asymmetric
policy: a **positive needs a confident date** (a wrong date rebuilds a plume-free chip under a plume
mask = label noise), whereas a **negative** only needs any plume-free scene over its footprint. This
yields **10,395 usable tiles — 409 confident positives (of 997) + all 9,986 negatives.** The reduced
positive count (and thin coverage at the northern high-emitter sites) is the main cost of working
from the metadata-stripped release, recorded as a limitation; an official index→(site, date) mapping
from the authors would supersede the recovered one.

### 9.3 Physics-informed input channels

The U-Net does not see raw reflectance. Its five input channels are built in
`openearth/methane/channels.py` — **pure NumPy, in `core`, so training and serving call the
byte-identical function** (the train/serve-consistency invariant, §9.1). The channel order *is* the
serving contract:

```
CHANNELS = ("mbmp_delta_r", "mbsp_delta_r", "ratio_b12_b11", "b12", "b11")
```

The first two are the fractional-signal fields from the retrieval (§1): the multi-band multi-pass
(MBMP, target vs. a clear reference) and single-pass (MBSP) ΔR maps. These sit **upstream of the
LUT**, so the channels are invariant to LUT recalibration (Phase 3.5) — a U-Net trained now stays
valid across LUT versions. Channels 3–5 give the network the raw SWIR context an ΔR field alone
discards: the B12/B11 band ratio and the two SWIR reflectances that carry the CH₄ absorption. Each
channel is robust-standardized `(x − median) / (1.4826·MAD)` with per-channel `median`/`MAD` frozen
into the model manifest (**data, not code** — computed once from the training chips and applied
verbatim at scan time); invalid pixels resolve to 0 after normalization. The fully convolutional
network is reflect-padded to a multiple of 32 at serve time (`pad_to_multiple`), so a scan chip need
not match the training tile size.

### 9.4 Cross-validation design and evaluation

**Site-held-out CV.** All 23 sites are Turkmenistan O&G, so a random tile split would leak surface
texture between train and test. The evaluation therefore uses **`GroupKFold` by site, 5 folds** — no
site appears in both the train and the held-out set of a fold. Augmentation is **D4 only** (flips +
90° rotations); no photometric jitter is applied to physical channels. The gate metric is
**scene-level F1**: a scene is *predicted positive* iff `candidates_from_prob(prob, threshold=0.5,
min_px=5)` is non-empty, *truth positive* iff the (regridded) CH4Net mask is non-empty — the same
rule and same `min_px` score the model and the baseline. The **physics baseline** is
`plume.detect_plume` on `−ΔR_MBMP` at the pipeline-default `k_sigma`, run on the identical chips and
folds. Pixel IoU on the true positives is reported (not gated).

Per-fold results (frozen in `scripts/data/ml_eval_v1.json`, `ml_eval_v1`):

| Fold | Held-out sites | n (pos) | **Model F1** | Model P / R | Baseline F1 | Baseline P / R | Pixel IoU (TP) |
|------|----------------|---------|-------------|-------------|-------------|----------------|----------------|
| 0 | T1, T10, T20, T6, T9 | 359 (108) | **0.489** | 0.354 / 0.787 | 0.387 | 0.299 / 0.546 | 0.241 |
| 1 | T13, T16, T18, T21, T22 | 309 (87) | **0.613** | 0.472 / 0.874 | 0.455 | 0.321 / 0.782 | 0.336 |
| 2 | T15, T23, T4, T7, T8 | 284 (79) | **0.479** | 0.315 / 1.000 | 0.428 | 0.317 / 0.658 | 0.249 |
| 3 | T11, T17, T3, T5 | 234 (68) | **0.702** | 0.590 / 0.868 | 0.551 | 0.404 / 0.868 | 0.343 |
| 4 | T12, T14, T19, T2 | 209 (53) | **0.700** | 0.563 / 0.925 | 0.500 | 0.361 / 0.811 | 0.432 |
| **Mean** | — | — | **0.597** | 0.459 / 0.891 | **0.464** | 0.341 / 0.733 | — |

The model beats the physics baseline on the scene-level F1 gate in every fold (mean **0.597 vs
0.464**), driven by recall (0.891 vs 0.733) at comparable-or-better precision — it flags more of the
truly-plumed scenes for the same false-positive budget. Three caveats bound what that means:

- **Label noise (the load-bearing caveat).** The CH4Net masks were drawn with MBMP guidance (§9.1),
  so they inherit MBMP's blind spots. A model that beats an MBMP-derived baseline on MBMP-derived
  labels is a **better candidate ranker** — it is *not* evidence it sees plumes MBMP cannot. This is
  why the tier feeds human review rather than replacing physics.
- **Geography.** All 23 sites are Turkmenistan O&G. Site-held-out CV controls intra-region leakage,
  not geography; expect degraded performance on other surfaces. The scan UI and this section say so.
- **Deployed vs. CV model.** The fold models estimate field performance; the **deployed** model is
  retrained on *all* data after CV with full-trainset stats (standard practice — shipping fold-0
  would waste 20 % of a small dataset). Its performance estimate is the CV aggregate above, recorded
  as `cv_scene_f1` in the manifest.

### 9.5 Serving — the ML scan and the disagreement flag

The trained network is exported to **ONNX (opset 18, dynamic H/W)** and served by the API through
**onnxruntime (CPU) only — never torch** (`packages/api` has no torch dependency; a
`test_no_ml_deps` guard enforces it, mirroring the no-UI-deps rule). The session and manifest load
lazily, so a missing model is a clean `503` at submit and the app boots with nothing installed.
Single-chip inference is ~16 ms on CPU (`latency_ms_p50` in the manifest), comfortably under the
1 s/chip budget.

`POST /methane/ml/scan {site_id, start, end, max_scenes?}` walks a site's S2 scenes: for each it
picks an MBMP reference, `fetch_chip`s target + reference, builds and normalizes the five channels,
runs the U-Net, and thresholds the probability map into candidate footprints. A scene with ≥ 1
candidate becomes a **detection row with `source="ml"`, `method="ml_unet"`, `status="candidate"`** —
it enters the *same* feed as physics detections, carrying a `score` (max candidate probability) and
a **single-pass Q**: the ΔR→ΔΩ→XCH₄ inversion and IME are run once over the ML footprint
(`ime.emission_over_mask`, no Monte-Carlo), so `q_kg_h` is magnitude-comparable in the feed while
`q_sigma_kg_h` is deliberately null — the full MC uncertainty budget stays a physics-tier feature.
The npz artifact carries `xch4_ppb`/`mask`/`prob`/`rgb`/`grid`, so the existing overlay and
`array.npz` routes serve ML rows unchanged.

**Disagreement flag.** Each ML row records `disagreement ∈ {agree, ml_only}`: `agree` if a physics
detection already exists for the same site + scene, else `ml_only`. The symmetric `physics_only`
view is a feed-level read, never a mutation of a physics row. In the Lab the flag surfaces as a chip
on the detection detail, next to the model version and score, under the fixed caption **"ML candidate
— requires review; not an autonomous detection."**

**License / ND consequence (restated because it binds deployment).** The trained weights are a
CH4Net derivative. CC-BY-NC-ND's **ND** term forbids redistributing them, so — like the chips and
masks — **no weights, ONNX file, or manifest is ever committed** (they live under the git-ignored
`data_dir/ml/models/`); the model ships out-of-band via a settings path. The manifest records the
license and a `not_for_public_deployment` flag; any future *public* deployment of the app must ship
without these weights (retrain on a redistributable dataset) until an appropriately-licensed model
exists.

## 10. EMIT tier — independent plume evidence (Phase 6)

EMIT (the *Earth surface Mineral dust source InvesTigation* imaging spectrometer on the ISS)
retrieves methane column enhancements at **60 m** from its SWIR bands — fine enough to resolve
individual plumes and their source facilities, unlike TROPOMI's ~7 km grid. The EMIT tier attaches
EMIT's **published plume-complex product** to our detections as *independent evidence from another
instrument*; it is **not** part of our retrieval and changes no reported column or flux. Two things
make this an honest add-on rather than a second detector:

- EMIT plumes are stored as an `emit_json` blob **on the existing detection row**, never as
  detection rows of ours — so the EMIT tier writes no `detections.source` and is fully decoupled
  from the physics/ML feed. A row that was never cross-matched has `emit_json IS NULL` ("never
  checked"), distinct from a checked-but-empty result (`matches: []`).
- Every plume carries a `provenance` tag, because there are **two sources with different coverage**
  (see §10.1). No UI or number ever implies EMIT covers "now" through the frozen mirror.

### 10.1 Two sources, one plume model

`packages/core/.../methane/emit.py` exposes one `EmitPlume` model fed by two paths, split by a hard
date boundary (`gee_available` / `GEE_CH4PLM_CUTOFF = 2024-10-26`):

- **GEE V001 mirror** (`NASA/EMIT/L2B/CH4PLM`, band `methane_plume_complex`, ppm·m): a *frozen*
  copy covering 2022-08-10 → 2024-10-26; V001 was decommissioned at LP DAAC upstream. `provenance =
  "gee_v001"`, no emission rate (V001 metadata only). The outline is **not** the granule footprint —
  the band is the full matched-filter field (negatives included) cropped to a small plume tile, so
  the outline is `reduceToVectors` over the **positive-enhancement mask** `band.gt(0)` (integral, as
  `reduceToVectors` requires; `selfMask` alone leaves a float band). All outlines in a query are
  vectorised server-side and pulled in one `ee_call`.
- **LP DAAC V002 GeoJSON** (`EMITL2BCH4PLM` v002, via **earthaccess**, lazy-imported in the API so
  `create_app()` stays credential-free): the live collection past the freeze. One granule's
  `CH4PLMMETA` JSON asset (never the COG) carries the outline, max-enhancement coords, and — new in
  V002 — an **emission-rate estimate ± uncertainty** (`q_kg_h` / `q_sigma_kg_h`). The parser is
  tolerant of the DAAC/portal schema variance: missing numerics arrive as the string `"NA"` and
  coerce to `None`. `provenance = "lpdaac_v002"`. **V001 and V002 rasters are not numerically
  identical** (V002 changed the matched-filter channel selection), so a quantitative comparison
  never mixes versions.

Windows straddling the boundary query both paths and de-duplicate (same instant + near-same
location; the V002 plume wins because it carries the rate). Cross-match against a detection reuses
`validation.haversine_km`: a plume within **≤ 5 km and ≤ 3 days** of the detection's scene is a
match, sorted nearest-first (ties by smaller |Δt|), each plume located by its max-enhancement point
(V002) or outline centroid (V001).

### 10.2 Column-unit cross-check (order-of-magnitude context, **not** a gate)

EMIT reports enhancement in **ppm·m** (mixing-ratio enhancement integrated over the light path);
our retrieval reports ΔΩ in **mol/m²**. They convert through the air number density
n = P/(RT): `ΔΩ [mol/m²] = ΔE [ppm·m] × 10⁻⁶ × n`. At **US-Standard surface** (P = 101.325 kPa,
T = 288.15 K), n = 42.3 mol/m³, so **1 ppm·m ≈ 4.3 × 10⁻⁵ mol/m²** (≈ 3.7 × 10⁻⁵ at the Permian's
~900 m / 300 K, i.e. ±~15 % across plausible surface P/T — state the assumption, never treat the
constant as exact).

Applied to one live matched event — the **2023-06-16 Permian super-emitter** (CH4PLM
`…20230616T211343…`, V001), the strongest EMIT plume in that scene:

| Quantity | EMIT CH4ENH (2023-06-16, masked to plume) | Our S2 MBMP (nearest overpass 2023-06-19, +3 d) |
|---|---|---|
| masked-mean ΔΩ | 968 ppm·m ≈ **4.1 × 10⁻² mol/m²** | **1.3 mol/m²** (ΔXCH₄ₘₐₓ ≈ 9500 ppb, Q ≈ 4.1 × 10⁴ kg/h) |
| peak ΔΩ | 7506 ppm·m ≈ 0.32 mol/m² | 3.4 mol/m² |

The two **independently confirm an extreme column-scale super-emitter at this exact location**, and
the unit relationship checks out — but the magnitudes differ by ~1.5 orders. This is *expected*, not
alarming, and is why this is context and never a gate: (a) the overpasses are **3 days and two
instruments apart** — the plume, wind, and emission all differ between 2023-06-16 and 2023-06-19;
(b) the masks/thresholds differ (EMIT's positive-enhancement footprint vs our kσ plume core); and
(c) our MBMP retrieval carries the **known calibration gap** (§7, §8.2) and runs high over the
Permian's bright soil. The takeaway the docs are allowed to draw is directional only: both sensors
flag a 10⁻²–10⁰ mol/m² event here; the EMIT plume is genuine independent corroboration of the
detection, not a calibration reference.
