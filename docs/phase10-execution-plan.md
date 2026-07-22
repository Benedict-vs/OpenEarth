<!-- docs/phase10-execution-plan.md — Phase 10 execution plan.
     Written 2026-07-21 (Fable planning session), from Benedict's locked product spec
     (interviewed 2026-07-21: sources, presets, staleness cap, grade suite, authoring modes,
     resolution policy, draft mode, acceptance scenes). Implements on a branch cut from main
     after PR #12 (Phase 9) merged. NOT related to Phase 9 — this is the Timelapse Production
     pass; the reference-selection ladder moves to Phase 11.

     Externally checkable facts verified 2026-07-21 in the planning session (do not re-derive):

     ── HLS (Harmonized Landsat Sentinel-2, NASA, GEE-native) ──
     - NASA/HLS/HLSL30/v002: Landsat 8/9 harmonized, 30 m, 2013-04-11 →; RGB = B4/B3/B2.
     - NASA/HLS/HLSS30/v002: Sentinel-2 harmonized, 30 m, 2015-11-28 →; RGB = B4/B3/B2, NIR
       narrow = B8A. Combined revisit 2–3 days.
     - Fmask band bits (both): 0 cirrus (reserved, UNUSED), 1 cloud, 2 adjacent to cloud/shadow,
       3 cloud shadow, 4 snow/ice, 5 water, 6–7 aerosol level. Cloud mask = bits 1|2|3 set.
     - Reflectance: catalog vis example min 0.01 / max 0.18 ⇒ float reflectance scale; assets
       carry REF_SCALE_FACTOR/ADD_OFFSET properties. ⚠️ Whether GEE assets arrive pre-scaled is
       NOT documented — Stage 0 spike verifies empirically before any provider code assumes it.

     ── Landsat Collection 2 Level 2 (GEE-native, all 30 m, SR scale ×0.0000275 − 0.2) ──
     - LANDSAT/LT05/C02/T1_L2: 1984-03-16 → 2012-05-05; RGB = SR_B3/SR_B2/SR_B1.
     - LANDSAT/LE07/C02/T1_L2: 1999-05-28 → 2024-01-19; RGB = SR_B3/SR_B2/SR_B1.
       SLC failed 2003-05-31 → wedge gaps, ~22 % of each scene lost. Post-2003 L7 is
       composite-only material (median over ≥3 scenes fills most wedges); never a lone frame.
     - LANDSAT/LC08/C02/T1_L2: 2013-03-18 →; LANDSAT/LC09/C02/T1_L2: 2021-10-31 →;
       RGB = SR_B4/SR_B3/SR_B2 (band NUMBERING SHIFTS between L5/7 and L8/9 — provider maps
       per-spacecraft, product keys stay uniform).
     - QA_PIXEL bits: 1 dilated cloud, 3 cloud, 4 cloud shadow, 5 snow.

     ── EE output limits ──
     - getThumbURL has NO documented dimension cap (the 10 000-px grid cap documented for
       getDownloadURL is a different endpoint). 4K frames (3840×2160 ≈ 8.3 MP PNG) are
       plausible but UNPROVEN → Stage 0 mints one empirically before the 4K ceiling is wired
       into schema/UI. Fallback if refused: computePixels windowed assembly (ee/pixels.py
       already does grid math + tiling) — slower, unlimited.

     ── In-repo facts verified at planning time (recon 2026-07-21) ──
     - composites.py: ONLY .mean() reducers exist (build_mean_composite L29, build_date_composite,
       get_compare_image). No median/qualityMosaic anywhere. Empty collections surface lazily;
       classification happens in timelapse via _is_empty_error/classify_ee_error.
     - Cloud masking is S2-ONLY: providers/s2.py s2cloudless join, DEFAULT_CLOUD_PROB_THRESH=50,
       scene prefilter CLOUDY_PIXEL_PERCENTAGE ≤ 65, QA60 unused; masked pixels are transparent
       (alpha=0) in minted PNGs. generic.py has NO masking/scaling — HLS/Landsat via the generic
       path would render unmasked/unscaled, hence dedicated providers.
     - Vis: thumb vis_params carry ONE scalar min/max for all RGB bands; no gamma, no per-band
       stretch. render_frames resolves one vis range once (explicit, else compute_vis_range on
       the MIDDLE window; percentile 0.5/99.5, 15 % headroom, clamps to valid range).
     - timelapse.py: FrameStatus = rendered|empty|failed (exactly three); "cancelled" is a
       manifest-level bool; dense re-index; FetchFn injectable; _PNG_MAGIC check; dead-pipeline
       breaker = EARLY_ABORT_PROBE 8 windows all-unrendered with ≥1 failed; no per-frame retry
       beyond ee_call. encode_movie: mp4 libx264/yuv420p (even dims), webm vp9, gif Pillow;
       expand_frames tween 0–4 with fps_out = fps·(tween+1); GIF cap enforced API-side.
     - Caps: MAX_FRAMES 400, MAX_DIM_VIDEO 1920, MAX_DIM_GIF 720, _MAX_GIF_FRAMES 200
       (post-tween), FRAME_FETCH_WORKERS 4, MAX_RUNNING_JOBS 4, fps 1–30, max_dim schema cap 1920.
     - API: renders table = id/title/dataset/product/params_json/roi_json/status/frame_count/
       fps/format/movie_bytes/timestamps; status running|succeeded|failed|cancelled; runner
       writes rows off-loop; SSE "frame" events {index,status,total} live-only (never replayed);
       DELETE /jobs/{id} cancels cooperatively; delete render 409 while running.
     - Web: TimelapsePage form → buildTimelapseRequest (lib/timelapse.ts); useFrameTransport
       rAF player; PlaybackBar docked on Explore via playbackStore; RenderGallery "partial"
       chip for cancelled+frames; SSE via api/sse.ts subscribeJob.
     - Catalog: ProductSpec.collection_id per-product override; providers/__init__.py
       dispatches s1/s2/s5p, else generic; adding a source = builtin DatasetSpec +
       providers/<src>.py + a dispatch branch. presets.py RoiPreset {name,bbox,category,
       date_hint}; category currently continent|city|methane_site. -->

