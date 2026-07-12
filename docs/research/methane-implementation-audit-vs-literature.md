# Methane implementation audit — code vs the literature dossier

*Audit session 2026-07-12 (Fable). Companion to
`docs/research/methane-detection-methods-research.md` (the dossier — read it first; § refs
below point there). Audited at commit `49102ef` on `v2/phase7-science-fixes`.*

> **Concurrency warning:** an Opus session is implementing Phase 7
> (`docs/phase7-execution-plan.md`) in this checkout. Stages 0–1 had landed at audit time;
> Stages 2–5 were in flight. Anything marked 🚧 below is *already owned by Phase 7* — do not
> re-plan it. Code line numbers may drift; symbols are the stable references.

**Verdict legend:** ✅ sound vs literature · 🚧 already owned by Phase 7 · ⚠️ needs review /
small change · ❌ gap — candidate work item.

---

## 0. Summary table

| Area | Verdict | One-liner |
|---|---|---|
| Chip fetch: anti-alias blur | ❌ | No σ=0.7 Gaussian before ratioing (Ehret); aliasing artifacts feed MBSP/MBMP *and* the ML channels |
| Chip fetch: chip-level cloud/valid check | ⚠️ | Cloud filtering is tile-level metadata only; no per-chip NaN/cloud fraction guard |
| MBSP `c` refit | ⚠️ | Exclusion cut uses `np.nanstd`, not robust σ — hot/flare clusters inflate it |
| Flare / hot-pixel handling | ❌ | None anywhere; MBMP has a concrete flare-transition false-plume channel |
| Reference selection | 🚧/❌ | Phase 7 adds the contamination flag; *selection* stays metadata-only — the dossier ladder (§2) is unowned |
| Plume masking | 🚧 | Median-centering, stability diagnostic, cross-tile flag all in Phase 7 Stage 2 |
| Ehret-style false-positive checks (B12-dimming sign, other-band correlation) | ❌ | Not implemented; cheap, fits the new flag family |
| IME / wind / MC | ✅ | Literature-standard; F6 borrowed-coefficients documented by Phase 7 |
| Detection-floor honesty | 🚧/❌ | Noise floor = Phase 7 Stage 3; Gorroño S2CH4 synthetic benchmark unowned — and may unlock the deferred α,β question |
| ML tier protocol | 🚧 | Stage 4; but note the blur↔channels retrain coupling below |
| Multi-gas evidence (S5P NO2/CO, EMIT CO2, flare state) | ❌ | All greenfield; plumbing exists for each |
| Methods doc language | ⚠️ | Stage 5 rewrite should absorb 3 short literature points (below) |

---

## 1. Chip fetch & preprocessing (`methane/retrieval.py`)

**❌ No anti-aliasing before ratioing (dossier §1.1, §3.4).** `fetch_chip` → `mbsp`/`mbmp`
ratio raw B11/B12 reflectances. Ehret et al. show S2 SWIR bands are aliased and that ratioing
amplifies it into structured artifacts; their fix is a Gaussian σ=0.7 on both bands before any
ratio. We have **no smoothing anywhere** (grepped: none in `retrieval.py`, `channels.py`,
`ee/pixels.py`). Part of our "MBSP shows structure, not plumes" complaint is plausibly
literal aliasing.
*Change shape:* blur inside the chip→ΔR path (and the `ratio_b12_b11` channel), behind a
single constant. **Sequencing constraints are the hard part** — see §8 (Interactions).

**⚠️ Cloud/validity is tile-level only.** `list_scenes` filters on `CLOUDY_PIXEL_PERCENTAGE`
(whole ~110 km tile); `analyze` lists with `max_cloud=90.0` and `pick_reference` defaults to
30 %. A 10 km chip can be fully clouded under a 5 %-cloudy tile (and vice versa). Ehret
discard >15 % cloud *at the AOI level*. We never compute a per-chip cloud or valid-pixel
fraction; the only NaN awareness is the `nan_in_mask` flag after masking.
*Change shape:* cheap per-chip diagnostics at fetch time (finite-pixel fraction; a crude
cloud proxy from B2 brightness if wanted) → `cloudy_chip` / `sparse_chip` flags in the
existing flag family. No EE round-trips beyond what's fetched.

✅ Fill→NaN handling, the shared-`GridSpec` subtraction contract, and the 1024² chip cap are
all sound.

## 2. MBSP calibration (`retrieval.mbsp`)

