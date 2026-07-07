<!-- docs/phase6-execution-plan.md — Phase 6 (EMIT + Embeddings + products v1) execution plan.
     Written 2026-07-07, deliberately BEFORE Phase 5 is implemented (see "Relationship to
     Phase 5" — this plan must be executable with Phase 5 merged, in flight, or absent).
     Expands the docs/roadmap.md "Phase 6" section; deviations from roadmap/plan.md sketches
     are listed at the end. Externally checkable facts re-verified 2026-07-07:
     - GEE EMIT mirrors are FROZEN V001 COPIES: `NASA/EMIT/L2B/CH4PLM` (ImageCollection,
       band `methane_plume_complex`, ppm·m; availability 2022-08-10 → 2024-10-26) and
       `NASA/EMIT/L2B/CH4ENH` (band `vertical_column_enhancement`, ppm·m; 2022-08-10 →
       2024-11-30). 60 m product resolution (per LP DAAC; ignore any scraped "72 km" — that
       is a catalog-page parsing artifact).
     - V001 was DECOMMISSIONED at LP DAAC on 2026-03-26. The live product is V002
       (EMITL2BCH4PLM/EMITL2BCH4ENH v002, published 2026-02; coverage Aug 2022 → present,
       instrument-off gap 2022-09-13 → 2023-01-06). Each CH4PLM V002 granule = one 60 m COG
       + one GeoJSON (plume outline, source-scene list, max-enhancement coords, and — new in
       V002 — an emission rate estimate + uncertainty). V002 also changed the matched-filter
       channel selection, so V001 and V002 rasters over the same scene are NOT numerically
       identical.
     - earthaccess 0.18.0 (2026-05-12), requires-python ≥ 3.12 — compatible with our 3.13 pin.
     - AlphaEarth: `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`, dataset version 1.1 (Nov 2025),
       annual images 2017–2024 (a 2025 layer was announced as "underway" — re-check at
       implementation), 64 bands A00–A63, 10 m, one image per UTM zone (`UTM_ZONE` property),
       embeddings are unit-norm (dot product needs no normalization; year-to-year change =
       angle/dot), license CC-BY 4.0 with required attribution: "The AlphaEarth Foundations
       Satellite Embedding dataset is produced by Google and Google DeepMind."
     - Wind particles: geoql/maplibre-gl-wind (MIT, v0.2.0 Jan 2026) requires deck.gl ≥ 9 via
       MapboxOverlay; weatherlayers/deck.gl-particle likewise rides deck.gl (with a commercial
       upsell). Decision below: vendor the classic webgl-wind custom-layer technique instead —
       no deck.gl dependency for one layer (license of mapbox/webgl-wind is ISC — re-confirm
       when vendoring and keep the attribution header). -->

# Phase 6 — EMIT + Embeddings + products v1: execution plan

**Goal (roadmap):** EMIT provider (GEE ≤ Oct 2024 + earthaccess V2 fallback) with detection
cross-validation; AlphaEarth embeddings explorer (similarity/change/clusters); wind particle
layer; first derived products as catalog recipes; compose.yaml + deploy doc.
*Exit: EMIT plumes overlay a known event and cross-match a detection; similarity search works
from a clicked seed; `docker compose up` serves the full app.*

**Branch:** `v2/phase6-emit-embeddings`, cut from **main**. One commit per stage, prefixed
`core:` / `api:` / `web:` / `infra:` / `docs:`. After every stage: `make check`; after any API
schema change: `make gen` in the same commit.

## Relationship to Phase 5 (NOT implemented at planning time — no dependency either way)

Phase 6 needs nothing from Phase 5 and is designed so nothing dangles if Phase 5 slips or is
dropped. The load-bearing decision making this true: **EMIT plumes are independent evidence
attached to existing detections, not detection rows** — Phase 6 never writes
`detections.source`, so Phase 5's `source="ml"` rows, source filter, and feed badges land (or
don't) without touching anything here. Contact points, all mechanical:

1. `docs/methane_methods.md`: Phase 5 owns §9 (ML tier); Phase 6 writes **§10 — EMIT tier**
   regardless of whether §9 exists yet (if it doesn't, leave an HTML comment placeholder
   `<!-- §9 reserved: ML tier (Phase 5) -->` so numbering never shifts).
2. Web: both phases edit `DetectionFeed.tsx` / `DetectionDetail.tsx` (Phase 5: badge + score;
   Phase 6: EMIT match chip). Textual collisions only — rebase onto main after any Phase 5
   stage merges and rerun `make gen`; never hand-merge `types.gen.ts`.
3. `ALGO_VERSION` (4 in-tree as of 3.5 Stage 2; 3.5 will end at 5): Phase 6 needs **no bump**
   — new cached ops (embeddings seeds/cluster training, EMIT plume lists) get new `op` names
   in `cache_key`, and no existing op changes semantics. On any merge conflict, 3.5's number
   wins (same rule as Phase 5).
4. DB: Phase 6 adds **migration 5** (`ALTER TABLE detections ADD COLUMN emit_json TEXT`).
   Migrations are append-only, so ordering vs any future Phase 5 migration is resolved by
   whoever merges second appending after the other — renumber at rebase, never reorder.

**Relationship to Phase 3.5:** consumer only. The 3.5 calibration event set
(`scripts/data/calibration_events.json`) already contains ≥ 5 EMIT-era events (2023-09-27
T13SGR, 2023-10-05 T48NTP, 2023-12-08 T39RUQ, 2024-02-20 T20PMR, 2024-09-13 T15QWB) — Stage 2's
cross-match gate draws its known event from this list instead of hunting for a new one.

**Standing rules (in addition to the Phase 3/3.5/5 sets, which still apply):**

- **earthaccess never appears under `packages/core`** (it drags an HTTP/auth stack; core's
  only network dependency stays `ee`). It is a `packages/api` dependency, **lazy-imported
  inside the service function** — `create_app()` stays importable with no Earthdata
  credentials, no EE, no DB (the OpenAPI export script and web CI depend on this).
- Earthdata auth via environment only (`EARTHDATA_TOKEN`, or the user's `~/.netrc`;
  `earthaccess.login(strategy="environment")`). Never committed, never logged.
- All EE round-trips through `ee_call`; tile URLs never cached; embeddings/EMIT compute
  results cached under new `cache_key` op names.
- Committed fixtures: one **trimmed** EMIT V002 GeoJSON granule (NASA data, US-gov public
  domain — fine to commit small) for the offline parser tests. No COGs, no bulk EMIT data.
- AlphaEarth attribution (CC-BY 4.0) appears in the catalog `attribution` field AND in the
  Embeddings view footer, verbatim string from the header above.
- No live CMR/Earthdata/EE calls in CI, ever. Live paths get `@pytest.mark.ee`-style manual
  tests only.

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 0 | EMIT CH4ENH as a builtin catalog dataset (browse/quicklook) | core | S | — |
| 1 | Core EMIT plumes: GEE query + V002 GeoJSON parser + cross-match | core | M | 0 |
| 2 | API + web EMIT: plumes route, earthaccess fallback, detection match, Lab overlay | api + web | L | 1 |
| 3 | Embeddings: core ops + API tile routes + Embeddings Explorer view | core + api + web | L | — |
| 4 | Wind particle layer | web | M | — |
| 5 | Derived products v1: compare recipes (DNBR, URBAN_HEAT, S1 VV change) | core + api + web | M | — |
| 6 | compose.yaml + deploy doc + CI image build | infra + docs | M | all (final) |

0→1→2 strictly sequential; 3, 4, 5 are independent of the EMIT chain and of each other
(execute in the listed order by default, but any of them can be pulled forward if EMIT work
blocks on external access); 6 last.

---

## Pinned contracts

### Stage 0 — EMIT CH4ENH builtin dataset

New builtin catalog module `catalog/builtin/emit.py`: dataset id `emit`, collection
`NASA/EMIT/L2B/CH4ENH`, one product `CH4ENH` (`source_band="vertical_column_enhancement"`,
unit ppm·m, 60 m default scale). No provider code: `emit` is not special-cased in the
dispatcher, so it flows through `get_generic_collection` — that is the point of the catalog
design, and this stage proves it for a builtin. Provisional vis range 0–1500 ppm·m with a
plume-style palette; the matched filter produces negative noise, so `valid_min` must be
generously negative — **tune both against live data before committing** and record the chosen
numbers in the commit message. Description text states the V001-mirror freeze
("GEE copy: Aug 2022 – Nov 2024; later granules via the EMIT plume fetcher").

Offline tests: registry round-trip, product resolution, generic-pipeline acceptance (the
existing catalog test patterns). *Exit: CH4ENH browsable in Explore over a known plume date
(live check, e.g. the 2023-09-27 T13SGR event).*

Commit 0: `core: EMIT CH4ENH builtin dataset (GEE V001 mirror, generic pipeline)`.

### Stage 1 — core `methane/emit.py`

Pure + EE-facing halves in one module (the `wind.py` precedent):

```python
@dataclass(frozen=True)
class EmitPlume:
    plume_id: str
    outline: dict            # GeoJSON geometry (EPSG:4326)
    time_utc: datetime
    max_enh_ppm_m: float | None
    max_enh_lat: float | None
    max_enh_lon: float | None
    q_kg_h: float | None     # V002 GeoJSON only; None for GEE V001 plumes
    q_sigma_kg_h: float | None
    provenance: str          # "gee_v001" | "lpdaac_v002"
    source_scenes: list[str]

def parse_v002_geojson(data: bytes) -> list[EmitPlume]        # pure, offline-tested (fixture)
def list_plumes_gee(bbox: BBox, start: str, end: str) -> list[EmitPlume]
    # CH4PLM filterBounds/filterDate; one image = one plume complex. Outline:
    # selfMask().reduceToVectors at 60 m within the image footprint (small rasters — cheap),
    # via ee_call; q fields None (emission rates are V002-metadata-only).
def cross_match(det_lat, det_lon, det_time_utc, plumes, *, max_km=5.0, max_days=3.0)
    -> list[EmitMatch]       # pure; reuses validation.haversine_km; sorted by (km, |Δt|)
```

The date-router lives here too: `gee_available(start, end)` — windows entirely ≤ 2024-10-26
use GEE; anything later needs the V002 path (callers combine both and de-duplicate by
`plume_id`/time+location when windows straddle the boundary).

Offline tests: fixture GeoJSON → `EmitPlume` fields (incl. the V002 emission rate),
`cross_match` distance/time windows + ordering, boundary router. mypy: EE-chain functions get
the scoped `warn_return_any` treatment like the other EE modules.

Commit 1: `core: EMIT plume model — GEE V001 query + V002 GeoJSON parser + cross-match`.

### Stage 2 — API + web EMIT

**Settings:** none new beyond the Earthdata env convention (documented in `.env.example` as a
comment; it is not an `OPENEARTH_` setting — earthaccess reads its own env).

**`services/emit.py`:**

- `list_plumes(bbox, start, end)` → GEE path via core; post-cutoff windows call the
  **earthaccess fallback**: `search_data(short_name="EMITL2BCH4PLM", version="002",
  bounding_box=…, temporal=…)`, download **only the `.json` asset** per granule (the COG is
  never fetched in Phase 6), bytes → `parse_v002_geojson`. Lazy `import earthaccess` inside
  the function; missing/invalid credentials → `502` with a "set EARTHDATA_TOKEN — see
  docs/deploy.md" detail. Results cached (`cache_key("emit_plumes", …)`, ~1 day TTL — V002 is
  a living collection).
