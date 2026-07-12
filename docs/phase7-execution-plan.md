<!-- docs/phase7-execution-plan.md — Phase 7 (post-review science fixes) execution plan.
     Written 2026-07-11, after the three-tier science review (docs/reviews/tier{1,2,3}-*.md).
     Consolidates candidate fixes 1–15 from the findings docs into decided stages.
     Externally checkable facts re-verified 2026-07-11 at planning time:
     - Varon et al. 2021 (AMT 14, 2771, §4.1), verbatim: "we define the plume mask by
       selecting methane columns above the 95th percentile for the scene and smooth with a
       3 × 3 median filter"; U_eff = 0.33·U10 + 0.45 m/s, LES-calibrated (five 3 h runs,
       100–300 W/m², mixed-layer 500–2000 m) explicitly "mimicking … the plume masking
       procedure"; L = √(mask area). So α, β are tied to the p95+median-filter mask — the
       basis for the fix-4 decision below.
     - IMEO MARS(-S2L) plume data fields (verified via the UNEP-IMEO/MARS-S2L HF dataset
       card; the portal data dictionary at methanedata.unep.org/dict-mars-emission-sources-
       plumes is Cloudflare-blocked to scripted fetches): `ch4_fluxrate` and
       `ch4_fluxrate_std` in **kg/h**, `lat`/`lon`, `tile_date` (ISO timestamp). SRON's
       weekly list uses the t/h convention (their site); exact SRON CSV headers were NOT
       verifiable — fix 13's design therefore never guesses units for unit-agnostic keys.
       Opus: eyeball the actual header row of whatever file is imported when testing.
     - LUT grid arithmetic: v4 DELTA_OMEGA_GRID = linspace(-0.5, 3.0, 351) has step exactly
       0.01 (3.5/350); linspace(-0.5, 6.0, 651) has step exactly 0.01 (6.5/650), so the
       first 351 grid points coincide bit-for-bit — the v5-vs-v4 shared-subgrid identity
       check below is well-posed.
     - License finding: docs/reviews/data/ml_label_q_estimate.json holds PER-TILE rows keyed
       by CH4Net tile index (site, label n_px, per-label Q). That is a CH4Net derivative
       ("no per-file manifests" — methods §9.1); it must NOT be committed. Stage 0 replaces
       it with an aggregate-only artifact. -->

# Phase 7 — post-review science fixes: execution plan

**Goal:** land the consolidated fixes from the three-tier science review — kill the two live
wrong-answer bugs, make per-event Q honest against a measured noise floor, redesign the
information-free `clipped_inversion` flag, extend the LUT ΔΩ range, rerun the ML evaluation
under a protocol that can actually support its claims, and rewrite the methods language the
findings invalidated. *Exit: one new frozen calibration baseline (v5), one frozen per-site
noise floor, one protocol-valid `ml_eval_v2.json`, and no UI surface that displays a number
the review showed to be wrong or unearned.*

**Branch:** `v2/phase7-science-fixes`, cut from **main**. One commit per stage, prefixed
`core:` / `api:` / `web:` / `ml:` / `docs:`. After every stage: `make check`; after any API
schema change: `make gen` in the same commit.

**Standing rules (Phase 3/3.5/5/6 sets still apply):**

- **License wall**: nothing CH4Net-derived is ever committed — chips, masks, weights, ONNX,
  manifests, or *per-tile statistics* (see the Stage 0 header note). Eval/provenance JSONs
  are aggregate-only.
- **ALGO_VERSION discipline**: exactly **one** bump this phase (5 → 6, Stage 2). Stages 1,
  3, 4 change no cached-result semantics (Stage 1 changes cache *keys*, which self-invalidate).
- **Append-only migrations**: this phase adds **no DB migration** — every new per-detection
  quantity lives in `result_json`, and the noise floor is a packaged JSON, deliberately.
- **torch never outside `packages/ml`**; onnxruntime-only serving; offline tests make zero
  EE calls; live instruments (`calibration_harness`, `validate_events`, `noise_floor`,
  `@pytest.mark.ee` contract tests) run manually with real auth only.
- **Never tighten toward the Varon anchor**: harness/validate bands stay sanity checks; no
  constant in this phase is fitted to published rates.
- Findings docs under `docs/reviews/` are the immutable evidence record — after Stage 0 the
  only permitted edits are pointer lines (e.g. where an evidence file moved), never findings.

---

## Triage — every fix, decided

