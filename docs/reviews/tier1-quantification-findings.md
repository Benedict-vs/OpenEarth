# Tier 1 science review — quantification skill (findings)

*2026-07-11 · evidence-driven review of the methane Q pipeline against the frozen
`calibration_baseline_v4.json` (git 13e5f0f, LUT v4). Findings only — no fixes applied.
Instrumented artifacts: `$CLAUDE_JOB_DIR/tmp/tier1_out/` (per-event npz + JSON), produced by
a wrapped rerun of `analyze` over `scripts/data/calibration_events.json`, plus a noise-chip
pass. The rerun reproduced the frozen baseline bit-for-bit (seeded MC), so all numbers below
describe the shipping pipeline.*

## Verdict

The Theil–Sen slope of 0.19 is **localized, and it is not one bug**. Per-event Q currently has
**no demonstrated skill in the 5–25 t/h range**: Spearman ρ(Q_ours, Q_pub) = 0.19 (p = 0.50,
n = 15). The dominant cause is that the pipeline's **effective noise floor is the same order as
the published rates** — plume-free scene pairs at the same sites yield "detections" of
3.7–54.8 t/h (median 6.3 t/h). Four mechanisms stack, each confirmed with event-level evidence
below; none alone explains the flat slope. The aggregate claims in methods §8.2 (median ratio
0.97, slope 1.04) remain true but describe only ensemble central tendency; "essentially
unbiased" overstates what the instrument can do per event.

## F1 — Noise floor: plume-free pairs quantify multi-t/h "plumes" (CONFIRMED, primary)

For each MBMP event, `analyze` was run with target = the event's pinned plume-free reference
and reference = a fresh auto-pick that explicitly excluded the event's plume scene. Result:
**11 of 14 pairs detected a plume**, Q = 3.7–54.8 t/h, median 6.3 t/h, mean 13.9 t/h:

| noise pair (site) | Q_noise (t/h) | n_px | note |
|---|---|---|---|
| hassi-messaoud | 4.0 ± 2.3 | 14 | clean arid site |
| neuquen | 5.2 ± 2.0 | 12 | |
| turkmenistan-caspian | 3.7 ± 2.1 | 12 | |
| rub-al-khali | 4.1 ± 2.2 | 62 | |
| marib | 7.4 ± 6.4 | 28 | |
| gulf-of-suez | 10.1 ± 3.5 | 32 | |
| turkmenistan-south | 19.7 ± 6.9 | 9 | low wind on ref date? |
| libya-sirte | 22.3 ± 9.5 | 87 | likely a REAL plume on the "plume-free" ref date (recurrent site) — see F4 |
| gulf-of-thailand | 34.2 ± 15.7 | 48 | offshore water, σ_noise ≈ 1.0 mol/m² |
| kazakhstan-almaty | 29.0 ± 11.1 | 95 | heterogeneous agriculture |
| permian | 54.8 ± 19.4 | 761 | cross-UTM-tile pair (T13SGR vs T14SKA) — see F5 |
| algeria-ghardaia, amudarya, korpezhe | no_plume | — | the 3/14 correct rejections |

Caveats: recurrent sites can hold real emissions on "plume-free" dates (libya-sirte almost
certainly does), so a few of these are contamination, not noise. But the clean arid sites
(hassi, neuquen, caspian, rub-al-khali) still produce 3.7–5.2 t/h — a hard floor at the *best*
surfaces, rising to tens of t/h over heterogeneous/offshore scenes. The §7 claim of a
"detection floor roughly 1–5 t/h for favourable surfaces" is unsupported; the k·σ + min-area
masking finds a component to quantify in ~80 % of no-plume scenes.

## F2 — Mask-size variability dominates the per-event error (CONFIRMED, primary)

corr(log ratio, log n_px) = **+0.55** (Pearson; Spearman +0.45) — the strongest correlate
found (wind: ≈ 0; sat_fraction: +0.37; log Q_pub: −0.29).

- Every strong over-estimator has an outsized mask: gulf-of-suez ×2.60 (316 px),
  kazakhstan-almaty ×2.53 (120 px), rub-al-khali ×2.48 (136 px).
- Every strong under-estimator sits at the minimum surviving mask: libya-sirte ×0.12,
  korpezhe ×0.51, amudarya ×0.51 — all exactly **9 px**, the smallest component that survives
  binary opening + `min_area_px=5` (a 3×3 block).

The mask is a noisy scene-dependent random variable, and Q inherits it on both sides: IME sums
whatever the mask engulfs; L = √A only partially compensates. The MC's k-jitter (1.5–2.5)
samples *around* the current mask; it does not correct a mask that is wrong by an order of
magnitude.

## F3 — `clipped_inversion` is information-free as implemented; in-mask hi-clipping is real but secondary (CONFIRMED / PARTIALLY CONFIRMED)

`detect._clipped` fires if **any** finite pixel in the whole chip touches **either** LUT edge.
The bright edge (ΔΩ = −0.5, reached at ΔR ≈ +0.03 ≈ 1–3 noise σ) is hit by 0.3–22 % of chip
pixels in every event — hence 15/15 flags and zero diagnostic value.