- `match_detection(detection_id)` → load the detection row, `list_plumes` around its site
  coords ± window, `cross_match`, write `emit_json` (matches + query provenance + checked-at
  timestamp) via **migration 5** (`ALTER TABLE detections ADD COLUMN emit_json TEXT`).
- **§10 numbers:** for one matched event, compare our masked mean ΔΩ (mol/m²) against EMIT
  CH4ENH over the same footprint using 1 ppm·m ≈ 4.3×10⁻⁵ mol/m² (n = P/RT at an assumed
  surface P/T — state the assumption; the constant is ~4.2–4.5×10⁻⁵ across plausible P/T).
  Order-of-magnitude context for the methods doc, **never a gate** (different instruments,
  different overpass times).

**Routes** (`routers/methane.py`, all `Depends(ensure_ee)` except where noted):
`GET /methane/emit/plumes?bbox&start&end` → `{plumes: EmitPlumeOut[]}` (GeoJSON outline +
provenance per plume); `POST /methane/detections/{id}/emit-match` → updated detection (no EE
needed for the DB write; EE/earthaccess used by the lookup). Detection schemas gain
`emit_json`. `make gen`.

API tests: monkeypatch the core fns + a fake earthaccess module into `services.emit`
(established pattern); fixture-driven plume list; match endpoint writes `emit_json`; 502
without credentials; migration 5 upgrade path on a v4 DB file.

