#!/usr/bin/env python
"""Offline synthetic-truth benchmark against the S2CH4 dataset (Gorroño et al.
2023, AMT 16:89; Harvard Dataverse doi:10.7910/DVN/KRNPEH v2, CC0 1.0).

The dataset forward-models WRF-LES methane plumes of **known flux** onto three
real Sentinel-2A L1C base scenes, giving a per-pixel truth column enhancement.
This script recomposes the physics pipeline's *pure* steps (retrieval → LUT
inversion → masking → IME quantification) on the file-fed arrays and scores them
against that truth. It never calls ``detect.analyze`` (which is Earth-Engine
bound); it reuses the same core functions and constants, so the benchmark and the
live pipeline invert identically.

    # after `uv run python scripts/fetch_s2ch4.py` populated <data_dir>/s2ch4/
    uv run python scripts/s2ch4_benchmark.py                 # print aggregates
    uv run python scripts/s2ch4_benchmark.py --freeze        # write the vN JSON

Reader facts (verified at planning time; see docs/phase9-execution-plan.md):
  * ``S2TOA`` is (75, 75, 13) float64 TOA **reflectance** in L1C band order
    B01,B02,B03,B04,B05,B06,B07,B08,B8A,B09,B10,B11,B12.
  * scalars ``SZA``/``VZA`` (deg) → the exact per-file AMF; ``U10`` = the true
    10 m wind the plume was transported with.
  * ``xch4`` (75, 75) float64 = per-pixel truth ΔXCH4 as a dimensionless
    column-averaged mole fraction (multiply by 1e9 for ppb).
  * the filename ``…_plume{P}_Q{Y}`` tag carries the TRUE flux Y in **kg/h**
    (Q0 = the plume-free version of the same scene).
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import h5py
import numpy as np
from numpy.typing import NDArray

from openearth.ee.pixels import _M_PER_DEG, GridSpec
from openearth.methane.conversion import (
    delta_omega_to_xch4_ppb,
    invert_fractional_signal,
    load_lut,
    load_mask_lut,
)
from openearth.methane.flare import nhi_hot_mask
from openearth.methane.ime import McParams, emission_over_mask, quantify
from openearth.methane.metrics import (
    log_scatter,
    median_ratio,
    slope_through_origin,
    spearman,
    theil_sen_slope,
)
from openearth.methane.plume import detect_plume
from openearth.methane.retrieval import mbsp
from openearth.methane.wind import WindSample
from openearth.settings import get_settings
from openearth_api.cache import ALGO_VERSION

_REPO_ROOT = Path(__file__).resolve().parents[1]

# All three base scenes are Sentinel-2A (confirmed across all 1345 files); the LUT
# and AMF are per-spacecraft, so this is pinned, not inferred per file.
SPACECRAFT = "Sentinel-2A"

# CLI site key → MGRS tile / human name. One acquisition date per site.
SITE_TILES = {"hassi": "32SKA", "permian": "13SGR", "korpeje": "40SBH"}
TILE_SITES = {v: k for k, v in SITE_TILES.items()}
SITE_NAMES = {"hassi": "Hassi Messaoud", "permian": "Permian", "korpeje": "Korpeje"}

# Position of each retrieval/context band inside the 13-band L1C S2TOA cube.
_BAND_INDEX = {"B11": 11, "B12": 12, "B8A": 8, "B4": 3, "B3": 2, "B2": 1}

# S2A_MSICH4_20210702T101031_N0301_R022_T32SKA_20210702T121947_plume0_Q0
_PRODUCT_RE = re.compile(
    r"^(?P<spacecraft>S2[AB])_MSICH4_"
    r"(?P<sensing>\d{8})T\d{6}_N\d+_R(?P<orbit>\d+)_"
    r"T(?P<tile>[0-9A-Z]{5})_\d{8}T\d{6}_"
    r"plume(?P<plume>\d+)_Q(?P<q>\d+)$"
)


@dataclass(frozen=True)
class ProductName:
    """The identity parsed out of an S2CH4 product filename."""

    label: str
    spacecraft: str  # 'Sentinel-2A' | 'Sentinel-2B' (all files here are S2A)
    site: str  # CLI key: 'hassi' | 'permian' | 'korpeje'
    tile: str  # MGRS tile, e.g. '32SKA'
    acquired: date
    plume: int  # 0..4 (LES plume shape)
    q_true_kg_h: float  # TRUE flux from the Q tag (kg/h); 0 = plume-free


@dataclass(frozen=True)
class Product:
    """One S2CH4 file: retrieval bands, grid, geometry, wind and truth."""

    name: ProductName
    bands: dict[str, NDArray[np.float64]]  # B11, B12, B8A, B4, B3, B2 (H, W)
    grid: GridSpec
    amf: float
    u10_ms: float
    truth_xch4: NDArray[np.float64]  # (H, W) dimensionless mole fraction


def _spacecraft_full(short: str) -> str:
    return {"S2A": "Sentinel-2A", "S2B": "Sentinel-2B"}[short]


def parse_product_name(name: str) -> ProductName:
    """Parse an S2CH4 filename (basename) into its :class:`ProductName`.

    Raises ``ValueError`` on any name that is not an S2CH4 product.
    """
    m = _PRODUCT_RE.match(name)
    if m is None:
        raise ValueError(f"not an S2CH4 product name: {name!r}")
    tile = m.group("tile")
    if tile not in TILE_SITES:
        raise ValueError(f"unexpected tile {tile!r} in {name!r}")
    sensing = m.group("sensing")
    return ProductName(
        label=name,
        spacecraft=_spacecraft_full(m.group("spacecraft")),
        site=TILE_SITES[tile],
        tile=tile,
        acquired=date(int(sensing[:4]), int(sensing[4:6]), int(sensing[6:8])),
        plume=int(m.group("plume")),
        q_true_kg_h=float(m.group("q")),
    )


def _amf(sza_deg: float, vza_deg: float) -> float:
    """Two-way air mass factor 1/cos(θ_sun) + 1/cos(θ_view).

    Mirrors ``openearth.methane.scenes.S2Scene.amf`` exactly (pinned by a parity
    test); replicated here so the reader stays independent of a full S2Scene.
    """
    return 1.0 / math.cos(math.radians(sza_deg)) + 1.0 / math.cos(math.radians(vza_deg))


def _grid_from_latlon(lat: NDArray[np.float64], lon: NDArray[np.float64]) -> GridSpec:
    """An axis-aligned EPSG:4326 grid at the file's true ~20 m sampling.

    The native S2/UTM crop is slightly rotated relative to north (latitude tracks
    columns, longitude tracks rows), but the benchmark works in array space and
    seeds the plume with a truth-field ``source_rc``, so orientation is
    immaterial — only the per-pixel ground area (via ``GridSpec``) matters, and
    that comes from the measured sampling distance along each axis. Distances use
    the library's own ``_M_PER_DEG`` equirectangular convention (as
    ``plume.pixel_area_m2`` does), so ``pixel_area_m2`` returns the true ~400 m².
    """
    h, w = lat.shape
    center_lat = float(lat.mean())
    center_lon = float(lon.mean())
    cos_lat = math.cos(math.radians(center_lat))

    def _mean_step_m(a_deg: NDArray[np.float64], axis: int, to_m: float) -> float:
        # Mean adjacent-pixel spacing along *axis*, in metres.
        return float(np.abs(np.diff(a_deg, axis=axis)).mean()) * to_m

    # Row axis (north-south component dominated by lon here) and column axis: the
    # per-pixel ground step is the hypotenuse of the lat/lon components.
    drow_m = math.hypot(
        _mean_step_m(lat, 0, _M_PER_DEG), _mean_step_m(lon, 0, _M_PER_DEG * cos_lat)
    )
    dcol_m = math.hypot(
        _mean_step_m(lat, 1, _M_PER_DEG), _mean_step_m(lon, 1, _M_PER_DEG * cos_lat)
    )
    yscale = drow_m / _M_PER_DEG
    xscale = dcol_m / (_M_PER_DEG * cos_lat)
    return GridSpec(
        x0=center_lon - 0.5 * w * xscale,
        y0=center_lat + 0.5 * h * yscale,
        xscale=xscale,
        yscale=yscale,
        width=w,
        height=h,
    )


def read_product(path: Path) -> Product:
    """Read one S2CH4 netCDF4/HDF5 file into a :class:`Product`."""
    name = parse_product_name(path.name)
    with h5py.File(path, "r") as f:
        toa = np.asarray(f["S2TOA"][:], dtype=np.float64)  # (H, W, 13) reflectance
        lat = np.asarray(f["lat"][:], dtype=np.float64)
        lon = np.asarray(f["lon"][:], dtype=np.float64)
        sza = float(f["SZA"][()])
        vza = float(f["VZA"][()])
        u10 = float(f["U10"][()])
        truth = np.asarray(f["xch4"][:], dtype=np.float64)
    bands = {b: np.ascontiguousarray(toa[:, :, i]) for b, i in _BAND_INDEX.items()}
    return Product(
        name=name,
        bands=bands,
        grid=_grid_from_latlon(lat, lon),
        amf=_amf(sza, vza),
        u10_ms=u10,
        truth_xch4=truth,
    )


def _iter_product_paths(root: Path, site: str | None) -> list[Path]:
    """All S2CH4 product files under *root*, optionally filtered to one site."""
    paths = [p for p in sorted(root.iterdir()) if p.is_file() and _PRODUCT_RE.match(p.name)]
    if site is not None:
        tile = SITE_TILES[site]
        paths = [p for p in paths if f"_T{tile}_" in p.name]
    return paths


# ── Scoring conventions (declared constants; see docs + the frozen JSON) ──

# Truth mask = pixels ≥ this fraction of the product's peak truth ΔXCH4. Because
# the forward model is exactly linear in flux (verified: Q50000 = 10·Q5000
# pixel-wise), this footprint is IDENTICAL across all Q of a given plume — so it
# never degenerates at low Q (the plan's STOP condition cannot trigger here).
TRUTH_MASK_FRACTION = 0.05

# Q recovery / IME uses the file's TRUE 10 m wind with ZERO uncertainty, isolating
# retrieval+mask+IME error from wind error (the transport wind is known exactly).
SIGMA_U10 = 0.0

# Cross-instrument comparability with scripts/calibration_harness.py: a mask
# dominated by out-of-validity columns is outside the forward model's range.
# (Mirrors the harness's MBSP_VALIDITY_DELTA_OMEGA / _INVALID_FRACTION_MAX; both
# are script-local diagnostics, not core pipeline constants.)
MBSP_VALIDITY_DELTA_OMEGA = 3.0  # mol/m²
INVALID_FRACTION_MAX = 0.20

# Detection-rate-vs-Q bin edges (kg/h); min detectable Q = the lower edge of the
# lowest bin with ≥ 50 % detection across the 5 plume shapes (declared).
_Q_BIN_EDGES = (500, 1000, 2000, 3000, 5000, 7500, 10000, 15000, 25000, 50001)
_MIN_DETECT_RATE = 0.5

# Full MC (seeded, n=500) on every Nth scored product for CI coverage.
_MC_SUBSET_EVERY = 10
_MC = McParams(n=500, seed=0)

# Varon et al. 2021 effective-wind constants, for the α,β decision box.
_VARON_ALPHA, _VARON_BETA = 0.33, 0.45
_U10_SPAN_MIN_MS = 3.0  # adopt-refit gate: U10 diversity floor

_METHODS = ("mbsp", "mbmp")
_SOURCE_MODES = ("hinted", "blind")

# The masking k·σ / min-area come from detect_plume's own defaults (never re-typed).
_K_SIGMA = float(inspect.signature(detect_plume).parameters["k_sigma"].default)
_MIN_AREA_PX = int(inspect.signature(detect_plume).parameters["min_area_px"].default)


@dataclass(frozen=True)
class ProductScore:
    """One (product × method × source-mode) scored against truth."""

    site: str
    plume: int
    q_true_kg_h: float
    method: str
    source_mode: str
    detected: bool
    n_px: int
    iou: float
    q_est_kg_h: float  # NaN when nothing was detected
    xch4_bias_ppb: float  # in-truth-mask mean(ours − truth); NaN if truth empty
    xch4_rms_ppb: float
    invalid_fraction: float
    u10_ms: float
    ime_kg: float
    l_m: float
    # NHI post-floor hot-pixel count on the target chip (per product; expected 0 on
    # the flare-free simulated scenes). Same across a product's method/mode rows.
    n_hot: int = 0
    # MC subset (None unless this product was sampled for the seeded MC):
    ci_contains_truth: bool | None = None
    q_mc_kg_h: float | None = None
    q_mc_sigma_kg_h: float | None = None


@dataclass(frozen=True)
class _Inverted:
    """A retrieval pass inverted through both LUTs (reporting + frozen mask)."""

    reporting: NDArray[np.float64]  # ΔΩ for columns / IME (mol/m²)
    mask: NDArray[np.float64]  # ΔΩ for the footprint (frozen mask LUT)


def _invert_pass(delta_r: NDArray[np.float64], spacecraft: str, amf: float) -> _Inverted:
    """Invert one ΔR field the way detect.py does: reporting LUT + frozen mask LUT."""
    return _Inverted(
        reporting=invert_fractional_signal(delta_r, load_lut(), spacecraft, amf),
        mask=invert_fractional_signal(delta_r, load_mask_lut(), spacecraft, amf),
    )


def _retrieve(
    bands: dict[str, NDArray[np.float64]], spacecraft: str, amf: float
) -> tuple[_Inverted, int]:
    """One retrieval pass, mirroring detect.analyze's ALGO-7 bundle exactly.

    Under ALGO 6 (v1) this is a plain default mbsp; under ALGO 7 (v2) it opts into
    the NHI flare exclusion + robust-σ refit and NaN-s the hot pixels before
    inversion. Returns the inverted pass and the post-floor NHI hot-pixel count
    (expected 0 on the flare-free simulated scenes — the false-positive regression).
    """
    if ALGO_VERSION < 7:
        dr = mbsp(bands["B11"], bands["B12"]).delta_r
        return _invert_pass(dr, spacecraft, amf), 0
    hot = nhi_hot_mask(bands, spacecraft)
    n_hot = int(nhi_hot_mask(bands, spacecraft, dilate=False).sum())
    dr = mbsp(bands["B11"], bands["B12"], robust_cut=True, exclude=hot).delta_r.copy()
    dr[hot] = np.nan
    return _invert_pass(dr, spacecraft, amf), n_hot


def _truth_mask(truth_xch4: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Pixels ≥ TRUTH_MASK_FRACTION of the product's peak truth enhancement."""
    peak = float(np.nanmax(truth_xch4))
    if peak <= 0.0:
        return np.zeros(truth_xch4.shape, dtype=bool)
    return truth_xch4 >= TRUTH_MASK_FRACTION * peak