# Phase 10 — Timelapse Production: broadcast-quality timelapse from an honest pipeline

**Goal:** rebuild the timelapse stack to a professional production standard — outputs a person
would pay for — without ever lying about data. Three artifact killers (median/clearest-pixel
compositing, alpha-driven temporal gap-fill with a declared staleness cap, sequence deflicker),
a three-tier source ladder (native S2 10 m → HLS 30 m fallback → Landsat deep-history to 1984),
a preset-first wizard Studio with layered simple→expert UX and an open design-direction
decision, MP4-hero output with a color-grade suite and share extras, and per-frame honesty
surfaces (valid fraction, source, staleness) everywhere. *Exit: the five acceptance scenes
render clean — canonical gate: Richmond Park, 1 year, RGB, zero visible artifacts — and every
quality claim is demonstrated on real renders, not asserted.*

**Branch:** `v2/phase10-timelapse-production`, cut from main after PR #12. One commit per
stage, prefixed `core:` / `api:` / `web:` / `docs:` / `scripts:`. After every stage:
`uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`; web
stages add `pnpm --dir apps/web lint && … typecheck && … test -- --run`; any API schema change
lands with its `make gen` diff in the same commit.

**Standing rules (Phases 3–9 sets still apply, plus):**

- **Physics-honesty wall for post-processing.** Deflicker, grading, and gap-fill blending
  operate on RGB *display* frames only, at/after fetch time — NEVER on scientific products'
  data values (their frame-to-frame consistency comes from the fixed vis range). Enforced in
  code (post-processing refuses non-RGB inputs), not by convention.