**Web (Methane Lab):** "EMIT plumes" toggle on the Lab map (outline layer, provenance-styled:
solid = lpdaac_v002, dashed = gee_v001; popup shows q ± σ when present); DetectionDetail gains
an "EMIT" section: match button → chip (`n` matches, nearest km/Δt) reading `emit_json`.
Explore untouched (CH4ENH browsing shipped in Stage 0).

*Exit gates:* plumes overlay a known event live (calibration-set event, GEE path) AND a
post-Oct-2024 window returns V002 plumes (earthaccess path); `emit-match` writes a non-empty
`emit_json` on at least one calibration-set detection.

Commits: `api: EMIT plume routes + earthaccess V002 fallback + detection cross-match
(migration 5)` and `web: EMIT overlay + match chip in the Methane Lab`.

### Stage 3 — Embeddings Explorer

**Core `embeddings.py`** (top-level module — it is not methane science):

```python
COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"   # 64 bands A00–A63, unit-norm, 10 m
def year_mosaic(year: int) -> ee.Image
def seed_vector(lat, lon, year) -> list[float]         # reduceRegion first() at 10 m, ee_call;
                                                       # raises a typed error on masked/no-data
def similarity_image(seed: list[float], year) -> ee.Image   # dot product → [-1, 1]
def change_image(year_a, year_b) -> ee.Image                # 1 − dot(a, b) → [0, 2]
def cluster_image(bbox, year, k, *, n_samples=5000, seed=0) -> ee.Image
    # wekaKMeans(k, seed=seed) trained on image.sample(region, numPixels=n_samples, seed=seed)
```