| # | Fix (tier) | Verdict | Stage | Rationale |
|---|---|---|---|---|
| 1 | Per-site empirical noise floor (T1) | **Implement** | 3 | Keystone: the cheapest honest answer to F1/F2; fixes 9 and the §7/§8 rewrites depend on it |
| 2 | Reference-quality defense (T1) | **Split**: contamination flag now; median-composite reference **deferred** | 2 / design pass | The flag is cheap (reference chip is already fetched); composite references change the retrieval contract → design pass |
| 3 | `clipped_inversion` redesign (T1) | **Implement** | 2 | Confirmed information-free; replacement (per-pass in-mask edge fractions) already prototyped in the harness |
| 4 | Mask robustness (T1) | **Implement** median-centered threshold + cross-tile flag + mask-stability diagnostic; **do not adopt** the Varon p95 mask; **do not recalibrate** α, β | 2 | See the decision box below — the load-bearing call of this plan |
| 5 | Extend LUT ΔΩ grid (T1) | **Implement**: hi end 3.0 → 6.0 mol/m², lo end stays −0.5 | 2 | Hi-clipping demonstrably caps strong events; lo end −0.5 is already near the physical floor (Ω_bg = 0.65 — extending it would tabulate negative total columns) |
| 6 | Honest CV rerun (T2) | **Implement** — absorbs 7 and 11 | 4 | One retrain → `ml_eval_v2.json`; protocol fixes are worthless piecemeal |
| 7 | Label-quality gate (T2) | **Absorbed into 6** | 4 | Must be inside the single retrain |
| 8 | `disagreement` semantics (T2) | **Implement** | 1 | Live wrong-answer display; read-time derivation fixes old rows too |
| 9 | ML Q display honesty (T2) | **Implement** in two parts | 1 (formatting) + 3 (floor context) | Point-estimate marking needs no floor; floor context arrives with fix 1 |
| 10 | Provenance + framing (T2) | **Implement** | 1 (scan-UI caption) + 4 (eval/manifest provenance) | The provenance code lives in `train.py`, so it ships with the retrain |
| 11 | Train/serve deviations (T2) | **Absorbed into 6** (train reflect-pad) + serve ref-pool change | 4 | Retraining anyway; aligning instead of documenting |
| 12 | Thumbnail cache key (T3) | **Implement** | 1 | Live wrong-answer bug (silent image collision) |
| 13 | Unit-safe validation import (T3) | **Implement** | 1 | Live ×1000 corruption for IMEO-style imports |
| 14 | Pin the EE contract (T3) | **Implement** | 1 | Two probes already written during review; freezing them is cheap |
| 15 | Document the sampling model (T3) | **Implement (docs-only)** | 5 | One methods §1 paragraph |

**Decision box — fix 4, the Varon-mask question.** The review posed: adopt Varon's
p95+median-filter mask (re-legitimizing the borrowed U_eff coefficients, F6) or recalibrate
our own. **Neither, this phase.** (i) A scene-p95 threshold always selects the top 5 % of
whatever window it sees — it is a *quantification* mask that presupposes a detection, so the
k·σ detection step must survive anyway, and the noise-floor problem (F1) is untouched by it.
(ii) Transferring it to our variable-size analysis areas introduces a free parameter (the
quantification window size) that would dominate the result: 5 % of a 10 km Lab area is ~12 500
px against event masks of 9–761 px, so any chosen window is doing more work than the
procedure it hosts. (iii) The n = 15 harness is an engineering diagnostic and cannot validate
a mask redesign (its own header says so). (iv) Recalibrating α, β without LES is only possible
by fitting to published rates — forbidden by the anchor rule. What ships instead: the
threshold-centering defect fix, a mask-*stability* diagnostic (the k-grid sweep the MC already
computes, surfaced), the cross-tile flag, and §7 language declaring the borrowed-coefficient
systematic (F6) as unquantified. The p95-mask study stays on the backlog as a possible future
dedicated calibration phase — it needs its own instrument, not a rider on this one.