def _iou(a: NDArray[np.bool_], b: NDArray[np.bool_]) -> float:
    """Intersection-over-union of two boolean masks (0.0 if both empty)."""
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return 0.0
    return float(np.count_nonzero(a & b)) / union


def _invalid_fraction(reporting: NDArray[np.float64], mask: NDArray[np.bool_]) -> float:
    """Fraction of masked reporting-ΔΩ pixels beyond the forward model's validity."""
    in_mask = reporting[mask]
    if in_mask.size == 0:
        return 0.0
    return float(np.mean(np.abs(in_mask) >= MBSP_VALIDITY_DELTA_OMEGA))


def _xch4_bias_rms(
    reporting: NDArray[np.float64], truth_xch4: NDArray[np.float64], truth_mask: NDArray[np.bool_]
) -> tuple[float, float]:
    """In-truth-mask per-pixel ΔXCH4 bias/RMS (ppb): ours − truth×1e9."""
    ours = np.asarray(delta_omega_to_xch4_ppb(reporting), dtype=np.float64)
    truth_ppb = truth_xch4 * 1e9
    sel = truth_mask & np.isfinite(ours)
    if not sel.any():
        return float("nan"), float("nan")
    resid = ours[sel] - truth_ppb[sel]
    return float(np.mean(resid)), float(np.sqrt(np.mean(resid**2)))


