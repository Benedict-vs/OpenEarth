<!-- docs/phase2-execution-plan.md — Phase 2 (Analysis backbone) execution plan.
     Written 2026-07-05 against the Phase-1-complete tree (branch v2/phase1-map-mvp).
     Decisions in here are made deliberately within docs/plan.md's settled architecture;
     implement within them. Where this doc refines or deviates from plan.md, the
     "Deviations" section says so explicitly. -->

# Phase 2 — Analysis backbone: execution plan

**Goal (roadmap exit criterion):** a 1-year S5P + S2-index series over a polygon streams
progress and lands < 30 s warm; GeoTIFF opens georeferenced in QGIS.

**Branch:** `v2/phase2-analysis`, cut from `v2/phase1-map-mvp` (rebase onto main once the
Phase 1 PR merges). One commit per stage below, prefixed `core:` / `api:` / `web:` as in the
existing history. After **every** stage: `make check` and (for stages touching the API
schema) `make gen` — CI diff-checks `openapi.json` / `types.gen.ts` drift.

**Standing rules (do not re-derive):**
- All blocking EE round-trips through `ee_call()` (`packages/core/src/openearth/ee/client.py`).
- `create_app()` stays EE-free and now also DB-free at creation time; everything
  environment-dependent happens in the lifespan (`app.py` pattern).
- One diskcache tier; keys via `cache_key()` in `openearth_api/cache.py`; tile URLs never
  cached; TTL via `ttl_for(end_date)`.
- Offline tests fake EE by monkeypatching core fns **imported by name** into
  `openearth_api/services/*` (see `packages/api/tests/test_tiles.py` for the pattern).