- **Back-compat defaults.** Every new `TimelapseRequest` field defaults to today's behavior
  (mean composite, no gap-fill, no deflicker, no grade, no fallback). Old clients and old
  workspace states keep rendering byte-equivalent output.
- **Honesty surfaces are not optional.** Whatever compositing/fill mode runs, the manifest
  records per-frame `source`, `valid_fraction`, and `filled_fraction`; the player and gallery
  surface them. Seamless ≠ silent.
- **Methane tier untouched.** No ALGO_VERSION change (nothing here touches retrieval); the
  methane Lab, harnesses, and frozen baselines are out of bounds this phase.
- **Offline tests make zero EE/network calls.** The post-processing layer tests on synthetic
  PNG sequences; provider tests use the established fake-EE monkeypatch pattern; live checks
  are `@pytest.mark.ee` or manual stage exits.
- **Skills are process, not garnish.** UI stages load `frontend-design` before building and
  run `web-design-guidelines` in each review loop; every web stage closes with a Playwright
  screenshot pass against the stage's stated visual criteria.

---

## Load-bearing design decisions

1. **EE composites, NumPy post-processing.** Median/clearest-pixel composites are EE-side
   reducers (bulk reduction belongs to EE). Gap-fill, staleness QC, deflicker, grading, and
   valid-fraction stats run **post-fetch on the frame PNGs' alpha channel + RGB planes** in a
   new pure module — offline-testable, injectable, no EE dependency. The alpha channel IS the
   mask: EE renders masked pixels transparent, so the pure layer needs no second data path.
2. **"Clearest-pixel" is defined, not vibes.** For S2: `qualityMosaic` on inverted s2cloudless
   probability (per-pixel least-cloudy observation). For HLS/Landsat: Fmask/QA_PIXEL-masked
   median (their cloud products are binary, so "clearest" degenerates to median-of-clear).
   Median is available for every source; mean stays the legacy default.
3. **Gap-fill = forward-fill with a declared staleness cap of 2 frame-windows.** A hole
   (alpha=0 after compositing) inherits the most recent valid pixel ≤ 2 windows old; older
   holes stay holes (Survey tints them, Showcase's wide windows make them rare). Cap is a
   declared constant; per-frame `filled_fraction` + a max-staleness map feed the QC surfaces.
   Fill runs BEFORE annotation burn-in, in window order (needs the sequential pass that the
   dense re-index loop already does).
4. **Deflicker = luminance anchor, not histogram surgery.** Per-frame valid-pixel luminance
   median/IQR matched to the sequence's rolling reference by a smooth gain curve (clamped,
   e.g. ±20 %) — kills exposure pumping without repainting content. Full histogram matching is
   explicitly rejected (hue shifts on snow/water scenes). RGB-only, post-fill, pre-grade.
5. **Grade suite = three curves + three sliders, encode-side.** Natural (identity), Vivid,
   Cinematic as fixed declared LUT curves; brightness/contrast/saturation sliders composable
   on top. Applied at frame post-processing after deflicker; burned into the movie and stills;
   never stored back into source frames (re-grading a render re-encodes from the kept frames).
6. **Source ladder is per-window and recorded.** Frame build tries the primary dataset; on
   empty → HLS (S30+L30 merged, Fmask-masked) when the user enabled fallback. The manifest
   records `source` per frame; the player badges non-primary frames (the 10 m→30 m sharpness
   dip is explained, never mysterious). Landsat 5/7/8/9 is NOT a fallback — it is a separately
   selectable `landsat` dataset for deep-history renders (1984+), with per-spacecraft RGB band
   mapping and post-2003 L7 restricted to ≥3-scene median composites (SLC wedges).
7. **HLS + Landsat are real providers, not generic TOML.** They need bit-mask clouds and SR
   scaling that `generic.py` lacks — so `providers/hls.py` + `providers/landsat.py` follow the
   s2.py pattern (mask, scale, RGB stack), new builtin `DatasetSpec`s, and two dispatch
   branches. The Explore/Compare views inherit both sources for free (same catalog pipeline).
