<!-- Stage 4 handoff spec. Stage 4a (backend) is built in this session; 4b (web + docs +
     live-scan exit gate) is the remaining work. The "Backend contracts" section is updated to
     AS-BUILT once the backend lands + make check passes. Committed doc — no CH4Net data. -->

# Phase 5 — Stage 4 handoff (ML scan API → feed → web → docs)

Stages 0–3 are complete and committed (recovery, channels, exporter, training/CV gate,
ONNX+manifest). Stage 4 wires the trained model into the app as a **candidate ranker feeding
human review — never an autonomous detector** (say so on every UI/docs surface).

## Stage 4a — backend (built this session)

**Status: AS-BUILT — see "Backend contracts (as-built)" at the bottom; this section is the plan.**

- Settings: `OPENEARTH_ML_MODEL_PATH` (default `{data_dir}/ml/models/plume_unet_v1.onnx`; manifest
  = sibling `.json`). `packages/api` gains `onnxruntime>=1.27` (never torch).
- `services/ml.py`: lazy singleton `ort.InferenceSession` (CPU EP) + manifest; missing model file →
  `503` on submit (`create_app()` stays EE-free *and* model-free at creation). `methane_ml_scan`
  job (same JobManager pattern as analyze/screening): `{site_id, start, end, max_scenes?}` →
  `list_scenes` → per scene `pick_reference`, two `fetch_chip`s, `build_channels` → `normalize`
  (manifest `ChannelStats`) → `pad_to_multiple` → ORT forward → `candidates_from_prob`. Scenes with
  a non-empty candidate list become detection rows (`source="ml"`), written **off-loop** (own
  Session, worker thread) like `persist_detection`; npz + `result_json`. SSE `progress` per scene
  `{scanned, hits}`; `done` → `{detection_ids}`.
- **Single-pass Q** (magnitude-comparable, no MC → `q_sigma` null): new core helper
  `ime.emission_over_mask(delta_omega, grid, mask, wind, sigma_u10)` (IME over a *given* mask, no
  `detect_plume`, no MC). The scan inverts ΔR→ΔΩ→XCH4 on the already-fetched target chip
  (`conversion.invert_fractional_signal` / `delta_omega_to_xch4_ppb`), thresholds the ML mask, and
  quantifies over it with `sample_wind_at`. The npz carries the same keys as physics
  (`delta_r/delta_omega/xch4_ppb/mask/rgb/grid`) so the existing overlay + `array.npz` routes work.
- **Disagreement flag**: `result_json.disagreement ∈ {agree, ml_only}` — set `ml_only` unless a
  physics detection exists for the same `site_id`+`scene_id`; the symmetric `physics_only` view is
  a feed-level computed field, not a physics-row mutation.
- Routes (`routers/methane.py`): `POST /methane/ml/scan` (`dependencies=[Depends(ensure_ee)]`) →
  `JobCreated`; `GET /methane/ml/status` → `{model_loaded, model_version, latency_ms_p50}`. `make gen`.
- API tests: monkeypatch the ORT session + core fns into `services.ml`; scan end-to-end with a fake
  session emitting a synthetic prob map; 503-without-model; ml detection-row shape + disagreement.

## Stage 4b — web + docs + live scan (REMAINING)

### Web (`apps/web`, Methane Lab) — no-refetch rule applies (paint/layout only; re-mints via `setTiles`)
- **RunPanel**: add an "ML scan" action; SSE job UX cloned from `ScreeningDialog` (progress
  `{scanned, hits}`); on `done`, refresh the detection feed. Inputs: site + date range (+ optional
  max_scenes). Caption near the action: *"ML candidate ranker — proposes scenes for review."*
- **DetectionFeed**: `source` badge (physics / **ml**), a score column (ML: `result_json.score` =
  max candidate prob), and a `source` filter control. (Backend `list_detections` gains a `source`
  filter param.)
- **DetectionDetail**: render the ML overlay via the *existing* overlay route (works because the ML
  npz has `xch4_ppb`); show `model_version`, score, the disagreement chip, and a fixed caption:
  **"ML candidate — requires review; not an autonomous detection."**
- **Settings view**: an ML model status line from `GET /methane/ml/status`
  (`model_loaded`, `model_version`, `latency_ms_p50`); "not installed" state when 503.