The physically meaningful quantity — target-pass in-mask fraction at the ΔΩ = +3.0 edge
(columns capped by the LUT range) — is nonzero for exactly the strong-column events:
gulf-of-thailand **67 %**, campeche **56 %** (excluded), turkmenistan-south **34 %**,
turkmenistan-caspian 17 %, algeria-ghardaia 16 %. Reference passes clip 0 % everywhere.
So the memory's hypothesis (clipping biases strong events low → flat slope) is **partially
confirmed**: it demonstrably caps turkmenistan-south (ratio 0.33) and contributes to
gulf-of-thailand, but the other big under-estimators (libya-sirte, marib, korpezhe, amudarya)
clip **0 %** in-mask — theirs is F4, not F3. Clipping is a contributor, not the driver.

## F4 — Reference contamination at recurrent sites (CONFIRMED; queue item 9)

The noise-chip run *independently* found 22 t/h on libya-sirte's pinned "plume-free" reference
date — direct evidence the event's reference was contaminated, matching its ×0.12 ratio
(contaminated reference over-subtracts). The same mechanism plausibly drives marib (×0.34),
korpezhe (×0.51), amudarya (×0.51), turkmenistan-south (×0.33): persistent emitters whose
5–10-day-offset references carry residual enhancement. `pick_reference` has no contamination
check (scores only Δt/orbit/spacecraft/cloud) and silently returns a contaminated candidate.

## F5 — Background ΔΩ is not centered and cross-tile pairs are unreliable (CONFIRMED, secondary)

Off-mask ΔΩ background stats (should be ≈ 0 mol/m²): mean ranges **−0.23 to +0.53**, typically
positively skewed (+1.0…+1.8), p99 up to the grid edge. Mechanisms: (i) the inverse LUT map is
convex, so symmetric ΔR noise acquires a positive ΔΩ mean (Jensen); (ii) the short bright-side
branch truncates at −0.5; (iii) surface artifacts. Interacts with a small threshold defect:
`detect_plume` thresholds at `field ≥ k·σ` measured **from zero**, while `robust_sigma` is
MAD-about-the-median — a non-zero background median silently shifts the effective k.
Separately, both permian runs (event ×1.93; noise chip 54.8 t/h, 761 px) used cross-UTM-tile
MBMP pairs — different-tile references add registration/BRDF structure and should be flagged
or refused.

## F6 — U_eff/L coefficients are calibrated to a different mask procedure (VERIFIED, unquantified)

`U_eff = 0.33·U10 + 0.45` and `L = √A` match Varon et al. 2021 §4.1 verbatim
(https://amt.copernicus.org/articles/14/2771/2021/). But Varon's α, β are LES-calibrated
**to their masking procedure** (scene-95th-percentile threshold + 3×3 median filter); ours is
k·σ = 2 robust threshold + opening + single-component selection, which produces structurally
different mask areas (F2). The borrowed coefficients do not automatically transfer; the size of
this systematic is unknown without recalibration.

## F7 — Cleared items

- **ERA5 timing**: `sample_wind_at` brackets the true `system:time_start` with the two hourly
  grids and interpolates; u10/v10 are instantaneous variables. Correct.
- **Wind is not the limiting error** in our regime: corr(log ratio, u10) ≈ 0. Varon's
  "wind dominates" conclusion applies to homogeneous scenes with trustworthy masks; our mask
  noise currently swamps it.
- **Regression discipline**: the instrumented rerun reproduced every frozen Q to 4 decimals.

## Consequences for methods §7/§8 language

- §8.2 "The central calibration is essentially unbiased" → true only in aggregate; add: the
  per-event Spearman ρ is 0.19 (p = 0.5) — individual rates are order-of-magnitude estimates,
  and ranking two sources by our Q is currently unsupported.
- §7 "Detection floor — roughly 1–5 t/h for favourable surfaces" → replace with the measured
  noise-chip floor: ~4–5 t/h at the best arid sites, tens of t/h over heterogeneous or offshore
  surfaces; ~80 % of plume-free pairs yield a quantifiable false component under the default
  mask settings.
- §7 "remaining ~25 % anchor offset … attributable to the named spectral omissions" → keep
  flagged as hypothesis (two predecessors already refuted; the anchor offset is anyway small
  against the mask-driven per-event scatter measured here).

## Candidate fixes (decision list — none applied)

1. **Per-site empirical noise floor** (attacks F1/F2 head-on): run the identical detection on
   N plume-free date pairs per site; report Q against that floor (gate, or fold into σ). Cheap,
   honest, uses only existing machinery — the noise-chip instrument is exactly this.
2. **Reference-quality defense** (F4, queue item 9): contamination diagnostic in
   `pick_reference` / Lab hint ("recurrent site → MBSP or median-composite reference");
   multi-scene median-composite reference à la Varon.
3. **Redesign `clipped_inversion`** (F3): replace with target-pass in-mask per-edge fractions
   (the harness's `sat_fraction` generalized), surfaced in the API detail + Lab.
4. **Mask robustness** (F2): threshold from the field median; consider Varon's p95+median-filter
   mask (which would also re-legitimize the borrowed U_eff coefficients, F6); flag or refuse
   cross-tile references (F5).
5. **Extend the LUT ΔΩ grid** beyond 3.0 mol/m² (F3) so strong columns aren't range-capped
   (turkmenistan-south, gulf-of-thailand, campeche).