def _score_product(
    prod: Product,
    reporting: NDArray[np.float64],
    mask_field: NDArray[np.float64],
    method: str,
    source_mode: str,
    truth_mask: NDArray[np.bool_],
    wind: WindSample,
    run_mc: bool,
    n_hot: int,
) -> ProductScore:
    """Score one product×method×source-mode: detect, IoU, IME Q, ΔXCH4 residual."""
    source_rc = None
    if source_mode == "hinted":
        r, c = np.unravel_index(int(np.argmax(prod.truth_xch4)), prod.truth_xch4.shape)
        source_rc = (int(r), int(c))

    plume = detect_plume(
        mask_field, prod.grid, k_sigma=_K_SIGMA, min_area_px=_MIN_AREA_PX, source_rc=source_rc
    )
    emission = emission_over_mask(reporting, prod.grid, plume.mask, wind, SIGMA_U10)
    bias, rms = _xch4_bias_rms(reporting, prod.truth_xch4, truth_mask)

    ci_ok: bool | None = None
    q_mc: float | None = None
    q_mc_sigma: float | None = None
    if run_mc:
        mc_est, _ = quantify(
            reporting,
            prod.grid,
            wind,
            SIGMA_U10,
            mask_field=mask_field,
            k_sigma=_K_SIGMA,
            min_area_px=_MIN_AREA_PX,
            source_rc=source_rc,
            mc=_MC,
        )
        if np.isfinite(mc_est.q_kg_h) and np.isfinite(mc_est.q_sigma_kg_h):
            q_mc = float(mc_est.q_kg_h)
            q_mc_sigma = float(mc_est.q_sigma_kg_h)
            ci_ok = bool(abs(prod.name.q_true_kg_h - q_mc) <= q_mc_sigma)

    return ProductScore(
        site=prod.name.site,
        plume=prod.name.plume,
        q_true_kg_h=prod.name.q_true_kg_h,
        method=method,
        source_mode=source_mode,
        detected=plume.n_pixels > 0,
        n_px=plume.n_pixels,
        iou=_iou(plume.mask, truth_mask),
        q_est_kg_h=float(emission.q_kg_h),
        xch4_bias_ppb=bias,
        xch4_rms_ppb=rms,
        invalid_fraction=_invalid_fraction(reporting, plume.mask),
        u10_ms=prod.u10_ms,
        ime_kg=float(emission.ime_kg),
        l_m=float(emission.l_m),
        n_hot=n_hot,
        ci_contains_truth=ci_ok,
        q_mc_kg_h=q_mc,
        q_mc_sigma_kg_h=q_mc_sigma,
    )


