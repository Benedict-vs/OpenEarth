# Tier 2 science review — ML protocol (findings)

*2026-07-11 · evidence-driven review of the Phase 5 ML tier against the frozen
`scripts/data/ml_eval_v1.json` (committed in e6e4ffd), the local chip manifest + recovery
metadata under git-ignored `data_dir/ml/` (license wall respected — nothing derived is committed),
and an offline label-Q instrument (per-tile rows are a CH4Net-derived license-walled artifact,
git-ignored at `data_dir/ml/review/ml_label_q_estimate.json`; committed aggregates only in
`docs/reviews/data/ml_label_q_aggregate.json`).
No retraining. Findings only — no fixes applied. Full offline suite: 532 passed.*

## Verdict

The eval's *conclusions are laundered by its own protocol before serving can launder them*: the
headline **F1 0.60 vs baseline 0.46 is measured on folds that are not actually site-disjoint on
the ground** (30 % of chips overlap a different-fold site's footprint), with the **checkpoint
selected on the same held-out fold that produces the reported metrics**, at an **untuned operating
point**, against labels of which **61 % sit below the Tier 1 noise floor** by our own Q estimator.
The serving layer itself is honest (candidate-ranker framing everywhere) with two exceptions: the
`disagreement` flag is **wrong in both directions** (a physics *no-plume* row displays as "Physics
agrees"), and the uncertainty-free single-pass Q reads *more* precise than the physics Q ± σ next
to it. None of this says the model is useless as a ranker — it says the frozen eval cannot
distinguish "better ranker" from "leaked surface texture", and the current numbers should not be
cited even with the existing caveats.

## F1 — Threshold provenance: not tuned, not leaked — but checkpoint selection *is* eval-fold
selection (CONFIRMED)

- The operating threshold is the constant `MODEL_THRESHOLD = 0.5` (`eval.py:32`), copied into the
  manifest by `export.py` (`THRESHOLD = 0.5`) and read back verbatim at serve time. It was never
  tuned on anything, so there is no threshold leak — but also no operating-point selection at all.
- The comparison is **single-operating-point**: model at prob ≥ 0.5 vs baseline `detect_plume` at
  the pipeline-default k·σ = 2. No PR curve / threshold sweep exists for either side, so the F1 gap
  partly reflects two arbitrary operating points (model recall 0.891 vs baseline 0.733 at
  precision 0.459 vs 0.341 — a k-sweep of the baseline could close an unknown share of the gap).
- The real leak is **early stopping**: `train_one` checkpoints on best Dice over the *held-out
  fold itself* (`cv` passes the eval fold as `val_ds`; patience 12) and `evaluate` then scores
  that same fold. There is no inner validation split — each fold's model is the one that looked
  best on its own test set. Classic optimistic bias; magnitude unquantifiable without a rerun.
  `best_val_dice` = 0.09–0.16 across folds also shows how weak the pixel-level signal is — the
  scene F1 is carried entirely by the "any component ≥ 5 px" rule.
- Minor: the `deployed` refit comments "no held-out val: train a fixed budget, checkpoint the last
  state" but `train_one` still early-stops and restores "best" state on a 16-chip *in-sample*
  probe — cosmetic, but the comment and behavior disagree.

## F2 — GroupKFold-by-site is not spatially disjoint: cross-fold footprint overlap (CONFIRMED, primary)

The grouping key (recovered CH4Net `site_id`, T1–T23) partitions correctly — every site's chips
land in exactly one fold (verified by construction, `test_site_folds_never_leak_a_site`, and the
frozen `fold_of_site`). But the premise that sites are distinct surfaces is false at chip scale
(~1.6 × 2.3 km bboxes): several CH4Net "sites" are neighbouring pads in the same field. Measured
from the recovery bboxes of the 1395 exported chips:

| cross-fold site pair | centroid distance | max chip-bbox overlap (frac of smaller) | chips involved |
|---|---|---|---|
| T6 (f0) × T7 (f2) | 0.42 km | **0.71** | all 24 vs all 65 |
| T2 (f4) × T3 (f3) | 1.44 km | 0.67 | all 24 vs 6/25 |
| T5 (f3) × T7 (f2) | 0.94 km | 0.41 | all 60 vs all 65 |
| T15 (f2) × T16 (f1) | 1.31 km | 0.32 | all 19 vs all 32 |
| T16 (f1) × T17 (f3) | 1.54 km | 0.29 | all 32 vs all 119 |
| T5 (f3) × T6 (f0) | 1.19 km | 0.26 | all 60 vs all 24 |
| T20 (f0) × T21 (f1) | 2.43 km | 0.12 | 43/171 vs all 28 |

**420 of 1395 chips (30 %, incl. 97 positives) share > 10 % ground footprint with a chip of a
site in a different fold** — the same pixels appear in train and validation. Additionally
**273 of 617 distinct target scenes are shared across folds** (same acquisition, same atmosphere,
different site crop). "Site-held-out CV controls intra-region leakage" (§9.4) is therefore not
true as run: the folds leak both surface texture and literal ground. An honest grouping needs
spatial clustering before folding (e.g. merge sites within ~5 km: {T5,T6,T7}, {T2,T3},
{T15,T16,T17}, {T20,T21,T22}, {T13,T14}).

## F3 — Labels vs the Tier 1 noise floor: the ceiling is lower than the caveat admits (CONFIRMED)

Docs §9.1/§9.4 already state the qualitative ceiling (labels are MBMP-guided, so beating an MBMP
baseline ≠ seeing what MBMP can't). The quantitative situation is worse. For each of the 395
usable positives, ΔΩ was inverted offline from the chip's own MBMP ΔR (reporting LUT,
solar-geometry AMF, Sentinel-2A assumed) and Q estimated over the CH4Net label footprint at the
nominal wind u10 = 3.98 m/s (median of the Tier 1 events); order-of-magnitude only, but the
distributional statement is robust to the wind assumption:

- Label footprints: 14–563 px at 20 m (median 139) — no empty regridded masks.
- **Median label Q_est ≈ 4.8 t/h. 61 % of training labels sit below the Tier 1 median noise floor
  (6.3 t/h); 45 % below the best-arid floor (4.0 t/h); 85 % below the mean floor (13.9 t/h).**
- **65/395 (16 %) label masks integrate to *negative* ΔΩ** in our rebuilt chips — the labeled
  "plume" has net-negative column enhancement. Under any wind these stay negative. Causes:
  recovered-date errors surviving the confidence gate (§9.2's asymmetric policy), reference
  contamination (Tier 1 F4 — same Turkmenistan recurrent-emitter geography), or annotation noise.

What F1 0.60 vs 0.46 *can* claim: on Turkmenistan O&G chips, the U-Net reproduces MBMP-guided
human annotations better than a fixed-k robust threshold on the same ΔR field. What it *cannot*
claim: detection skill at the labeled rates — the majority of those rates are at or below the
level where Tier 1 showed the physics instrument "detects" plume-free scenes, so scene-level
agreement in this regime is substantially agreement about correlated noise/texture, on folds that
F2 shows are not independent.

## F4 — Serving-claim laundering: mostly clean (CLEARED, two nits)

- `GET /methane/ml/status` returns only `model_loaded`/`model_version`/`latency_ms_p50` — no eval
  numbers cross the API. The Settings page and the Lab's scan panel both carry "candidate ranker …
  proposes scenes for review, never an autonomous detector"; every ML detection's `result_json`
  and detail view carry "ML candidate — requires review; not an autonomous detection." Docs §9.4
  states the label-noise, geography, and deployed-vs-CV caveats. No F1 appears in the UI.
- Nit 1: §9.1 promises the geography caveat "stated wherever the scan UI … could imply
  generality" — the scan UI says "candidate ranker" but never "trained on Turkmenistan O&G only".
- Nit 2: the model manifest records `cv_scene_f1: 0.597` as bare provenance; given F1–F3 that
  number should carry its protocol qualifiers wherever it resurfaces (it is the "performance
  estimate" §9.4 assigns to the *never-evaluated* deployed refit).

## F5 — `disagreement` flag is wrong in both directions; uncertainty-free ML Q is not
"magnitude-comparable" (CONFIRMED, bug + design)

- **Bug**: `_disagreement` (`services/ml.py`) returns `"agree"` iff *any* physics row exists for
  the same site + scene. But `persist_detection` writes a physics row **unconditionally** — a run
  that found nothing still writes a row (flags `["no_plume"]`, q null, status "candidate"). So a
  physics run that explicitly found *no plume* on the scene makes the ML hit display **"Physics
  agrees"** — the opposite of the truth. Conversely `"ml_only"` (UI: "ML-only (no physics
  detection)") actually means "physics never ran", not "physics disagrees". The flag currently
  encodes *row existence*, not agreement, and never compares footprints or magnitudes.
- **Design**: the feed renders ML rows via the same `formatEmission` — physics shows "5.2 ± 2.0
  t/h", ML shows "4.8 t/h" (single-pass `emission_over_mask`, `q_sigma` deliberately null). With
  Tier 1's result — per-event physics Q has no demonstrated skill and the noise floor sits at
  signal level *with* the MC budget — a point estimate over an ML probability footprint (pixel
  IoU on true positives 0.24–0.43) carries strictly less information, yet displays as a cleaner
  number. "Magnitude-comparable in the feed" (§9.5) is not defensible: comparable formatting is
  being read as comparable evidence. At minimum the ML Q needs the same noise-floor context Tier 1
  fix #1 proposes for physics Q, or explicit order-of-magnitude marking; ranking sources by it is
  unsupported either way.

## F6 — Channel parity: holds at the function seam; two undeclared train/serve deviations (PARTIALLY CONFIRMED)

- The byte-identity claim is real where it is claimed: `build_channels` + `normalize` are the
  single core implementations imported by the exporter (`scripts/export_ch4net_chips.py`), the
  eval (`openearth_ml.eval.model_prob` runs the serve path `pad_to_multiple → forward → unpad`),
  and the serve scan (`services/ml.py`). `ChannelStats` order is validated on construction; the
  manifest stats are applied verbatim; fold stats come from train folds only, deployed stats from
  the full set. torch↔ORT parity is tested at a non-training shape (96², tol 1e-4) with dynamic
  H/W. `fetch_chip` defaults (20 m) match the exporter's `SCALE_M`.
- Deviation 1 (geometry): training tensors are built by `_fit_to` — **zero**-pad (bottom/right) /
  center-crop to 128² — while serving **reflect**-pads to a /32 multiple. The eval used the serve
  path, so the frozen numbers include this mismatch's effect on fold models; but the deployed
  model was *trained* under zero-pad statistics and is *served* under reflect-pad, and §9.3
  documents only the serve behavior.
- Deviation 2 (reference pool): training references were picked from a ±150-day window at
  max_cloud 60 around each tile date; the serve scan picks references from **the user's scan
  window only** (`candidates = scenes` of the request range). Short scan windows produce
  short-Δt/fewer-candidate references — a train/serve distribution shift outside the
  "byte-identical channels" guarantee.
- Edge cases cleared: `np.pad(mode="reflect")` handles pads larger than the axis (verified: H=10
  → 32 without error), so sub-32-px serve chips don't crash; `normalize` NaN/Inf→0 and flat-MAD
  guards are shared; `candidates_from_prob` reuses the physics 8-connectivity and min_px.

## F7 — Eval provenance nit: the recorded git hash predates the eval code (CONFIRMED, minor)

`ml_eval_v1.json` records `git_hash 534b72d9` — the *parent* commit ("package skeleton +
exporter"); `train.py`/`eval.py` themselves were uncommitted when `cv` ran and landed only in
e6e4ffd. The hash therefore does not pin the code that produced the frozen numbers (the
`data_manifest_sha256` does pin the chips). No drift since e6e4ffd (only `export.py` + parity
test added), so the frozen eval does describe today's code — but the provenance field is
misleading and would not catch a dirty-tree rerun.

## Candidate fixes (decision list — extends the Tier 1 list, none applied)

6. **Re-run CV honestly** (F1+F2, one command, no new data): spatial-cluster grouping (merge
   sites < ~5 km before folding) + an inner train/val split for early stopping (never the eval
   fold) + a threshold/k sweep reported for model *and* baseline (PR curves, not one point).
   Freeze as `ml_eval_v2.json`; retire v1 numbers from docs §9.4.
7. **Label-quality gate** (F3): exclude or down-weight positives whose rebuilt chips integrate
   ΔΩ ≤ 0 (16 %); report the below-noise-floor share of labels in §9.4 and reframe the F1 claim
   as "annotation agreement", explicitly not detection skill at the labeled rates.
8. **Fix `disagreement` semantics** (F5): `agree` only when a physics detection with a non-empty
   plume exists; add a distinct `physics_no_plume` state; relabel `ml_only` as "physics not run";
   optionally compare footprint overlap rather than row existence.
9. **Stop displaying single-pass Q as a plain rate** (F5, ties into Tier 1 fix #1): suppress or
   order-of-magnitude-mark ML Q in the feed until the per-site empirical noise floor exists; then
   gate both tiers' Q against that floor.
10. **Provenance + framing** (F4/F7): record the true tree state (hash + dirty flag) in eval
    JSONs; add the Turkmenistan-only note to the scan UI caption; qualify `cv_scene_f1` in the
    manifest with its protocol caveats.
11. **Declare or align the train/serve deviations** (F6): document zero-pad-train vs
    reflect-pad-serve and the reference-pool difference in §9.3; if retraining under fix 6 anyway,
    train with reflect-pad at 128² and consider serve-matching reference windows (±150 d around
    the target) instead of the scan range.
