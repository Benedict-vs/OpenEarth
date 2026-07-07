<!-- docs/phase5-execution-plan.md — Phase 5 (ML segmentation) execution plan.
     Written 2026-07-06 against main at d551fb5 (Phase 4 merged). Expands the docs/roadmap.md
     "Phase 5" section; where this doc refines or deviates from that sketch (or from plan.md),
     the "Deviations" section says so explicitly.
     Externally checkable facts were re-verified 2026-07-06:
     - CH4Net paper: Vaughan et al. 2024, AMT 17, 2583–2593 (doi:10.5194/amt-17-2583-2024) —
       925 hand-annotated plume masks out of 10,046 S2 images, 23 super-emitter sites (all
       Turkmenistan), train 2017–2020 / test 2021, 200×200 px tiles with all 13 bands
       interpolated to 10 m, annotation guided by MBMP; headline result 84 % plume detection
       vs 24 % for an MBMP-threshold baseline.
     - DATA HAS MOVED AND THE LICENSE IS NOT WHAT plan.md ASSUMED: the Zenodo record
       (8267966) referenced by plan.md is dead (404 — it was the preprint-era link). The
       published paper's data availability points to Hugging Face `av555/ch4net`
       (doi:10.57967/hf/2117): 9.76 GB (masks AND imagery), **CC-BY-NC-ND 4.0, gated**
       (login + terms acceptance required). The GitHub code (github.com/anna-allen/CH4Net)
       is MIT; the paper itself is CC-BY. The dataset's internal file layout could NOT be
       inspected remotely (gated) — see Stage 0.
     - Stack: torch 2.12.1 (cp313 macosx arm64 wheels; MPS needs macOS ≥ 14),
       segmentation-models-pytorch 0.5.0 (in_channels > 4 ⇒ first conv randomly initialized
       even with encoder_weights="imagenet"), onnxruntime 1.27.0 (cp313 macosx arm64 wheel
       exists), torch.onnx.export(dynamo=True) is the recommended exporter for opset ≥ 18.
     - Known hazard: open pytorch issues report MPS unavailable on macOS 26 (Tahoe) with
       torch 2.9–2.10 era builds — check torch.backends.mps.is_available() before assuming
       MPS; CPU fp32 is an acceptable fallback at this dataset size. -->

# Phase 5 — ML segmentation: execution plan

**Goal (roadmap):** CH4Net masks + GEE chip-rebuild pipeline (license check first); U-Net
(smp, resnet18, physics-informed channels) with site-held-out CV; eval vs physics baseline;
ONNX export; `/methane/ml/scan`; ML candidates in the detection feed; physics/ML disagreement
flags. *Exit: site-held-out scene-level F1 ≥ physics baseline; ONNX inference < 1 s/chip.*

**Branch:** `v2/phase5-ml`, cut from **main** (not from the Phase 3.5 branch). One commit per
stage, prefixed `ml:` / `core:` / `api:` / `web:` / `docs:`. After every stage: `make check`;
after any API schema change: `make gen` in the same commit.

## Relationship to Phase 3.5 (runs in parallel — no blocking dependency)

Phase 5 needs nothing from Phase 3.5. The U-Net's physics channels are ΔR fields from
`retrieval.py` (`mbsp`/`mbmp`), which sit *upstream* of the LUT — 3.5 Stage 3 (LUT v4) never
touches them, and 3.5 Stage 2 only changes the masking domain inside `detect.py`. Three
coordination points, all mechanical:

1. **Physics-baseline masks use the ΔR domain from day one** (threshold on `−ΔR_MBMP` via
   `plume.detect_plume`) — that is where 3.5 Stage 2 is taking the codebase, and it makes the
   Phase 5 baseline immune to the 3.5 merge. Do not build the baseline on ΔΩ-domain masks.
2. `ALGO_VERSION` (currently 3) will be bumped to 4 and 5 by 3.5. Phase 5 needs **no** bump
   of its own (scan results live in the DB keyed by `model_version`, not in the diskcache);
   if a conflict appears at merge, 3.5's number wins.
3. Both branches touch `docs/roadmap.md` / `CLAUDE.md` and Phase 5 adds a methods section —
   rebase onto main after each 3.5 stage merges; never edit `methane_methods.md` §1–§8 here
   (Phase 5 adds a new §9 only).