def score_all(paths: list[Path]) -> list[ProductScore]:
    """Score every Q>0 product against its same-plume Q0 (perfect) reference.

    Reads at most two products at a time (a plume's Q0 + one target), so the full
    1345-file run stays within memory. The MC subset is chosen by a global scored
    counter so it is deterministic and site-balanced.
    """
    by_group: dict[tuple[str, int], list[Path]] = defaultdict(list)
    for p in paths:
        name = parse_product_name(p.name)
        by_group[(name.site, name.plume)].append(p)

    scores: list[ProductScore] = []
    scored_idx = 0
    for (site, plume), group in sorted(by_group.items()):
        q0_path = next((p for p in group if parse_product_name(p.name).q_true_kg_h == 0.0), None)
        if q0_path is None:
            print(f"  ! no Q0 reference for {site} plume{plume}; skipping group", file=sys.stderr)
            continue
        ref = read_product(q0_path)
        ref_pass, _ = _retrieve(ref.bands, SPACECRAFT, ref.amf)  # Q0 is flare-free

        for path in group:
            name = parse_product_name(path.name)
            if name.q_true_kg_h == 0.0:
                continue
            prod = read_product(path)
            t_pass, n_hot = _retrieve(prod.bands, SPACECRAFT, prod.amf)
            truth_mask = _truth_mask(prod.truth_xch4)
            wind = WindSample.from_uv(
                datetime(name.acquired.year, name.acquired.month, name.acquired.day, tzinfo=UTC),
                u=prod.u10_ms,
                v=0.0,
                collection_id="s2ch4_truth_u10",
            )
            fields = {
                "mbsp": (t_pass.reporting, t_pass.mask),
                "mbmp": (t_pass.reporting - ref_pass.reporting, t_pass.mask - ref_pass.mask),
            }
            run_mc = scored_idx % _MC_SUBSET_EVERY == 0
            for method in _METHODS:
                reporting, mask_field = fields[method]
                for mode in _SOURCE_MODES:
                    scores.append(
                        _score_product(
                            prod,
                            reporting,
                            mask_field,
                            method,
                            mode,
                            truth_mask,
                            wind,
                            run_mc,
                            n_hot,
                        )
                    )
            scored_idx += 1
        print(f"  scored {site} plume{plume} ({len(group) - 1} products)")
    return scores