- Web: no-refetch rule for layer controls; `src/api/types.gen.ts` is generated, never edited.
- mypy strict; if a new core module wraps EE chains, scope `warn_return_any = false` for it
  in the root `pyproject.toml` like the existing provider modules — don't blanket-disable.

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 1 | DB layer + job manager + `/jobs` + SSE | api | L | — |
| 2 | Timeseries v2 engine | core | M | — |
| 3 | `/timeseries` endpoints + parquet cache | api | M | 1, 2 |
| 4 | Chart panel (ECharts) + stats cards + `useJob` SSE hook | web | L | 3 |
| 5 | Pixel inspector | api + web | S | 3, 4 |
| 6 | Exports: `ee/pixels.py`, `export.py`, `/export/*`, ExportDialog | core + api + web | L | 1 |
| 7 | Wind: field sampler, `/wind*`, arrow overlay | core + api + web | M | — (routes reuse 1's patterns) |
| 8 | Saved AOIs + workspaces | api + web | M | 1 (DB layer) |
| 9 | Hardening, docs, exit verification | all | S | all |

Stages 2, 6, 7 are independent of each other; if parallelizing sessions, 1 must land first
(6/7/8 reuse its DB/job/router patterns), and 3→4→5 is a strict chain.

---

## Pinned contracts

These are the interfaces Phase 3 will consume — get them right, change them reluctantly.

### New dependencies

- `packages/api`: `sqlmodel>=0.0.22,<1`, `sse-starlette>=2.1,<3`, `pyarrow>=17`.
- `packages/core`: `rasterio>=1.4,<2` (GeoTIFF assembly; add `rasterio.*` to mypy
  `ignore_missing_imports`).
- root dev group: `pytest-asyncio` (job-manager unit tests; explicit `@pytest.mark.asyncio`,
  no auto mode).
- `apps/web`: `echarts` (import from `echarts/core` with explicit component registration —
  keep the bundle lean; no `echarts-for-react`).

### Settings

No new env vars. Derived paths: DB at `settings.data_dir / "openearth.db"`, exports at
`settings.data_dir / "exports"` (mkdir in lifespan). Timeseries chunk size and worker count
are module constants in core (`CHUNK_DAYS = 90`, pool size = `ee_max_concurrency`).

### Database (`openearth_api/db.py` + `models.py`)

SQLite via SQLModel, WAL mode, `PRAGMA user_version` migrations — a list of DDL scripts
applied in order; version = index reached. Alembic stays out (plan.md).

```
migration 1 (this phase, stage 1):  jobs
migration 2 (this phase, stage 8):  aois, workspaces
(Phase 3 will add sites, detections, reference_events as migration 3+.)
```

`jobs` row: `id TEXT PK` (uuid4 hex), `kind TEXT` ("timeseries" | "export_geotiff" | …),
`status TEXT` (`queued|running|succeeded|failed|cancelled|interrupted`), `params_json TEXT`,
`result_json TEXT NULL` (small JSON — e.g. `{"cache_key": …}` or `{"filename": …}`, never
bulk data), `error TEXT NULL`, `progress_done INT`, `progress_total INT`,
`message TEXT NULL`, `created_at`, `started_at NULL`, `finished_at NULL`.

**Write discipline:** the SQLite writer is the event-loop side of the job manager only.
Worker threads never touch the DB; they emit events through the thread-safe `JobContext`.
This makes WAL + a single `Session` factory sufficient — no cross-thread session juggling.

### Job manager (`openearth_api/jobs.py`)

In-process, on `app.state.jobs`, started/stopped in the lifespan. On startup, any persisted
`running`/`queued` rows → `interrupted` (restart visibility, per plan.md).

```python
class JobContext:            # handed to the runner, safe to call from worker threads
    cancelled: threading.Event
    def progress(self, done: int, total: int, message: str | None = None) -> None: ...
    def publish(self, event: str, data: dict[str, Any]) -> None: ...   # e.g. "points"

Runner = Callable[[JobContext], dict[str, Any] | None]   # return value → result_json

class JobManager:
    async def submit(self, kind: str, params: dict[str, Any], runner: Runner) -> str: ...
    def get(self, job_id: str) -> JobRow | None: ...
    async def cancel(self, job_id: str) -> None: ...          # sets ctx.cancelled
    def subscribe(self, job_id: str) -> AsyncIterator[tuple[str, dict]]: ...
```

Mechanics: `submit` persists the row (`queued`), then schedules an `asyncio.Task` that runs
the runner via `asyncio.to_thread` (EE concurrency is already bounded by the core semaphore;
additionally cap concurrently *running* jobs with `asyncio.Semaphore(4)` so a stack of
export jobs can't starve interactive tile mints). `JobContext.publish/progress` post onto an
`asyncio.Queue` via `loop.call_soon_threadsafe`; the manager's per-job consumer fans events
out to subscribers and persists progress (throttle DB writes to ≥ 250 ms apart; always
persist terminal states). Cancellation is cooperative: `DELETE` sets the event, runners
check it at chunk boundaries; if the runner raises after the event is set, status is
`cancelled` (not `failed`) regardless of exception type. `JobError` (already in
`openearth.errors`) → `failed` with its message as `error`; unexpected exceptions → `failed`
with the classified EE message where applicable (`classify_ee_error`).

### SSE wire format (`GET /api/jobs/{id}/events`)

`sse-starlette` `EventSourceResponse`, ping every 15 s. Named events, JSON `data`:

```
event: progress   data: {"done": 3, "total": 5, "message": "chunk 2019-04-01…2019-06-29"}
event: points     data: {"points": [{"date": "2019-04-02", "value": 0.61, "count": 812}, …]}
event: done       data: {"status": "succeeded", "result": {…result_json…}}
event: error      data: {"status": "failed", "detail": "…"}        # also for cancelled
```

Subscribe semantics: if the job is already terminal, emit the single terminal event and
close. Otherwise emit one `progress` snapshot, then live events; close after terminal.
**`points` events are a progressive preview only and are not persisted or replayed** — on
EventSource reconnect (or late attach) intermediate `points` are gone, so the client MUST
fetch the full result via the result endpoint upon `done`. This rule keeps the manager
generic and the memory footprint flat; bake it into `sse.ts` (stage 4), not into user code.

SSE payloads don't appear in OpenAPI — hand-write their TS types in `src/api/types.ts`
(the existing home for hand-curated aliases), next to a comment pointing here.

### New/changed API surface (all under `/api`, `make gen` after each stage)

| Route | Stage | Notes |
|---|---|---|
| `GET /jobs/{id}`, `GET /jobs/{id}/events`, `DELETE /jobs/{id}`, `GET /jobs?limit=` | 1 | list endpoint is trivial and Phase 4's timelapse gallery wants it |
| `POST /timeseries` → `{job_id}`; `GET /timeseries/{job_id}/result?format=json\|csv\|parquet` | 3 | ROI **required**; RGB products 422 |
| `POST /inspect` | 5 | point value of the current composite |
| `POST /export/geotiff` → `{job_id}`; `GET /export/{job_id}/download`; `POST /export/png` (sync stream) | 6 | CSV export = timeseries result format |
| `GET /wind?lat&lon&time`; `GET /wind/field?west&south&east&north&time&nx&ny` | 7 | diskcached, `ttl_for(time)` |
| `GET/POST/DELETE /aois`; `GET/POST/PUT/DELETE /workspaces` | 8 | |

EE-touching routes depend on `deps.ensure_ee` exactly like `tiles.py` does today. Job
submission routes validate + resolve catalog/ROI **before** submitting (422/404 at request
time, not inside the job).

---

## Stage 1 — DB layer + job manager + `/jobs` + SSE (api)

Files: `db.py`, `models.py`, `jobs.py`, `routers/jobs.py` (new); `app.py`, `deps.py`
(`get_jobs`, `get_db`), `schemas.py` (`JobOut`, `JobCreated`), `errors.py` (nothing new —
`JobError` mapping to 500 with detail already fits the taxonomy handler; verify).

Lifespan order: settings → catalog dir → cache → **engine + migrate + interrupted-sweep →
JobManager start** → EE attempt; teardown in reverse (stop manager: cancel active contexts,
await tasks with a 5 s grace, persist `interrupted` for stragglers).

Tests (`packages/api/tests/test_jobs.py`):
- manager unit tests via `pytest-asyncio` + `Settings(_env_file=None, data_dir=tmp_path)`:
  happy path (progress → done, row persisted), failure (`JobError` → failed), cancel
  mid-run (runner blocked on an event, checks `ctx.cancelled`), publish fan-out to two
  subscribers, late subscribe on a finished job gets exactly the terminal event.
- interrupted-sweep: pre-insert a `running` row, boot the app (TestClient context), assert
  `interrupted`.
- SSE over HTTP: `httpx.AsyncClient(transport=ASGITransport(app=app))` streaming a
  fake instant job (submit directly on `app.state.jobs` inside the test); assert event
  names/ordering. TestClient's sync portal can't consume infinite streams cleanly — use
  the async client for this one.

Commit: `api: SQLite layer + in-process job manager with SSE`.

## Stage 2 — Timeseries v2 engine (core)

File: `packages/core/src/openearth/timeseries.py` (+ tests). Pure parts separated from the
one EE-touching function so they're offline-testable:

- `chunk_ranges(start: date, end: date, max_days: int = 90) -> list[tuple[date, date]]` —
  half-open chunks covering [start, end); pure.
- `aggregate_daily(rows: list[SceneValue]) -> pd.DataFrame` — per-scene `(timestamp, value,
  count)` → daily rows: value = count-weighted mean of scene means, count = sum, indexed by
  UTC date; drops None-valued scenes; pure.
- `daily_timeseries(data_key, source, roi, start, end, *, scale_m=None, on_chunk=None,
  cancel=None) -> pd.DataFrame` — the engine. Per chunk: `get_collection(...)` (the existing
  dispatcher in `openearth.providers`), map each image to a Feature with
  `system:time_start` and `reduceRegion(Reducer.mean().combine(Reducer.count(), sharedInputs=True),
  roi.to_ee_geometry(), scale=scale_m, bestEffort=True, maxPixels=1e8)` of the product band,
  then **one** `ee_call(fc.getInfo)` per chunk. Chunks run in
  `ThreadPoolExecutor(get_settings().ee_max_concurrency)`; the shared EE semaphore arbitrates
  against tile mints (plan.md, settled). As each chunk completes:
  `on_chunk(done, total, df_chunk)`. `cancel: threading.Event` checked before dispatching
  and between completions; when set, raise `JobError("cancelled")` — the manager overrides
  status to `cancelled` because the context flag is set.
- `scale_m` default: the dataset's `default_scale_m`; the coarse pass passes 4× that.
  Empty chunks (EmptyCollectionError or zero features) contribute zero rows — an entirely
  empty result is **not** an error (a flat "no data" series is honest output; the API layer
  reports point count).

RGB products are refused here with `ValueError` (scalar band required) — the API maps it to
422 in stage 3.

Tests (`packages/core/tests/test_timeseries.py`): chunk boundary cases (exact multiples,
single day, leap year); weighted daily aggregation vs hand-computed values; None dropping;
engine end-to-end offline by monkeypatching `get_collection` + `ee_call` **in the
`openearth.timeseries` namespace** (fake FeatureCollection payloads) — assert chunk count,
`on_chunk` ordering args, final frame; cancel between chunks raises and stops dispatch.

Commit: `core: timeseries v2 engine — chunked, concurrent, progress-reporting`.

## Stage 3 — `/timeseries` endpoints + parquet cache (api)

Files: `services/timeseries.py`, `routers/timeseries.py`; `schemas.py` additions
(`TimeseriesRequest {dataset, product, roi: RoiIn, dates: DateRangeIn, scale:
Literal["coarse","native"] = "native"}`, `JobCreated {job_id}`, `TimeseriesResultOut
{points, unit, display_scale, scale_m, band}`).

Service flow (`POST /timeseries`):
1. Resolve catalog + ROI (reuse `services.tiles.resolve_catalog` / ROI conversion; RGB →
   422; `validate_date_range`).
2. Compute `key = cache_key("timeseries", dataset=…, product=…, roi=roi_key_part(roi),
   dates=…, scale_m=effective_scale)`.
3. Submit a job whose runner: on cache hit, publishes all cached points as one `points`
   event and returns `{"cache_key": key, "cached": True}` (uniform client flow, ~ms);
   on miss, runs `daily_timeseries` wiring `on_chunk` → `ctx.progress` + `ctx.publish
   ("points", …)`, then stores the DataFrame as parquet **bytes inside the existing
   diskcache** (`df.to_parquet(BytesIO)`; `expire=ttl_for(end)`) and returns
   `{"cache_key": key}`. One cache tier preserved — no side directory of parquet files.

`GET /timeseries/{job_id}/result?format=`: 404 unknown job, 409 while not `succeeded`,
410 if the cache entry was evicted (message: re-run the series). `json` →
`TimeseriesResultOut`; `csv` → `text/csv` attachment (`date,value,count` + unit header
comment); `parquet` → bytes passthrough, `application/vnd.apache.parquet`.

Tests: monkeypatch `daily_timeseries` by name in `services/timeseries.py` (canned frame,
fires `on_chunk` twice) — full flow: POST → SSE shows `progress`+`points`+`done` → all three
result formats parse; second POST with identical params → runner hits cache (assert the
fake engine ran exactly once via call counter); RGB 422; result-before-done 409.

Run `make gen`; commit `api: timeseries jobs — SSE progress, parquet-in-diskcache results`.

## Stage 4 — Chart panel + stats cards + SSE hook (web)

Files: `src/api/sse.ts`; `src/stores/analysisStore.ts`; `src/features/explore/ChartPanel.tsx`,
`SeriesChart.tsx`, `StatsCards.tsx`; `src/lib/series.ts` (pure helpers); `ExplorePage.tsx` +
`index.css` layout change (bottom drawer under the map, collapsible; map flexes).

- `sse.ts`: `subscribeJob(jobId, handlers): () => void` over native `EventSource`
  (same-origin `/api/...` — the vite proxy covers dev; **checkpoint**: verify events arrive
  unbuffered through the proxy with `curl -N` and a browser test; if buffered, give the
  `/api` proxy entry an explicit config disabling compression). Terminal event → close.
  Per the contract: on `done`, the subscriber refetches the full result; `points` events
  only feed the live preview.
- `analysisStore` (zustand): `open`, `layerId` (defaults to topmost visible ready layer),
  `coarse: Map<date, Point>`, `fine: Map<date, Point>`, `fineJobId`, `status`, actions.
  "Run" fires the coarse job (`scale: "coarse"`) and the native job concurrently; render
  rule (pure fn in `lib/series.ts`, tested): fine value where present, else coarse — fine
  chunks visibly replace the preview as they land.
- `SeriesChart.tsx`: thin imperative ECharts wrapper (init once on a ref, `setOption` on
  data change, dispose on unmount, `ResizeObserver`). `dataZoom` slider + inside, tooltip
  with date/value/count, optional 7-day centered rolling-mean overlay line
  (`rollingMean(points, 7)` in `lib/series.ts`), raw daily line beneath.
- `StatsCards.tsx`: computed client-side from the displayed series (`seriesStats` in
  `lib/series.ts`): n days, mean ± σ, min/max with dates, linear trend per year
  (least-squares slope), coverage (n / days-in-range). No server endpoint — deliberate,
  see Deviations.
- Export CSV button: plain `<a href={/api/timeseries/${fineJobId}/result?format=csv}>`
  once the fine job succeeds.

Vitest: `rollingMean`, `seriesStats`, coarse/fine merge, sse.ts event dispatch with a mocked
EventSource. Then drive it in a real browser via the Playwright MCP: polygon over a small
region, NDVI layer, run — coarse series appears near-instantly, daily fills in chunk-wise,
brush/zoom works, stats update, CSV downloads.

Commit: `web: ECharts series panel — progressive SSE fill, stats cards`.

## Stage 5 — Pixel inspector (api + web)

- API: `POST /inspect` — body = the `TilesRequest` composite fields minus viz, plus
  `lon`, `lat`. Service reuses `services.tiles.build_image`, then samples the band at the
  point: `reduceRegion(Reducer.first(), ee.Geometry.Point, scale=native)` through `ee_call`.
  Response `{value: float | null, band, unit, display_scale}` (`null` = masked pixel —
  render as "no data", not an error). Same 404/422 semantics as tiles.
- Web: crosshair toggle button on the map (`map/useInspector.ts` + a small popup component).
  Click → topmost visible ready layer's current mint params (`buildTilesRequest` reuse) +
  lonlat → POST → MapLibre popup with `value × display_scale` + unit + a "series here"
  button that sets the ROI-for-analysis to a small bbox around the point (±10 × native
  scale, via `boundsToBBox`) and opens the ChartPanel coarse run — mini time series with
  zero new backend surface. Cursor state and toggle live outside React render loops
  (imperative, matching `useTerraDraw`).

Tests: service test with monkeypatched sampler; 422 on RGB stays allowed here (RGB inspect
returns first-band? No — keep symmetric: RGB → 422 "pick a scalar product"). `make gen`.

Commit: `api+web: pixel inspector — point sample + mini-series reuse`.

## Stage 6 — Exports (core + api + web)

Core, `ee/pixels.py` (pulled forward from Phase 3 — export needs it; Phase 3 retrieval
chips will reuse it unchanged):
- `GridSpec` (frozen): `crs="EPSG:4326"`, affine `(xscale, 0, x0, 0, -yscale, y0)`, width,
  height. `grid_for(bbox, scale_m)` — degrees-per-pixel from `scale_m` with cosine
  correction at the bbox center latitude; pure, unit-tested against hand values.
- `tile_windows(spec, max_px=1024)` — list of (row, col, window) covering the grid; pure.
- `fetch_pixels(image, spec, bands) -> np.ndarray` — `ee.data.computePixels` per window
  through `ee_call` (`fileFormat="NUMPY_NDARRAY"`, `grid={dimensions, affineTransform,
  crsCode}`), assembled into one `(H, W, B)` float32 array. Size guard **before** any
  request: refuse > 6 bands or an estimated single-window payload > 48 MB
  (`RetrievalError`… no — `ValueError`; RetrievalError is methane-specific). Verify the
  exact `computePixels` request shape against the pinned `earthengine-api>=1.7` at
  implementation time — the plan assumes the modern dict API accepting an `ee.Image`
  expression directly.

Core, `export.py`:
- `estimate_bytes(spec, n_bands)`; `export_geotiff(image, product_spec, roi, scale_m,
  dest: Path) -> Path`: small (≤ 32 MB estimated) → `ee/render.download_url` fast path
  (fetch with `urllib.request` — core takes no httpx dep); large → `fetch_pixels` windows →
  `rasterio` write (GTiff, CRS 4326, transform from `GridSpec`, `nodata=nan`,
  per-window `write` so memory stays bounded). Optional `on_progress(done, total)` per
  window — wired to job progress.
- PNG and CSV need no core code (thumbnail pipeline / timeseries result respectively).

API: `routers/export.py` + `services/export.py`. `POST /export/geotiff` (body =
`TilesRequest` minus viz + optional `scale_m`, default dataset native) → validates, builds
the image via `services.tiles.build_image`, submits a job writing to
`data_dir/exports/{job_id}.tif`; result `{"filename": …}`. `GET /export/{job_id}/download`
→ `FileResponse` with a descriptive download name (`{dataset}_{product}_{start}_{end}.tif`),
404/409 semantics as in stage 3. `POST /export/png` → synchronous: reuse the thumbnail
service at up to 2048 px with `Content-Disposition: attachment` (it's one server-rendered
fetch — a job would be ceremony).

Web: `ExportDialog.tsx` in the explore feature (button in the LayerPanel row): format
GeoTIFF / PNG / CSV; GeoTIFF shows job progress (window count) via `subscribeJob`, then a
download link; PNG is a direct link; CSV points at the chart panel's export (needs a run
series — say so in the dialog).

Tests: grid math vs hand-computed values at lat 0/49/70; window tiling exact cover;
assembly round-trip with a faked `computePixels` returning synthetic gradients — write via
rasterio to a temp file, re-open, assert CRS/transform/values (this is the offline proxy for
"opens in QGIS"); size-guard refusal; API route with `export_geotiff` monkeypatched. Live EE
(`@pytest.mark.ee`): tiny Heidelberg NDVI GeoTIFF via the fast path — rasterio opens it,
CRS EPSG:4326, data non-empty.

Run `make gen`; commit `core+api+web: exports — computePixels GeoTIFF assembly, PNG, CSV`.

## Stage 7 — Wind (core + api + web)

Core (`methane/wind.py` — extend, don't fork; the existing conventions/tests stay green):
- Refactor the bracketing-images selection out of `sample_wind_at` into a private helper
  (behavior-preserving — existing tests must not change).
- `sample_wind_field(bbox, when, nx, ny, *, collection_id, fallback_collection_id) ->
  WindField {when, bbox, nx, ny, u, v}` (row-major from NW corner): build the
  time-interpolated u/v image server-side (`img_before.multiply(1-w).add(img_after
  .multiply(w))`), reduce over an nx×ny FeatureCollection of cell rectangles
  (`Reducer.mean()`, `reduceRegions`) — **one** `getInfo`. Guard `nx, ny ≤ 50`. Fully-masked
  cells → NaN; a fully-NaN field triggers the fallback collection like `sample_wind_at`.
  Cell-center/grid math is a pure function with tests.
- The global-ERA5 fallback collection id: plan.md names ERA5-Land as primary; **verify the
  global hourly id (`ECMWF/ERA5/HOURLY` expected) in the EE catalog at implementation** and
  pin it as a module constant next to `ERA5_LAND_HOURLY_ID`.

API: `routers/wind.py` — `GET /wind?lat&lon&time` (point: `sample_wind_at` over a ±0.05°
bbox; response mirrors `WindSample`) and `GET /wind/field?west&south&east&north&time&nx&ny`
(defaults nx=24, ny from aspect). Both `ensure_ee`, both diskcached
(`cache_key("wind_point"/"wind_field", …)`, `expire=ttl_for(time.date())` — ERA5's ~5-day
latency makes near-present queries short-lived, which `ttl_for` already handles).

Web: `map/WindOverlay.tsx` — a `<canvas>` over the map (pointer-events: none), arrows at
grid points projected with `map.project()`, length/alpha scaled by speed (clamped 8–28 px),
redrawn on `move`/`zoom` and data change; TanStack Query keyed on viewport bounds rounded to
2 dp + the active date. Toggle in a new side-panel "Wind" section. Time semantics: single
mode → `targetDate` 12:00 UTC; range mode → `dates.end` 12:00 UTC; label the overlay with
the sampled instant so it's honest (overpass-matched wind is a Phase 3 concern; this is
browsing weather context).

Tests: grid/cell-center math; API route with `sample_wind_field` monkeypatched; live EE:
small field over the Permian Basin on a historical date, all finite. `make gen`.

Commit: `core+api+web: wind — field sampler, endpoints, canvas arrow overlay`.

## Stage 8 — Saved AOIs + workspaces (api + web)

Migration 2: `aois(id, name UNIQUE, roi_json, created_at)`, `workspaces(id, name UNIQUE,
state_json, created_at, updated_at)`.

API: `routers/aois.py`, `routers/workspaces.py` (no EE, no jobs — plain CRUD; 409 on
duplicate name). Workspace `state` is a **versioned** pydantic schema, `WorkspaceState
{v: Literal[1], layers: [{dataset, product, label, opacity, visible, viz_overrides}], roi:
RoiIn | null, date: {mode, start, end, target_date, half_window_days}, wind: bool}` —
version field so Phase 3+ can migrate shapes without guessing.

Web: RoiToolbar gains "Save AOI…" (name prompt) and a "Saved" group in the presets
dropdown (apply / delete). Header `WorkspaceMenu`: Save as… / Update / Load / Delete.
`applyWorkspace(state)` is a pure-ish orchestration fn (tested): clears the layer stack,
seeds stores, then re-adds layers through `addLayer` + patches — minting flows through the
existing `useMintLayer` reaction, no new mint path.

Tests: CRUD round-trips on tmp DB via TestClient; duplicate-name 409; state schema
round-trip incl. rejection of unknown `v`; vitest for `applyWorkspace` store effects.
`make gen`; commit `api+web: saved AOIs + versioned workspaces`.

## Stage 9 — Hardening, docs, exit verification

- Docs: `architecture.md` "Built in Phase 2" section; `roadmap.md` Phase 2 ✅ with the
  as-built one-liner; plan.md header line; CLAUDE.md architecture bullets (timeseries,
  jobs/SSE, pixels/export, new tables) — keep terse.
- Exit criteria, measured not asserted:
  1. Scripted timing (scratch script hitting the API): 1-year S5P NO₂ **and** S2 NDVI
     series over a real polygon ROI — record cold and warm wall-clock; warm must be < 30 s
     (cached repeat should be ~instant; "warm" = EE-warmed second run of a new range).
  2. GeoTIFF: export a mid-size ROI (forcing the computePixels path at least once) and a
     small one (fast path); verify with rasterio (CRS/transform) **and open in QGIS
     manually** — georeferenced position over basemap correct.
  3. E2E golden path via Playwright MCP: draw polygon → NDVI layer → run series (watch the
     coarse→fine fill) → export CSV → save workspace → reload app → load workspace →
     everything returns.
  4. Full suite: `make check`, `pnpm --dir apps/web lint && typecheck && test -- --run`,
     plus one `OPENEARTH_EE_TESTS=1 uv run pytest -m ee` live sweep.

Commit: `docs+tests: Phase 2 hardening and exit verification`.

---

## Deviations from / refinements of plan.md (deliberate)

| Decision | Rationale |
|---|---|
| `ee/pixels.py` built now, not Phase 3 | `export.py` needs it; Phase 3 retrieval chips reuse it unchanged |
| Stats cards computed client-side; no `/stats` endpoint | series data is already in the browser; zero extra EE round-trips; server stats would duplicate logic Phase 3 doesn't need |
| Progressive delivery = `points` SSE events (preview) + mandatory result refetch on `done` | keeps the job manager generic and reconnect semantics trivial; no replay buffer |
| Parquet cache = parquet **bytes in the existing diskcache**, not a parquet file tree | preserves the settled "one cache tier" rule, inherits LRU/size caps and `ttl_for` |
| `POST /export/png` synchronous, not a job | it's one thumbnail fetch; jobs are for multi-step work |
| Timeseries requires an ROI | a global reduceRegion series is unbounded compute for no use case |
| Pixel inspector mini-series reuses `/timeseries` with a small bbox | one engine, one cache, no bespoke endpoint |
| Workspaces get `PUT` (update) and a versioned state schema | "Update" is table stakes UX; `v` field future-proofs Phase 3 additions |
| Wind overlay samples a labeled fixed instant (noon UTC of the active date) | overpass matching needs a scene context that Explore doesn't have; honesty via labeling — Phase 3's Methane Lab does it properly |

## Implementation pitfalls (read before coding)

- **SSE through the vite proxy**: verify unbuffered delivery early (curl -N + browser);
  sse-starlette's ping keeps intermediaries from timing out the stream.
- **EventSource auto-reconnects** re-hit the endpoint: the terminal-replay behavior above
  makes that harmless, but never assume `points` continuity — the `done`-refetch rule.
- **SQLite threading**: only the event-loop side writes; WAL on; one engine per app. Don't
  hand `Session`s into `asyncio.to_thread` runners.
- **Don't starve tile mints**: timeseries chunks + tile mints share the core EE semaphore
  by design; the job-level `asyncio.Semaphore(4)` is the only extra brake. Resist adding
  per-feature pools.
- **`computePixels` API shape**: check against the installed `earthengine-api` before
  writing tests; the request dict format changed across releases.
- **mypy strict**: new EE-chain modules (`timeseries`, `pixels`, wind additions) likely
  need the scoped `warn_return_any = false` treatment in root `pyproject.toml` — scope per
  module, mirroring the provider modules.
- **`make gen` in the same commit** as any schema change — CI diffs the artifacts.
- **No bare `getInfo`** — every new EE round-trip goes through `ee_call` (grep before each
  commit: `rg "getInfo\(\)" packages | rg -v ee_call`).
- **Existing wind tests are a contract**: the stage-7 refactor of `sample_wind_at` must not
  touch `test_wind.py`.
