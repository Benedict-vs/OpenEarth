# Tier 3 science review — silent-corruption plumbing (findings)

*2026-07-11 · targeted audit of the quiet-accuracy plumbing: grid math, resampling, unit
boundaries, cache versioning, and the Phase 6 EMIT additions. Evidence: offline tests (532
passed), two small live-EE probes (grid convention + resampling; project from `.env`), git
history, and hand-verification of the §10.2 constants. Findings only — no fixes applied.*

## P1 — `ee/pixels.py` grid alignment: CORRECT, now live-verified (CLEARED)

The corner-vs-center question is settled by a live probe: `ee.Image.pixelLonLat()` fetched
through `fetch_window` over a `grid_for` grid returns exactly **our pixel centers**
(`x0 + (col+0.5)·xscale`, `y0 − (row+0.5)·yscale`), max error ~1.5 × 10⁻⁶ deg ≈ 0.15 m. So EE
interprets `affineTransform.translateX/Y` as the **top-left corner of pixel (0, 0)** — precisely
`grid_for`'s convention (`x0 = bbox.west`, `y0 = bbox.north`) and consistent with
`regrid_mask_nearest`'s center math (`(col+0.5)`), `GridSpec.affine` (GDAL layout), and
`window_grid`'s offset arithmetic (offline-tested: stitching, exact cover, offsets). Half-pixel
errors would in any case have been **common-mode** (target and reference share the same grid
dict), but there is now direct evidence there is none. Recommendation: freeze the probe as an
`@pytest.mark.ee` test so an upstream convention change gets caught.

## P2 — 20 m B11/B12 resampling: nearest-neighbor, consistent across passes (CLEARED, one doc gap)

Live probe: fetching S2 `B11` (native 20 m UTM) through our EPSG:4326 grid at 10 m yields ~2×2
duplicated value blocks (57 %/50 % neighbor duplication, 17 unique values in an 8×8 window) —
EE's default **nearest-neighbor**, no interpolation or averaging; at 20 m nearly every output
pixel picks a distinct native pixel. No `.resample()` call exists anywhere under `packages/core`,
so the default applies uniformly: target and reference are sampled by the same policy onto the
same grid. The *differential* effect is that each scene's native UTM registration phase differs —
same tile + orbit pairs (what `pick_reference` prefers) see near-identical sampling, while
cross-orbit and especially cross-UTM-tile pairs acquire up-to-half-pixel ground shifts per band.
That is the mechanism behind Tier 1 F5's cross-tile noise (permian: 54.8 t/h on a plume-free
pair) — not a bug here, but methods §1 nowhere states the NN-sampling/registration assumption;
it should, next to the cross-tile flag proposed in Tier 1 fix #4.

## P3 — Unit boundaries: one real hazard in `validation.py`; all other seams clean (CONFIRMED)

Audited every t/h↔kg/h site:

- Core computes kg/h once, correctly: `Q = U_eff/L · IME[kg] · 3600 [s/h]` (both `quantify` and
  `emission_over_mask`); `ime_kg` = mol/m² · m² · kg/mol. `pixel_area_m2` is cosine-corrected and
  consistent with `grid_for`. ✓
- Reporting boundaries divide by 1000 exactly once: `calibration_harness.py`,
  `validate_events.py`, web `kghToTh` (`formatEmission` labels t/h). ✓
- EMIT V002 rates pass through in kg/h with the unit in the key itself
  (`"Emissions Rate Estimate (kg/hr)"`), `"NA"` → None. ✓