# ── Aggregation ──


def _q_bin_index(q: float) -> int | None:
    """Index of the Q bin containing *q* (kg/h), or None if outside the range."""
    for i in range(len(_Q_BIN_EDGES) - 1):
        if _Q_BIN_EDGES[i] <= q < _Q_BIN_EDGES[i + 1]:
            return i
    return None


def _detection_curve(rows: list[ProductScore]) -> list[dict[str, object]]:
    """Per-bin detection rate across the 5 plume shapes."""
    detected: dict[int, int] = defaultdict(int)
    total: dict[int, int] = defaultdict(int)
    for row in rows:
        i = _q_bin_index(row.q_true_kg_h)
        if i is None:
            continue
        total[i] += 1
        detected[i] += int(row.detected)
    curve: list[dict[str, object]] = []
    for i in range(len(_Q_BIN_EDGES) - 1):
        n = total.get(i, 0)
        curve.append(
            {
                "q_lo_kg_h": _Q_BIN_EDGES[i],
                "q_hi_kg_h": _Q_BIN_EDGES[i + 1],
                "n": n,
                "detect_rate": round(detected.get(i, 0) / n, 4) if n else None,
            }
        )
    return curve


def _min_detectable_q(curve: list[dict[str, object]]) -> int | None:
    """Lower edge of the lowest bin with detect_rate ≥ _MIN_DETECT_RATE."""
    for bin_ in curve:
        rate = bin_["detect_rate"]
        if isinstance(rate, float) and rate >= _MIN_DETECT_RATE:
            return int(bin_["q_lo_kg_h"])  # type: ignore[arg-type]
    return None


