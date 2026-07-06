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
precomputed lookup table, `methane/data/ch4_lut_v1.npz`.

**Physics (`scripts/generate_ch4_lut.py`, run once with network):** Beer–Lambert band
transmittance, no scattering.

- CH4 absorption cross sections σ(ν) from **HITRAN** via HAPI `absorptionCoefficient_Voigt`
  (main isotopologues, T = 288.15 K, p = 1 atm), on 0.005 cm⁻¹ grids spanning B11
  (≈ 5946–6497 cm⁻¹) and B12 (≈ 4310–4812 cm⁻¹) ± 50 cm⁻¹.
- Slant optical depth `τ(ν; ΔΩ, AMF) = (Ω₀ + ΔΩ) · AMF · N_A · 1e−4 · σ(ν)`, with the
  background column `Ω₀ = 0.65 mol/m²` (1875 ppb) and `AMF = 1/cos θ_sun + 1/cos θ_view`.
- SRF-weighted band transmittance `T_b = ∫ SRF_b · e^{−τ} dν / ∫ SRF_b dν`, using the ESA
  Sentinel-2 spectral response functions (document COPE-GSEG-EOPG-TN-15-0007, issue 3.2;
  the B11/B12 columns are committed at `scripts/data/s2_srf_b11_b12.csv`).
- Per-band fractional signal `m_b(ΔΩ) = T_b(Ω₀+ΔΩ)/T_b(Ω₀) − 1`, combined to
  `m_MBSP = (1 + m_B12)/(1 + m_B11) − 1`, computed **separately for Sentinel-2A and
  Sentinel-2B** (their B12 SRFs differ enough to matter).

The LUT tabulates `m_MBSP` over ΔΩ ∈ [−0.5, 2.0] (251 points) × AMF ∈ [2.0, 4.0] (9 points).
`conversion.py` loads it (cached), interpolates the forward curve along AMF, and inverts it
(monotonic `np.interp`, clamping ΔR outside the tabulated range to the grid ends). ΔXCH4 in
ppb is `ΔΩ / Ω_air · 1e9` with the dry-air column `Ω_air = 3.567e5 mol/m²`.

**Anchor (verified in `test_varon_anchor`):** at AMF = 1/cos 40° + 1 ≈ 2.305 and a doubled
background (ΔΩ = 0.65 mol/m²), the LUT gives `m_MBSP ≈ −0.037` (S2A) / `−0.028` (S2B), within
±30 % of Varon's published −0.029 / −0.022 (our model omits scattering, other gases, and
radiance weighting), and with the correct S2A/S2B ordering (`|m_S2A| > |m_S2B|`, ratio 1.33 vs
the published 1.32).

**MBMP inversion** is per-pass: `ΔΩ_MBMP = invert(ΔR_target; AMF_t, sat_t) −
invert(ΔR_ref; AMF_r, sat_r)`. Inverting each pass with its own AMF and spacecraft (then
subtracting the columns) is Varon's definition and handles mixed S2A/S2B pairs correctly.

## 3. Plume masking

`plume.py` thresholds the **positive** enhancement tail of the ΔΩ field at `k·σ`, where σ is
a robust background estimate (`1.4826 · MAD`, NaN-aware). It optionally applies a 1-px
`binary_opening` (removes speckle), labels connected components with 8-connectivity, drops
components below `min_area_px`, and keeps the component(s) intersecting a 7×7 window around a
supplied source pixel — or, failing that, the component holding the peak enhancement. No plume
above threshold is a valid, empty result (not an error). The mask is vectorised to an
EPSG:4326 MultiPolygon outline (`rasterio.features.shapes`; pixel-cornered, unsmoothed).

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
- **LUT physics** — Beer–Lambert only (no multiple scattering, aerosols, or interfering gases);
  the ±30 % anchor agreement reflects this.

## 8. Reproduction results (Phase 3 exit gate)

`OPENEARTH_EE_TESTS=1 uv run python scripts/validate_events.py` reproduces two documented
super-emitter events against live Earth Engine (values verified against Varon et al. 2021):

| Event | Method | Published | Ours | Verdict |
|---|---|---|---|---|
| Korpezhe, Turkmenistan, 2018-06-19 | MBMP (ref 2018-06-24) | 11.2 ± 5.2 t/h | 9.6 ± 5.4 t/h | ✅ within ±50 %, σ overlaps |
| Hassi Messaoud blowout (Nov 2019 – Jan 2020) | MBSP, mean of 3 scenes | mean 9.3 ± 5.5 t/h | 8.3 t/h (6.0, 13.1, 5.7) | ✅ within ±50 % |

Korpezhe's reference is pinned (its auto pick is the unusable same overpass); Hassi Messaoud is
a continuous blowout, so single-scene MBSP at the well is averaged over three cloud-free scenes.