- Generated types: run `make gen` after the API schema change; never hand-edit `types.gen.ts`.
- Verify with the Playwright/Chrome MCP on the golden path (scan → feed → detail).

### Docs
- `methane_methods.md` **§9** — expand beyond §9.1/§9.2 with: channels recap, CV design, the eval
  table from `ml_eval_v1.json` (F1 0.597 vs 0.464; per-fold), candidate-ranker framing, the
  **MBMP-annotation label-noise** caveat, the all-23-Turkmenistan geography caveat, and the **ND
  consequence** for any future public deployment of the weights.
- `docs/roadmap.md`: Phase 5 ✅ + a one-line as-built (recovery pivot, model≥baseline, ONNX serve).
- `CLAUDE.md`: terse — `packages/ml`, the scan route, the standing license rule.
- `README.md`: one paragraph on the ML tier.

### Live-scan exit gate (needs the user / EE)
1. Ensure the model is installed: `data/ml/models/plume_unet_v1.onnx` (+ `.json`) — already produced
   by `python -m openearth_ml.train deployed` + `python -m openearth_ml.export` this session.
2. `POST /methane/ml/scan` over one site-month (e.g. a high-plume site like the Korpezhe/Turkmenistan
   preset) → confirm ML candidates appear in the feed, reviewable, with overlays.
3. Confirm the **disagreement flag** is observed on ≥1 physics-analyzed scene (run a physics
   `analyze` on a scene the ML scan also flags, or vice-versa).
4. `make gen` diff-clean; full `make check` + `pnpm --dir apps/web lint && typecheck && test` green.

Commits: `api: /methane/ml/scan — ONNX candidate scan into the detection feed` (4a) and
`web+docs: ML candidates in the Lab + methods §9 + roadmap tick` (4b).

## Standing rules (unchanged)
License wall: nothing CH4Net-derived committed (weights/onnx/manifest/chips/metadata live in
git-ignored `data_dir`). torch/smp never under core or api (onnxruntime only). The model is a
candidate ranker, never autonomous. CI trains nothing / makes no EE calls.

## Backend contracts (AS-BUILT — Stage 4a, `make check` + `make gen` green)

Routes (`routers/methane.py`; both under `/api`):
- `POST /methane/ml/scan` `dependencies=[Depends(ensure_ee)]`, body `MlScanRequest`
  `{site_id: int, start: date, end: date, max_scenes?: int(1..200)}` → `JobCreated {job_id}`.
  503 at submit if the model is not installed; 404 if the site is unknown.
- `GET /methane/ml/status` → `MlStatusOut {model_loaded: bool, model_version?: str,
  latency_ms_p50?: float}` — never raises.
- `GET /methane/detections` gains `source?: str` filter; `DetectionOut` gains `score?: float`
  (ML max-prob; null for physics).

Job `methane_ml_scan` (SSE `progress {done,total,message="scanned i/N, k hit(s)"}`;
result `{detection_ids: [...]}`). Each hit → a `Detection` row `source="ml"`, `method="ml_unet"`,
written off-loop; npz keys `delta_r/delta_omega/xch4_ppb/mask/prob/rgb/grid` (so the existing
overlay + `array.npz` routes work); `q_kg_h` from single-pass `ime.emission_over_mask`,
`q_sigma_kg_h=null`. `result_json` = `{score, model_version, n_candidates, disagreement∈{agree,
ml_only}, flags:[], review:"ML candidate — requires review; not an autonomous detection."}`.

Serving: `services/ml.py` lazy singleton `ort.InferenceSession` (CPU EP) + manifest via
`load_model(settings)` (the test seam — monkeypatch it + `list_scenes`/`pick_reference`/
`fetch_chip`/`sample_wind_at`). Model path: `settings.ml_model_path` or
`{data_dir}/ml/models/plume_unet_v1.onnx` (+ sibling `.json`). `packages/api` deps `onnxruntime`
(never torch). Tests: `packages/api/tests/test_ml.py` (status-absent, 503-without-model,
scan end-to-end, feed score, detail model_version/disagreement/review, overlay PNG).

For the web layer, read `score` off `DetectionOut`, `disagreement`/`model_version`/`review` off
`DetectionDetailOut.result`, and the overlay off the existing `.../overlay.png`.
