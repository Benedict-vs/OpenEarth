<!-- docs/phase3-execution-plan.md — Phase 3 (Methane Lab, physics) execution plan.
     Written 2026-07-05 against the Phase-2-complete tree (branch v2/phase3-methane-lab,
     cut from main at ef78573). Decisions in here are made deliberately within
     docs/plan.md's settled architecture; implement within them. Where this doc refines
     or deviates from plan.md, the "Deviations" section says so explicitly.
     Literature facts (formulas, coefficients, published event rates) were re-verified
     against Varon et al. 2021 (AMT 14:2771, open access) on 2026-07-05. -->

# Phase 3 — Methane Lab, physics: execution plan

**Goal (roadmap exit criterion):** reproduce ≥ 2 documented super-emitter events with Q
within ~±50 % of published values and defensible σ; synthetic-plume test suite green; every
detection persisted and reviewable.

**Branch:** `v2/phase3-methane-lab` (already cut from main). One commit per stage,
prefixed `core:` / `api:` / `web:` / `docs:`. After **every** stage: `make check`; after any
API-schema stage: `make gen` (CI diff-checks `openapi.json` / `types.gen.ts`).

**Standing rules (do not re-derive):**
- All blocking EE round-trips through `ee_call()`; no bare `getInfo()`
  (`rg "getInfo\(\)" packages | rg -v ee_call` before each commit).
- Science-critical math on NumPy arrays, offline-testable; EE only browses/reduces/fetches.
- `create_app()` stays EE-free and DB-free at creation; env-dependent work in the lifespan.
- Offline API tests fake EE by monkeypatching core fns **imported by name** into
  `openearth_api/services/*`.
- One diskcache tier for *recomputable* results only — detection artifacts are primary data
  and live on disk + SQLite, never in the evictable cache.
- mypy strict; new EE-chain modules get the scoped `warn_return_any = false` treatment in
  root `pyproject.toml` (mirror the provider entries, per-module).
- Web: types via `make gen`; no-refetch rule for layer controls.

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 1 | CH4 LUT: constants, generator script, committed `ch4_lut_v1.npz`, `conversion.py` | core + scripts | L | — |
| 2 | Scene catalog: `methane/scenes.py` (search, metadata, reference selection) | core | M | — |
| 3 | Retrieval: `methane/retrieval.py` (chips, calibrated MBSP, MBMP) | core | L | 2 |
| 4 | Plume masking: `methane/plume.py` | core | M | — |
| 5 | Quantification: `methane/ime.py` + overpass wind σ | core | L | 1, 4 |
| 6 | Orchestrator: `methane/detect.py` + synthetic end-to-end golden test | core | M | 1–5 |
| 7 | S5P screening: `methane/tropomi.py` | core | M | — |
| 8 | API: migration 3, sites/scenes/analyze/detections, artifacts, overlay PNG, quicklook tiles | api | XL | 6 |
| 9 | API: screening job + validation importer/cross-match (`methane/validation.py`) | core + api | M | 7, 8 |
| 10 | Methane Lab UI | web | XL | 8, 9 |
| 11 | `docs/methane_methods.md`, event reproduction, exit verification | docs + scripts | M | all |

Stage 1 is the long pole (HITRAN download + LUT generation are manual, networked, one-time);
stages 2, 4, 7 are independent of it and of each other. 3 needs 2 (AMF metadata), 5 needs
1 + 4, 6 wires everything. 8 → 9 → 10 is a strict chain.

---

## Pinned contracts

### New dependencies