**⚠️ The refit's exclusion threshold is not robust.** `sigma = float(np.nanstd(dr0))` — a
flare cluster or bright outlier inflates the *std*, so the `|ΔR| ≤ 1σ` keep-set retains more
contaminated surface pixels (the flare pixels themselves, many σ out, still get dropped;
the damage is leverage on `c_initial` plus a loosened cut). `plume.robust_sigma` already uses
1.4826·MAD — the calibration cut should use the same robust family. One-line change +
a hot-cluster regression test. (Philosophically identical to Phase 7's fix-4a
median-centering — masks got the robustness treatment, calibration didn't.)

**❌ No hot-pixel exclusion (dossier §3.3).** Nothing in the retrieval path knows flares
exist (`grep -ri flare methane/` → only the unrelated TROPOMI `Hotspot` class). Two concrete
failure channels at flare sites:

1. *MBSP:* the flame's thermal SWIR emission drives ΔR strongly **positive** at the stack —
   wrong sign for a plume, so no false mask, but it biases `c_initial` and (via the non-robust
   cut above) the refit.
2. *MBMP — the dangerous one:* a flare **lit in the reference and off in the target** makes
   `ΔR_MBMP = ΔR_t − ΔR_r` strongly *negative* at the stack pixels → inverts to a large
   positive ΔΩ → survives the k·σ mask **as a fake plume at exactly the facility location**
   (or grafts onto a real plume's mask, corrupting IME). The reverse transition creates a
   negative hole. Since unlit-flare *venting* is the single most common super-emitter mode
   (24/29 in Turkmenistan, dossier §3.2), flare-state transitions between target and
   reference are *expected* at precisely the sites we monitor.

*Change shape (dossier §3.3):* NHI hot-pixel mask, `(B12 − B11)/(B12 + B11) > 0`, on both
chips (bands already in the npz); exclude NHI-positive pixels (+1–2 px dilation) from the `c`
fits, σ estimation, and `detect_plume` input; emit `flare_lit_target` / `flare_lit_reference`
flags. The flag pair *is* the §3.2 evidence story (lit→unlit transition = venting likely
started) and doubles as the §7 multi-gas panel's flare-state input. Pure NumPy,
offline-testable, no schema beyond flags + two result_json scalars.

## 3. Reference selection (`scenes.pick_reference`, `detect.analyze` step 1)

**🚧 Owned by Phase 7:** fix 2's `possible_reference_contamination` (re-running
`detect_plume` on the reference's own mask-LUT ΔΩ) + Lab hint; median-composite reference
explicitly deferred to the design pass. Also the `cross_tile_reference` flag (fix 4b) and
`different_orbit_reference` (existing).

**❌ Unowned: the selection itself is still metadata-only.** Cloud %, |Δt| ≤ 120 d, +30
different-orbit / +5 different-spacecraft penalties, nearest wins. Never looks at pixels, so
it cannot minimize actual MBMP noise or notice surface-state mismatch (snow, moisture,
harvest). The dossier ladder (§2.5) maps cleanly onto the repo:

1. *k-candidate σ-scoring:* run the metadata score, keep top-k (3–5), fetch chips (cached),
   compute `robust_sigma(ΔR_MBMP)` per candidate, pick the minimum; record
   `reference_sigma` + runner-up σs as provenance; a floor on the best σ → explicit
   `no_acceptable_reference` outcome instead of a silently bad retrieval.
2. *Median-of-references:* pixelwise median ΔR over the best 2–3 candidates (Gorroño's
   2-reference averaging, made robust) — also the natural concrete design for the deferred
   "median-composite reference" so the design pass shouldn't re-derive it from scratch.
3. *Ehret regression background* (dossier §1.1/§2.3): the strategic replacement; a phase of
   its own (chip-stack fetcher on one `GridSpec`, log-ratio + projection + two-step refit —
   all pure NumPy after the fetch). Solves recurrent emitters that have **no** plume-free
   reference, which the Phase 7 contamination flag can only *report*.

✅ Existing heuristics match Gorroño's advice directions (closest-in-time; same viewing
geometry via the orbit penalty; SRF matching via the spacecraft penalty). `min_days`
same-overpass exclusion is correct and ahead of the published baseline (Varon picked
references manually — dossier §2.1).

## 4. Plume masking (`methane/plume.py`)

**🚧 Owned by Phase 7 Stage 2:** median-centered threshold (the σ is MAD-about-median but the
threshold was zero-centered — fix 4a), mask-stability diagnostic (4c), clip-fraction
diagnostics (fix 3). These directly answer Gorroño's "surface structure under the plume
dominates heterogeneous-scene error".

**⚠️ One addition to review with the NHI work (§2 above):** hot pixels must be excluded from
the *mask field* too, not just calibration — in MBMP the flare-transition dipole otherwise
enters the positive tail directly. If NHI lands after Stage 2, re-check `detect_plume`'s
callers so the exclusion happens once, upstream (NaN-ing the pixels in the ΔR fields is
sufficient — everything downstream is already NaN-aware).

## 5. Automated false-positive checks (Ehret's validation checklist → flags)

Kayrros validates **manually** with two specific checks (dossier §1.1); both are automatable
as diagnostics in our existing flag family, and neither is in Phase 7:

- **❌ B12-dimming sign check:** confirm the masked anomaly is an actual dimming in B12 of the
  target (in-mask mean of `c·R12 − R11` negative in the target pass, not created by the
  reference or by a B11 brightening — Ehret call out snow's B11>B12 contrast inversion
  producing dimming-like ratio artifacts). Flag: `not_b12_dimming`.
- **❌ Other-band correlation check:** a real CH4 plume must not co-locate with structure in
  CH4-blind bands; we already fetch B4/B3/B2. Compute e.g. |corr(mask footprint, RGB
  gradient/anomaly)| over the mask + a dilated ring; high correlation → `surface_correlated`
  flag. This is the single highest-yield false-positive killer in the Kayrros pipeline and
  costs one NumPy function here.

Both slot naturally beside Phase 7's new diagnostics (same result_json + Lab-chip pattern).
✅ The human-review framing itself (`ML = candidate ranker`, feed triage) matches industry
practice everywhere (Kayrros manual step, MARS analysts, SRON two-step + review).

## 6. Quantification (`methane/ime.py`, `methane/wind.py`)

✅ **Sound and literature-standard.** IME with `L = √(n·A)`, `U_eff = α·U10 + β`, ERA5 10 m,
seeded joint MC; identical formula family to Ehret/Varon. Wind sampling *time-interpolates*
between bracketing hourly grids and takes a t±1 h spread for σ_u10 — strictly better than
Ehret's "closest time before sensing". The borrowed-α,β systematic (F6) is documented, not
fixed, by Phase 7's decision box — correctly, given the anchor rule. Gorroño's error budget
(U10 ~50 % dominant for homogeneous scenes) matches our documented posture.

*Optional later idea (dossier §1.1):* Ehret's uncertainty-by-plume-transplantation (re-insert
the retrieved plume into other dates of a chip stack, spread of Q = empirical uncertainty) —
complementary to the MC and the Stage 3 noise floor, nearly free once a chip-stack fetcher
exists for ladder step 3. Design-pass material, don't duplicate the noise floor.