8. **Authoring modes are one compiler, two inputs.** Duration-first ("~15 s") and frame-first
   (step/window/fps) both compile to the SAME `{windows, fps, tween}` plan via a pure
   `plan_pacing()` helper with the math exposed to the UI ("73 windows → 24 frames @ 12 fps").
   No second render path.
9. **Native-locked resolution.** `max_dim` schema cap rises 1920 → 3840, but the effective
   dimension is `min(request, native_px(roi, gsd), 3840)` — never upscaled. The UI shows the
   drawn ROI's native limit before submit. Gated on the Stage 0 spike; if EE refuses 4K thumbs,
   the ceiling stays 1920 this phase (computePixels assembly recorded as the Phase 11+ path)
   and the schema cap is NOT raised — decide from evidence, not hope.
10. **Draft mode is the same job, smaller.** `draft=true` forces max_dim 480/mp4/no extras,
    marks the render row (params_json), and the gallery offers "Render final" prefilled. No
    second pipeline.
11. **Preflight is cheap and honest.** `POST /timelapse/preflight` returns per-window scene
    count + mean scene-level cloud metadata (CLOUDY_PIXEL_PERCENTAGE / Fmask-derived where
    available) — collection aggregates only, no pixel stats, so it answers in seconds. The
    Studio renders it as the availability strip BEFORE submit; empty spans show as "no data
    here" rather than a failed render later.
12. **Design directions before Studio code.** Stage 4 produces 2–3 clickable static mockups
    (≥1 app-consistent, ≥1 distinct "editing-suite" identity). Benedict picks; Stage 5 builds
    the winner. No UI code before the pick — the decision was deliberately left open.

---

## Stage 0 — `scripts:` spikes: the two unknowns (manual, live EE)

- **4K thumb spike:** mint + fetch one 3840×2160 S2 RGB thumb (Richmond Park bbox) through
  the existing `thumb_url` path; record success/latency/bytes — or the exact EE refusal — in
  the stage notes. This gates decision 9.
- **HLS scaling spike:** fetch one HLSL30 + one HLSS30 image's band stats + the
  REF_SCALE_FACTOR/ADD_OFFSET properties live; pin whether GEE assets are pre-scaled floats.
  Wrong assumptions here would silently wash out every HLS frame.
- Both results recorded as a dated "Stage 0 findings" block appended to this plan file.

**Exit:** both unknowns pinned in-repo; the 4K decision (raise cap vs hold 1920) is made.

## Stage 1 — `core:` compositing modes + HLS/Landsat providers

- `composites.py`: `build_composite(..., mode: "mean"|"median"|"clearest")` (mean = legacy
  default; `build_mean_composite` stays as a thin alias so existing callers are untouched).
  `clearest` = s2cloudless `qualityMosaic` (S2), Fmask/QA-masked median elsewhere (decision 2).
- `providers/hls.py`: merged HLSS30+HLSL30 collection, Fmask bit mask (bits 1|2|3; snow/water
  NOT masked — they are landscape, not defect), scaling per the Stage 0 spike, RGB product.
  `providers/landsat.py`: LT05/LE07/LC08/LC09 merged per requested window, QA_PIXEL mask
  (bits 1|3|4), SR scale ×0.0000275 − 0.2, per-spacecraft RGB mapping, L7-post-2003 composite
  guard (decision 6). Builtin `DatasetSpec`s (`hls`, `landsat`) + dispatch branches + catalog
  tests. NDVI/NDWI product recipes on both (sci products get the new sources too).
- Offline tests: dispatch, band mapping per spacecraft, mask-bit math on synthetic QA arrays,
  spec integrity; `@ee`-marked live smoke for one HLS + one Landsat composite.

**Exit:** offline suite green; `@ee` smoke renders both new sources; Explore can browse them.

## Stage 2 — `core:` frame post-processing (the artifact killers, pure layer)