`seed_vector` results are cached (`cache_key("embed_seed", lat, lon, year)`); cluster
training happens inside the mint call chain (EE-side; nothing to cache but the tile URL,
which is never cached — re-mints retrain, hence the pinned `seed=` for reproducibility).

**API** `routers/embeddings.py` + `services/embeddings.py`:
`POST /embeddings/similarity` `{lat, lon, year, roi?}` → `TileRef` + `{seed_norm}` (sanity
echo ≈ 1.0); `POST /embeddings/change` `{year_a, year_b, roi?}` → `TileRef`;
`POST /embeddings/cluster` `{roi, year, k}` (k clamped 2–12; ROI required — training needs a
region) → `TileRef` + cluster count. Vis: similarity fixed [-0.2, 1.0] diverging→sequential
ramp; change [0, 1] (values above 1 are rare antipodal cases — clamp); clusters via a fixed
qualitative palette cycled to k. Years validated against the available range (2017–2024;
**probe the collection at implementation for a 2025 image** rather than hardcoding the upper
bound — derive it once at startup-time first use and cache it). `make gen`.

**Web:** new `embeddings` view in `App.tsx` (sixth entry in the `View` union) — own MapLibre
instance (Compare precedent): year picker, click-to-seed similarity (marker + tile layer +
"find places like this" affordance), A/B year change mode, k-means mode with k slider +
cluster legend, CC-BY attribution footer. Layer swaps go through `setTiles` on one source
(no-refetch rule).

API tests: monkeypatch core fns; year validation; k clamping; seed-echo shape. Core offline
tests: pure parts only (vis-range constants, request validation helpers) — the EE chain is
covered by one manual live check.

*Exit gate (roadmap):* clicking a seed on a live map returns a similarity layer where
same-class surfaces visibly light up (manual live check over Heidelberg: river vs forest vs
urban); change 2018→2023 highlights a known construction site; clusters render with a legend.