## 7. Multi-gas / evidence extensions (all ❌, all greenfield — dossier §4)

- **S5P combustion-evidence panel:** `catalog/builtin/s5p.py` already ships NO2/SO2/CO/O3/
  HCHO; `tropomi.py`'s grid-cell z-score machinery generalizes. Site-window NO2+CO anomaly
  vs background annulus beside the CH4 verdict ("CH4 absent + NO2 elevated → lit flare;
  CH4 up + NO2 flat → venting"). Pairs with the NHI flare-state flags (§2).
- **EMIT CO2 cross-match:** `services/emit.py` is CH4-only (`_V002_SHORT_NAME =
  "EMITL2BCH4PLM"`); CO2 = `EMITL2BCO2PLM` (V001) / `EMITL2BCO2ENH` (V002) via the same
  earthaccess path. **No GEE mirror exists for CO2** (verified) — so unlike CH4 there is no
  frozen-mirror fast path; it's earthaccess-or-nothing, credentials required (502 pattern
  already exists).
- **Attribution caveat for methods §7:** S2 physically cannot confirm the anomaly *is* CH4
  (two bands, no spectral fingerprint — dossier §4.1); a sentence stating "ΔR anomalies are
  attributed to CH4, not spectrally identified" belongs in the limitations list. The §5
  checks above are the practical mitigation.

## 8. ML tier (`methane/channels.py`, `packages/ml`) — one coupling to flag

🚧 Protocol overhaul is Phase 7 Stage 4. The audit adds one **sequencing coupling**:

⚠️ **The anti-alias blur (§1) passes through the train/serve seam.** `CHANNELS` =
(`mbmp_delta_r`, `mbsp_delta_r`, `ratio_b12_b11`, `b12`, `b11`) — three of five channels
change distribution if chips get blurred, `ChannelStats` (frozen in the manifest) shifts, and
train/serve byte-identity breaks unless the blur lands in `build_channels`' shared path *and*
the model is retrained (`model_version` bump). Therefore: **do not slip the blur in as a
"one-liner" while Stage 4's retrain is in flight** — it either goes *into* that retrain
deliberately or waits for the next one. Same class of constraint as Phase 7's single
ALGO_VERSION bump: blur changes every cached retrieval → needs its own bump + baseline
re-freeze (v6/v5.1) + `noise_floor_v2` (a floor measured unblurred is wrong for a blurred
pipeline).

*Longer-term (design pass):* a synthetic-plume training path (Gorroño-style simulation or
Orbio Eucalyptus-style injection — dossier §1.4/§5) would produce license-clean training
data and could retire the CH4Net wall entirely; the ViT single-image detector (dossier §2.4)
is the reference-free end state. Both post-Phase-7.