- New `openearth/timelapse_post.py` (pure NumPy/Pillow, no EE): `valid_stats(frame_rgba)`
  (valid/filled fractions), `forward_fill(frames, cap_windows=2)` (decision 3, returns fill
  masks), `deflicker(frames, strength)` (decision 4), `grade(frame, curve, b/c/s)` (decision
  5, declared curve constants), `tint_holes(frame, color)` (Survey mode). All operate on RGBA
  arrays; every function refuses non-RGB product context (honesty wall).
- `render_frames` integration: post-processing runs in the existing sequential dense-index
  loop (fill needs window order); per-frame QC lands in a **manifest v2** — each frame gains
  `{source, valid_fraction, filled_fraction}`, top level gains `{composite, post: {…}}`.
  Manifest v1 readers (web detail view) tolerate missing fields — additive only.
- Per-window source ladder in the frame builder (decision 6): primary → HLS on empty when
  enabled; `source` recorded per frame; `empty` only when the whole ladder is empty.
- Offline tests: synthetic RGBA sequences — fill cap honored (a 3-window hole stays a hole),
  fill masks correct, deflicker gain clamp, grade curves monotone + slider bounds, tint mode,
  QC stats exact, manifest round-trip. This stage is the testing heart of the phase.

**Exit:** offline suite green; a synthetic cloudy sequence renders hole-free with honest
`filled_fraction` numbers; mypy strict clean.

## Stage 3 — `api:` schema v2, preflight, extras, draft

- `TimelapseRequest` v2 (all defaulted to legacy behavior): `preset?`, `composite`,
  `cloud_display` ("composite"|"tint:<hex>"|"raw"), `gap_fill: bool`, `deflicker: bool`,
  `grade {curve, brightness, contrast, saturation}?`, `fallback_source: bool`, `draft: bool`,
  `extras {still_frames?, crops?: ("1:1"|"9:16")[], title_card?, watermark?}`, `max_dim` cap
  per Stage 0 decision. Pacing fields per decision 8 (`duration_s?` XOR step/fps). `make gen`.
- `POST /timelapse/preflight` (decision 11) + service; cached briefly (same diskcache tier).
- Encode extras: title/end card (Pillow, declared layout), watermark composite, crop
  re-encodes (center-crop re-encode from kept frames), still export endpoint
  (`GET /timelapse/{id}/still/{index}` = the un-annotated full-res frame if kept, else the
  annotated one — decide in-stage, document). Draft flow per decision 10.
- Renders listing gains `draft` + preset name surfaced from params_json (no migration unless
  gallery filtering truly needs a column — prefer none).
- Tests: schema validation edges (GIF+4K, duration XOR step, crop enums), preflight service
  with fake EE, extras encode on tiny synthetic renders, draft flag flow.

**Exit:** `make gen` diff committed; offline suite green; a draft→final round-trip works
against fake EE.

## Stage 4 — design directions (Benedict checkpoint — no production code)

- Load `frontend-design`; produce 2–3 static clickable mockups of the wizard Studio
  (area → span → preset cards with visual explanations → availability strip → preview frame →
  render queue/player): ≥1 consistent with the app shell, ≥1 distinct "editing suite" look.
  Real copy, not lorem — the layered simple→expert language is part of what's being judged.
- **STOP: Benedict picks the direction (and can mix elements).** Record the pick in this file.

**Exit:** direction chosen and recorded; Stage 5 builds exactly that.

### Stage 4 decision — recorded 2026-07-22 (Benedict's pick)

Three clickable directions were delivered (`docs/phase10-studio-directions.html`, also a private
Artifact): **A · Console** (app-native devtool wizard), **B · Cut** (editing suite: program-
monitor hero, grade inspector, filmstrip-timeline availability strip with a source-ladder track),
**C · Atlas** (each timelapse a published map plate with a legend-as-data-sheet).

**Pick: B · Cut for authoring + Atlas's citable plate as an opt-in export ("Cut + plate").**

