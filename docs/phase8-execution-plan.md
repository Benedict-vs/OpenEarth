<!-- docs/phase8-execution-plan.md — Phase 8 (design pass) execution plan.
     Written 2026-07-11, from the post-Phase-6 review queue (the items the Phase 7 science
     round deliberately deferred to a design pass). Implements after Phase 7 merges.
     Externally checkable facts re-verified 2026-07-11 at planning time:
     - Varon et al. 2021 (AMT 14, 2771) uses ONE reference scene per spacecraft for all
       multi-pass retrievals ("We use one reference observation per Sentinel-2 MSI for all
       multi-pass (SBMP and MBMP) column retrievals performed at each site") and explicitly
       leaves the persistent-emitter case open ("It may be challenging to identify a
       plume-free satellite pass when monitoring persistent methane sources") — no composite
       or averaged reference anywhere in the paper.
     - Ehret et al. 2022 (EST 56, 10517; arXiv:2110.11832) is the literature's answer for
       recurrent monitoring, and it is NOT a median composite: per-pixel linear projection of
       the current log-band-ratio image onto the previous T−1 = 29 dates (sliding window
       T = 30), two-step outlier-rejecting estimation, optional albedo clustering, combined
       with MBSP-style band ratio. That machine (co-registration + long series + robust
       regression) is out of scope here; it is the documented upgrade path.
     ⇒ The Stage 4 median-composite reference is therefore pinned as OUR OWN declared
       design (median across k same-orbit scenes; 50 % breakdown point against intermittent
       contamination), literature-adjacent but not literature-borrowed — the earlier
       "Varon-style" shorthand in planning notes was wrong and must not appear in docs.
     Code facts verified in-repo at planning time:
     - DELETE /jobs/{job_id} (cooperative cancel via threading.Event) exists end-to-end;
       no web UI calls it. services/timelapse.py already passes should_cancel into
       render_frames; core raises JobError("cancelled") and discards staging frames.
     - timelapse.py render_frames _work(): a non-"empty" exception from the composite/thumb
       mint RE-RAISES and kills the whole render (the ~74-frame failure Benedict hit);
       fetch failures already degrade to per-frame "failed".
     - Compare sides hardcode half_window_days: 3 (useCompareSide.ts); TilesRequest already
       speaks composite:"date_window" + target_date + half_window_days — the unified window
       model needs NO tiles-schema change.
     - dateStore consumers (the whole Stage 0 surface): DateControl, useMintLayer,
       useBrowseFrames(+AnimationBar), ChartPanel, ExportDialog, useInspector,
       WindOverlay/WindParticles, RoiToolbar, CatalogBrowser, workspace.ts. -->

# Phase 8 — design pass: one time model, honest animation, resilient renders, composite reference

**Goal:** retire the app's conflated date semantics in favour of two named concepts — a
**window** (what a composite shows) and a **period** (a span you chart, animate, search, or
step through) — used with the same vocabulary and the same two components everywhere; rework
the Animate area around that model (honest, buffer-aware preview + relocated render
playback); make timelapse renders interruptible and per-frame failure-tolerant with partial
results kept; and ship the median-composite MBMP reference as an opt-in answer to the
persistent-emitter problem, with an A/B against calibration baseline v5 recorded before any
promotion. *Exit: no view has two controls for the same time concept or one control for two;
a mid-render EE failure costs one frame, not the render; a running render can be stopped and
its partial result kept; a recurrent-emitter site can be analyzed against a composite
reference from the Lab.*

**Branch:** `v2/phase8-design-pass`, cut from **main after Phase 7 merges** (Stage 4 extends
Phase 7's `possible_reference_contamination` hint; Stages 0–3 don't strictly need Phase 7 but
a clean cut avoids a rebase). One commit per stage, prefixed `core:` / `api:` / `web:` /
`docs:`. After every stage: `make check` + `pnpm --dir apps/web lint && … typecheck && …
test -- --run`; after any API schema change: `make gen` in the same commit.

**Standing rules (Phases 3–7 sets still apply):**

- **No-refetch rule**: layer controls touch paint/layout/moveLayer only; playback and
  preview swap visibility on pooled sources, never re-mint the visible layer.
- **Append-only migrations**: this phase adds **no DB migration** (the renders table already
  has every column Stage 3 needs; workspace state is a versioned JSON blob).
- **No ALGO_VERSION bump**: nothing here changes the semantics of a cached operation —
  analyze results are DB rows keyed by job, tiles are never cached, and the composite
  reference is a new request parameter, not a reinterpretation of an old one.
- **Anchor rule**: Stage 4's A/B is recorded evidence, never a fitting target; no constant
  is tuned to published rates; `calibration_baseline_v5.json` is not superseded this phase.
- **Generated types are law**: `types.gen.ts` never edited by hand; every schema change
  lands with its `make gen` diff in the same commit (CI diff-checks drift).
- **torch never outside `packages/ml`**; offline tests make zero EE calls; live
  verification (Playwright + real EE) runs manually at stage exits.

---

## The time model (the load-bearing design)

Two concepts, named once, used everywhere:

| Concept | Meaning | Shape | Where it appears |
|---|---|---|---|
| **Window** | The time slice a single composite averages over | `{ center: ISO date, halfDays: int }` | Explore layer, each Compare side, each Preview frame, each Timelapse frame |
| **Period** | A span of time you look *across* | `{ start, end }` | Chart, Preview axis, Timelapse extent, Lab scene search, ML scan |

**Decision box — why Explore's Range mode dies.** Today Explore has two modes ("Range" =
mean over `[start, end]`, "Single date" = mean over `center ± half`) that produce the same
thing — a mean composite over an interval — through two mental models, and the review call
was explicit: *"a 6-day range and a single date ± 3 days are the same thing… wants one
unified concept."* But the range state was quietly serving second masters: the chart plots
over it and Browse animates across it. That is the actual confusion — one control conflating
what-you-see with what-you-scan. So: the **layer window** becomes the only "what am I looking
at" control (center + width), and the **period** becomes an explicit, separate control owned
by the features that scan time (chart, preview, timelapse). Explore's sidebar shows the
window; the chart and preview panels each show the shared period. Asymmetric ranges lose
first-class UI (they were an accident of the old control, not a feature); the API keeps
accepting arbitrary `start/end` unchanged, so nothing is lost at the wire level. The window
compiles to `composite: "date_window"` requests exactly as today — **no tiles-schema
change** — and center+width was deliberately kept a *frontend* concept: start/end is the
more general primitive, and the server has nothing to do with a center (revisit only if
center-weighted composites ever become a product).

**Vocabulary contract (UI copy, pinned):** the words are always "window" and "period".
Window caption format: `≙ 2026-05-28 → 2026-06-27 · mean composite, clouds masked`.
Width presets: **Day** (±0), **±3 d**, **±15 d**, **±45 d**, **Custom…** (0–183, integer).
Preview caption: "Slides your window across the period, minting composites on demand — for
smooth playback, render a timelapse." Timelapse "Window (days)" help text reuses the same
sentence pattern ("each frame = one window, stepped along the period").

---

## Triage — every design-pass item, decided

| # | Item (queue ref) | Verdict | Stage |
|---|---|---|---|
| D1 | Unified date+window model across views (q6/q7/q8) | **Implement** — shared `TimeWindowPicker` + `PeriodPicker`, workspace v2 migration | 0 (+1 for Compare) |
| D2 | Compare temporal smoothing (q6) | **Implement** — per-side window width (the presets ARE the smoothing) | 1 |
| D3 | AnimationBar two-modes rework (q7) | **Implement** — Preview-only bar + buffer-aware transport; Playback relocates to the Gallery ("Play on map") | 2 |
| D4 | Animate "plays without changing" (q7) | **Implement** — transport never advances past an unready frame; optional bounded prefetch-all | 2 |
| D5 | Mid-render EE failure kills the job (q5) | **Implement** — mint failures degrade to per-frame `failed` + dead-pipeline breaker | 3 |
| D6 | Interrupt + discard, partial salvage (q5) | **Implement** — Cancel button; cancel keeps completed frames + partial movie; delete stays separate | 3 |
| D7 | Frame-to-frame smoothing (q5) | **Implement** — `tween` blend frames at encode time (Off/2×/4×) | 3 |
| D8 | Median-composite reference (q9 / T1 F4 / Phase 7 fix-2 deferral) | **Implement, opt-in default-off** — k same-orbit median composite; A/B vs baseline v5 recorded; promotion deferred | 4 |
| D9 | Reference-picker UX | **Implement minimal** — mode radio + member list + "Retry with composite" on the contamination hint; a full picker redesign is out of scope | 4 |
| D10 | Ehret-style regression background | **Not this phase** — documented upgrade path (§7); needs co-registration + long series, a different machine | — |
| D11 | ML tier fate (Phase 7 handoff) | **Decision now unblocked** — `ml_eval_v2.json` exists (scene-F1 0.571 ≥ gate 0.416); deliberately not decided here, separate call | — |
| D12 | v5.1 event re-curation (Phase 7 handoff) | **Not this phase** — follows the Stage 4 A/B evidence (composite mode is exactly what a re-curated libya-sirte needs) | — |

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 0 | Shared time model: window/period stores, pickers, workspace v2 | web + api (workspace schema) | L | — |
| 1 | Compare per-side windows | web | S | 0 |
| 2 | Preview transport + playback relocation | web | M | 0 |
| 3 | Timelapse resilience: per-frame failure, cancel-salvage, tween | core + api + web | M | — |
| 4 | Composite reference (gated, opt-in) | core + api + web | L | Phase 7 merged |
| 5 | Docs: methods §7 addendum, app-vocabulary notes, roadmap, CLAUDE.md | docs | S | 0–4 |

3 is code-independent of 0–2 (different packages) and may run in parallel; 4 last among the
code stages (it wants Phase 7's contamination flag in main and its Lab hint text); 5 last.

---

## Pinned contracts

### Stage 0 — shared time model (web + workspace schema)

**`lib/timeWindow.ts` (new, pure, vitest):**

- `TimeWindow { center: string; halfDays: number }`.
- `windowRange(w): { start, end }` — inclusive ISO dates, `end` clamped to today (a
  future-leaning window is legal input; the caption shows the clamp).
- `rangeToWindow(start, end): TimeWindow` — center = midpoint, `halfDays =
  ceil(spanDays / 2)` (the v1-workspace migration primitive).
- `WINDOW_PRESETS = [Day ±0, ±3 d, ±15 d, ±45 d]` + custom bound `[0, 183]`.
- `formatWindowCaption(w)` — the pinned caption string.

**`dateStore` v2 shape** (breaking, internal):

```ts
{ window: TimeWindow; period: { start: string; end: string };
  setWindow(patch); setPeriod(start, end) }
```

`mode`/`start`/`end`/`targetDate`/`halfWindowDays` die. Defaults: window `{ center:
today − 15 d, halfDays: 15 }` (≈ the old last-30-days composite), period = last 12 months.

**Components:** `TimeWindowPicker` (center date input + preset chips + custom ± field +
computed caption; a `compact` variant for Compare/inline use) and `PeriodPicker` (from/to
pair, consistent labels). `DateControl.tsx` is replaced by `TimeWindowPicker` in the Explore
sidebar; the semantics paragraph from the quick-wins batch collapses into the caption.

**Consumer migration (the full dateStore surface, each with its role):**

- `useMintLayer.buildTilesRequest` — plain products: `composite: "date_window"`,
  `target_date: window.center`, `half_window_days: window.halfDays` (the single-mode path
  becomes the only path). `needs_ref` compare products: `composite: "mean"`, `dates:
  windowRange(window)`, `ref` unchanged — the post window is the layer window.
- `ChartPanel` — plots over `period`; gains a `PeriodPicker` in its header row.
- `AnimationBar` — reads `period` for its date axis (Stage 2 owns the rest).
- `ExportDialog` — describes and exports the layer window (`formatWindowCaption`).
- `useInspector` — samples the layer window (it already reuses `buildTilesRequest`).
- `WindOverlay`/`WindParticles` — field time = `window.center` + `T12:00:00Z` (replaces the
  `mode === "single" ? targetDate : end` branch).
- `RoiToolbar` preset hints — a site's date hint sets `period` = the hint span AND `window`
  = `rangeToWindow` of it, `halfDays` capped at 45 (a hint is a season, not a composite).
- `CatalogBrowser` — the needs_ref default ref window keys off `windowRange(window).start`.

**Workspace v2 + migration:**

- `WorkspaceState.date` (API schema) gains optional `center`, `period_start`, `period_end`;
  the v1 fields (`mode`, `start`, `end`, `target_date`) become optional; `v` captured as 2.
  Server stays a dumb store — it validates shape, never semantics. `make gen`.
- `applyWorkspace` accepts both: v1 `range` → window = `rangeToWindow(start, end)`, period =
  `{start, end}`; v1 `single` → window = `{target_date, half_window_days}`, period =
  center ± 180 d (end-clamped). v2 → direct. `captureWorkspace` writes v2 only. Vitest: one
  case per v1 mode + a v2 round-trip.

**Other views' vocabulary alignment (labels only, no logic):** Lab scene-search dates
relabel to "Search period" (PeriodPicker styling); Timelapse form start/end relabel to
"Period", and the "Window (days)" help text adopts the pinned sentence pattern.

*Exit gate:* zero remaining references to `DateMode`/`targetDate`/`halfWindowDays` outside
`timeWindow.ts`; workspace migration tests green; live Playwright pass — add layer, move
window, chart over period, save + reload a v1 workspace fixture.

Commits: `web: unified window/period time model (shared pickers, store v2)` and
`api+web: workspace state v2 (window+period) with v1 migration`.

### Stage 1 — Compare per-side windows (the smoothing feature)

- `SideConfig` gains `halfDays: number` (default 3 — today's hardcoded behaviour is the
  migration default). `useCompareSide` sends `half_window_days: cfg.halfDays`; the
  hardcoded literal dies.
- `CompareControls`: each side (linked AND independent modes) renders `TimeWindowPicker
  compact` — center date + width chips replace the bare date input. The presets are the
  requested smoothing: "1 week" ≈ ±3 d, "1 month" ≈ ±15 d. Caption per side shows the
  computed range, so "why is my left side blank" self-answers.
- compareStore vitest updated; no API change (`date_window` already accepts the field).

*Exit gate:* live Playwright — one side Day, other ±15 d over a gappy coastal scene: the
wide side fills in, the narrow side shows honest gaps.

Commit: `web: per-side compare windows — width presets replace the hardcoded ±3 d`.

### Stage 2 — Preview transport + playback relocation

**AnimationBar becomes Preview-only:**

- The Browse/Playback mode toggle dies. The bar renders: `PeriodPicker` (shared store) +
  frames count + transport + the pinned Preview caption. Internal naming follows
  (`PreviewControls`; the `useBrowseFrames` hook keeps its name — it *is* browse
  infrastructure).
- **Buffer-aware play (the honesty fix):** the play timer advances only when the *next*
  frame's pool entry is `ready`; otherwise it holds on the current frame and the UI shows a
  buffering state (pulsing frame-dot + "buffering…" note). Pure helper in `lib/animation.ts`
  — `advanceFrame(status: Record<number, FrameStatus>, index: number, total: number):
  number` (returns the same index when blocked; wraps only through a ready frame 0) —
  vitest-covered. fps becomes "up to N fps". Scrubbing stays on-demand (unchanged pool).
- **Bounded prefetch:** a "Prefetch all" button when `frames ≤ PREFETCH_MAX = 24`: expands
  the pool radius to cover every index (existing `MAX_IN_FLIGHT = 2` still paces the mints;
  declared constant, commented as an EE-budget bound). Play with everything prefetched is
  the smooth case; the caption still points to Timelapse for real playback.
- **"Render as timelapse…" button:** seeds the Studio form from the current state —
  dataset/product from the active layer, `roiSource: "current"`, period → start/end,
  `stepMode: "interval"`, `intervalDays = max(1, ceil(periodDays / frames))`, `windowDays =
  2·halfDays + 1 || null`, title prefilled — then switches to the Timelapse view. View
  switching: `App.tsx` owns the view state; lift a `navigate(view)` callback into a tiny
  `uiStore` (App subscribes) so features can request a view change without prop-drilling.

**Playback relocates to the Gallery:**

- `RenderGallery` rows (succeeded renders) gain **"Play on map"**: sets a new
  `playbackStore` `{ renderId: string | null }` and navigates to Explore.
- New `features/timelapse/PlaybackBar.tsx`: rendered by `ExplorePage` while
  `playbackStore.renderId` is set — a slim docked bar (render title, the existing
  `useImageFrames` transport UI, close ✕ that clears the store). The map overlay mechanics
  (`useImageFrames`) are unchanged; only the entry point and ownership move. The old
  `PlaybackControls` in AnimationBar dies, including its awkward dataset/product matching —
  the gallery row already *is* the selection.

*Exit gate:* vitest on `advanceFrame`; live Playwright — play a 12-frame preview over a
slow product and watch it hold instead of lie; gallery → "Play on map" → frames on the
Explore map → ✕ restores normal state. No-refetch rule audit: visibility swaps only.

Commit: `web: Preview transport (buffer-aware) + gallery "Play on map" — AnimationBar
two-mode split dies`.

### Stage 3 — timelapse resilience: per-frame failure, cancel-salvage, tween

**Per-frame failure tolerance (`core timelapse.py`):**

- In `_work`, a non-"empty" exception from the composite/thumb mint is recorded as
  `FrameResult(window, "failed", None)` — same degradation the fetch path already has
  (`ee_call` has already retried inside `thumb_url`; a frame that still fails is data, not
  a crash). The bare `raise` dies.
- **Dead-pipeline breaker** so 74 doomed mints don't burn quota: track the first
  `EARLY_ABORT_PROBE = 8` completed windows; if *zero* rendered and ≥ 1 failed among them,
  raise `JobError` ("Earth Engine failing consistently — aborted after N windows with no
  usable frame"). Declared constant, commented. The existing "nothing rendered at all"
  terminal check stays.
- Offline tests (existing fake-composite/fetch seams): one failing window among many →
  render succeeds, manifest records `failed`; all-failing → early abort with the breaker
  message, not 74 attempts.

**Cancel-salvage (`core` + `api` + `web`):**

- `render_frames` on a tripped `should_cancel`: cancel pending futures, *keep* completed
  results, and — if ≥ 1 frame rendered — write the manifest with `"cancelled": true`
  (`FrameManifest` gains `cancelled: bool = False`) and **return it** instead of raising.
  Zero rendered frames → today's `JobError("cancelled")` (nothing to salvage).
- Runner (`services/timelapse.py`): a returned cancelled manifest → encode the movie when
  `rendered_count ≥ 2`, update the row to `status="cancelled"` **with** `frame_count` (and
  `movie_bytes` when encoded). The status enum is untouched — a cancelled row with frames
  is the "partial" state, no migration.
- Detail/gallery: `cancelled` + `frame_count > 0` renders as **"Partial — N frames"** with
  playable frames and (if encoded) a downloadable movie; delete works as today (the 409
  guards only `running`).
- Web: a **Cancel** button beside the running job's progress in the Studio (wired to the
  existing `DELETE /jobs/{job_id}`; the SSE terminal event already triggers the refetch).
  Copy: "Stop render — completed frames are kept". No confirm dialog (it's recoverable:
  delete remains available).
- Offline tests: cancel after 2 completions → returned manifest, 2 frames, `cancelled`
  flag; cancel before any completion → JobError as before. API test: runner writes the
  partial row.

**Tween smoothing (`core` + `api` + `web`):**

- `encode_movie(..., tween: int = 0)`: insert `tween` linear blends between consecutive
  frames (`Image.blend`, α = j/(tween+1)) and scale the encoder fps by `(tween+1)` so
  wall-clock pacing is preserved. Pure frame-sequence expansion — offline-testable
  (`expand_frames(paths, tween)` helper returning the blend plan; test the plan, not the
  codec).
- API: `TimelapseRequest.tween: int = 0` (bounds 0–4); the GIF frame cap is checked
  **post-expansion** at submit. `make gen`.
- Web: form field "Smoothing: Off / 2× / 4×" (tween 0/1/3) with one help line ("blends
  between frames at encode time — a display effect, not more data").

*Exit gate:* offline suites green; live — start a deliberately long render, cancel midway,
play the partial; one render with Smoothing 2× and visually confirm the crossfade.

Commits: `core: per-frame mint failure tolerance + cancel-salvage manifests + tween
encoding` and `api+web: timelapse cancel button, partial renders in gallery, smoothing
option`.

### Stage 4 — composite MBMP reference (gated, opt-in)

**The design (our own, declared — see the header note on attribution):** a per-pixel,
per-band **median across k same-orbit reference chips** replaces the single reference chip,
upstream of an unchanged retrieval. The median's 50 % breakdown point is the mechanism: an
intermittent plume must contaminate the *same pixels in half the members* to survive into
the background, whereas the single-reference design fails on one bad pick (libya-sirte's
"plume-free" reference independently measured ~22 t/h — Tier 1 F4). Reference noise also
drops ≈ √k for the homogeneous case. This is deliberately NOT Ehret's regression background
(D10): no co-registration machinery, no long series requirement — a chip-level drop-in at
the existing `ref_chip` seam.

**Core (`methane/scenes.py`, `detect.py`):**

- `pick_reference_set(target, candidates, k, ...)` (pure, beside `pick_reference`): filter
  to **same spacecraft AND same relative orbit** (hard constraints here — the LUT is
  per-spacecraft and viewing geometry must be fixed for a meaningful median; the soft
  penalties of the single picker are not enough when averaging), same cloud/Δt bounds as
  `pick_reference` (`min_days` still excludes the same overpass), rank by |Δt|, take up to
  k. Unit tests mirror `pick_reference`'s.
- `analyze(..., reference_mode: Literal["single", "composite"] = "single")`. k is
  `COMPOSITE_SIZE = 5`, a declared core constant, not a request knob (fewer dials; revisit
  with evidence). Fewer than `COMPOSITE_MIN = 3` eligible members → **fall back to single**
  + flag `composite_reference_unavailable` (the run proceeds; the Lab says why).
- Composite build: fetch member chips through the existing `fetch_chip` (serially or via
  the `ee_call` semaphore — never a new parallel EE path), stack, per-band
  `np.nanmedian`; a pixel missing in some members medians over the rest. The composite
  plays `ref_chip` unchanged.
- **Ref-pass AMF = median member AMF** (declared approximation): solar zenith drifts over
  the ±120 d candidate span, so record every member's AMF in the result and flag
  `composite_amf_spread` when `max−min > AMF_SPREAD_MAX` (declared constant with a comment
  deriving a sensible bound from the LUT's AMF grid spacing — Opus: read `conversion.py`'s
  AMF interpolation before pinning the number). Result additions (all `result_json`, no DB
  migration): `reference_mode`, `reference_scene_ids`, per-member `{scene_id, days_from_target,
  amf}`, the spread.
- `DetectionResult.reference` stays the nearest member (display anchor); Phase 7's
  contamination check runs on the composite's own ΔΩ field unchanged — still flagged ⇒ the
  Lab hint escalates: "even a 5-scene composite reference shows an enhancement — treat this
  source as continuously emitting; MBSP is the honest mode here."
- **ML scan untouched, deliberately**: the model was trained against single references
  (Phase 7 aligned the serve pool to training); composite refs would break channel parity.
  A comment at the scan's reference seam says so.

**Harness + evidence (the gate):**

- `scripts/calibration_harness.py` gains `--reference-mode {single,composite}` (threaded to
  `analyze`; recorded in the run header). The A/B: one live run `--compare` against
  `calibration_baseline_v5.json` in composite mode, same events, movement table recorded in
  the Stage 5 docs. Hypotheses stated up front (recorded, not gated): libya-sirte's ratio
  rises toward 1 (its contamination is the textbook case); homogeneous-site scatter drops;
  no expectation on Spearman. `validate_events.py` gains the same flag; the two-event ±50 %
  gate must pass in *single* mode as always (composite is additional evidence, not the new
  default).
- **No new frozen baseline this phase.** Composite stays opt-in default-off; promotion to
  default + `calibration_baseline_v6` + the v5.1 event re-curation (D12) is one future
  decision made with this A/B in hand.

**API + web:**

- `AnalyzeRequest.reference_mode: "single" | "composite" = "single"` (the analyze schema in
  `schemas.py`; `make gen`).
  422 if `reference_scene_id` is set together with `composite` (pick one: an explicit scene
  IS single mode).
- Lab RunPanel reference section: radio — "Single scene (auto or picked)" / "Composite —
  median of up to 5 same-orbit scenes" with one help line each (composite: "robust
  background for recurrent emitters — an intermittent plume must appear in half the scenes
  to contaminate it"). Detail view: reference block lists members (date, Δt, AMF) when
  composite; flags chip already renders the two new flags via the existing mechanism.
- Phase 7's contamination hint gains a **"Retry with composite reference"** action
  (re-submits the same run with `reference_mode: "composite"`).

*Exit gate:* offline tests (picker-set constraints, median/NaN behaviour, fallback flag,
AMF spread flag, request validation); live — Korpezhe composite run within the validation
band, libya-sirte single-vs-composite from the Lab UI (Playwright) with the movement
recorded; harness A/B table saved for Stage 5.

Commits: `core: median-composite MBMP reference (pick_reference_set, opt-in analyze mode)`,
`api+web: reference_mode on analyze + Lab composite UI + retry action`, `docs(data): none —
no baseline change, evidence lands in Stage 5 docs`.

### Stage 5 — docs

- `docs/methane_methods.md` §7: a "composite reference (opt-in)" subsection — the design,
  the 50 % breakdown-point argument, the same-orbit/same-spacecraft constraints, the median
  AMF approximation + spread flag, the A/B movement table vs baseline v5, and the honest
  literature framing: Varon 2021 uses a single reference per spacecraft and names the
  persistent-emitter gap without solving it; Ehret et al. 2022's regression background is
  the literature's machine for recurrent monitoring and remains the upgrade path (D10).
  The Phase 7 "reference contamination" bullet gets a pointer here.
- App-vocabulary note (README or `docs/` app tour section, wherever the views are described
  today): the window/period model in three sentences; preview-vs-timelapse framing;
  partial renders.
- `docs/roadmap.md`: Phase 8 entry + as-built one-liner. `CLAUDE.md`: terse deltas — time
  model (window/period stores + pickers), workspace v2, PlaybackBar/uiStore, render
  cancel-salvage + tween, `reference_mode` (+ the "ML scan stays single-ref" rule).
- Leftover handoffs restated where future-us will look: D10 (regression background), D11
  (ML tier fate — `ml_eval_v2.json` now exists, decision pending), D12 (v5.1 re-curation — now unblocked by
  the Stage 4 evidence), and the AnimationBar follow-up *if* Preview usage shows people
  still expect real playback there.

Commit: `docs: methods §7 composite-reference addendum + Phase 8 roadmap tick`.

---

## Deviations from / refinements of the review queue (deliberate)

| Decision | Rationale |
|---|---|
| Window/period stays a frontend model; tiles schema untouched | start/end is the more general primitive; the server has nothing to do with a center; `date_window` requests already exist |
| Explore loses asymmetric ranges as first-class UI | They were an accident of the old two-mode control; the API keeps accepting them; workspaces migrate losslessly via `rangeToWindow` |
| Playback moves to the Gallery; AnimationBar is Preview-only | The review's own suggestion; the two modes were "conceptually different things sharing one control" — relocation IS the fix, not better labeling |
| Play holds on unready frames instead of prerendering everything | Prerendering all frames = a timelapse, which exists; the preview's job is honesty (buffer state) within the EE mint budget (±2 pool, 2 in flight), with a bounded 24-frame prefetch opt-in |
| Cancel = stop-and-keep; delete = discard; no third state | One button each; a cancelled row with frames is "partial" by construction — no enum change, no migration |
| Composite reference is opt-in default-off, k = 5 fixed, no baseline v6 | Q-changing default needs the A/B evidence first (anchor rule discipline); a k knob without evidence is a dial that invites fitting |
| Composite candidates: same orbit AND spacecraft, hard | Median across mixed geometries/SRFs averages physics, not noise; the single picker's soft penalties exist because it must always return *something* — the composite has a single fallback instead |
| ML scan keeps single references | Channel parity with training (Phase 7 fix 11); a comment guards the seam |
| "Varon-style composite" wording banned from docs | Verified false at planning time — Varon 2021 is single-reference; the composite is our declared design, Ehret 2022 is the literature's (different) recurrent-monitoring answer |
| No DB migration, no ALGO_VERSION bump anywhere | Renders table already carries the partial state; analyze results are job-keyed rows; nothing cached changes meaning |

## Implementation pitfalls (read before coding)

- **Stage 0 is a semantic refactor wearing a rename** — every dateStore consumer changes
  meaning slightly (wind time = center, not end; chart = period, not layer range). Do them
  in one commit with the store change; a half-migrated tree type-checks but lies.
- **Workspace fixtures**: keep a committed v1 JSON fixture in the vitest suite so the
  migration path stays exercised after v1 stops being writable.
- **`useBrowseFrames` pool identity**: the pool key must now include the period, not the
  old start/end — rebuilding on window-width change is correct (frames are windows).
- **Buffer-aware play must not deadlock**: an all-error pool (EE down) holds forever —
  the helper treats `error` frames as skippable after one hold cycle; test that case.
- **`render_frames` breaker ordering**: results are consumed in window order; the probe
  counts *completed* windows regardless of status — don't let a leading run of legitimate
  "empty" windows (winter gaps) trip it (empty ≠ failed in the breaker condition).
- **Cancel-salvage staging cleanup**: keep `_cleanup_staging` for the zero-rendered path;
  the salvage path must only rename frames it kept and still remove stragglers.
- **Tween × GIF cap**: enforce post-expansion at submit (422), not in core — core encodes
  what it's given.
- **Composite chip fetches ride `ee_call`** — k = 5 sequential chip fetches per analyze;
  never a new thread pool around EE.
- **AMF seam**: `detect.py` currently inverts with `reference.spacecraft, reference.amf` —
  read that seam (and `conversion.py`'s AMF handling) before wiring the median AMF;
  the target-pass inversion is untouched.
- **`make gen` stages**: 0 (workspace v2), 3 (tween), 4 (reference_mode). Stages 1–2 are
  web-only — CI's diff check must show zero schema drift there.
- **Playwright at every stage exit** (user-standing rule for web work): the flows named in
  each exit gate, in a real browser, against live EE where the gate says so.