**Frozen-artifact accounting (the review's "regenerate once" requirement):**

| Artifact | Regenerated | When |
|---|---|---|
| `ch4_lut_v5.npz` (reporting LUT) | once | Stage 2 |
| `ch4_lut_mask.npz` (mask LUT) | **never** — stays frozen; footprint invariance test must keep passing | — |
| `calibration_baseline_v5.json` | once (`--freeze`), after *all* Q-changing code (fixes 3+4+5) | Stage 2 exit |
| `ALGO_VERSION` 5 → 6 | once | Stage 2 |
| `noise_floor_v1.json` | once, after Stage 2 (the floor must describe the *new* pipeline) | Stage 3 |
| `ml_eval_v2.json` + deployed ONNX + manifest | once (fixes 6+7+10+11 in one retrain) | Stage 4 |
| `calibration_events.json` | **unchanged** — libya-sirte/permian re-curation deferred (see Stage 2 pitfalls) | — |

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 0 | Commit the review record (license-safe) | docs | S | — |
| 1 | Quick-wins batch: fixes 12, 13, 14, 8, 9a, 10a | api + core + web | M | — |
| 2 | Physics honesty batch: fixes 3, 4, 5, 2-flag → LUT v5 + baseline v5 | core + api + web | L | — |
| 3 | Noise floor: fix 1 + fix 9b — instrument, frozen JSON, surfacing | core + api + web | M | 2 |
| 4 | ML protocol batch: fixes 6 (+7, +11), 10b → `ml_eval_v2.json` | ml + api | L | — (docs interlock with 3) |
| 5 | Methods + docs rewrite: fix 15, §7/§8.2/§9 language | docs | M | 2, 3, 4 |

0 and 1 are independent and can land immediately; 2 → 3 strictly sequential; 4 is
code-independent of 2/3 (different packages, no shared artifacts) and may run in parallel,
but its §9 language lands in Stage 5 with everything else; 5 last, once the real numbers
(v5 baseline, floor, v2 eval) exist.

---

## Pinned contracts

### Stage 0 — commit the review record

- Commit `docs/reviews/tier{1,2,3}-*.md`, `docs/reviews/data/instrumented.json`,
  `docs/reviews/data/noise_chip.json` as-is.
- **Do not commit `docs/reviews/data/ml_label_q_estimate.json`** (per-CH4Net-tile rows =
  license-walled derivative). Replace it with `ml_label_q_aggregate.json`: the
  distributional facts the Tier 2 doc actually cites — n rows, per-site *counts only*,
  Q percentiles (p5/p25/p50/p75/p95), the below-floor shares (61 % / 45 % / 85 %), the
  negative-ΔΩ count (65/395), the nominal u10, and the method note. Move the per-row
  original under `data_dir/ml/review/` (git-ignored). Update the one pointer line in
  `tier2-ml-protocol-findings.md` (permitted edit: pointers only).

Commit 0: `docs: three-tier science review findings + license-safe evidence`.

### Stage 1 — quick-wins batch (fixes 12, 13, 14, 8, 9a, 10a)

**Fix 12 — thumbnail cache key** (`services/thumbnails.py`):

- Add `ref=[req.ref.start, req.ref.end] if req.ref else None` and likewise `methane_ref`
  to `render_thumbnail`'s `cache_key(...)`. Leave `auto_range` out **with a comment**
  ("unused by the thumbnail path — viz comes from viz_overrides; add to the key if that
  ever changes").
- **Schema-diff test** so the next added field can't repeat the bug: keep an explicit
  module constant `_KEYED_FIELDS` + `_DECLARED_IRRELEVANT` (with per-field justification
  comments: `dataset`… keyed; `auto_range` irrelevant-why) and a test asserting
  `set(TilesRequest.model_fields) | set(ThumbnailRequest-only fields) == _KEYED_FIELDS ∪
  _DECLARED_IRRELEVANT`. A new request field fails the test until classified.
- Existing cached thumbnails self-invalidate (key composition changed) — harmless misses,
  no ALGO_VERSION bump (that is for same-key/different-meaning, the opposite problem).

**Fix 13 — unit-safe reference import** (`core methane/validation.py` + API route):

- Split the rate aliases by unit provenance:
  - t/h keys (`source_rate_t_h`, `q_t_h`) → ×1000;
  - kg/h keys (**add** `ch4_fluxrate`; sigma alias `ch4_fluxrate_std` — the verified IMEO
    MARS names; also accept `*_kg_h`/`*_kgh` suffixes) → ×1;
  - unit-agnostic keys (`rate`, `q`, `emission_rate`; sigma: `sigma`, `uncertainty`,
    `q_sigma`, `error`) → **never guessed**.
- `parse_events(..., unit: Literal["t_h", "kg_h", "auto"] = "auto")`. In `auto`,
  unit-suffixed keys self-describe; a row whose only rate key is unit-agnostic imports with
  `q_kg_h=None` and is counted (the event still cross-matches on space/time — verdicts never
  needed the rate). An explicit `unit=` applies to unit-agnostic keys only (suffixed keys
  always win; a conflict between an explicit param and a suffixed key = suffix wins, count it).
- Sanity guard: any parsed rate > **500 t/h equivalent** → rate dropped to None + counted
  (declared bound with a comment; S2-scale published point-source rates top out far below).
- Provenance: `ReferenceEvent` gains `rate_unit: str | None` (`"t_h" | "kg_h" | None`),
  stored into `raw`/the DB row's raw JSON so corruption is diagnosable downstream.
- API: the import route gains an optional `unit` form field (default `auto`);
  `ValidationImportOut` gains `rates_dropped` (beside the existing `imported`/`skipped`).
  `make gen`.
- Offline tests: an IMEO-style CSV fixture (`ch4_fluxrate` in kg/h) round-trips **without**
  the ×1000; SRON-style `source_rate_t_h` still ×1000; unit-agnostic `rate` with
  `unit="auto"` → None + counted, with `unit="t_h"` → ×1000; the 500 t/h guard.

**Fix 14 — pin the EE contract** (`packages/core/tests/test_ee_contract.py`, all
`@pytest.mark.ee`):

- Probe A (grid convention): `ee.Image.pixelLonLat()` through `fetch_window` over a
  `grid_for` grid equals our pixel centers (`x0 + (col+0.5)·xscale`, `y0 − (row+0.5)·yscale`)
  within 1e-5 deg.
- Probe B (resampling): S2 `B11` fetched at 10 m shows ≥ 40 % nearest-neighbor duplication
  (adjacent-pixel equality rate) while at 20 m it stays well below — the NN-default evidence
  from Tier 3 P2, frozen so an upstream EE change surfaces in the live suite.

**Fix 8 — `disagreement` semantics** (`services/ml.py` + feed/detail assembly + web):

- New states: `agree` (a physics detection for the same site + scene exists **with a
  non-empty plume** — no `no_plume` flag, `n_pixels > 0`), `physics_no_plume` (physics ran,
  found nothing — the current false-"agrees" case), `physics_not_run` (no physics row).
- **Derived at read time** in the feed/detail services (one grouped query over the page's
  site+scene pairs), so every existing ML row displays correctly without a data migration.
  The value written into `result_json` at scan time stays as an at-scan-time historical
  record; display always uses the live derivation. Expose the derived value as a typed field
  on feed/detail schemas → `make gen`.
- Web: relabel — "Physics agrees (plume found)" / "Physics found no plume" /
  "Physics not run" (the old "ML-only (no physics detection)" text dies). Footprint-overlap
  comparison is **not** implemented (row-level truth first; geometric agreement is a later
  refinement — note it in the code).
- Tests: three-state derivation against a seeded DB (physics row with plume / with
  `no_plume` / absent).

**Fix 9a — ML Q formatting** (`apps/web/src/lib/methane.ts`):

- ML rows (`source === "ml"`) format as `~4.8 t/h` (tilde prefix) with a tooltip/caption
  "single-pass point estimate over the ML footprint — no uncertainty budget"; physics
  formatting unchanged. Vitest cases updated. (Floor context lands in Stage 3.)

**Fix 10a — scan-UI geography caption** (web): the ML scan panel caption gains "trained on
Turkmenistan O&G scenes only — expect degraded performance elsewhere" (the §9.1 promise the
review found unmet).

Commits: `api+core: thumbnail cache key + unit-safe validation import + EE contract tests`
and `api+web: disagreement semantics (read-derived) + ML Q point-estimate marking`.

### Stage 2 — physics honesty batch (fixes 3, 4, 5, 2-flag)

All Q-changing code lands here, then **one** live verification pass.

**Fix 5 — LUT v5** (`scripts/generate_ch4_lut.py`, `core methane/conversion.py`):

- `DELTA_OMEGA_GRID = np.linspace(-0.5, 6.0, 651)` (step stays exactly 0.01), version `"5"`,
  output `ch4_lut_v5.npz`; `_LUT_FILENAME` → v5. **`ch4_lut_v4.npz` stays committed** (it
  anchors the identity test and the baseline history); `ch4_lut_mask.npz` untouched.
- Generator addition: assert the forward curve `m(ΔΩ)` stays strictly monotonic over the
  extended range for both spacecraft at all AMFs and print `dm/dΔΩ` at the top edge (the
  Beer–Lambert curve flattens with saturation; if the slope within the new range decays to
  the point of ill-conditioned inversion, stop and reconsider the 6.0 choice — record the
  numbers in the commit message either way).
- **Shared-subgrid identity test** (offline): v5's first 351 ΔΩ points and their `m` values
  match v4 (`np.allclose`, tight rtol). If they don't, the *inputs* drifted (HAPI line-list
  fetch) — investigate before freezing, never ship a v5 whose sub-range disagrees with v4.