def _q_recovery(rows: list[ProductScore]) -> dict[str, object]:
    """Slope/ratio/scatter/Spearman of detected, in-validity q_est vs q_true."""
    pairs = [
        (r.q_est_kg_h, r.q_true_kg_h)
        for r in rows
        if r.detected
        and np.isfinite(r.q_est_kg_h)
        and r.q_est_kg_h > 0.0
        and r.invalid_fraction <= INVALID_FRACTION_MAX
    ]
    if len(pairs) < 3:
        return {"n": len(pairs)}
    ours = np.array([p[0] for p in pairs])
    true = np.array([p[1] for p in pairs])
    rho, pval = spearman(ours, true)
    return {
        "n": len(pairs),
        "slope_through_origin": round(slope_through_origin(ours, true), 4),
        "median_ratio": round(median_ratio(ours, true), 4),
        "log_scatter": round(log_scatter(ours, true), 4),
        "theil_sen_slope": round(theil_sen_slope(ours, true), 4),
        "spearman_rho": round(rho, 4),
        "spearman_p": round(pval, 4),
    }


def _ci_coverage(rows: list[ProductScore]) -> dict[str, object]:
    """Fraction of MC-subset detections whose ±1σ band contains Q_true."""
    sampled = [r for r in rows if r.ci_contains_truth is not None]
    if not sampled:
        return {"n": 0, "coverage": None}
    covered = sum(1 for r in sampled if r.ci_contains_truth)
    return {"n": len(sampled), "coverage": round(covered / len(sampled), 4)}


def _median(values: list[float]) -> float | None:
    finite = [v for v in values if np.isfinite(v)]
    return round(float(np.median(finite)), 4) if finite else None


def _aggregate(scores: list[ProductScore]) -> dict[str, object]:
    """Aggregates per site × method × source-mode."""
    grouped: dict[tuple[str, str, str], list[ProductScore]] = defaultdict(list)
    for s in scores:
        grouped[(s.site, s.method, s.source_mode)].append(s)

    out: dict[str, object] = {}
    for site in sorted(SITE_TILES):
        site_block: dict[str, object] = {}
        for method in _METHODS:
            method_block: dict[str, object] = {}
            for mode in _SOURCE_MODES:
                rows = grouped.get((site, method, mode), [])
                if not rows:
                    continue
                curve = _detection_curve(rows)
                detected = [r for r in rows if r.detected]
                method_block[mode] = {
                    "n_products": len(rows),
                    "n_detected": len(detected),
                    "min_detectable_q_kg_h": _min_detectable_q(curve),
                    "detection_curve": curve,
                    "q_recovery": _q_recovery(rows),
                    "ci_coverage": _ci_coverage(rows),
                    "median_iou_detected": _median([r.iou for r in detected]),
                    "median_xch4_bias_ppb": _median([r.xch4_bias_ppb for r in rows]),
                    "median_xch4_rms_ppb": _median([r.xch4_rms_ppb for r in rows]),
                }
            if method_block:
                site_block[method] = method_block
        out[site] = site_block
    return out