- **`validation.parse_events` applies a blanket ×1000 (t/h → kg/h) to every rate alias** —
  including the unit-agnostic `rate`, `q`, and `emission_rate` — on the comment "published rates
  are conventionally tonnes/hour". That convention is SRON's (`source_rate_t_h`); **UNEP IMEO's
  Eye on Methane platform publishes plume rates in kg CH₄/h**
  ([methanedata.unep.org](https://methanedata.unep.org/download-dataset)), and §6 names IMEO as a
  supported source. An IMEO import through the generic aliases silently inflates every reference
  rate ×1000. Cross-match *verdicts* are unaffected (space/time only), but the stored/displayed
  reference Q — the number a reviewer compares a detection against — is corrupted. The parser
  records no unit provenance, so the corruption is undetectable downstream.

## P4 — ALGO_VERSION discipline: held; the real cache defect is a key-composition bug (CONFIRMED)

- Bump history is exactly the results-changing sequence: 1→2 (LUT v2), 2→3 (LUT v3), 3→4 (frozen
  mask LUT), 4→5 (LUT v4). Phase 5 writes no cached results (ML scan → DB rows + npz, uncached);
  Phase 6 added only **new** cache ops (`emit_plumes`, `embed_seed`) — new keys, no stale-read
  risk; git shows zero post-v5 changes to the existing cached pipelines
  (`timeseries`/`wind`/`methane_render`). Discipline held.
- **But the thumbnail cache key omits fields the render depends on**: `ThumbnailRequest` inherits
  `ref` and `methane_ref` from `TilesRequest`, and the shared `build_image` uses both (the compare
  path and the CH4_ANOMALY quicklook); `render_thumbnail`'s `cache_key(...)` includes neither. Two
  thumbnails of the same `needs_ref` product (DNBR / URBAN_HEAT / FLOOD_VV_CHANGE) differing only
  in the reference window — or two CH4_ANOMALY quicklooks with different `methane_ref` — collide
  onto one cached PNG: the second request **silently serves the first request's image** (TTL up to
  the `_effective_end_date` policy). This is a Phase 6 regression class ALGO_VERSION cannot catch
  (same version, same key, different meaning). `auto_range` is also absent but is genuinely unused
  by the thumbnail path today — worth a comment, not a key field.

## P5 — Phase 6 EMIT additions: fixture real, parse paths covered, constants verified (CLEARED)

- **Fixture provenance**: `packages/core/tests/data/emit_v002_plm_granule.geojson` is a real,
  trimmed **LP DAAC V002 CH4PLMMETA granule** (plume `CH4_PlumeComplex-3374`, Permian
  2025-09-22, DAAC-schema properties incl. `DCID`/`Orbit`) — not the interim portal-derived file.
  Its emission rate is genuinely `"NA"` (no concurrent wind).
- **Queue item 4 closed**: the numeric emission-rate parse path is exercised by a *synthetic*
  V002 feature (1620.4 ± 540.2 kg/h, plus portal-style Scene FIDs) with explicit assertions
  (`q_kg_h ≈ 1620.4`), alongside the real-granule assertions that `"NA"` coerces to `None` (not
  0.0, not the string). Both schema variants covered.
- **§10.2 constant + P/T assumption**: hand-verified. n = P/(RT) = 101325/(8.314·288.15) =
  42.30 mol/m³ → 1 ppm·m = 4.23 × 10⁻⁵ mol/m² (docs say "≈ 4.3 × 10⁻⁵", US-Standard surface
  stated); Permian ~900 m/300 K → 3.65 × 10⁻⁵ (docs "≈ 3.7 × 10⁻⁵", ±~15 % spread stated). The
  worked example's arithmetic checks out (968 ppm·m → 4.1 × 10⁻², 7506 → 0.32 mol/m²), and the
  text correctly confines the comparison to order-of-magnitude context, never a gate.

## Candidate fixes (decision list — extends the Tier 1 + Tier 2 lists, none applied)

12. **Thumbnail cache key** (P4, the one live wrong-answer bug here): add `ref` and `methane_ref`
    to the key; add a test that fails when `build_image` consumes a `TilesRequest` field the
    thumbnail key omits (schema-diff style, so the next added field can't repeat this).
13. **Unit-safe reference import** (P3): per-alias scale map (×1000 only for `*_t_h` keys;
    kg/h for IMEO-style columns) or an explicit unit parameter on the importer; store the unit in
    `raw` so provenance survives. Sanity guard: reject/flag events > ~500 t/h.
14. **Pin the EE contract** (P1/P2): commit the two live probes as `@pytest.mark.ee` tests
    (pixel-center identity; NN block-duplication) so upstream EE changes surface in the live
    suite instead of as silent georeferencing/resampling drift.
15. **Document the sampling model** (P2, folds into Tier 1 fix #4): one methods §1 paragraph —
    EPSG:4326 grid, corner-origin affine, EE nearest-neighbor, registration consequences for
    same-orbit vs cross-orbit vs cross-tile MBMP pairs (the cross-tile flag/refusal from Tier 1).