- **Authoring is Cut, unchanged** — the program monitor + grade inspector + filmstrip timeline
  (per-window scene density + source ladder + per-frame valid/filled/source, all scrubbable).
- **The plate is an optional add-on at render-complete**, not a second authoring mode. A finished
  render gets an **Export plate** action that composites the hero still + a provenance data sheet
  (source ladder per window, measured/filled/blank %, coordinates, scale, attribution, the recipe)
  into one downloadable card. It sits alongside the existing extras (stills, 1:1/9:16 crops, title
  card, watermark); the movie stays the primary output.
- **No new "truth":** the plate only packages provenance the pipeline already records (the Stage 2
  manifest per-frame source/valid_fraction/filled_fraction). Public hosted share links stay parked
  (GEE terms) — the plate is a self-contained downloadable, not a hosted URL.
- **Stage 5 consequence:** build the Cut Studio shell, and add one new **plate export** render path
  (still + data-sheet composite → PNG) reachable from the finished-render view. Console's numbered-
  wizard clarity informs the entry flow; Atlas's visual language is scoped to the plate only.

## Stage 5 — `web:` the Studio rebuild + player/gallery

- **Direction: "Cut + plate" (Stage 4 decision).** Cut editing-suite shell for authoring — a
  program-monitor hero, a grade inspector, and a filmstrip-timeline availability strip carrying
  the source-ladder + per-frame QC; the preset cards + pacing readout + Advanced panel live in
  Cut's layout (Console's numbered-wizard clarity informs the entry order).
- Preset cards (Showcase/Survey/Every pass/Seasonal pulse) carrying their policy explanations;
  availability strip from preflight; single-frame preview; draft/final buttons; authoring-mode
  toggle with the pacing math readout; Advanced panel exposing every knob (composite, cloud
  display + tint color, gap-fill, deflicker, grade suite, fallback, resolution with native-limit
  readout, tween, fps, format, extras).
- Player: QC badges per frame (valid/filled/source) on the scrubber; gallery: draft chip,
  "Render final", extras downloads, existing partial/rename/delete kept.
- **Plate export (the citable add-on):** a finished render gets an **Export plate** action that
  composites the hero still + provenance data sheet (source ladder, measured/filled/blank %,
  coords, scale, attribution, recipe) into one downloadable PNG — Atlas visual language, scoped
  to the plate. New render path (still + data-sheet composite); opt-in, alongside the movie/extras.
- Every substage: Playwright screenshot loop vs the mockup + stated criteria;
  `web-design-guidelines` audit + fresh-context `/code-review` before the stage commit.

**Exit:** web gates green; the Studio walks a first-time user from nothing to a correct
Showcase render without reading docs (self-explaining test: Playwright run scripted ONLY from
on-screen copy).

## Stage 6 — acceptance renders + docs (manual, live EE)

1. The five scenes, rendered for Benedict's judgment: **Richmond Park 1 yr RGB (canonical —
   zero visible artifacts)**; Po Valley seasonal agriculture (Seasonal pulse); Gigafactory
   Berlin construction (gap-fill truthfulness — no smeared buildings); an Alpine glacier
   (snow/cloud stress); Las Vegas 1984→2024 (Landsat deep-history). Scene ids + settings +
   outcomes recorded in the stage notes; artifacts kept in the gallery.
2. Docs: `docs/architecture.md` + CLAUDE.md timelapse sections rewritten; roadmap Phase 10
   entry + tick; this plan's decisions that changed during implementation annotated.
3. **Decision box:** any acceptance scene showing a visible artifact → diagnose and fix before
   merge, or descope THAT preset/feature with the evidence recorded. The canonical Richmond
   Park gate is non-negotiable — the phase does not merge with visible artifacts there.

**Exit:** five renders accepted; docs merged; PR opened with the acceptance evidence linked.

---

## Explicitly out of scope (parked with reasons)