def _alpha_beta(scores: list[ProductScore]) -> dict[str, object]:
    """F6 evidence: implied U_eff = Q_true·L/(IME·3600) vs the file's U10.

    Uses the MBMP-hinted detections (the perfect-reference, source-known config —
    the cleanest emission estimate). Fits U_eff = α·U10 + β and applies the
    pre-declared decision box: adopt a refit only if the U10 span ≥ 3 m/s AND the
    fit CI excludes the Varon constants — otherwise recorded evidence only.
    """
    pts = [
        (r.u10_ms, r.q_true_kg_h * r.l_m / (r.ime_kg * 3600.0))
        for r in scores
        if r.method == "mbmp"
        and r.source_mode == "hinted"
        and r.detected
        and np.isfinite(r.ime_kg)
        and r.ime_kg > 0.0
        and r.invalid_fraction <= INVALID_FRACTION_MAX
    ]
    u10_values = sorted({round(u, 3) for u, _ in pts})
    span = (max(u10_values) - min(u10_values)) if u10_values else 0.0

    fit: dict[str, object] = {"fitted": False}
    excludes_varon = False
    if len(pts) >= 3 and span > 1e-6:
        u = np.array([p[0] for p in pts])
        ueff = np.array([p[1] for p in pts])
        (alpha, beta), cov = np.polyfit(u, ueff, 1, cov=True)
        se_a, se_b = float(np.sqrt(cov[0, 0])), float(np.sqrt(cov[1, 1]))
        a_ci = (alpha - 1.96 * se_a, alpha + 1.96 * se_a)
        b_ci = (beta - 1.96 * se_b, beta + 1.96 * se_b)
        excludes_varon = not (a_ci[0] <= _VARON_ALPHA <= a_ci[1]) or not (
            b_ci[0] <= _VARON_BETA <= b_ci[1]
        )
        fit = {
            "fitted": True,
            "alpha_hat": round(float(alpha), 4),
            "beta_hat": round(float(beta), 4),
            "alpha_ci95": [round(a_ci[0], 4), round(a_ci[1], 4)],
            "beta_ci95": [round(b_ci[0], 4), round(b_ci[1], 4)],
        }

    adopt = span >= _U10_SPAN_MIN_MS and excludes_varon
    return {
        "config": "mbmp/hinted",
        "n_points": len(pts),
        "u10_values_ms": u10_values,
        "u10_span_ms": round(span, 4),
        "varon": {"alpha": _VARON_ALPHA, "beta": _VARON_BETA},
        "fit": fit,
        "adopt_refit": adopt,
        "decision": (
            "adopt_refit"
            if adopt
            else ("insufficient_wind_diversity" if span < _U10_SPAN_MIN_MS else "ci_includes_varon")
        ),
    }


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def build_result(scores: list[ProductScore]) -> dict[str, object]:
    """Assemble the frozen benchmark JSON (provenance + conventions + aggregates)."""
    return {
        "schema": 1,
        "provenance": {
            "git_hash": _git_hash(),
            "algo_version": ALGO_VERSION,
            "lut_version": load_lut().version,
            "mask_lut_version": load_mask_lut().version,
            "dataset": {"doi": "10.7910/DVN/KRNPEH", "version": 2, "license": "CC0-1.0"},
            "run_utc": datetime.now(UTC).isoformat(),
            "n_products_scored": len({(s.site, s.plume, s.q_true_kg_h) for s in scores}),
        },
        "conventions": {
            "reference": "same-plume Q0 (perfect-reference upper bound; not a "
            "reference-selection test — that is Phase 10, on live pairs)",
            "spacecraft": SPACECRAFT,
            "truth_mask": f"xch4 >= {TRUTH_MASK_FRACTION} * per-product peak xch4 "
            "(Q-invariant: the forward model is linear in flux)",
            "detect": {"k_sigma": _K_SIGMA, "min_area_px": _MIN_AREA_PX},
            "sigma_u10_ms": SIGMA_U10,
            "invalid_fraction_max": INVALID_FRACTION_MAX,
            "min_detectable_q": "lower edge of the lowest Q bin with "
            f">= {_MIN_DETECT_RATE:.0%} detection across the 5 plume shapes",
            "ci_band": "MC q_median +/- 1 sigma (n=500, seed=0, sigma_u10=0)",
            "mc_subset": f"every {_MC_SUBSET_EVERY}th scored product",
        },
        "sites": _aggregate(scores),
        "alpha_beta": _alpha_beta(scores),
        "nhi": _nhi_summary(scores),
    }