## 9. Instruments & benchmarks

**❌ Gorroño S2CH4 synthetic benchmark (dossier §5) — unowned, and strategically important.**
Real S2 L1C scenes with embedded WRF-LES plumes at known flux (Harvard Dataverse
10.7910/DVN/KRNPEH). Two distinct uses:

1. *Measurement instrument:* an offline-ish harness scoring retrieval+mask+IME against known
   truth — the missing tool for A/B-ing the blur (§1), reference ladder (§3), and NHI (§2)
   instead of asserting them. Complements (does not replace) the literature-anchor harness
   and the Stage 3 noise floor.
2. **A possible legal route around the F6 deadlock.** Phase 7's decision box rules out
   recalibrating U_eff α,β because "recalibrating without LES is only possible by fitting to
   published rates — forbidden by the anchor rule." But S2CH4's truth *is* WRF-LES-simulated
   flux, not published retrieval rates — fitting α,β for **our actual mask procedure**
   against it would be LES calibration in exactly Varon's sense, not anchor-fitting. This
   could unlock the deferred p95-mask study / α,β question with a defensible instrument.
   **Needs a careful design-pass argument** (simulation fidelity, plume-shape coverage,
   whether 3 sites × limited met conditions generalize) — flagged here, not decided.

🚧 Noise floor (Stage 3) and the harness Spearman additions (Stage 2) are in flight and
match the dossier's honesty findings; the Sherwin CI-coverage result (95 % CIs contain truth
only 52–70 % of the time across *nine* professional systems) is a good §7/§8.2 citation for
Stage 5 — our MC bands deserve the same humility framing.

## 10. Docs (`docs/methane_methods.md`) — three cheap additions for Stage 5

Stage 5 already rewrites §1/§7/§8.2/§9. Worth absorbing (coordinate with Opus — additive
sentences, not new sections):

1. §7: the **attribution caveat** (§7 above) and a pointer to flare physics — lit flares
   combust the CH4 (~91 % efficiency, Plant 2022), so "flame visible, no plume" is often
   correct; unlit flares are the dominant venting mode (Irakulis 2022).
2. §1 or §7: **aliasing** — B11/B12 are aliased and we currently ratio unfiltered chips
   (Ehret Fig. 6); named as a known noise contributor until the blur lands.
3. §7: the reference problem's literature context — one line noting the operational fix is
   multi-temporal backgrounds (Ehret), tracked as design-pass work.

---

## Prioritized candidate queue (post-Phase-7, merged with dossier §6)

Ordering assumes Phase 7 lands as planned; nothing here blocks or modifies it.

| # | Item | Audit § | Effort | Notes / sequencing |
|---|---|---|---|---|
| 1 | NHI flare mask + `flare_lit_*` flags (excl. from c-fit, σ, mask) | 2, 4 | S–M | Pure NumPy; changes results → ALGO_VERSION bump; bundle with 2–4 to spend one bump |
| 2 | Robust-σ refit cut in `mbsp` | 2 | S | Bundle with 1 |
| 3 | B12-dimming sign check + other-band correlation flag | 5 | S | Bundle with 1; the Kayrros false-positive killers |
| 4 | Anti-alias blur σ=0.7 | 1 | S code / M process | Must co-land with an ML retrain (channels seam) + baseline v6 + noise_floor v2 — see §8 |
| 5 | Chip-level cloud/valid-fraction flags | 1 | S | Independent, flag-only, no bump |
| 6 | Data-driven reference selection (k-candidate σ-scoring) | 3 | M | Provenance + `no_acceptable_reference`; feeds the design pass |
| 7 | S2CH4 benchmark harness | 9 | M | The instrument that measures 1–6; also the F6/α,β unlock question → design pass |
| 8 | S5P combustion-evidence panel | 7 | M | Reuses tropomi.py machinery; pairs with 1's flare flags |
| 9 | EMIT CO2 cross-match | 7 | M | earthaccess-only (no GEE mirror); clone of Phase 6 V002 path |
| 10 | Median-of-references composite | 3 | M | Concrete shape for the design-pass "composite reference" thread |
| 11 | Ehret regression background (chip stacks) | 3, 6 | L (phase) | Strategic; enables recurrent-emitter sites + transplantation uncertainty |
| 12 | Synthetic-plume training path (license-clean ML) | 8 | L (phase) | Contingent on Stage 4's v2 verdict + design pass |

**Explicit non-findings** (checked, fine as-is): wind sampling and σ_u10 (§6), IME formula
family (§6), grid/subtraction contract (§1), same-overpass reference exclusion (§3),
human-review framing (§5), S5P/EMIT CH4 tier scope, mask-LUT footprint-invariance design.