- `packages/core`: `scipy>=1.14,<2` (`scipy.ndimage` for plume labeling; add `scipy.*` to
  mypy `ignore_missing_imports` if the bundled stubs don't cover `ndimage`).
- `packages/api`: `pillow>=10,<12` (overlay PNG rendering), `python-multipart` (the
  validation-import `UploadFile` form needs it — FastAPI errors without it).
- root `[dependency-groups]`: new group `lut = ["hitran-api>=1.2", "openpyxl>=3.1"]` — used
  ONLY by `scripts/generate_ch4_lut.py` (`uv run --group lut python scripts/generate_ch4_lut.py`).
  HAPI must never become a package dependency; the runtime loads the committed `.npz` only.
- `apps/web`: none (ECharts, zustand, TanStack Query already present).

### Settings

One new optional setting: `lut_path: Path | None = None` (override for LUT experiments;
default = the packaged `ch4_lut_v1.npz`). Update `.env.example`. Detection artifacts live at
`settings.data_dir / "detections"` (mkdir in lifespan, like `exports`).

### Science constants (`methane/constants.py` — every value cited inline)

```python
M_CH4_KG_PER_MOL = 0.01604
# Vertical column of dry air: P0 / (g · M_air) = 101325 / (9.80665 · 0.0289644)
OMEGA_AIR_MOL_M2 = 3.567e5
# Background CH4 column at 1875 ppb ≈ 0.65 mol/m² (Varon et al. 2021, Sect. 2)
OMEGA_CH4_BACKGROUND_MOL_M2 = 0.65
# Effective wind speed for S2 IME inversion, LES-calibrated (Varon et al. 2021, Sect. 3):
# U_eff = 0.33 · U10 + 0.45  [m/s]
UEFF_ALPHA = 0.33
UEFF_BETA_MS = 0.45
# Modeling choices, NOT literature values (documented in docs/methane_methods.md):
SIGMA_U10_FLOOR_MS = 1.5        # reanalysis 10 m wind 1σ error floor
IME_MODEL_SIGMA_FRAC = 0.15     # multiplicative IME-model error in the Monte Carlo
```

### LUT artifact + `methane/conversion.py`

The LUT is **committed inside the package** at
`packages/core/src/openearth/methane/data/ch4_lut_v1.npz` (the repo's `data/` dir is
gitignored — the LUT must ship with the library; it is a few hundred KB). Load via
`importlib.resources`. `.npz` keys:

```
delta_omega : (N,)  float64, mol/m² — grid −0.5 … 2.0, 251 points
amf         : (M,)  float64 — grid 2.0 … 4.0, step 0.25 (9 points)
m_s2a       : (M,N) float64 — MBSP fractional signal for Sentinel-2A
m_s2b       : (M,N) float64 — same for Sentinel-2B
version     : str ("1")
provenance  : str — JSON: HITRAN fetch date + line-list ids, SRF document issue,
              T/p, ν grid, script git hash
```

Physics (what the generator computes — Beer–Lambert band transmittance, no scattering):
- CH4 absorption cross sections σ(ν) via HAPI `absorptionCoefficient_Voigt`
  (T = 288.15 K, p = 1 atm), ν grids covering B11 (≈ 5946–6497 cm⁻¹) and B12
  (≈ 4310–4812 cm⁻¹) ± 50 cm⁻¹ margin at 0.005 cm⁻¹ step.
- Slant optical depth: τ(ν; ΔΩ, AMF) = (Ω₀ + ΔΩ) · AMF · N_A · 1e−4 · σ(ν), with
  Ω₀ = `OMEGA_CH4_BACKGROUND_MOL_M2`. (1e−4 converts σ from cm² to m² per molecule.)
- SRF-weighted band transmittance: T_b(ΔΩ, AMF) = ∫SRF_b(ν)·e^(−τ) dν / ∫SRF_b(ν) dν.
- **Fractional signal relative to background** (this refines plan.md's shorthand):
  per band m_b(ΔΩ) = T_b(Ω₀+ΔΩ)/T_b(Ω₀) − 1, and
  `m_MBSP(ΔΩ) = (1 + m_B12) / (1 + m_B11) − 1` (B11 has weak but nonzero CH4 absorption).
- Computed separately for S2A and S2B — their B12 SRFs differ enough to matter
  (Varon 2021 reports m ≈ −0.029 vs −0.022 for the same scenario, see anchor test).

SRFs: ESA "Sentinel-2 Spectral Response Functions (S2-SRF)", document
COPE-GSEG-EOPG-TN-15-0007 (xlsx, issue 3.2+ from the Sentinel Online / SentiWiki document
library). The script parses the xlsx; **extract the B11/B12 columns for S2A + S2B into a
committed CSV** `scripts/data/s2_srf_b11_b12.csv` with a provenance header comment (document
issue + download date), so LUT regeneration never depends on ESA's URL surviving.

```python
@dataclass(frozen=True)
class CH4Lut:
    delta_omega: np.ndarray          # (N,)
    amf: np.ndarray                  # (M,)
    m: dict[str, np.ndarray]         # {"Sentinel-2A": (M,N), "Sentinel-2B": (M,N)}
    version: str
    provenance: str

def load_lut(path: Path | None = None) -> CH4Lut                      # cached (lru_cache)
def forward_signal(lut, spacecraft: str, amf: float) -> tuple[np.ndarray, np.ndarray]
    # (delta_omega, m) 1-D curve, linear interp along the AMF axis; clamps AMF into range
def invert_fractional_signal(delta_r: np.ndarray, lut, spacecraft, amf: float) -> np.ndarray
    # monotonic 1-D np.interp on the inverted curve; NaN passes through; values outside
    # the m range clip to the grid ends
def delta_omega_to_xch4_ppb(delta_omega: np.ndarray | float) -> np.ndarray | float
    # ΔΩ / OMEGA_AIR_MOL_M2 * 1e9
```

### Database (migration 3 — append to `_MIGRATIONS`, never edit 1–2)

```sql
CREATE TABLE sites (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    west REAL NOT NULL, south REAL NOT NULL, east REAL NOT NULL, north REAL NOT NULL,
    date_hint_start TEXT, date_hint_end TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE detections (
    id              TEXT PRIMARY KEY,          -- uuid4 hex
    site_id         INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    source          TEXT NOT NULL,             -- 'physics' now; 'ml'|'emit' later phases
    status          TEXT NOT NULL,             -- 'candidate'|'accepted'|'rejected'
    method          TEXT NOT NULL,             -- 'mbmp'|'mbsp'
    scene_id        TEXT NOT NULL,             -- S2 system:index
    scene_time_utc  TEXT NOT NULL,
    ref_scene_id    TEXT,
    q_kg_h          REAL, q_sigma_kg_h REAL, xch4_max_ppb REAL, ime_kg REAL,
    u10_ms          REAL, wind_from_deg REAL,
    params_json     TEXT NOT NULL,             -- AnalyzeRequest as submitted (repro)
    result_json     TEXT NOT NULL,             -- full numbers: percentiles, histogram,
                                               -- calibration c's, L, U_eff, σ terms, flags
    mask_geojson    TEXT,                      -- plume outline polygons (EPSG:4326)
    array_path     TEXT NOT NULL,              -- detections/{id}.npz relative to data_dir
    notes           TEXT,
    validation_json TEXT,                      -- verdict + matched event ids, or NULL
    created_at      TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX ix_detections_site   ON detections (site_id, created_at);
CREATE INDEX ix_detections_status ON detections (status);
CREATE TABLE reference_events (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,               -- 'imeo'|'sron'|'manual'
    event_time_utc TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL,
    q_kg_h REAL, q_sigma_kg_h REAL,
    raw_json      TEXT NOT NULL,
    imported_at   TEXT NOT NULL
);
```

Numeric headline fields (`q_kg_h`, `status`, …) are real columns so the feed filters/sorts in
SQL; everything else stays in the JSON blobs. **Seeding:** in the lifespan, after `migrate`,
if `sites` is empty insert the 7 `METHANE_SITES` presets (name without the `"CH4: "` prefix,
bbox, date hints) — idempotent by the empty-check, no separate seed script.

**Write-discipline refinement (deliberate, see Deviations):** the `jobs` table remains
event-loop-only, but analyze runners write their own `detections` row from the worker thread
via a fresh short-lived `Session` — WAL handles the concurrency. Add
`PRAGMA busy_timeout = 5000` next to the WAL pragma in `create_db_engine`.

### Core module contracts (get these right; Phase 5 ML reuses retrieval + plume unchanged)

`methane/scenes.py` — one `ee_call` per search; EE work = metadata only:
```python
@dataclass(frozen=True)
class S2Scene:
    scene_id: str            # system:index
    time: datetime           # UTC from system:time_start
    cloud_pct: float         # CLOUDY_PIXEL_PERCENTAGE
    relative_orbit: int      # SENSING_ORBIT_NUMBER
    spacecraft: str          # SPACECRAFT_NAME: 'Sentinel-2A' | 'Sentinel-2B'
    sun_zenith_deg: float    # MEAN_SOLAR_ZENITH_ANGLE
    view_zenith_deg: float   # MEAN_INCIDENCE_ZENITH_ANGLE_B12
    @property
    def amf(self) -> float:  # 1/cos(sun) + 1/cos(view)

def list_scenes(roi: ROI, start, end, *, max_cloud: float = 80.0) -> list[S2Scene]
    # COPERNICUS/S2_HARMONIZED (L1C), filterBounds/date/cloud; map each image to
    # ee.Feature(None, {props}) and getInfo the FeatureCollection — never getInfo the
    # ImageCollection itself (band metadata bloat). Sort by time.
def pick_reference(target: S2Scene, candidates: list[S2Scene], *,
                   max_cloud: float = 30.0, max_days: int = 120) -> S2Scene | None
    # pure, unit-tested: exclude the target; require cloud_pct ≤ max_cloud and
    # |Δt| ≤ max_days; score = |Δt|_days + (0 if same relative_orbit else 30)
    # + (0 if same spacecraft else 5); return argmin or None
```

`methane/retrieval.py`:
```python
@dataclass(frozen=True)
class RetrievalChip:
    scene: S2Scene
    grid: GridSpec                     # from ee.pixels.grid_for(bbox, 20)
    bands: dict[str, np.ndarray]       # (H,W) float32 TOA reflectance; NaN = masked

CHIP_BANDS = ("B11", "B12", "B04", "B03", "B02")   # 5 ≤ MAX_BANDS; RGB = UI context

def fetch_chip(scene: S2Scene, bbox: BBox, *, scale_m: int = 20) -> RetrievalChip
    # ee.Image('COPERNICUS/S2_HARMONIZED/' + scene_id) — or filter the collection on
    # system:index — .unmask(_FILL) with _FILL = -9999 BEFORE fetch_pixels, then
    # fill→NaN and DN/1e4 → reflectance. Refuse grids > 1024×1024 (ValueError) so a
    # fat-fingered bbox can't fan out into a windowed mega-fetch.

@dataclass(frozen=True)
class MbspResult:
    delta_r: np.ndarray    # (H,W) fractional signal, NaN-safe
    c: float               # final calibration constant
    c_initial: float
    n_excluded: int        # pixels dropped by the refit

def mbsp(r11: np.ndarray, r12: np.ndarray) -> MbspResult
    # Varon et al. 2021: ΔR = (c·R12 − R11)/R11, c from zero-intercept least squares of
    # R11 on R12: c = Σ(R11·R12)/Σ(R12²) over valid pixels; then ONE refit excluding
    # |ΔR| > 1σ so the plume can't bias its own calibration.
def mbmp(target: MbspResult, reference: MbspResult) -> np.ndarray
    # element-wise ΔR_target − ΔR_reference (grids are identical by construction:
    # same bbox+scale ⇒ same GridSpec; EE resamples each scene onto it)
```

Column retrieval (in `detect.py`, using stage 1): invert **each pass separately with its own
scene's AMF and spacecraft**, then subtract:
`ΔΩ_MBMP = invert(ΔR_target; AMF_t, sat_t) − invert(ΔR_ref; AMF_r, sat_r)` — this is
Varon's MBMP definition and handles S2A-target/S2B-reference pairs correctly.

`methane/plume.py` (works on any 2-D field + grid; unit = whatever the field is in):
```python
@dataclass(frozen=True)
class PlumeMask:
    mask: np.ndarray           # (H,W) bool
    sigma: float               # robust background σ of the field
    k_sigma: float
    n_pixels: int
    area_m2: float

def robust_sigma(field: np.ndarray) -> float          # 1.4826 · MAD, NaN-aware
def pixel_area_m2(grid: GridSpec) -> float            # from x/yscale · _M_PER_DEG · cos(lat)
def detect_plume(field: np.ndarray, grid: GridSpec, *,
                 k_sigma: float = 2.0, min_area_px: int = 5,
                 opening: bool = True,
                 source_rc: tuple[int, int] | None = None) -> PlumeMask
    # threshold field ≥ k·σ (the field is ΔΩ or ΔXCH4 — positive enhancements);
    # optional 1-px binary_opening; scipy.ndimage.label with 8-connectivity
    # (structure=np.ones((3,3))); drop components < min_area_px; keep components
    # intersecting a 7×7 window around source_rc, else the component containing the
    # max-enhancement pixel; empty result → mask of False (not an error)
def mask_outline_geojson(mask: np.ndarray, grid: GridSpec) -> dict
    # rasterio.features.shapes(mask.astype(np.uint8), transform=Affine(*grid.affine))
    # → MultiPolygon FeatureCollection in EPSG:4326
```

`methane/ime.py`:
```python
def ime_kg(delta_omega: np.ndarray, mask: np.ndarray, grid: GridSpec) -> float
    # Σ_mask ΔΩ · A_pix · M_CH4 (NaN-in-mask contributes 0; count them as a QC flag)
def plume_length_m(mask: np.ndarray, grid: GridSpec) -> float     # √(n_px · A_pix)
def u_eff_ms(u10: float) -> float                                 # UEFF_ALPHA·u10 + UEFF_BETA

@dataclass(frozen=True)
class McParams:
    n: int = 500
    seed: int = 0
    k_grid: tuple[float, ...] = (1.5, 1.75, 2.0, 2.25, 2.5)

@dataclass(frozen=True)
class EmissionEstimate:
    q_kg_h: float                  # MC median
    q_sigma_kg_h: float            # MC std
    percentiles: dict[str, float]  # p05, p25, p50, p75, p95
    histogram: dict[str, list[float]]   # {"edges": 25, "counts": 24} for the UI
    ime_kg: float; l_m: float; u_eff_ms: float
    u10_ms: float; sigma_u10_ms: float; wind_from_deg: float
    n_mc: int

def quantify(delta_omega, grid, wind: WindSample, sigma_u10: float, *,
             k_sigma: float = 2.0, min_area_px: int = 5,
             source_rc=None, mc: McParams = McParams()) -> tuple[EmissionEstimate, PlumeMask]
```
Monte-Carlo design (n = 500, seeded `np.random.default_rng(seed)`; joint per draw):
1. **Mask/threshold jitter:** precompute `detect_plume` + IME + L for each k in `k_grid`
   once (5 labelings, not 500); each draw picks a k uniformly.
2. **Wind:** u10 ~ Normal(U10, σ_u10) truncated at ≥ 0.1 m/s; σ_u10 comes from the caller
   (see wind below).
3. **Retrieval noise:** bootstrap n_mask values from the off-plume ΔΩ population, add their
   (signed) sum · A_pix · M_CH4 to the drawn IME.
4. **IME model error:** multiply the draw's Q by Normal(1, IME_MODEL_SIGMA_FRAC).
Q_draw = u_eff(u10_draw) / L_k · IME_draw · 3600. The returned PlumeMask is the k = k_sigma
one (the display mask).

Overpass wind (used by `detect.py`, no new module — extends nothing):
`sample_wind_at(chip bbox, scene.time, fallback=GLOBAL_ERA5_HOURLY_ID)` for the central
value, plus samples at t − 1 h and t + 1 h;
`sigma_u10 = sqrt(std(speeds)² + SIGMA_U10_FLOOR_MS²)`.

`methane/detect.py`:
```python
@dataclass(frozen=True)
class DetectionResult:
    target: S2Scene; reference: S2Scene | None; method: str
    grid: GridSpec
    delta_r: np.ndarray            # MBSP or MBMP fractional signal
    delta_omega: np.ndarray        # mol/m²
    xch4_ppb: np.ndarray
    plume: PlumeMask
    emission: EmissionEstimate
    wind: WindSample
    calibration: dict[str, float]  # c_target, c_ref, n_excluded_*
    flags: list[str]               # e.g. 'clipped_inversion', 'nan_in_mask', 'no_plume'

def analyze(bbox: BBox, target_scene_id: str, *,
            reference_scene_id: str | None = None,   # None → pick_reference (MBMP)
            method: str = "mbmp",                     # 'mbsp' skips the reference pass
            k_sigma: float = 2.0, min_area_px: int = 5,
            source_lonlat: tuple[float, float] | None = None,
            mc: McParams = McParams(),
            on_progress: Callable[[int, int, str], None] | None = None,
            cancel: threading.Event | None = None) -> DetectionResult
```
Progress steps (fixed, so the UI can label them): 1 list scenes / resolve target,
2 pick + fetch reference chip, 3 fetch target chip, 4 sample wind (×3), 5 retrieve + invert,
6 mask, 7 Monte Carlo. Check `cancel` between steps → `JobError("cancelled")`. No plume above
threshold is a **valid result** (`flags += ['no_plume']`, Q = None fields NaN→ null in API),
not an exception — screening for absence is a legitimate use.

`methane/tropomi.py` (Tier 1, EE bulk reduction + pure stitching):
```python
@dataclass(frozen=True)
class Hotspot:
    lat: float; lon: float
    mean_enh_ppb: float; max_enh_ppb: float
    score: float               # mean_enh / robust σ of all cells
    weeks_flagged: int; weeks_observed: int

def screen_region(bbox: BBox, start: date, end: date, *,
                  background_days: int = 30, cell_deg: float = 0.05,
                  sigma_thresh: float = 2.0, top_n: int = 50,
                  on_progress=None, cancel=None) -> list[Hotspot]
```
Mechanics: background image = per-pixel median of the QA'd CH4 collection
(`get_trace_gas_collection("CH4", …)`) over `[start − background_days, start)`. Split
`[start, end)` into ISO weeks; per week: mean image − background, `reduceRegions` over the
cell lattice (reuse the `wind_grid` cell pattern — mean + count reducers, one `ee_call` per
week). Stitching, per-cell weekly flags (> sigma_thresh · robust σ), persistence counts and
ranking are pure functions on the returned feature lists (offline-tested). Score-ranked
`top_n` cells returned; a typical 3-month screen is ~13 `ee_call`s.

`methane/validation.py` (pure — no EE):
```python
@dataclass(frozen=True)
class ReferenceEvent:  # mirrors the DB row, minus ids
def parse_events(data: bytes, *, fmt: Literal["csv", "geojson"], source: str) -> list[ReferenceEvent]
    # CSV: tolerant header mapping — lat/latitude, lon/lng/longitude, date/datetime/
    #   detection_date, rate/q/source_rate_t_h (t/h → kg/h ×1000), sigma/uncertainty.
    #   Unparseable rows are skipped and counted, not fatal.
    # GeoJSON: Point features; same property aliases.
def haversine_km(lat1, lon1, lat2, lon2) -> float
def match_detection(det_lat, det_lon, det_time, events, *,
                    max_km: float = 15.0) -> tuple[str, list[int]]
    # verdict: 'confirmed'  = ≥1 event within max_km AND ±14 days
    #          'plausible'  = ≥1 event within max_km AND ±60 days
    #          'unvalidated' otherwise
    # 'contradicted' is never auto-assigned — it's a human call via PATCH.
```

### New/changed API surface (all `/api`, `make gen` in the same commit)

| Route | Stage | Notes |
|---|---|---|
| `GET/POST /methane/sites`, `PATCH/DELETE /methane/sites/{id}` | 8 | plain CRUD, 409 dup name; no EE |
| `GET /methane/sites/{id}/scenes?start&end&max_cloud` | 8 | `ensure_ee`; `S2Scene` list + `ref_ok` flag (cloud ≤ 30) |
| `POST /methane/analyze` → `{job_id}` | 8 | validates site/roi + dates at request time; job kind `methane_analyze`; result `{"detection_id"}` |
| `GET /methane/detections?site_id&status&limit&offset` | 8 | summary rows for the feed |
| `GET /methane/detections/{id}` | 8 | detail: numbers, params, mask GeoJSON, overlay bounds `[[w,n],[e,n],[e,s],[w,s]]`, validation |
| `PATCH /methane/detections/{id}` | 8 | `{status?, notes?}` (accept/reject/annotate) |
| `DELETE /methane/detections/{id}` | 8 | row + npz file |
| `GET /methane/detections/{id}/overlay.png?vmin&vmax` | 8 | Pillow-rendered RGBA (below-vmin transparent) |
| `GET /methane/detections/{id}/array.npz` | 8 | `FileResponse` |
| `POST /tiles` gains optional `methane_ref: {start, end}` | 8 | unlocks `CH4_ANOMALY` quicklook via `compute_methane_anomaly`; builder products still 422 **without** it |
| `POST /methane/screening` → `{job_id}` | 9 | job kind `methane_screening`; hotspot list (≤ top_n) fits `result_json` |
| `POST /methane/validation/import` (multipart: file + `source` + `fmt`) | 9 | → `reference_events`; returns `{imported, skipped}` |
| `GET /methane/validation/events` | 9 | list for the UI table |
| `POST /methane/detections/{id}/validate` | 9 | runs `match_detection` vs all events; persists `validation_json` |

EE-touching routes depend on `deps.ensure_ee`. The analyze job's SSE uses `progress` events
only (7 fixed steps, message = step label) — no `points`; the client refetches the detection
on `done` per the Phase 2 contract.

### Detection artifact (`data_dir/detections/{id}.npz`)

Written by the analyze runner; keys: `delta_r`, `delta_omega`, `xch4_ppb`, `mask` (uint8),
`rgb` (H,W,3 float32 from B04/B03/B02 — the UI's context image), `grid` (json str),
`lut_version`, `params` (json str). The overlay endpoint renders from this file on demand
(diskcache the PNG bytes keyed on id+vmin+vmax — cheap to recompute, fine to evict).

---

## Stage 1 — CH4 LUT: constants + generator + committed artifact + conversion (core)

Files: `methane/constants.py`, `methane/conversion.py`, `methane/data/ch4_lut_v1.npz`
(generated then committed), `scripts/generate_ch4_lut.py`, `scripts/data/s2_srf_b11_b12.csv`
(committed extract + provenance header), tests.

Script flow (run manually, once, with network): fetch CH4 lines via HAPI (`fetch` of the
main isotopologues over both band ranges into a local `.data` scratch dir — use the
scratchpad, don't commit HITRAN tables) → cross sections → band transmittances on the
ΔΩ × AMF grid for S2A + S2B → `.npz` with provenance JSON. Keep the script pure-functional
enough that its band-integration helper can be imported and unit-tested with a synthetic
top-hat SRF + constant σ (analytic expectation: m = exp(−AMF·ΔΩ·N_A·1e−4·σ_const) − 1 …
per-band, then the ratio form).

Tests (`packages/core/tests/test_conversion.py`) — all against the **committed** npz:
- structural: keys present, shapes consistent, provenance parses.
- m(0) == 0 for every AMF/satellite; m strictly decreasing in ΔΩ; |m| increasing in AMF.
- **Anchor (Varon 2021, Sect. 2):** at AMF = 1/cos 40° + 1 ≈ 2.305 and ΔΩ = 0.65 mol/m²
  (doubled background), m_MBSP ≈ −0.029 (S2A) and −0.022 (S2B); assert within ±30 %
  (our model omits scattering/other gases/radiance weighting) AND |m_s2a| > |m_s2b|.
- round-trip: `invert(forward(ΔΩ)) ≈ ΔΩ` to 1e-3 over the grid interior; NaN → NaN;
  out-of-range m clips without raising.
- `delta_omega_to_xch4_ppb(0.65) ≈ 1822 ppb` (sanity vs Ω_air).

Commit: `core: CH4 absorption LUT — HITRAN generator + committed v1 + ΔR→ΔXCH4 conversion`.

## Stage 2 — Scene catalog (core)

`methane/scenes.py` per the contract. The FeatureCollection-of-properties pattern keeps the
payload small; parsing + sorting + `pick_reference` are pure.

Tests: `pick_reference` (same-orbit preferred over nearer different-orbit up to the +30
penalty; cloud gate; max_days gate; None when no candidate); property parsing from a canned
`getInfo` payload (monkeypatch `ee_call` in the `openearth.methane.scenes` namespace);
`S2Scene.amf` cardinal values (sun 40°, view 0° → 2.305). Live EE test: Korpezhe bbox,
2018-06-01…07-01 — non-empty, has the 2018-06-19 acquisition.

Commit: `core: methane scene search + reference auto-selection`.

## Stage 3 — Retrieval (core)

`methane/retrieval.py` per the contract. `fetch_chip` reuses `ee.pixels.grid_for` /
`fetch_pixels` unchanged; the only new EE surface is building the single-scene L1C image
(select CHIP_BANDS, `.unmask(_FILL)`).

Tests (offline): synthetic scenes — generate correlated R11/R12 with a known c and an
injected Gaussian "plume" depression in R12; assert `mbsp` recovers c within 1 % **only
with** the refit (the plumeless fit must show the bias, proving the refit matters);
NaN propagation; `mbmp` cancels a shared surface-structure field (inject the same structured
background into two synthetic passes, assert residual σ drops); chip-size refusal > 1024².
Fill-value → NaN conversion from a faked `computePixels` structured payload.
Live EE: one 2 km Korpezhe chip, bands finite, reflectance in (0, 1.5).

Commit: `core: calibrated MBSP/MBMP retrieval on computePixels chips`.

## Stage 4 — Plume masking (core)

`methane/plume.py` per the contract (scipy dep lands here).

Tests: synthetic Gaussian plume + Gaussian noise — recovered mask area within 25 % of truth
at k = 2, salt-noise-only field yields empty mask; min-area filter; opening removes
single-pixel speckle but preserves a 20-px plume; component selection by source window vs
max-pixel fallback; `mask_outline_geojson` round-trips through `shapely`-free assertions
(coordinates within bbox, ring closed); `pixel_area_m2` vs hand value at lat 38°.

Commit: `core: plume masking — robust σ threshold + connected components`.

## Stage 5 — IME + Monte-Carlo quantification (core)

`methane/ime.py` per the contract.

Tests: `ime_kg`/`plume_length_m` hand-computed on a 3-px mask; `u_eff_ms(3.2) == 1.506`;
**synthetic golden**: build a Gaussian-plume ΔΩ field with known IME₀/L₀, fixed wind
u10 = 4 m/s, run `quantify` with `mc=McParams(n=1)` and noise terms forced to zero (seeded)
→ Q equals (U_eff/L₀)·IME₀·3600 exactly; full MC (n = 500, seed fixed) → median within
15 % of the deterministic Q, percentiles monotone, histogram counts sum to n; determinism:
same seed ⇒ identical estimate; truncation: u10 draws never < 0.1.

Commit: `core: IME quantification with joint Monte-Carlo uncertainty`.

## Stage 6 — Orchestrator + golden test (core)

`methane/detect.py` per the contract; wires scenes → chips → wind(×3) → retrieval →
inversion (per-pass AMF/satellite, then subtract for MBMP) → masking (on the ΔΩ field) →
quantify. `flags` populated: `no_plume`, `clipped_inversion` (any pixel hit the LUT edge),
`nan_in_mask`, `different_orbit_reference`, `wind_fallback_used`.

Tests: end-to-end offline with `list_scenes`, `fetch_chip`, `sample_wind_at` monkeypatched
**in the `openearth.methane.detect` namespace** — synthetic pair with an injected plume of
known Q recovers Q within 20 %; `on_progress` called with (i, 7, label) in order; cancel
between steps raises `JobError`; `no_plume` path returns a result (not an exception) with
emission fields NaN; MBSP method skips reference fetch (progress total still 7, step 2
message "skipped").

Commit: `core: methane detection orchestrator + synthetic golden path`.

## Stage 7 — S5P screening (core)

`methane/tropomi.py` per the contract.

Tests (offline): weekly chunking boundaries; flag/persistence/ranking stitch from canned
per-week feature payloads (monkeypatch `ee_call`); robust-σ scoring; cancel between weeks.
Live EE: Turkmenistan bbox, one month of 2023, returns ≥ 1 hotspot, all lat/lon inside bbox.

Commit: `core: S5P Tier-1 screening — weekly enhancement lattice + persistence ranking`.

## Stage 8 — API: DB + sites/scenes/analyze/detections + artifacts (api)

Files: `db.py` (migration 3 + busy_timeout), `models.py` (SQLModel rows), `schemas.py`
(SiteIn/Out, SceneInfoOut, AnalyzeRequest {site_id | roi, target_scene_id,
reference_scene_id: str | None, method, k_sigma, min_area_px, source_lonlat?, seed?},
DetectionOut, DetectionDetailOut, TilesRequest.methane_ref), `routers/methane.py`,
`services/methane.py`, `services/methane_render.py` (npz → RGBA PNG via Pillow; colormap =
linear interpolation over the catalog's CH4 diverging palette, vmin default 0, vmax default
p98 of the array), `services/tiles.py` (`build_image` routes `builder == "methane_anomaly"`
+ `methane_ref` → `compute_methane_anomaly`; keep the 422 when `methane_ref` is absent),
lifespan (detections dir, site seeding).

Analyze job runner (in `services/methane.py`): calls `detect.analyze` wiring
`on_progress → ctx.progress` and `cancel → ctx.cancelled`; on success writes the npz, then
inserts the detection row **via its own Session** (see write-discipline note), returns
`{"detection_id": id}`. Request-time validation: site exists / roi valid, scene id
non-empty; EE-dependent failures stay inside the job.

Tests (`packages/api/tests/test_methane.py`): migration 3 applies on a fresh tmp DB and on
a v2 DB (both paths); seeding idempotence (boot twice → 7 sites once); sites CRUD + 409;
scenes route with `list_scenes` monkeypatched; analyze flow with `detect.analyze`
monkeypatched by name (canned DetectionResult) — POST → SSE progress (7 steps) → done →
GET detail matches, npz written, columns populated; PATCH status/notes; DELETE removes the
npz; overlay.png returns image/png and honors vmin/vmax; tiles with `methane_ref` calls the
monkeypatched anomaly builder, without it still 422s. `make gen`.

Commit: `api: methane sites/scenes/analyze/detections — migration 3, artifacts, overlay PNG, quicklook tiles`.

## Stage 9 — API: screening + validation (core + api)

Files: `methane/validation.py` (core, pure), `routers/methane.py` additions, screening
runner (wires `screen_region` progress per week).

Tests: `parse_events` CSV header tolerance + t/h→kg/h conversion + skip counting; GeoJSON
path; haversine cardinal check; verdict windows (13 d → confirmed, 45 d → plausible, 90 d →
unvalidated, 20 km → unvalidated); API: import a small CSV fixture (multipart) → rows,
validate a seeded detection → `validation_json` persisted; screening job with
`screen_region` monkeypatched → hotspots in `result_json`. `make gen`.

Commit: `core+api: S5P screening job + IMEO/SRON validation importer and cross-match`.

## Stage 10 — Methane Lab UI (web)

Files: `App.tsx` (third view `"methane"` in the existing switcher — **no router dep**),
`stores/methaneStore.ts`, `map/LabMap.tsx`, `features/methane/`:
`MethanePage.tsx` (3-pane: sites | map | feed), `SiteList.tsx` (list + create/edit dialog;
"screen region" action), `SceneStrip.tsx`, `RunPanel.tsx`, `DetectionFeed.tsx`,
`DetectionDetail.tsx`, `McHistogram.tsx`, `ValidationPanel.tsx`, `ScreeningDialog.tsx`,
`lib/methane.ts` (pure helpers: overlay bounds → maplibre coordinates, histogram → ECharts
option, verdict → badge styling).

- **LabMap** is its own small imperative MapLibre instance (reuses `basemap.ts`), separate
  from Explore's map/stores: raster source for S2 RGB context tiles (existing `/tiles`,
  `single_scene` composite at the target timestamp — zero new backend), optional
  `CH4_ANOMALY` quicklook layer (tiles + `methane_ref` = site date hint), `image` source for
  the detection overlay (`overlay.png` + bounds), GeoJSON line layer for the mask outline,
  a rotated wind-arrow DOM marker (`wind_from_deg`).
- Flow: select site (map flies to bbox, dates seed from hint) → scenes load (table: date,
  cloud %, orbit, spacecraft, ref-ok dot) → pick target; reference defaults to "auto" →
  RunPanel (method toggle, k slider 1–3, min-area, seed) → run → `subscribeJob` progress
  (step labels) → on done, refetch detection → feed card (date, Q ± σ t/h, method, status
  chip, flags) → detail: overlay + outline + wind arrow over RGB context, MC histogram
  (ECharts bar from `histogram`), numbers table (IME, L, U_eff, U10 ± σ, ΔXCH4max,
  calibration c's), accept/reject/notes, validation badge + "validate" button, re-run
  shortcut with edited k. `no_plume` detections render honestly (no overlay pretense, feed
  card says "no plume ≥ kσ").
- ScreeningDialog: bbox = current map view or site, date range, run → ranked hotspot table →
  "create site here" (0.3° box around cell).
- Validation import dialog (file + source select) lives in the Lab header.

Vitest: `lib/methane.ts` helpers (bounds corners, histogram transform, verdict styling);
store transitions (run lifecycle, detail selection). Then drive the full flow in a real
browser via the Playwright/Chrome MCP against live EE: Korpezhe, June 2018, target
2018-06-19, auto reference, run, inspect the detection, accept it.

Commit: `web: Methane Lab — sites, scene picker, analyze runner, detection feed + detail`.

## Stage 11 — Methods doc + event reproduction + exit verification (docs + scripts)

- `scripts/validate_events.py`: hits the running API (or core directly) for the pinned
  events, prints an ours-vs-published table, exits non-zero outside tolerance. Pinned
  targets (published values verified against Varon et al. 2021):
  1. **Korpezhe, 2018-06-19** — published S2 estimate 11.2 ± 5.2 t/h (GHGSat-D coincident:
     11.6 ± 8.8 t/h). Pass: our Q ∈ [5.6, 16.8] t/h with σ overlapping.
  2. **Hassi Messaoud blowout (Oct 2019 – Aug 2020)** — published mean 9.3 ± 5.5 t/h over
     101 plumes. Pass: mean of ≥ 3 cloud-free scenes in Nov 2019–Feb 2020 within ±50 % of
     9.3 t/h. (Digitize 1–2 per-scene values from Varon Fig. 6 during implementation if a
     per-date comparison is preferred; record whichever is used in the methods doc.)
  Note the preset date *hints* (2024) are irrelevant here — the script pins the historical
  event dates itself.
- `docs/methane_methods.md`: retrieval theory (MBSP/MBMP with the exact formulas + refit),
  LUT physics + provenance + anchor comparison, plume masking, IME + U_eff citation, the
  full MC error budget **including which σ's are literature-based vs our modeling choices**
  (SIGMA_U10_FLOOR, IME_MODEL_SIGMA_FRAC), Tier-1 screening method, validation verdict
  rules, limitations (no scattering, surface heterogeneity false positives, ~1–5 t/h
  detection floor, ERA5 vs local wind), and the reproduction results table.
- Docs sweep: `architecture.md` "Built in Phase 3", `roadmap.md` ✅ + as-built one-liner,
  `plan.md` header line, `CLAUDE.md` (methane module map, migration 3, new routes — terse).
- Exit verification: full `make check`; web lint/typecheck/test; one
  `OPENEARTH_EE_TESTS=1` sweep; the Playwright golden path from stage 10; both
  `validate_events.py` targets green (this is the phase gate — if a target misses ±50 %,
  debugging it IS Phase 3 work, not deferrable).

Commit: `docs+tests: methane methods writeup + super-emitter reproduction + Phase 3 exit`.

---

## Deviations from / refinements of plan.md (deliberate)

| Decision | Rationale |
|---|---|
| LUT committed at `packages/core/src/openearth/methane/data/`, not `data/` | repo `data/` is gitignored scratch; the LUT is versioned library payload, loaded via `importlib.resources` |
| Forward model uses T(Ω₀+ΔΩ)/T(Ω₀) with Ω₀ = 0.65 mol/m², per satellite | plan.md's "T_B12/T_B11 − 1" shorthand ignores the background column and the S2A/S2B SRF difference; Varon's reference numbers (−0.029/−0.022) only reproduce with both |
| LUT verified against Varon's two published anchor values, not digitized curves | exact numbers from the same paper beat error-prone figure digitization; monotonicity + round-trip tests cover the curve shape |
| MBMP inverts each pass with its own AMF/satellite, then subtracts columns | that is Varon's ΔΩ_MBMP definition; inverting the ΔR difference at the target's AMF is wrong for mixed S2A/S2B pairs |
| Plume threshold applied to the ΔΩ (enhancement) field, positive tail | plan.md left the sign/domain ambiguous; enhancements are the physical quantity and the σ estimate is domain-consistent with the IME noise bootstrap |
| MC k-jitter via 5 precomputed masks, not 500 relabelings | identical statistics, ~100× less work per detection |
| σ_u10 and IME-model σ are declared modeling constants (1.5 m/s floor, 15 %) | Varon's error terms (GEOS-FP vs mesonet, LES hold-out) aren't reproducible here; honest, documented constants beat fake rigor |
| Analyze runners write `detections` rows from the worker thread (own Session, WAL + busy_timeout) | the Phase 2 single-writer rule protected the manager-owned `jobs` table; a completed-detection insert from the runner is the natural owner and WAL handles it — routing it through the event loop would add a bespoke channel for one insert |
| Detection arrays = npz files under `data_dir/detections/`, not diskcache | the cache is LRU-evictable; detections are primary, reviewable data. Overlay PNGs (recomputable) do use the cache |
| `no_plume` is a persisted, valid detection outcome | absence at a watched site is information; exceptions would poison the feed workflow |
| Site seeding = lifespan empty-check, no `scripts/seed_db.py` | one fewer entry point; idempotent by construction |
| Quicklook = existing `CH4_ANOMALY` builder behind `TilesRequest.methane_ref` | plan.md's "server-side quicklook MBSP" without new EE code; export/inspect inherit it for free via `build_image` |
| Methane Lab keeps the state-based view switcher (no react-router) | third view in an existing pattern; URL routing is backlog ("shareable state") |
| 'contradicted' verdict is manual-only | event lists prove presence, not absence; auto-contradiction from a 30-day-embargoed feed would be dishonest |

## Implementation pitfalls (read before coding)

- **HAPI is script-only.** `hitran-api` lives in the `lut` dependency group; importing
  `hapi` anywhere under `packages/` is a defect. HITRAN line tables are scratch, never
  committed. The generator runs manually — never in CI or tests.
- **L1C fill values:** `computePixels` returns EE's fill for masked pixels — `.unmask(-9999)`
  explicitly and convert to NaN after fetch; do NOT treat DN 0 as data. Sanity-check one
  2024 scene: S2_HARMONIZED has the PB4 radiometric offset already harmonized away, so
  DN/1e4 is correct across years — verify reflectances land in (0, 1.5).
- **Zenith metadata names:** `MEAN_SOLAR_ZENITH_ANGLE` / `MEAN_INCIDENCE_ZENITH_ANGLE_B12`
  — confirm both exist on an L1C scene early (live test in stage 2); a missing property
  must fail loudly in `list_scenes`, not NaN-poison the AMF downstream.
- **Grid identity for MBMP:** both chips must be fetched with the *same* `GridSpec`
  (same bbox + scale) — never `grid_for` per scene footprint.
- **σ for masking vs σ for noise bootstrap** are both computed on the *off-plume* ΔΩ
  population; after masking, recompute σ excluding the mask for the bootstrap (the plume
  would inflate its own error).
- **Wind at overpass:** `scene.time` is UTC ms already; pass the *chip bbox* not the site
  polygon; 3 samples (t, t±1 h) = 6 EE round-trips through the semaphore — fine, don't
  parallelize them specially.
- **`rasterio.features.shapes`** needs `transform=Affine(*grid.affine)` and a uint8 mask;
  GeoJSON rings from rasterio are pixel-cornered — that's acceptable for outlines, don't
  smooth.
- **Pillow RGBA orientation:** array row 0 = grid north row = image top; no flips. MapLibre
  image-source coordinates go [top-left, top-right, bottom-right, bottom-left].
- **JSON NaN:** `result_json` and API responses must map NaN → `null` explicitly
  (`math.isnan` guards when building the payload) — FastAPI will happily emit invalid JSON
  otherwise.
- **mypy:** `scenes`, `retrieval` (fetch half), `tropomi`, `detect` wrap EE chains → scoped
  `warn_return_any = false` per module. Pure modules (`conversion`, `plume`, `ime`,
  `validation`) must pass fully strict — no exemptions there.
- **Seeds everywhere:** `McParams.seed` flows from the API request (default 0) so a re-run
  reproduces a detection bit-for-bit; the MC rng must be local (`default_rng`), never global
  `np.random`.
- **Job cancellation:** check `ctx.cancelled` between the 7 steps AND between screening
  weeks; a cancelled analyze must not leave an orphan npz (write npz + DB row only after
  the full result exists; temp-file + rename).
- **`make gen` in the same commit** as any schema change (stages 8, 9).
- **Don't starve the map:** analyze fetches ≤ 2 chips × a few windows + 6 wind calls through
  the shared semaphore — no extra pools, `MAX_RUNNING_JOBS = 4` already brakes job stampedes.