Commits: `core+api: AlphaEarth embedding ops (similarity/change/cluster)` and
`web: Embeddings Explorer view`.

### Stage 4 — wind particle layer

**No deck.gl.** The app's map binding is deliberately thin imperative MapLibre; adding deck.gl
for one layer is the tail wagging the dog (the MIT `maplibre-gl-wind` package requires
deck.gl ≥ 9 via MapboxOverlay — that is the rejected alternative, revisit only if the vendored
approach fights back). Instead: vendor the classic webgl-wind particle technique
(mapbox/webgl-wind, ISC — keep the attribution header) as a **MapLibre custom layer** under
`apps/web/src/map/wind/` (~400 lines GLSL + TS, adapted to typed modules).

Data path: the existing `GET /wind/field?bbox&time&nx&ny` JSON grid (max dim 50) → client
builds the u/v texture (`generateTexture` from the regular grid — no IDW needed, GPU linear
sampling smooths); u/v range encoded in texture metadata client-side. **No API change, no
`make gen`.** Explore gains a "Wind particles" toggle next to the existing arrow overlay
(shares its time source); particle count adapts to zoom; layer respects the no-refetch rule
(time change = texture swap on the same layer).

Tests: texture-encoding unit test (grid → RGBA round-trip within quantization error) in
vitest; the shader path is verified manually via the Playwright-MCP pass (screenshot: visible
coherent flow over a synthetic uniform field and over a live ERA5 field).

*Exit gate:* particles advect along the arrow overlay's directions (same field, two
renderings — they must agree visually); no WebGL errors on view switches/resize.

Commit: `web: wind particle layer (vendored webgl-wind custom layer, /wind/field texture)`.

### Stage 5 — derived products v1: compare recipes

The roadmap rule is "TOML catalog recipe, not bespoke endpoint" — so the deliverable is a
**minimal two-window recipe capability**, then three products that use ≤ that capability:

- `ProductSpec` gains `needs_ref: bool = False` (TOML: `needs_ref = true`; stays allowed in
  user TOML, unlike `builder`). Semantics: the product's `expression` may reference bands
  prefixed `pre_` / `post_`; the pipeline builds two mean composites — reference window and
  request window — via the **existing** `get_collection` per window (so S2 cloud masking, S1
  polarization handling etc. are inherited), renames bands with the prefixes, `addBands`,
  applies the expression. Implemented in `providers/__init__.py` as
  `get_compare_image(data_key, roi, ref_start, ref_end, start, end, source)`; the generic
  single-window path is untouched.
- `TilesRequest` gains optional `ref: DateWindow | None` (the existing `methane_ref` field
  stays exactly as-is for the CH4 quicklook — do not alias or migrate it; a later cleanup can
  unify them). `needs_ref` products 422 without `ref` (CH4_ANOMALY precedent). Same plumbing
  for thumbnail/export routes **only if free**; otherwise tiles-only in Phase 6 and note it.
  `make gen`.
- **Products** (builtin, in `catalog/builtin/s2.py` / `s1.py`):
  - `DNBR` (S2): `(pre_NBR) − (post_NBR)` written out over B8/B12
    (`(pre_B8−pre_B12)/(pre_B8+pre_B12) − (post_B8−post_B12)/(post_B8+post_B12)`), vis
    −0.5…+1.0, USGS burn-severity palette; `needs_ref=true`.
  - `URBAN_HEAT` (S2): NDBI − NDVI, single window (no `needs_ref`) — proves the trio isn't
    all one mechanism; vis −1…1 diverging.
  - `FLOOD_VV_CHANGE` (S1): `post_VV − pre_VV` in dB, vis −10…+10 dB diverging (open-water
    flooding shows strongly negative); `needs_ref=true`.
- **Deviation, explicit:** plan.md sketched an "S1+S2 flood mask"; true multi-*source* fusion
  needs a cross-collection pipeline the recipe schema cannot honestly express — it stays in
  the backlog (with `deferred products.md` P1 updated to say which items the compare
  capability now unlocks: dNBR ✅, single-source temporal deltas ✅; fusion/regression items
  still deferred).