**Standing rules (in addition to the Phase 3/3.5 sets, which still apply):**

- **License wall.** CH4Net data is CC-BY-NC-ND 4.0 and gated. Consequences, non-negotiable:
  nothing derived from it is ever committed — no masks, no imagery, no rebuilt chips, no
  scene manifests, no trained weights. Everything lives under `data_dir/ml/` (already
  git-ignored by the root `data/` rule — verify the actual `data_dir` in `.env` is ignored
  too). The repo commits only: code, configs, and **metrics/provenance JSON** (numbers about
  the model, not the data). NC is satisfied (personal research); ND means the trained model
  must not be published — record this in the model manifest and in the "public deployment"
  backlog caveat. The HF token stays in the environment, never in the repo.
- **torch/smp never appear under `packages/core` or `packages/api`.** The API serves via
  onnxruntime only. Extend the `test_no_ui_deps.py` mechanism with a `test_no_ml_deps.py`
  that walks core+api sources and rejects `torch`/`segmentation_models_pytorch` imports.
- **Train/serve consistency is the design invariant.** Whatever function builds the input
  tensor for training is byte-for-byte the function the API calls at scan time. That is why
  channel building lives in **core** (pure NumPy) and why chips are rebuilt through our own
  `fetch_chip`, not taken from the HF imagery (their tiles are Sentinel-Hub L1C interpolated
  to 10 m; ours are GEE L1C at 20 m — training on theirs would deploy a distribution shift).
- **The model is a candidate ranker feeding the human-review feed, never an autonomous
  detector.** Every UI/docs surface says so. Physics stays the load-bearing tier.
- Live runs (chip export, training, live scan) are never in CI. CI trains nothing; ml unit
  tests use tiny synthetic tensors.

---

## Stage overview and dependency order

| # | Stage | Package(s) | Size | Depends on |
|---|-------|-----------|------|------------|
| 0 | License gate + gated download + dataset inventory | scripts | S | — (manual HF step) |
| 1 | `packages/ml` skeleton + `core` channels module + chip-rebuild exporter | ml + core + scripts | L | 0 |
| 2 | Training + site-held-out CV + physics baseline eval | ml | L | 1 |
| 3 | ONNX export + torch↔ORT parity + model manifest | ml | M | 2 |
| 4 | API `/methane/ml/scan` + detection-feed integration + web UI + docs | api + web + docs | L | 3 |

Strictly sequential. Stage 0 is deliberately tiny: the roadmap's "license check first" gate
has already *fired* during planning (see header) — Stage 0 executes its consequences.

---

## Pinned contracts

### Stage 0 — license gate + data acquisition

- **Manual prerequisite (user, not agent):** accept the gate at
  `huggingface.co/datasets/av555/ch4net` and export `HF_TOKEN`.
- `scripts/fetch_ch4net.py` (live, manual): `huggingface_hub.snapshot_download` →
  `data_dir/ml/ch4net/raw/`. `huggingface_hub` goes in a new root `[dependency-groups]`
  entry `ml-data` (same pattern as `lut` — never a package dependency).
- **Inventory step (the real deliverable):** the dataset's internal layout is unverified
  (gated). After download, inspect it against the MIT GitHub code (`anna-allen/CH4Net`,
  whose loaders define the expected layout) and write
  `data_dir/ml/ch4net/inventory.json` (file counts, mask format, tile extent/georeferencing,
  how scene IDs/dates and site coordinates are recorded, positive/negative convention).
  Committed to the repo: only `docs/methane_methods.md` §9 gains a short "dataset" paragraph
  with aggregate facts (925/10,046/23 sites, license, provenance) — cite the paper, not files.