def _nhi_summary(scores: list[ProductScore]) -> dict[str, object]:
    """NHI flare-fire count over unique products — expected 0 on the simulated
    (flare-free) scenes. A non-zero count is the false-positive regression signal,
    NOT a win (the bundle's positive evidence is unit tests + a live spot check)."""
    per_product: dict[tuple[str, int, float], int] = {}
    for s in scores:
        per_product[(s.site, s.plume, s.q_true_kg_h)] = s.n_hot
    return {
        "n_products": len(per_product),
        "products_fired": sum(1 for v in per_product.values() if v > 0),
        "total_hot_pixels": sum(per_product.values()),
        "note": "expected 0 — simulated scenes have no flares; NHI firing here would "
        "be a false positive (regression guard, never reported as a win)",
    }


def _print_summary(result: dict[str, object]) -> None:
    prov = result["provenance"]  # type: ignore[index]
    print(
        f"\nALGO {prov['algo_version']}  LUT v{prov['lut_version']}  "
        f"git {prov['git_hash']}  ({prov['n_products_scored']} products)"
    )
    print(
        f"{'site':<10}{'method':<7}{'mode':<8}{'minQ(kg/h)':>11}{'slope':>8}{'CIcov':>8}{'IoU':>7}"
    )
    print("-" * 60)
    for site, methods in result["sites"].items():  # type: ignore[union-attr]
        for method, modes in methods.items():
            for mode, agg in modes.items():
                qr = agg["q_recovery"]
                slope = qr.get("slope_through_origin", float("nan"))
                cov = agg["ci_coverage"]["coverage"]
                iou = agg["median_iou_detected"]
                print(
                    f"{site:<10}{method:<7}{mode:<8}"
                    f"{agg['min_detectable_q_kg_h']!s:>11}"
                    f"{slope if isinstance(slope, float) else float('nan'):>8.3f}"
                    f"{(cov if cov is not None else float('nan')):>8.2f}"
                    f"{(iou if iou is not None else float('nan')):>7.2f}"
                )
    ab = result["alpha_beta"]  # type: ignore[index]
    print(
        f"\nα,β: n={ab['n_points']} U10={ab['u10_values_ms']} span={ab['u10_span_ms']} m/s "
        f"→ {ab['decision']}"
    )
    nhi = result["nhi"]  # type: ignore[index]
    print(
        f"NHI fires: {nhi['products_fired']}/{nhi['n_products']} products, "
        f"{nhi['total_hot_pixels']} hot pixels (expected 0)"
    )


def _freeze_path(algo_version: int) -> Path:
    version = {6: 1, 7: 2}.get(algo_version, algo_version)
    return _REPO_ROOT / "scripts" / "data" / f"s2ch4_benchmark_v{version}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=sorted(SITE_TILES), help="restrict to one base scene")
    parser.add_argument("--freeze", action="store_true", help="write the versioned benchmark JSON")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="override the settings data_dir"
    )
    args = parser.parse_args()

    data_dir = args.data_dir if args.data_dir is not None else get_settings().data_dir
    root = data_dir / "s2ch4"
    if not root.is_dir():
        print(f"no S2CH4 data at {root}; run scripts/fetch_s2ch4.py first", file=sys.stderr)
        return 1

    paths = _iter_product_paths(root, args.site)
    print(f"scoring {len(paths)} products under {root}")
    scores = score_all(paths)
    result = build_result(scores)
    _print_summary(result)

    if args.freeze:
        if args.site is not None:
            print("\nrefusing to freeze a single-site subset; run all sites", file=sys.stderr)
            return 1
        path = _freeze_path(int(ALGO_VERSION))
        with open(path, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nfroze benchmark → {path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