- **Web:** LayerPanel shows a reference-window picker for `needs_ref` products (reuses the
  quicklook's window-picker component if extractable; otherwise a sibling); legend/export
  behave as for any product.

Offline tests: loader accepts/validates `needs_ref` (+ rejects `needs_ref` without
`expression`), compare-image band-prefix contract via a monkeypatched collection builder,
422 without `ref`, DNBR/FLOOD expression strings parse under the existing expression checks.

*Exit gate:* DNBR over a documented burn (e.g. 2023 Rhodes fires: pre = June 2023, post =
Aug 2023) renders with visibly elevated severity inside the burn scar, live; URBAN_HEAT
renders over Heidelberg; FLOOD_VV_CHANGE over a documented flood (e.g. Emilia-Romagna
May 2023) shows the inundation footprint.

Commit: `core+api+web: two-window compare recipes — DNBR, URBAN_HEAT, FLOOD_VV_CHANGE`.

### Stage 6 — compose.yaml + deploy doc + CI

- **`compose.yaml`** (repo root): `api` — multi-stage uv image (builder: `uv sync --frozen
  --no-dev --package openearth-api`; runtime: slim python 3.13, uvicorn on :8000, healthcheck
  `GET /healthz` or the existing meta route); `web` — node 20 + pnpm build → `nginx:alpine`
  serving `dist/` and proxying `/api` → `api:8000` with **`proxy_buffering off` and a long
  `proxy_read_timeout` on the `/api` location — SSE dies behind default nginx buffering**;
  volume `./data:/data` with `OPENEARTH_DATA_DIR=/data` (SQLite + diskcache + artifacts
  survive restarts).
- **EE auth in containers:** document both paths in `docs/deploy.md` — mounted user
  credentials (`~/.config/earthengine`) for personal use, service-account JSON +
  `GOOGLE_APPLICATION_CREDENTIALS` for headless; plus `OPENEARTH_EE_PROJECT`,
  `EARTHDATA_TOKEN`, and the note that the app boots fine with none of them (EE routes 503
  via `ensure_ee` — verify actual auth plumbing against `ee/client.py` at implementation and
  document what is true, not what is assumed).
- **CI:** `docker build` of both images on tags only (plan.md pin — keeps PR CI fast);
  compose config validated (`docker compose config -q`) in regular CI since it's free.
- `docs/deploy.md`: compose quickstart, env table, volume/backup note (SQLite + diskcache +
  timelapse/methane artifacts all under one dir), the ND/licensing reminder for any future
  public deployment (backlog item stays authoritative).
- Roadmap: Phase 6 ✅ + as-built one-liner; CLAUDE.md: new routes/views/dataset + earthaccess
  rule + compose command (terse); README: deploy paragraph; methods §10 lands here if not
  already merged in Stage 2's docs pass.

*Exit gate (roadmap):* `docker compose up` on a clean checkout (with mounted EE creds) serves
the full app — Explore tiles render, one methane analyze job completes, SSE progress visible
through the nginx proxy.

Commits: `infra: compose.yaml — api (uv multi-stage) + web (nginx, SSE-safe proxy)` and
`docs: deploy guide + roadmap tick`.

---

## Deviations from / refinements of the roadmap and plan.md sketches (deliberate)

| Decision | Rationale |
|---|---|
| EMIT plumes are evidence on existing detections (`emit_json`, migration 5), **not** `source="emit"` detection rows | plan.md's `detections(source: physics\|ml\|emit)` predates Phase 5 planning; EMIT complexes are another instrument's product, not our pipeline's output — and this decouples Phase 6 from Phase 5's feed changes entirely |
| earthaccess lives in `packages/api`, lazy-imported; core gets only the pure V002 GeoJSON parser | core's no-heavy-deps discipline (timelapse's injectable-FetchFn precedent); the parser is the science-adjacent, offline-testable part |
| V002 fallback fetches only the GeoJSON asset, never the COG | the outline + emission rate satisfy the Phase 6 exit; raster work (STARCOP/EMIT tier training) is explicitly backlog |
| CH4ENH ships as a builtin catalog dataset through the generic pipeline (Stage 0) | zero provider code — and it proves the catalog design claim on a builtin |
| No deck.gl; wind particles = vendored webgl-wind custom layer fed by the existing `/wind/field` JSON | one layer doesn't justify a second rendering framework beside the thin MapLibre binding; no API change needed |
| Flood product is single-source S1 VV change, not the plan.md "S1+S2 flood mask" | multi-source fusion can't be a TOML recipe without inventing a fusion pipeline — violates the "recipe, not bespoke endpoint" rule; stays backlog |
| Two-window capability = `needs_ref` + `pre_`/`post_` band prefixes + `TilesRequest.ref`; `methane_ref` untouched | smallest schema that expresses dNBR/flood-change; unifying with `methane_ref` is a cleanup, not a Phase 6 requirement |
| Embeddings cluster ROI is required, k ∈ [2, 12], fixed sampling/training seeds | wekaKMeans needs a training region; unseeded runs would re-cluster differently on every tile re-mint |
| No `ALGO_VERSION` bump | new ops get new cache-key op names; nothing existing changes semantics |