- **Resolve at inventory time (blocking questions for Stage 1, answers go in the Stage 1
  commit message):** (a) exact tile extent (paper text implies ~2×2 km at 200 px/10 m —
  the "0.01°" phrasing in the paper does not square with that; measure it); (b) are the
  9,121 unannotated images labeled plume-free by construction or merely unannotated (the
  paper's 2021 test protocol implies empty masks = negatives — confirm from their code);
  (c) are S2 product IDs present or only date+site (date+site → resolve product IDs via
  `list_scenes` over `COPERNICUS/S2_HARMONIZED`).

Commit 0: `docs: CH4Net license gate + acquisition notes (CC-BY-NC-ND — data never committed)`.

### Stage 1 — `packages/ml` + core channels + chip rebuild

**New workspace member `packages/ml`** (dist `openearth-ml`, import `openearth_ml`) — auto-
joins via `members = ["packages/*"]`. Deps: `openearth` (workspace), `torch>=2.12`,
`segmentation-models-pytorch>=0.5`, `onnxruntime>=1.27` (for the parity test), `typer`.
Root `pyproject.toml` additions:

- `[tool.uv.sources]` pin torch to the CPU index on Linux
  (`https://download.pytorch.org/whl/cpu`, marker `sys_platform == 'linux'`) so CI never
  downloads CUDA wheels; macOS keeps the default arm64 wheel. Verify uv syntax at
  implementation — this is the documented uv pattern, but the exact table shape has churned.
- mypy: add `openearth_ml` to `packages`, with a relaxed override block
  (`module = ["openearth_ml.*"]`: `disallow_untyped_decorators = false`,
  `warn_return_any = false`) + `ignore_missing_imports` for
  `segmentation_models_pytorch.*`. Signatures stay typed regardless.
- pytest `testpaths` gains `packages/ml/tests`; ruff picks the package up automatically;
  `known-first-party` gains `openearth_ml`.

**Core: `openearth/methane/channels.py`** — pure NumPy, strict-mypy, offline-tested; used
identically by training and serving:

```python
CHANNELS = ("mbmp_delta_r", "mbsp_delta_r", "ratio_b12_b11", "b12", "b11")  # order is API

def build_channels(target: RetrievalChip, reference: RetrievalChip) -> NDArray[np.float32]
    # (H, W, 5) — mbsp()/mbmp() from retrieval.py; NaN-safe (invalid px → 0 after norm)
def normalize(x, stats: ChannelStats) -> NDArray[np.float32]   # (x − median) / (1.4826·MAD)
def pad_to_multiple(x, m: int = 16) -> tuple[NDArray, PadSpec] # reflect-pad; PadSpec undoes it
def candidates_from_prob(prob, *, threshold: float = 0.5, min_px: int) -> list[MlCandidate]
    # connected components on prob ≥ threshold (reuse plume.py's component machinery);
    # per-candidate: px count, mean/max prob (score), outline polygon (same GeoJSON shape
    # the physics mask uses)
```

`ChannelStats` (per-channel median/MAD) is *data*, not code — computed from training chips,
frozen into the model manifest (Stage 3), and applied verbatim at serve time. Offline tests:
channel order stability, MBSP/MBMP wiring against tiny synthetic chips, pad round-trip,
`candidates_from_prob` on synthetic blobs, NaN handling.

**`scripts/export_ch4net_chips.py`** (live, resumable): for every annotated scene + a
site-balanced sample of negatives (target ≈ 2× positives per site — full 10 k would be
~hours of computePixels for no eval benefit): resolve the S2 product ID, pick the MBMP
reference via our own `pick_reference` (consistency with serving beats matching CH4Net's
references), `fetch_chip` target+reference over the CH4Net tile bbox at 20 m, `build_channels`,
resample the CH4Net mask onto our EPSG:4326 20 m grid (nearest-neighbor; pure grid math via
`ee/pixels.grid_for` — offline-testable), write one `npz` per sample + a manifest JSON
(status per sample: ok / no-scene / cloud-fail / ref-fail) under `data_dir/ml/ch4net/chips/`.
Resumable = skip existing; all EE round-trips through `ee_call`. Offline test: mask-regrid
round-trip on synthetic masks (area preserved within tolerance at the 10 m→20 m step).

Commits: `core: methane channel stack for ML tier (pure NumPy)` and
`ml: package skeleton + CH4Net chip-rebuild exporter`.

### Stage 2 — training + evaluation

**`openearth_ml` modules:**

- `data.py`: npz chip dataset; **GroupKFold by site, 5 folds** — the 23 sites are all
  Turkmenistan O&G, so random splits would leak surface texture (the reason this is pinned).
  Augmentation: D4 only (flips + rot90) — no photometric jitter on physical channels.
- `models.py`: `smp.Unet(encoder_name="resnet18", encoder_weights="imagenet", in_channels=5,
  classes=1)`. Known smp behavior: with in_channels = 5 the first conv is random-init even
  with imagenet weights (rest of the encoder is still pretrained). Ablations (report, don't
  gate): `encoder_weights=None`, and CH4Net's own raw-band channel set — the physics-channel
  claim must be demonstrated, not assumed. If the measured tile size makes 5 encoder stages
  degenerate (bottleneck < 4 px), drop `encoder_depth` to 4 and pad to /16.
- `train.py`: typer CLI, TOML config-driven (`packages/ml/configs/*.toml`). Defaults:
  Dice+BCE (equal weights), AdamW lr 3e-4 / wd 1e-4, cosine decay, batch 16, ≤ 100 epochs
  with early stopping on fold-val Dice, seed 0. Device: `mps` if
  `torch.backends.mps.is_available()` else `cpu` — do not assume MPS works on this macOS 26
  machine (open upstream issues; CPU fp32 is fine at ~2–3 k chips). Checkpoints + CSV logs
  under `data_dir/ml/runs/<name>/`.
- `eval.py`: metrics per held-out fold —
  - **Scene-level F1** (the roadmap gate): a scene is *predicted positive* iff
    `candidates_from_prob(prob, threshold=0.5, min_px=<physics plume.py component minimum>)`
    is non-empty; truth positive iff the CH4Net mask is non-empty. Same rule, same `min_px`,
    for model and baseline.
  - Pixel IoU/Dice on true positives (secondary, reported not gated).
  - **Physics baseline on the identical chips and folds:** `plume.detect_plume` on
    `−ΔR_MBMP` with the pipeline-default `k_sigma`, scored with the exact same scene rule.
    Quote CH4Net's published 84 %-vs-24 % for context only — their baseline and ours differ.

**Per-fold normalization discipline:** fold models use `ChannelStats` from their own training
folds; the **deployed** model is retrained on *all* data after CV (standard practice — say so
in §9) with full-trainset stats; its field-performance estimate is the CV aggregate.

**Committed artifact:** `scripts/data/ml_eval_v1.json` — per-fold scene-level F1/precision/
recall for model and physics baseline, pixel metrics, ablation rows, plus provenance (git
hash, data-manifest sha256, seed, config, fold→site assignment). Offline test: file parses,
schema fields present, `folds == 5`, model-vs-baseline aggregate consistent with the raw rows.

*Exit gate (falsifiable):* mean site-held-out scene-level **F1(model) ≥ F1(physics baseline)**
in `ml_eval_v1.json`. If it fails, that is a finding — diagnose (label noise from
MBMP-guided annotation is the first suspect) before touching architecture; do not proceed to
Stage 3 shipping a model that loses to the baseline.

Commit: `ml: U-Net training + site-held-out CV vs physics baseline (frozen ml_eval_v1)`.

### Stage 3 — ONNX export + parity + manifest

- `export.py`: `torch.onnx.export(model, dummy, path, dynamo=True)`, **opset pinned at 18**,
  dynamic H and W axes (the network is fully convolutional; serve-time chips differ from the
  training tile size — inputs are padded to the stage multiple by `pad_to_multiple`).
- **Parity test (CI-runnable, in `packages/ml/tests`):** random 5-channel tensors →
  torch-eval vs onnxruntime CPU EP, `max |Δ sigmoid| ≤ 1e-4`. Uses an untrained tiny model
  built in the test — no committed weights needed.
- **Model manifest** `plume_unet_v1.json` next to `plume_unet_v1.onnx` under
  `data_dir/ml/models/`: `model_version`, channel order (must equal `channels.CHANNELS`),
  `ChannelStats`, pad multiple, opset, threshold/min_px defaults, training provenance (git
  hash, config, data-manifest sha256, CV F1), and a license line: *"trained on CH4Net
  (CC-BY-NC-ND 4.0) — do not redistribute; not for commercial use."* The manifest is the
  serving contract: the API reads stats and channel order from it, never from code constants.
- Measure and record single-chip ORT CPU latency in the manifest (`latency_ms_p50`,
  chip size used). *Exit gate:* < 1 s/chip (expect ~50–200 ms for a resnet18 U-Net at
  ~128–256 px — comfortable; if not, that is a bug, not a tuning problem).

Commit: `ml: ONNX export (opset 18, dynamic HW) + parity test + model manifest`.

### Stage 4 — API scan + feed + web

**Settings:** `OPENEARTH_ML_MODEL_PATH` (default `{data_dir}/ml/models/plume_unet_v1.onnx`;
manifest resolved as sibling `.json`). `packages/api` gains a hard `onnxruntime>=1.27` dep
(plan.md pin: api depends on onnxruntime only, never torch).

**`services/ml.py`:**

- Lazy singleton `ort.InferenceSession` (CPU EP) + manifest load; missing model file →
  `503` with a "model not installed — see docs" detail on submit (the app must keep working
  with no model present; `create_app()` stays EE-free *and* model-free at creation).
- `ml_scan` job runner (kind `"methane_ml_scan"`, same JobManager pattern as
  `methane_analyze`/`screening`): params `{site_id, start, end, max_scenes?}` →
  `list_scenes` → per scene: `pick_reference`, two `fetch_chip`s, `build_channels` +
  `normalize` + `pad_to_multiple`, ORT forward, `candidates_from_prob`. Scenes with a
  non-empty candidate list become **detection rows** (`source="ml"`), written off-loop like
  the analyze runner (migration-3 `busy_timeout` already covers this): mask + prob map into
  the npz artifact (so the existing overlay/`array.npz` routes work), `result_json` gains
  `score` (max candidate prob), `model_version`, `n_candidates`, and **single-pass Q**: run
  the per-pass ΔΩ inversion + IME + `sample_wind_at` over the ML mask (no MC — `q_sigma`
  null) so ML candidates are magnitude-comparable in the feed without pretending to a full
  uncertainty budget.
  - **Disagreement flag:** if a physics detection exists for the same `site_id` +
    `scene_id`, set `result_json.disagreement` ∈ {`agree`, `ml_only`}; symmetric check is a
    feed-level computed field, not a physics-row mutation.
- SSE: `progress` per scene (`{scanned, hits}`), `done` → `{detection_ids}`.
- Routes (`routers/methane.py`): `POST /methane/ml/scan` (`dependencies=[Depends(ensure_ee)]`)
  → `JobCreated`; `GET /methane/ml/status` → `{model_loaded, model_version, latency_ms_p50}`
  for the Settings page. Existing feed/detail/overlay routes serve ML rows unchanged
  (`source` filter param on `GET /methane/detections` if not already present). `make gen`.
- API tests: fake the ORT session + core fns by monkeypatching into `services.ml` (the
  established pattern); scan job end-to-end with a fake session emitting a synthetic prob
  map; 503-without-model; detection row shape.

**Web (Methane Lab):** RunPanel gains an "ML scan" action (SSE job UX cloned from
ScreeningDialog); DetectionFeed: source badge (physics/ml) + score column + source filter;
DetectionDetail: renders the ML overlay via the existing overlay route, shows
`model_version`, score, disagreement chip, and a fixed caption: *"ML candidate — requires
review; not an autonomous detection."* Settings view: ML model status line. Playwright-MCP
manual pass on the golden path.

**Docs:** `methane_methods.md` **§9 — ML tier** (dataset + license wall, channels, CV design,
eval table from `ml_eval_v1.json`, candidate-ranker framing, MBMP-annotation label-noise
caveat, ND consequence for any future public deployment); roadmap Phase 5 ✅ + as-built
one-liner; CLAUDE.md updates (packages/ml, scan route, standing license rule — terse);
README one paragraph.

*Exit gates:* live scan over one site-month yields ML candidates reviewable in the feed;
disagreement flag observed on at least one physics-analyzed scene; `make gen` diff-clean;
full `make check` + web lint/typecheck/test green.

Commits: `api: /methane/ml/scan — ONNX candidate scan into the detection feed` and
`web+docs: ML candidates in the Lab + methods §9 + roadmap tick`.

---

## Deviations from / refinements of the roadmap and plan.md sketches (deliberate)

| Decision | Rationale |
|---|---|
| Data source = Hugging Face `av555/ch4net` (gated), not Zenodo 8267966 | the Zenodo record is dead (preprint-era); the published paper points to HF (doi:10.57967/hf/2117) — verified 2026-07-06 |
| **License wall: CC-BY-NC-ND 4.0**, so no CH4Net-derived artifact (chips, masks, weights, manifests) is ever committed; model ships via `data_dir` + settings path | plan.md assumed CC-BY; the actual dataset license forbids redistribution of derivatives and commercial use — private-repo research use is fine, publishing weights is not |
| Chips rebuilt through our own `fetch_chip` at 20 m; HF imagery used only for spot QA | train/serve consistency: `/ml/scan` sees our GEE L1C 20 m pipeline, so training must too; also sidesteps ND questions about redistributing their imagery |
| Channel building + candidate extraction live in **core** (`methane/channels.py`), not `packages/ml` | serving needs them without torch; they are pure NumPy and belong with the physics they reuse; `packages/ml` imports them from core |
| Physics baseline masks on `−ΔR_MBMP` from day one | aligns with 3.5 Stage 2's mask domain, decoupling Phase 5's frozen eval numbers from the 3.5 merge order |
| ML detections carry single-pass Q (no MC), `q_sigma` null | magnitude-comparable feed without fake uncertainty; the full MC budget stays a physics-tier feature |
| Negatives = site-balanced ~2× sample, not all 9,121 | export cost scales with computePixels calls; eval quality does not need 9 k negatives |
| Deployed model retrained on all data after CV; CV aggregate is its performance estimate | shipping fold-0 wastes 20 % of a small dataset; standard practice, stated openly in §9 |
| `torch` pinned to the CPU wheel index on Linux via `tool.uv.sources` | default Linux torch drags CUDA wheels (~GBs) into CI for a package that never trains in CI |
| No `ALGO_VERSION` bump | scan results are DB rows keyed by `model_version`; nothing cache-keyed changes semantics |

## Implementation pitfalls (read before coding)

- **The dataset layout is unverified** (gated). Stage 0's inventory is a real gate: tile
  extent, georeferencing, scene-ID availability, and the negative-label convention all feed
  Stage 1 contracts. Do not write the exporter before the inventory exists.
- **Annotation label noise:** CH4Net masks were drawn guided by MBMP — the labels inherit
  MBMP's blind spots. A model "beating the baseline" on these labels means it ranks
  candidates better, not that it sees plumes MBMP cannot. §9 must say this.
- **All 23 sites are Turkmenistan.** Site-held-out CV controls intra-region leakage, not
  geography: expect degraded performance on other surfaces, and say so wherever the scan UI
  or docs could imply generality.
- **smp first-conv random init at in_channels=5** — pretrained-encoder gains are partial by
  construction; that is what the `encoder_weights=None` ablation measures.
- **MPS on macOS 26 may be unavailable** (open torch issues). Detect, log the device, and
  keep CPU runs acceptable. Never let device choice change committed eval numbers silently —
  record the device in `ml_eval_v1.json` provenance.
- **Normalization constants are part of the model.** Serving reads `ChannelStats` from the
  manifest; a model swapped without its manifest must fail loudly (manifest sha or version
  mismatch → refuse to load).
- **Dynamic-shape ONNX:** export with dynamic H/W and *test* a non-training shape in the
  parity test; U-Net + dynamo export has stage-multiple constraints — `pad_to_multiple` at
  serve time is load-bearing, not cosmetic.
- **Job-runner DB writes happen off-loop** — copy the analyze runner's session/`busy_timeout`
  discipline for detection inserts; don't invent a new pattern.
- **`create_app()` stays model-free and EE-free at creation** — lazy ORT session; the
  OpenAPI export script and web CI depend on the app importing with nothing installed in
  `data_dir`.
- **CI weight:** the ml package makes `uv sync --all-packages` heavier (torch CPU). If CI
  time degrades noticeably, cache the uv venv keyed on the lockfile before considering
  anything more invasive.
- **HF token / gated download:** `fetch_ch4net.py` must fail with a clear message when the
  gate has not been accepted; never bake the token or retry-loop the gated endpoint.