- **Optical-flow frame interpolation** — tween crossfade + fps scaling covers pacing; flow
  synthesis invents pixels (honesty wall) and adds a heavy dependency.
- **Audio/music tracks** — encode scope creep; title cards + pacing carry the "produced" feel.
- **Public share links / hosted gallery** — parked with public deployment (GEE licensing).
- **computePixels 4K assembly** — only if the Stage 0 spike refuses 4K thumbs, and then as
  its own follow-up (windowed assembly + stitch is a real subsystem).
- **Reference-selection ladder** — Phase 11 (was "Phase 10" pre-renumbering; memory updated).
- **HLS/Landsat in Compare recipes (DNBR etc.)** — the sources land generically browsable;
  recipe-level cross-source products are the fusion backlog item.

---

## Stage 0 findings (2026-07-22, live EE — project openearth-488015)

Run: `uv run python scripts/spike_4k_thumb.py` and `scripts/spike_hls_scaling.py`.

### 4K thumb spike → **DECISION: raise `max_dim` cap 1920 → 3840 (native-locked)**

`getThumbURL` served both frames over the Richmond Park bbox (S2 RGB, mean over
2023-06→08) with no refusal:

| dimensions | latency | bytes | payload |
|---|---|---|---|
| 1920×1080 (control) | 21.6 s | 619,495 (0.62 MB) | valid PNG |
| 3840×2160 (4K) | 25.0 s | 1,056,052 (1.06 MB) | valid PNG |

4K cost only ~+3.4 s and ~+0.44 MB over 1920 — latency is dominated by the
composite reduction, not pixel count. The endpoint has ample headroom, so
**decision 9 proceeds: the schema `max_dim` cap rises to 3840**, with the
effective dimension still `min(request, native_px(roi, gsd), 3840)` so nothing
upscales past native GSD. computePixels 4K assembly stays parked (not needed).

### HLS scaling spike → **DECISION: GEE HLS is pre-scaled float reflectance — provider does NOT re-scale**

Both `NASA/HLS/HLSL30/v002` and `NASA/HLS/HLSS30/v002` deliver reflectance bands
already as physical floats in ~[0, 1] (with small negatives from atmospheric
correction), NOT raw DN — despite each band carrying a `B*_scale=0.0001` metadata
property. Applying that scale would wash every HLS frame to black.

- L30 (Landsat 8/9) mean reflectance over the Permian window: B4=0.232, B3=0.163,
  B2=0.107 (min −0.048, max 0.64). S30 (Sentinel-2) mosaic: B4=0.231, B3=0.158,
  B2=0.104, B8A=0.330 — identical magnitude convention.
- **GEE band names (unpadded, confirm the plan):** RGB = **B4/B3/B2** on both.
  - L30 bands: `B1 B2 B3 B4 B5 B6 B7 B9 B10 B11 Fmask SZA SAA VZA VAA` — **no B8/B12**;
    NIR-narrow = **B5**; SWIR = B6/B7; B10/B11 are thermal (scale 0.01, Kelvin, unused).
  - S30 bands: `B1 B2 B3 B4 B5 B6 B7 B8 B8A B9 B10 B11 B12 Fmask …`; NIR-narrow = **B8A**,
    NIR-broad = B8; SWIR = B11/B12.
  - **`Fmask` is an integer bit-packed QA band** (sampled 64–240): the S30 mosaic
    showed bits 4–7 set (snow/water + aerosol-level) exactly as documented — cloud
    mask = bits 1|2|3.
- **Provider consequence (Stage 1):** `providers/hls.py` selects bands directly as
  reflectance (no `.divide(1e4)`), masks on `Fmask` bits 1|2|3, and renames
  per-sensor NIR (L30 B5 / S30 B8A) to a canonical name before merging S30+L30 so
  NDVI/NDWI band math is source-uniform. Landsat C2 L2 (Stage 1) is the separate
  case that DOES need the `×0.0000275 − 0.2` SR scale (verified in the plan header).