## Implementation pitfalls (read before coding)

- **The GEE EMIT mirrors are frozen V001.** Availability ends 2024-10-26 (CH4PLM) /
  2024-11-30 (CH4ENH) and will not grow; V001 is decommissioned upstream. Every plume carries
  `provenance`, windows straddling the boundary must merge + de-duplicate both paths, and no
  UI surface may imply GEE covers "now".
- **V001 vs V002 rasters differ** (matched-filter channel change). Never mix versions inside
  one quantitative comparison; the §10 CH4ENH cross-check uses one version and says which.
- **GEE CH4PLM outline ≠ image footprint.** The footprint is the granule extent;
  the plume outline needs `selfMask().reduceToVectors` (cheap at 60 m over one complex, but
  it is an extra EE round-trip per plume — batch via one `ee_call` per plume list, and cache).
- **ppm·m → mol/m² needs a stated surface P/T assumption** (~4.3×10⁻⁵ mol/m² per ppm·m,
  ±5 % over plausible conditions). Context number, never a gate — and don't let the docs
  imply EMIT and S2 saw the same instant.
- **Embeddings mosaics cross UTM zones** (one image per zone, `UTM_ZONE` property): sample
  seeds and train clusterers at 10 m with an explicit projection/scale, expect minor
  swath-offset artifacts (documented upstream), and probe — don't hardcode — the newest
  available year.
- **Similarity legend honesty:** dot products of unit-norm embeddings are cosine similarity;
  negative values are meaningful ("actively dissimilar"), so the [-0.2, 1] range clips —
  fine for a quicklook, but the legend must label the clamp.
- **wekaKMeans without pinned seeds re-clusters on every re-mint** (tile expiry → new
  training). Pin `seed=` in both `sample` and the clusterer; cluster *colors* are still
  arbitrary integers — legend maps index→color, never pretends class semantics.
- **Particle layer + view switching:** the custom layer owns GL resources — implement
  `onRemove` cleanly; MapLibre custom layers get the map's GL context, don't create a second
  canvas. Test view switches and map style operations (moveLayer ordering with tile layers).
- **Compare recipes inherit each source's compositing quirks:** S2 cloud masking differs
  pre/post (a cloudy pre-window poisons dNBR — surface the composite's image count in the
  tile response if cheap, else document); S1 VV change should note ascending/descending
  mixing in the description (orbit filtering is a backlog refinement, not Phase 6).
- **422-vs-refuse for `needs_ref`:** the generic single-window path must *refuse* compare
  products loudly (builder-product precedent) — a silent single-window DNBR render would be
  a wrong-number generator.
- **SSE through nginx dies silently with default buffering** — `proxy_buffering off`,
  `proxy_read_timeout` ≥ job lifetimes, and verify with a real analyze job through compose,
  not just a curl of the endpoint.
- **`create_app()` invariants extend to Phase 6:** importable with no earthaccess creds, no
  model files, no EE — new routers keep all heavy imports inside service functions.
- **Migration 5 is `ALTER TABLE ADD COLUMN`** — trivially append-only, but test the upgrade
  on a copy of a real v4 `openearth.db`, and remember rows predating the migration have
  `emit_json IS NULL` (feed schemas must treat null as "never checked", not "no match").