**Fix 3 — clip diagnostics replace `clipped_inversion`** (`core methane/detect.py` /
`conversion.py`, harness, API, web):

- New pure core fn (generalizing the harness's `_lut_saturated_fraction`):
  per-pass, in-mask edge fractions — `target_hi`, `target_lo`, `ref_hi`, `ref_lo` (fraction
  of masked pixels whose per-pass ΔΩ sits on the reporting-LUT grid ends). `DetectionResult`
  gains `clip_fractions: dict[str, float]`.
- The whole-chip any-pixel `_clipped` check and the `clipped_inversion` flag are **removed**.
  Replacement flag: `lut_hi_clipped_mask` when `target_hi > 0.05` (declared constant,
  commented). API: fractions into `result_json`; Lab detail renders them (a small "inversion
  range" row). No typed-schema change → no `make gen` from this item.
- **Harness validity guard keeps its teeth**: with the grid extended, "fraction at the grid
  edge" no longer catches MBSP surface blowups (they now invert to large *finite* columns).
  The harness's exclusion criterion switches from grid-edge fractions to a **declared
  validity bound**: fraction of masked pixels with `|ΔΩ| ≥ 3.0 mol/m²`
  (`MBSP_VALIDITY_DELTA_OMEGA = 3.0`, i.e. exactly the old edge, now decoupled from the
  grid) still `> 0.20` → `excluded_lut_saturated` (rename to `excluded_inversion_validity`).
  This deliberately keeps campeche/caspian-MBSP-style exclusions stable across the grid
  extension.

**Fix 4 — mask robustness** (`core methane/plume.py`, `detect.py`, `ime.py`):

- **(a) Median-centered threshold**: `detect_plume` thresholds
  `field ≥ median(finite) + k·σ` (σ already MAD-about-the-median — the threshold and the σ
  finally agree on a center). Offline tests: a synthetic field with a +0.3 background offset
  detects the same component as the zero-offset field; the old behavior demonstrably didn't.
- **(b) Cross-tile flag**: parse the MGRS tile from target/reference `scene_id`s
  (`_T\d{2}[A-Z]{3}` suffix); differ → flag `cross_tile_reference` (precedent:
  `different_orbit_reference`). Lab shows a warning chip ("reference from a different UTM
  tile — registration/BRDF structure inflates noise; prefer a same-tile reference").
  Flag, don't refuse — the harness records it (permian stays quantified, annotated).
- **(c) Mask-stability diagnostic**: `quantify` already sweeps `npix_k` over
  `mc.k_grid` — surface it: `EmissionEstimate` gains `mask_npx_by_k: dict[str, int]`; flag
  `unstable_mask` when the k-sweep's max/min pixel ratio ≥ 4 or any k in the grid yields an
  empty mask while the display mask is non-empty (declared constants, commented — this is
  F2's order-of-magnitude mask noise made visible, not fixed). Into `result_json` + a Lab
  chip.
- **Not** in scope (decision box above): the Varon p95 quantification mask; any α, β change
  (`UEFF_ALPHA`/`UEFF_BETA_MS` constants untouched; §7 gets the F6 systematic instead).

**Fix 2-flag — reference contamination diagnostic** (`core methane/detect.py`):

- After the reference pass, run `detect_plume` on the reference's **own** mask-LUT ΔΩ field
  (`mask_d_omega_r`, already computed) with the same k/min-area/source window. A surviving
  component → flag `possible_reference_contamination`. Zero extra EE round-trips.
- Lab hint when flagged: "the reference scene itself shows an enhancement near the source —
  a recurrent emitter may have no plume-free reference; consider MBSP or pin a different
  date." The *fix* for contamination (median-composite references) is design-pass scope —
  this stage only stops the failure from being silent.
- Offline test: synthetic reference field with an injected blob → flag; clean → no flag.

**Cache/versioning**: fixes 4a (masks move) and 5 (columns move) change results reproducible
from cached ops → **`ALGO_VERSION` 5 → 6** (`cache.py`), the phase's single bump, in this
commit.

**Spearman in the harness** (`scripts/calibration_harness.py`): add `spearman_rho` and
`spearman_p` (scipy, already a core dep) to `aggregates` — the review's headline skill metric
becomes a first-class tracked diagnostic. Offline test alongside the other aggregate fns.

**Live verification pass (one sitting, real EE auth):**

1. `uv run pytest` + full offline suite green first (including the footprint-invariance test
   — it must still pass: the mask LUT and the reporting-LUT *decoupling* are untouched even
   though masks move via 4a).
2. `OPENEARTH_EE_TESTS=1 uv run python scripts/validate_events.py` — the two-event ±50 %
   gate must still pass under median-centering + v5. If Korpezhe leaves the band, stop and
   diagnose before any freeze.
3. `OPENEARTH_EE_TESTS=1 uv run python scripts/calibration_harness.py --compare` (against
   v4, expect movement — record it), then `--freeze` → **`calibration_baseline_v5.json`**.
   Health gate: `n_quantified ≥ 10`. Expected-direction checks (recorded, not gated):
   turkmenistan-south and gulf-of-thailand in-mask hi-clip fractions drop and their ratios
   move toward 1 (fix 5's direct effect); no expectation is placed on Spearman — these fixes
   are honesty and range, not a skill claim, and the plan does not pretend otherwise.
4. Do **not** re-curate `calibration_events.json` this phase: re-pinning libya-sirte's
   contaminated reference or permian's cross-tile pair would change the event set between
   the v4 and v5 baselines and confound the A/B. The new flags will annotate them in the
   §8.2 table (Stage 5); re-curation + a v5.1 baseline is a documented follow-up.

Commits: `core: LUT v5 (ΔΩ to 6.0) + median-centered masks + clip/stability/contamination
diagnostics (ALGO_VERSION 6)` and `api+web: surface clip fractions, mask stability,
cross-tile + contamination hints in the Lab`, then `docs(data): freeze calibration baseline
v5`.

### Stage 3 — noise floor (fix 1 + fix 9b)

**Instrument** (`scripts/noise_floor.py`, formalizing the Tier 1 noise-chip run):

- For each of the 7 seeded methane sites: a **10 km bbox centered on the site** (the Lab's
  default analysis-area scale — the floor must describe what users actually analyze), list
  scenes over a fixed 2-year window, seeded-RNG-pick **N = 5** target scenes
  (cloud ≤ 30 %), auto-pick each reference via `pick_reference` (target excluded), run the
  full `analyze` (default k·σ = 2, min_area 5, MC seeded) — i.e. *the identical detection* on
  presumed-plume-free pairs. Record per pair: scene ids, flags, `no_plume`, Q ± σ, n_px, u10.
- Output → **`packages/api/src/openearth_api/data/noise_floor_v1.json`** (packaged like
  core's LUT, generated by `--freeze`): per site `{n_pairs, detect_rate, q_noise_kg_h:
  [...], floor_kg_h: median of detected noise Qs}`, plus `global.floor_kg_h` = pooled median
  across all sites' detected noise Qs, plus provenance (git hash, LUT version, detect
  params, run date). Schema documented in the script header; an offline test validates a
  committed fixture of the schema + the loader.
- Honesty note baked into the JSON + docs: at recurrent-emitter sites the "floor" includes
  real residual emissions — it is an **upper bound on trustworthiness, which is the
  conservative direction for a floor**. `detect_rate` is reported so a site where 5/5
  plume-free pairs "detect" reads as exactly that.

**Surfacing (decision: display context + flag — never a gate, never folded into σ):**

- A hard gate would hide rows from a review feed whose whole design is human triage; folding
  the floor into `q_sigma` would double-count noise the MC already bootstraps and bury an
  empirical site number inside a model budget. So: report, mark, filter — don't suppress.
- API (feed + detail, **derived at read time** like fix 8, so old rows get context too):
  `noise_floor_kg_h`, `floor_source` (`"site" | "global"`), `below_noise_floor: bool`
  (`q_kg_h ≤ floor`). Site match by the detection's site; unknown/custom sites → global
  floor. Typed schema fields → `make gen`. Applies to **both** physics and ML rows —
  this completes fix 9: the ML single-pass Q now carries the same floor context as physics Q.
- Web: feed rows and detail get a "below noise floor" chip (tooltip: "at or below the median
  Q this pipeline retrieves from plume-free scene pairs at this site (N pairs) —
  indistinguishable from retrieval noise"); the detail shows the floor value + source
  alongside Q. Lab analysis panel shows the site floor as static context before a run.

**Ordering**: runs strictly after Stage 2 (the floor must be measured with median-centered
masks + v5 — a floor frozen against the old pipeline would be a wrong number the day it
ships).

*Exit gate:* `noise_floor_v1.json` frozen from a live run; feed/detail render floor context
on an old physics detection, a new one, and an ML row (manual check); offline tests cover the
loader, the site→floor resolution (site/global), and the flag threshold.

Commits: `core+api: noise-floor instrument + frozen v1 floor + feed/detail context` and
`web: noise-floor chips in feed, detail, Lab`.

### Stage 4 — ML protocol batch (fix 6 absorbing 7 + 11, plus 10b)

One retrain, locally (MPS; license wall: everything derived stays under `data_dir/ml/`,
only the aggregate JSON is committed).

**Spatial-cluster grouping** (`packages/ml/data.py`):

- Replace GroupKFold-by-site with **GroupKFold-by-site-cluster**: single-linkage
  agglomeration of site centroids (from the git-ignored recovery metadata) at a **5 km**
  threshold, computed at train time — never hardcode the cluster lists (they are derived
  data; F2's measured merges {T5,T6,T7}, {T2,T3}, {T15,T16,T17}, {T20,T21,T22}, {T13,T14}
  are the expected outcome and go in the eval JSON as aggregate counts, nothing per-tile).
- **Hard assertion after folding** (train-time check, not CI): zero cross-fold chip pairs
  with ground-footprint overlap > 10 % (computed from recovery bboxes — the F2 instrument
  becomes a guard). Abort the run if violated.
- Residual same-*acquisition* scene sharing across distant clusters is measured and
  **reported** in `ml_eval_v2.json` (a declared limitation), not chased: full
  scene-disjointness across 23 sites in one region would collapse the folds entirely.

**Inner validation + operating point** (`train.py`, `eval.py`):

- Within each outer fold's train set, hold out one cluster-group (same deterministic
  round-robin as the outer assignment) as **inner val**: early stopping (patience 12) and
  checkpoint selection use inner-val Dice **only** — the eval fold is touched exactly once,
  by the final frozen model.
- **Threshold selection on inner val**: sweep prob thresholds 0.05…0.95 (step 0.05) on
  scene-level F1 → per-fold selected threshold, applied frozen to the eval fold.
- **Both sides get curves**: the eval JSON records the full scene-level PR/F1 sweep for the
  model (prob thresholds) *and* the baseline (`k_sigma` 1.0…4.0, step 0.25) on each eval
  fold. Headline comparison = model at inner-val-selected threshold vs baseline at the
  pipeline default k = 2.0; the baseline's eval-oracle best-k is also reported, explicitly
  labeled "oracle upper bound" (that asymmetry favors the baseline — deliberately).

**Label-quality gate (fix 7)** (`packages/ml/labelq.py`, formalizing the review
instrument):

- Per positive chip: invert the chip's own MBMP ΔR (reporting LUT, solar-geometry AMF),
  integrate ΔΩ over the CH4Net label footprint. Positives with **integral ≤ 0 are excluded
  from training** (they are internally contradictory labels) and from the primary eval truth.
- Eval reports **two truth sets**: primary = quality-filtered labels (the citable numbers);
  secondary = all labels (continuity with v1). Below-noise-floor label shares (vs the
  Stage 3 floor) are recorded as aggregate counts in the JSON and drive the §9.4 rewrite:
  the F1 claim is *annotation agreement*, not detection skill at the labeled rates.

**Train/serve alignment (fix 11)**:

- Training tensors: replace `_fit_to`'s zero-pad with **reflect-pad** to 128² using the same
  bottom/right convention as the serve path's `pad_to_multiple` (center-crop for oversize
  stays). One padding convention end-to-end; §9.3's claim becomes true instead of documented-
  around.
- Serve reference pool (`services/ml.py`): the scan's candidate pool is minted once per scan
  as `list_scenes(bbox, start − 150 d, end + 150 d, max_cloud=60.0)` — matching the training
  exporter's ±150-day/cloud-60 reference environment — instead of the user's scan window.
  Offline test: a fake `list_scenes` asserts the widened window + that targets still come
  only from the requested range.

**Deployed refit + export**:

- `train_one` gains `early_stop: bool`; the deployed refit runs the fixed budget with **no**
  early stopping and no best-state restore (the comment/behavior mismatch dies). Deployed
  threshold = **median of the five folds' inner-val-selected thresholds** (pinned decision —
  the deployed model has no inner val of its own).
- Re-export ONNX (opset 18, dynamic HW), rerun the torch↔ORT parity test, re-measure
  `latency_ms_p50`.

**Provenance + framing (fix 10b)**:

- `_git_hash()` → full hash + `-dirty` suffix when `git status --porcelain` is non-empty
  (both `train.py` and the manifest writer).
- Manifest: keep `cv_scene_f1` (now the v2 number) but add `cv_protocol` — a one-line
  qualifier ("site-cluster-grouped 5-fold CV, inner-val early stop + threshold selection,
  quality-filtered labels; see ml_eval_v2.json") that travels wherever the number resurfaces.
- **`ml_eval_v1.json` stays committed** (history, like the v3/v4 calibration baselines);
  `ml_eval_v2.json` lands beside it; `EVAL_JSON` in `train.py` points at v2. §9.4 retires the
  v1 *numbers* in Stage 5 — i.e. only once v2 exists, which this stage guarantees. Fallback
  if the retrain is blocked > a few days: Stage 5 annotates §9.4's v1 table as
  protocol-invalid (spatial leakage, eval-fold early stopping, untuned operating point)
  without replacement numbers — the annotation must not wait on the rerun.

**Honesty expectation, stated up front**: the v2 headline will very likely be **worse** than
0.60-vs-0.46 — that is the point. The gate for this stage is protocol validity, not a metric
target; `gate_model_ge_baseline` may legitimately fail, and if it does the serving tier's
candidate-ranker framing already carries the consequence (the model stays a ranker under
human review either way; a v2 where the model loses outright additionally goes to §9.4 as a
finding and puts "retire the ML tier" on the design-pass agenda — decision deferred until
the number exists).

*Exit gates:* fold assertion passes (zero >10 % cross-fold overlap); `ml_eval_v2.json`
schema-validated offline (fixture test), aggregate-only (grep the diff for per-tile keys
before committing); parity test green; a serve smoke against the new manifest (threshold
read, scan path) via the existing monkeypatch patterns; `git status` under `data_dir/` paths
clean by construction (git-ignored — verify nothing slipped into the commit).

Commits: `ml: cluster-grouped CV + inner-val protocol + label-quality gate + reflect-pad
(ml_eval_v2)` and `api: serve reference pool aligned to training (±150 d)`.

### Stage 5 — methods + docs rewrite (fix 15 + the language the findings dictate)

`docs/methane_methods.md`:

- **§1 (fix 15)**: one new paragraph — EPSG:4326 grid, corner-origin affine (live-verified,
  Stage 1's contract test), EE nearest-neighbor default resampling, and the registration
  consequence hierarchy: same-orbit pairs near-identical sampling → cross-orbit sub-pixel
  shifts → cross-UTM-tile up-to-half-pixel per-band shifts (now flagged,
  `cross_tile_reference`).
- **§7 rewrites** (Tier 1 consequences, verbatim intent):
  - Detection floor: replace "roughly 1–5 t/h for favourable surfaces" with the measured
    numbers — ~4–5 t/h best arid sites, tens of t/h heterogeneous/offshore, ~80 % of
    plume-free pairs yield a quantifiable component at default settings; point at the
    committed `noise_floor_v1.json` and the feed's floor context.
  - New bullet — **borrowed IME coefficients (F6)**: U_eff α, β are LES-calibrated to
    Varon's p95+median-filter mask; ours is k·σ+opening+component selection; the transfer
    systematic is unquantified. Declared, with the decision-box rationale for not adopting
    p95 linked to this plan.
  - New bullet — **reference contamination**: recurrent emitters may have no plume-free
    reference; `possible_reference_contamination` flags it; the composite-reference fix is
    future work (design pass).
  - Keep the "~25 % anchor offset" hypothesis flagged as hypothesis (two refuted
    predecessors) — unchanged posture, now with the noise-floor context making its practical
    weight explicit.
- **§8.2**: add the per-event skill statement — Spearman ρ = 0.19 (p = 0.5, n = 15) on v4:
  "the central calibration is essentially unbiased **in aggregate**; individual rates are
  order-of-magnitude estimates, and ranking two sources by our Q is unsupported." Add the v5
  baseline paragraph (v4 → v5 aggregate movement table, same format as the v3 → v4 one, incl.
  the new `spearman_rho`), annotate libya-sirte (reference independently measured ~22 t/h —
  contaminated) and permian (cross-tile) rows, and describe the harness's validity-bound
  exclusion rename.
- **§9.3**: padding now uniform reflect-pad (deviation resolved, not documented-around);
  reference-pool alignment noted.
- **§9.4**: v2 protocol description (cluster grouping + inner val + threshold selection +
  label gate + both-sides curves), v2 numbers as the citable table, v1 table compressed to a
  short "v1 (superseded — protocol-invalid: spatial fold leakage, eval-fold early stopping,
  untuned operating point)" note keeping the file reference. The claim vocabulary shifts to
  *annotation agreement*; the below-floor label shares appear here.
- **§9.5**: disagreement tri-state semantics; ML Q's point-estimate marking + floor context.
- `CLAUDE.md`: terse deltas only — LUT v5, ALGO_VERSION 6, noise-floor JSON + routes context,
  ml_eval_v2, the unit-param importer. `docs/roadmap.md`: Phase 7 entry + as-built one-liner.
- Findings docs: untouched except Stage 0's pointer edit — this plan is the disposition
  record.

Commit: `docs: methods §1/§7/§8.2/§9 honesty rewrite + Phase 7 roadmap tick`.

---

## Relationship to the design pass (flagged overlaps — NOT absorbed here)

Per the agreed sequencing (fix round now, design pass after), these review threads
deliberately stop at the diagnostic/flag level in Phase 7 and hand off:

| Thread | Phase 7 delivers | Design pass owns |
|---|---|---|
| Reference quality (fix 2 / T1 F4 / queue item 9) | `possible_reference_contamination` flag + Lab hint | Multi-scene median-composite reference (Varon-style), reference-picker UX, interplay with the unified date+window model |
| Date/window semantics | untouched | Unified center-date+window model across Explore/Compare/Timelapse/Lab (queue items 6–8) — fix 2's hint text must be revisited then |
| Varon p95 quantification mask (T1 F6) | documented systematic (§7) | Optional dedicated mask-calibration study with its own instrument |
| ML tier fate if v2 fails its gate | the honest number + §9.4 language | Retire / retrain-on-other-data decision |
| Timelapse/AnimationBar reworks | out of scope entirely | queue items 5, 7 |

## Deviations from / refinements of the review's candidate-fix list (deliberate)

| Decision | Rationale |
|---|---|
| Fix 4: no Varon-mask adoption, no α/β recalibration — diagnostics + docs instead | Decision box: p95 presupposes detection, window size becomes the dominant free parameter, n = 15 can't validate a mask redesign, anchor rule forbids fitting |
| Fix 1 surfacing: display context + flag; never a gate, never σ-folded | Review-feed triage must not hide rows; σ-folding double-counts bootstrapped noise and hides an empirical number inside a model budget |
| Floor lives in a packaged API JSON, generated by a script | The API must serve it without scripts/ at runtime; calibration baselines stay scripts/data (they are instruments, not served data) |
| Fix 8 derived at read time (persisted value kept as historical record) | Old rows become correct instantly; no migration; one grouped query per feed page is cheap at this scale |
| Fix 13 never guesses units (`auto` drops unit-agnostic rates to None + counts them) | A wrong rate is worse than a missing one; verdicts never used the rate; IMEO kg/h names verified, SRON headers unverifiable at planning time |
| Harness exclusion decoupled from LUT grid edges (declared 3.0 mol/m² validity bound) | Extending the grid would otherwise silently disarm the MBSP-blowup guard (campeche/caspian class) |
| LUT lo end stays −0.5 | Ω_bg = 0.65 mol/m²; a lower edge tabulates negative total columns — unphysical; the asymmetric-truncation skew is documented (§7) and visible via the new lo-clip fraction |
| `calibration_events.json` unchanged; libya-sirte/permian annotated, not re-pinned | Keeps the v4 → v5 baseline A/B clean; re-curation is a documented follow-up (v5.1) |
| `ml_label_q_estimate.json` committed only as aggregates | Per-CH4Net-tile rows are license-walled derivatives ("no per-file manifests") |
| No DB migration anywhere | Everything new is `result_json`, read-time derivation, or packaged JSON — append-only discipline preserved by not appending |
| One ALGO_VERSION bump (5 → 6) in Stage 2 only | Stage 1 changes key composition (self-invalidating), Stage 3/4 add no cached ops and change no cached semantics |

## Implementation pitfalls (read before coding)

- **LUT regeneration inputs**: `generate_ch4_lut.py` runs under `uv run --group lut` and
  pulls HITRAN line data via HAPI — confirm the line-list inputs are identical to v4's (HAPI
  cache/provenance) *before* regenerating; the shared-subgrid identity test is the tripwire.
  HAPI never appears under `packages/`.
- **Median-centering moves every mask** — `validate_events.py` and the harness *both* rerun
  live before the v5 freeze; the footprint-invariance test (reporting-LUT swap) must still
  pass bit-identically, because the mask LUT and the decoupling are untouched.
- **Order inside Stage 2 matters for the freeze only**: land all code, run the live pass
  once, freeze once. Never freeze between fixes.
- **Noise floor after Stage 2, always** — a floor measured on the old pipeline is wrong the
  day it ships. If Stage 2's live pass and Stage 3's floor run happen in one EE session,
  fine — but the floor run uses the committed Stage 2 code.
- **`_disagreement` read-derivation** must batch its physics-row lookup per feed page (one
  `IN`-query over (site, scene) pairs), not one query per row.
- **Thumbnail key change** orphans every existing cached thumbnail — expected, harmless;
  do not "fix" it with a TTL purge.
- **Unit importer**: suffixed keys beat the explicit `unit` param; `raw` keeps the original
  strings so a mis-imported batch is diagnosable; the 500 t/h guard drops the *rate*, never
  the event.
- **Retrain nondeterminism**: MPS ops are not bit-reproducible even seeded — provenance
  records device + seed + dirty-flag; don't promise bit-repro in the eval JSON, promise
  protocol.
- **License-wall diff check** before every Stage 4 commit: `git diff --staged --stat` must
  show only code/configs/`ml_eval_v2.json`; any path under `data_dir/`, any per-tile key in
  JSON = stop.
- **`make gen` stages**: 1 (import route + disagreement field) and 3 (floor fields). Stage 2
  adds `result_json` content only — no schema drift, CI's diff check will prove it.
- **Web changes stay paint/property-level** where they touch the map (Lab chips are DOM,
  not layers) — the no-refetch rule is not in play this phase, keep it that way.
- **Floor JSON is versioned data, not config**: a rerun writes `noise_floor_v2.json` +
  loader constant bump — never mutate v1 in place (baseline discipline, same as the LUT).
