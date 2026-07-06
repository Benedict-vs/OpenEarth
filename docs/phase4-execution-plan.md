<!-- docs/phase4-execution-plan.md — Phase 4 (Compare + Timelapse → retire Streamlit)
     execution plan. Written 2026-07-06 against the Phase-3-complete tree (branch
     v2/phase4-compare-timelapse, cut from main after PR #3 merged). Decisions in here are
     made deliberately within docs/plan.md's settled architecture; implement within them.
     Where this doc refines or deviates from plan.md, the "Deviations" section says so.
     External facts verified 2026-07-06: @maplibre/maplibre-gl-compare 0.5.0 on npm
     (peer maplibre-gl >=1.14, last publish 2026-03 — compatible with our 5.8);
     imageio-ffmpeg 0.6.0 on PyPI (bundles a static ffmpeg binary, no system dependency);
     MapLibre GL JS v5 ImageSource exposes updateImage() and setCoordinates(). -->

# Phase 4 — Compare + Timelapse → retire Streamlit: execution plan

**Goal (roadmap exit criterion):** parity checklist ticked; `legacy/` deleted in one
commit; README rewritten.

**Branch:** `v2/phase4-compare-timelapse` (already cut from main). One commit per stage,
prefixed `core:` / `api:` / `web:` / `docs:` / `repo:`. After **every** stage: `make check`;
after any API-schema stage: `make gen` in the same commit (CI diff-checks `openapi.json`).

**Standing rules (do not re-derive):**
- All blocking EE round-trips through `ee_call()`; no bare `getInfo()`.
- Pure math / file assembly offline-testable; EE only mints URLs and builds composites.
- `create_app()` stays EE-free and DB-free at creation; env-dependent work in the lifespan.
- Offline API tests fake EE by monkeypatching core fns **imported by name** into
  `openearth_api/services/*`.
- Timelapse renders are **primary, reviewable data** (like detections): files on disk +
  SQLite row, never in the evictable diskcache.
- DB migrations are append-only `PRAGMA user_version` batches; the `jobs` table is written
  only from the event loop, domain tables (here: `renders`) may be written from runner
  threads (WAL + per-connection `busy_timeout=5000` — detections precedent).
- Web: types via `make gen`; **no-refetch rule** for layer controls; animation and swipe
  never round-trip React renders (refs + rAF, imperative MapLibre).

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 1 | core: `timelapse.py` — frame stepping + Pillow annotations (pure) | core | M | — |
| 2 | core: frame rendering + movie encoding pipeline | core | L | 1 |
| 3 | api: migration 4 (`renders`), timelapse job + gallery routes | api | L | 2 |
| 4 | web: Timelapse Studio view (form → live progress → player → gallery) | web | XL | 3 |
| 5 | web: Explore animation transport (date slider + frame playback overlay) | web | M | 4 |
| 6 | web: Compare view (`@maplibre/maplibre-gl-compare`) | web | L | — |
| 7 | Parity sweep: `docs/parity-checklist.md` + close small gaps | all | M | 4–6 |
| 8 | Retire Streamlit: delete `legacy/`, tooling cleanup, README rewrite | repo | M | 7 |
| 9 | Exit verification + docs sweep | docs | S | all |

Stage 6 (Compare) is independent of 1–5 and can be done any time before 7. Stages 1→2→3→4
are a strict chain; 5 reuses 4's player component.

---

## Pinned contracts

### New dependencies

- `packages/core`: `pillow>=10.1,<12` (frame annotations — `ImageFont.load_default(size=…)`
  needs ≥ 10.1), `imageio-ffmpeg>=0.6,<1` (MP4/WebM encoding; wheels bundle a static ffmpeg,
  no system install). Pillow is an imaging library, not a UI framework — it does not violate
  `test_no_ui_deps.py`; extend that test's allowlist only if it flags PIL.
- `packages/api`: none new (pillow already pinned there since Phase 3; keep both pins).
- `apps/web`: `@maplibre/maplibre-gl-compare@^0.5.0`. The package may not ship TS types —
  if `pnpm typecheck` fails on the import, add an ambient declaration at
  `apps/web/src/types/maplibre-gl-compare.d.ts` (constructor `(mapA, mapB, container,
  options?: {orientation?: "vertical"|"horizontal", mousemove?: boolean})`, methods
  `setSlider(x: number)`, `remove()`; also import its CSS
  `@maplibre/maplibre-gl-compare/dist/maplibre-gl-compare.css`).

### Settings / directories

No new settings. Render artifacts live at `settings.data_dir / "timelapse" / {render_id}/`
(`frame_0000.png`, …, `movie.{mp4|gif|webm}`, `manifest.json`); mkdir the parent in the
lifespan next to `exports` and `detections`.

### Frame-budget constants (in `core/timelapse.py`)

```python
MAX_FRAMES = 400          # request 422s above this before any EE work
MAX_DIM_VIDEO = 1920      # longest edge, mp4/webm
MAX_DIM_GIF = 720         # Pillow holds every GIF frame in RAM
FRAME_FETCH_WORKERS = 4   # ThreadPoolExecutor width (EE semaphore still gates round-trips)
```

### `core/timelapse.py` — pure layer (stage 1)

```python
StepMode = Literal["interval", "monthly", "quarterly"]

@dataclass(frozen=True)
class FrameWindow:
    index: int
    start: date      # inclusive
    end: date        # inclusive
    label: str       # burned-in date label

def frame_windows(start: date, end: date, *, mode: StepMode,
                  interval_days: int = 16, window_days: int | None = None
                  ) -> list[FrameWindow]: ...
```

Semantics (unit-test each):
- `interval`: window starts every `interval_days` from `start`; each window spans
  `window_days or interval_days` days (so `window_days > interval_days` = rolling overlap);
  the final window is clipped to `end`; label = `"{start} – {end}"` for multi-day windows,
  `"{start}"` when the window is one day.
- `monthly`: calendar months intersecting [start, end], clipped at both ends; label `"YYYY-MM"`.
- `quarterly`: calendar quarters, clipped; label `"YYYY-Qn"`.
- Raise `ValueError` on `end < start`, non-positive intervals, or > `MAX_FRAMES` windows.
  Leap years and month-length edges come free from `datetime.date` arithmetic — test
  Feb + a 31→30 boundary anyway.

Annotation helpers (pure Pillow, offline-tested on synthetic images):
- `scale_bar_spec(bbox: BBox, width_px: int) -> tuple[float, int]` — pick a round km length
  (1/2/5×10ⁿ) spanning ≤ ~25 % of the frame width at the bbox's centre-latitude
  metres-per-pixel (cosine-corrected, same `_M_PER_DEG` convention as `ee/pixels.py`);
  return `(km, px)`. Pure math, unit-tested.
- `render_colorbar(palette: list[str], vis_min: float, vis_max: float, *, width: int,
  height: int) -> PIL.Image` — horizontal gradient strip + min/max tick labels.
- `annotate_frame(img: PIL.Image, *, label: str, attribution: str,
  colorbar: PIL.Image | None, scale_bar: tuple[float, int] | None) -> PIL.Image` —
  composites a translucent dark strip along the bottom: date label left, scale bar centre,
  colorbar + attribution right. Font: `ImageFont.load_default(size=…)` — **no TTF committed**
  (keeps the repo font-license-free); acceptable at 1080 p.

### `core/timelapse.py` — EE + encoding layer (stage 2)

```python
FetchFn = Callable[[str], bytes]     # default: urllib.request (export.py precedent —
                                     # core keeps no HTTP dependency); injectable for tests

@dataclass(frozen=True)
class FrameResult:
    window: FrameWindow
    status: Literal["rendered", "empty", "failed"]
    path: Path | None

def render_frames(dataset: str, product: str, roi: ROI, windows: list[FrameWindow], *,
                  out_dir: Path, max_dim: int, even_dims: bool,
                  vis_min: float | None, vis_max: float | None,
                  annotations: AnnotationOptions, fetch: FetchFn = _fetch_bytes,
                  on_progress: Callable[[int, int], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> FrameManifest: ...

def encode_movie(frame_paths: list[Path], out_path: Path, *, fmt: Literal["mp4","gif","webm"],
                 fps: int) -> None: ...
```

Pinned behaviors (each is a test):
- **One geometry for all frames**: compute `geo_dimensions(bbox, max_dim)` once; when
  `even_dims` (mp4/webm), round W and H **down to even** *before* rendering any frame —
  libx264/yuv420p rejects odd dimensions and every frame must match the movie exactly.
- **One vis range for the whole render** (scientific honesty — no per-frame auto-scale
  flicker): use request `vis_min`/`vis_max` if given, else `compute_vis_range` **once** on
  the middle window's composite and reuse for every frame *and* the colorbar.
- Per frame: build the composite via the existing generic pipeline
  (`build_mean_composite` over the window — same path `services/tiles.build_image` uses for
  `composite="mean"`), mint the thumb URL through `ee/render.thumb_url` (which goes through
  `ee_call`), then HTTP-GET the PNG bytes via `fetch`.
- **Frame status taxonomy**: `EmptyCollectionError` at composite build → `"empty"` (skipped,
  recorded); non-200 or non-PNG fetch response → `"failed"` (recorded, not raised). The run
  raises `JobError` only if **zero** frames rendered. Rendered frames are re-indexed densely
  (`frame_0000.png` = first *rendered* frame) so the movie has no holes; the manifest maps
  movie index → window.
- Concurrency: `ThreadPoolExecutor(FRAME_FETCH_WORKERS)`; results written in window order;
  `on_progress(done, total)` per completed frame; check `should_cancel()` between frames and
  raise `JobError("cancelled")`.
- `manifest.json` (written last, temp-file + `os.replace`): `{render_id?, dataset, product,
  width, height, fps?, format?, vis: [min, max], frames: [{index, start, end, label,
  status}]}`.
- Encoding: `mp4` → `imageio_ffmpeg.write_frames(out, size=(W,H), fps=fps,
  codec="libx264", pix_fmt_out="yuv420p")` feeding raw RGB bytes frame-by-frame;
  `webm` → same with `codec="libvpx-vp9"` (bundled ffmpeg includes libvpx; if the encoder
  errors, surface the ffmpeg stderr in the JobError — don't silently fall back);
  `gif` → Pillow `save(append_images=…, duration=round(1000/fps), loop=0, optimize=True)`.
  Movie written to a temp path in the same dir, then `os.replace` — a cancelled job never
  leaves a half-written gallery item.
- Offline tests: synthetic 8×6 PNGs through a fake `fetch`; assert dense re-indexing,
  even-dim rounding, manifest contents, GIF round-trips through Pillow open, and MP4/WebM
  encode (imageio-ffmpeg works offline — it's a local binary; keep the test frames tiny so
  the suite stays fast). Live `@pytest.mark.ee` test: 2 frames over a small ROI.

### API (stage 3)

**Migration 4** (append to `_MIGRATIONS`; comment that runners write this table off-loop):

```sql
CREATE TABLE renders (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    product     TEXT NOT NULL,
    params_json TEXT NOT NULL,
    roi_json    TEXT NOT NULL,
    status      TEXT NOT NULL,          -- running | succeeded | failed | cancelled
    frame_count INTEGER,
    fps         INTEGER NOT NULL,
    format      TEXT NOT NULL,          -- mp4 | gif | webm
    movie_bytes INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX ix_renders_created_at ON renders (created_at);
```

**Routes** (`routers/timelapse.py` thin, `services/timelapse.py` does the work):

| Route | Behavior |
|---|---|
| `POST /api/timelapse` | validate → job; returns `{job_id, render_id}` |
| `GET /api/timelapse` | gallery list (SQL only), newest first |
| `GET /api/timelapse/{render_id}` | row + parsed `manifest.json` |
| `GET /api/timelapse/{render_id}/frames/{index}` | frame PNG (`FileResponse`; long `Cache-Control` — frames are immutable) |
| `GET /api/timelapse/{render_id}/download` | movie with download name `{dataset}_{product}_{start}_{end}.{ext}` |
| `DELETE /api/timelapse/{render_id}` | 409 while `running`; else delete dir + row |

`TimelapseRequest`: `{title?, dataset, product, roi (required — no global timelapse),
dates: {start, end}, step: {mode, interval_days?, window_days?}, fps (1–30, default 6),
format ("mp4"|"gif"|"webm", default "mp4"), max_dim (default 1080; clamp to MAX_DIM_GIF for
gif), annotations: {date_label=true, colorbar=true, scale_bar=true, attribution?},
vis_min?, vis_max?}`.

Validation order (mirrors `submit_export_geotiff`): catalog resolve (404 unknown; **422 for
`builder=` products** — `CH4_ANOMALY` timelapse is out of scope this phase), ROI 422,
`frame_windows` computed up front (422 on < 2 or > `MAX_FRAMES` windows). Runner (off-loop):
insert `renders` row (`status="running"`) with its own Session (WAL + busy_timeout,
detections precedent), call `render_frames` publishing SSE `frame` events
`{index, status, total}` via `ctx.publish` (live-preview precedent: `points`), then
`encode_movie`, then update the row terminal (`frame_count`, `movie_bytes`, status). Job
result: `{render_id}`. On cancel/failure the row is updated to `cancelled`/`failed` — the
gallery shows honest states, and DELETE cleans up.

Tests: monkeypatch `render_frames`/`encode_movie` by name in `services/timelapse.py`;
migration 4 idempotency; full job round-trip with fakes writing real tiny files; 409 delete
while running; 422 budget/builder/roi cases. `make gen` in the same commit.

### Web — Timelapse Studio (stage 4)

New view in the `App.tsx` switcher (now: Explore, Compare, Methane Lab, Timelapse,
Settings). Components under `features/timelapse/`:

- `TimelapsePage` — left rail: settings form (dataset/product via the catalog hooks the
  explore view uses; ROI from saved AOIs / presets / current explore ROI; dates; step mode +
  interval/window; fps; format; max_dim; annotation toggles; optional fixed vis range) →
  submit → `useJob(jobId)` SSE; frame strip fills in live as `frame` events arrive (thumb =
  the frame endpoint, render only `status==="rendered"` ones). Centre: `FramePlayer`
  preview once done + download button. Bottom or right: `RenderGallery`.
- `FramePlayer` — the transport used by the studio preview *and* stage 5's map overlay:
  preload **all** frame `HTMLImageElement`s before enabling play (progress indicator while
  loading); `requestAnimationFrame` loop advancing an index held in a **ref** (no React
  state per tick); play/pause/seek slider/fps override/loop toggle; renders to a `<canvas>`
  in the studio (map overlay mode instead calls `ImageSource.updateImage` — stage 5).
- `RenderGallery` — TanStack Query over `GET /api/timelapse`; poster = middle rendered
  frame; status chips for failed/cancelled; delete with confirm; click → load into player.
- `timelapseStore` (zustand): form state only; server data stays in TanStack Query.

Vitest: `frame_windows`-mirroring is server-side — web tests cover the transport math
(frame-index advance at fps, preload gating) with fake images, and the store.

### Web — Explore animation transport (stage 5)

`AnimationBar` in the explore view, two modes (plan.md's split):

- **Browse (tiles)**: a date slider over the active layer's date axis. Stepping mints a
  `date_window` composite for the new date via the existing `POST /tiles` path but keeps a
  small pool of **hidden preloaded raster sources at ±2 steps** (mint-ahead on idle,
  evict beyond the pool); the visible swap is `setLayoutProperty(visibility)` between
  already-loaded sources — never a re-mint on the visible layer (no-refetch rule).
  Concurrency: at most 2 mint-ahead requests in flight.
- **Playback (frames)**: pick a finished render whose dataset/product matches the active
  layer (or deep-link the user to the studio to create one). Overlay = MapLibre **image
  source** with `coordinates` `[[w,n],[e,n],[e,s],[w,s]]` (top-left, top-right,
  bottom-right, bottom-left — that order), driven by the `FramePlayer` transport calling
  `getSource(id).updateImage({url: frames[i]})` per tick. All frames preloaded first;
  zero tile churn, zero React re-render per tick.

New hook `map/useImageFrames.ts` (name from plan.md): owns the image source lifecycle +
transport binding. Vitest the pure pieces (coordinate corners from bbox, pool
eviction policy).

### Web — Compare view (stage 6)

New `features/compare/` + `CompareView` in the switcher:

- Two `LabMap` instances side by side in one container; verify `MapContext` is a
  per-instance React provider (as-built check — it should be; if anything module-level
  leaked in, e.g. a singleton re-mint scheduler, scope it per map).
- `@maplibre/maplibre-gl-compare` attached to both maps + container (import its CSS);
  orientation toggle (vertical/horizontal); `.remove()` on unmount. The plugin syncs
  move via `@mapbox/mapbox-gl-sync-move` internally — don't add our own sync.
- `compareStore` (zustand): `{mode: "linked" | "independent", orientation, left, right}`.
  - **linked**: one dataset/product/viz shared, two dates — left = date A, right = date B
    (the classic change-comparison). Changing product updates both sides.
  - **independent**: each side its own layer config (reuse the existing layer-panel
    machinery scoped per side).
- Layers per side use the existing `useMintLayer`/`useRasterLayer`/re-mint scheduler —
  per-map instances. Swipe position is plugin-internal DOM; no React involvement.
- Vitest: store logic (linked-mode fan-out, mode switching preserves configs).

### Parity sweep (stage 7)

Produce `docs/parity-checklist.md`: **sweep `legacy/app` systematically** (every
`st.*` widget/expander in `main.py`, `tabs/*.py`, `wind_overlay.py`, `roi.py`) and list
every user-facing capability with a disposition: **ported** (where), **superseded** (by
what), or **dropped** (why). No silent gaps. Pre-enumerated from the planning sweep —
verify each against the as-built tree, don't trust this list blindly:

| Legacy feature | Expected disposition |
|---|---|
| Gas/index/raw-band/RGB/S1 layers | ported — catalog builtins (Phase 1) |
| ROI presets, drawn ROIs | ported — presets + terra-draw (Phase 1/2) |
| Scale settings incl. auto-range | **verify**: `compute_vis_range` exists in core; if the explore UI has no auto-range control, close the gap here (smallest honest surface, e.g. `auto_range` flag on `TilesRequest` → legend range in response) |
| Temporal animation (both variants) | superseded — Phase 4 animation transport |
| Compare (abandoned v1 tab) | superseded — Phase 4 Compare view |
| Vegetation/water masking toggles (methane quicklook) | dropped — superseded by the Methane Lab physics suite; core `masking.py` retained for future products |
| Source classification layer | dropped — same rationale; document explicitly |
| Export image (PNG/GeoTIFF) | ported — Phase 2 export |
| Batch export by period | dropped — superseded by Timelapse Studio (visual) + timeseries CSV (numeric); note in backlog if anyone misses batch GeoTIFF |
| Wind arrows overlay | ported — Phase 2 `WindOverlay` |
| Statistics tab | ported — `StatsCards` |
| Time-series + rolling smooth | ported — `ChartPanel`/`SeriesChart`; **verify** smoothing is exposed |
| CSV download | ported — Phase 2 export CSV |

Anything discovered in the sweep that is missing, small, and genuinely wanted → fix in this
stage (keep each fix minimal); anything larger → explicit "dropped/backlog" line, never a
silent omission. The checklist ships in the same commit as the fixes.

### Retire Streamlit (stage 8) — ONE commit, `repo:` prefix

- `git rm -r legacy/`.
- Root `pyproject.toml`: drop `exclude = ["legacy"]` (workspace), `extend-exclude`
  (ruff), `exclude` (mypy) legacy entries.
- `Makefile`: drop the `legacy` target (+ `.PHONY` entry).
- `CLAUDE.md`: remove the `make legacy` line and the `legacy/` architecture bullet
  (and the "frozen v1" sentence in the header).
- Sweep for stragglers: `rg -i "legacy|streamlit" --glob '!docs/plan.md' --glob '!docs/roadmap.md'`
  (plan/roadmap keep their historical mentions).
- **README.md rewritten** for v2: what OpenEarth is (one paragraph), stack summary
  (core/api/web split, the EE-for-reduction/NumPy-for-physics principle), quickstart
  (`uv sync --all-packages`, EE auth pointer, `make dev`), the Methane Lab in two
  sentences with a pointer to `docs/methane_methods.md`, feature overview (explore,
  compare, timelapse, methane), pointers to `docs/architecture.md` + `docs/roadmap.md`.
  Screenshots optional — placeholder section is fine.

### Exit verification + docs sweep (stage 9)

- Full `make check`; `pnpm --dir apps/web lint && typecheck && test -- --run`; `make gen`
  no-drift; one `OPENEARTH_EE_TESTS=1 uv run pytest -m ee` sweep.
- Playwright golden paths (dev servers running): (a) Compare — linked mode, two dates,
  swipe moves; (b) Timelapse — tiny live render (2 frames, small ROI, low dim), watch SSE
  frames arrive, play preview, download link 200s; (c) gallery delete removes the card.
- Docs: `architecture.md` "Built in Phase 4" section; `roadmap.md` Phase 4 ✅ + as-built
  one-liner; `plan.md` header line; `CLAUDE.md` terse updates (timelapse module map,
  migration 4, new routes, compare/timelapse views).

---

## Deviations from plan.md (deliberate, with rationale)

| Deviation | Rationale |
|---|---|
| No react-router: Compare/Timelapse are switcher views, not `/compare`, `/timelapse` routes | Phase 2/3 precedent (state-based switcher in `App.tsx`); plan.md's route list predates that decision |
| Frames fetched in core via `urllib` with an injectable `FetchFn` | `export.py` precedent — core keeps zero HTTP deps; httpx stays an API-layer concern |
| `imageio-ffmpeg` used directly (`write_frames`), not `imageio` | one dependency fewer; imageio's GIF writer is Pillow underneath anyway, so GIF goes straight to Pillow |
| Gallery table named `renders`, runner writes it off-loop | plan.md names no table; detections precedent for worker-thread domain writes |
| One fixed vis range per render (explicit or computed once) | per-frame auto-range makes physically meaningless flicker; the colorbar must describe every frame |
| Annotation font = Pillow's bundled bitmap font at size | no committed TTF → no font licensing in the repo; fine at 1080 p |
| Browse-mode preload capped at ±2 steps, 2 mints in flight | plan.md's "±N" unbounded pool would hammer getMapId quota and browser memory |
| Wind particle layer NOT in this phase | roadmap places it in Phase 6 polish; plan.md lists it as "polish phase" too |
| Batch GeoTIFF export from legacy not ported | superseded (timelapse for visual sequences, timeseries CSV for numbers); recorded in the parity checklist, backlog if missed |
| `CH4_ANOMALY` timelapse refused (422) | builder products need the methane_ref unlock; compositing physics quicklooks over arbitrary windows is Phase 5+ territory if ever |

---

## Pitfalls (read before coding)

- **Even dimensions for video**: yuv420p/libx264 rejects odd W or H. Round the
  `geo_dimensions` result down to even *before* rendering frame 0 — resizing frames after
  the fact would resample annotations.
- **Frame/manifest atomicity**: movie and manifest via temp-file + `os.replace`; a
  cancelled or crashed job must not leave a gallery item that 200s on `/download` with a
  truncated file (Phase 3 temp+rename precedent).
- **Distinguish empty from failed frames**: `EmptyCollectionError` at composite build =
  "empty" (fine, e.g. a cloudy month with zero scenes); a non-200/non-PNG thumb fetch =
  "failed". Only an all-unusable run raises. Dense re-indexing of rendered frames — the
  movie must not contain holes, and the manifest maps movie index → date window.
- **getThumbURL limits**: stay ≤ 1920 longest edge; EE rejects oversized thumb requests.
  The URL mint goes through `ee_call` (retries quota); the byte fetch does NOT retry —
  record "failed" and move on.
- **ImageSource corner order** is top-left, top-right, bottom-right, bottom-left —
  `[[w,n],[e,n],[e,s],[w,s]]`. Wrong order renders mirrored, not an error.
- **Preload before play**: start the rAF transport only when every `HTMLImageElement` has
  fired `load` — otherwise the first loop stutters and `updateImage` may race.
- **Two maps double the load**: re-mint scheduling, wind overlay, inspector must all be
  per-map-instance. If anything in `map/` is module-scoped (check `remintScheduler`
  usage), scope it per map in stage 6 *before* wiring compare.
- **maplibre-gl-compare lifecycle**: construct after both maps' `load` events; call
  `.remove()` on unmount or the sync handlers leak across view switches.
- **GIF memory**: Pillow holds all frames in RAM to write a GIF — that's why
  `MAX_DIM_GIF = 720`; also warn (422) when `gif` × frame count > ~200 frames.
- **SQLite discipline**: the runner touches only `renders` (own Session per write, WAL +
  busy_timeout); the `jobs` table stays event-loop-only. Copy the detections runner shape,
  don't invent a new one.
- **`make gen` in the same commit** as any schema change (stage 3), or CI's drift check
  fails the next commit.
- **Deleting `legacy/`**: the uv workspace `exclude`, ruff/mypy excludes, and the Makefile
  target all reference it — remove them in the same commit or `make check` breaks on a
  path that no longer exists.
