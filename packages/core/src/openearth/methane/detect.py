"""Methane detection orchestrator: scenes → chips → wind → retrieval →
inversion → masking → quantification, in 7 labeled, cancellable steps.

Everything science-critical is delegated to the tested modules; this file wires
them and manages progress/cancellation. Offline tests fake ``list_scenes``,
``fetch_chip`` and ``sample_wind_at`` in this namespace.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from openearth.errors import JobError, RetrievalError
from openearth.methane.constants import SIGMA_U10_FLOOR_MS
from openearth.methane.conversion import (
    delta_omega_to_xch4_ppb,
    invert_fractional_signal,
    load_lut,
)
from openearth.methane.ime import EmissionEstimate, McParams, quantify
from openearth.methane.plume import PlumeMask
from openearth.methane.retrieval import fetch_chip, mbmp, mbsp
from openearth.methane.scenes import S2Scene, list_scenes, pick_reference
from openearth.methane.wind import (
    GLOBAL_ERA5_HOURLY_ID,
    WindSample,
    sample_wind_at,
)
from openearth.settings import get_settings

if TYPE_CHECKING:
    from openearth.ee.pixels import GridSpec
    from openearth.geometry import BBox

ProgressCallback = Callable[[int, int, str], None]

_TOTAL_STEPS = 7
# Days on each side of the target to gather MBMP reference candidates.
_REFERENCE_WINDOW_DAYS = 130

_DEFAULT_MC = McParams()


@dataclass(frozen=True)
class DetectionResult:
    """The complete result of one methane analysis (all arrays share ``grid``)."""

    target: S2Scene
    reference: S2Scene | None
    method: str
    grid: GridSpec
    delta_r: NDArray[np.float64]  # MBSP or MBMP fractional signal
    delta_omega: NDArray[np.float64]  # mol/m²
    xch4_ppb: NDArray[np.float64]
    rgb: NDArray[np.float32]  # (H,W,3) B4/B3/B2 TOA reflectance — UI context
    plume: PlumeMask
    emission: EmissionEstimate
    wind: WindSample
    calibration: dict[str, float]  # c_target, c_ref, n_excluded_*
    flags: list[str]


def _scene_date(scene_id: str) -> datetime:
    """Parse the sensing date from an S2 system:index (``YYYYMMDDT…``)."""
    return datetime.strptime(scene_id[:8], "%Y%m%d")


def _resolve_source_rc(
    source_lonlat: tuple[float, float] | None, grid: GridSpec
) -> tuple[int, int] | None:
    """Map a (lon, lat) source hint to a grid (row, col), or None if outside."""
    if source_lonlat is None:
        return None
    lon, lat = source_lonlat
    col = round((lon - grid.x0) / grid.xscale)
    row = round((grid.y0 - lat) / grid.yscale)
    if 0 <= row < grid.height and 0 <= col < grid.width:
        return (row, col)
    return None


def _clipped(delta_omega: NDArray[np.float64], lo: float, hi: float) -> bool:
    """True if any inverted pixel landed on a LUT ΔΩ grid edge (saturation)."""
    finite = delta_omega[np.isfinite(delta_omega)]
    if finite.size == 0:
        return False
    return bool(np.any((finite <= lo) | (finite >= hi)))


def _overpass_wind(bbox: BBox, when: datetime) -> tuple[WindSample, float, bool]:
    """Central 10 m wind at *when* plus a σ_u10 from the t±1 h spread.

    Returns ``(central_sample, sigma_u10, fallback_used)``. σ_u10 combines the
    three-sample temporal spread with the reanalysis error floor in quadrature.
    """
    samples = [
        sample_wind_at(
            bbox, when + timedelta(hours=dt), fallback_collection_id=GLOBAL_ERA5_HOURLY_ID
        )
        for dt in (0, -1, 1)
    ]
    central = samples[0]
    speeds = np.array([s.speed_ms for s in samples])
    sigma_u10 = float(np.sqrt(np.std(speeds) ** 2 + SIGMA_U10_FLOOR_MS**2))
    fallback_used = central.collection_id == GLOBAL_ERA5_HOURLY_ID
    return central, sigma_u10, fallback_used


def analyze(
    bbox: BBox,
    target_scene_id: str,
    *,
    reference_scene_id: str | None = None,
    method: str = "mbmp",
    k_sigma: float = 2.0,
    min_area_px: int = 5,
    source_lonlat: tuple[float, float] | None = None,
    mc: McParams = _DEFAULT_MC,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> DetectionResult:
    """Run one methane detection over *bbox* for *target_scene_id*.

    ``method='mbmp'`` (default) differences a reference scene (auto-selected
    unless *reference_scene_id* is given); ``'mbsp'`` skips the reference pass.
    No plume above threshold is a valid result (``flags`` gains ``'no_plume'``),
    not an exception.
    """
    if method not in ("mbmp", "mbsp"):
        raise ValueError(f"Unknown method {method!r}; expected 'mbmp' or 'mbsp'.")

    def progress(step: int, label: str) -> None:
        if on_progress is not None:
            on_progress(step, _TOTAL_STEPS, label)

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise JobError("cancelled")

    flags: list[str] = []
    lut = load_lut(get_settings().lut_path)

    # ── Step 1: list scenes / resolve target + reference ──
    check_cancel()
    progress(1, "Listing scenes")
    target_day = _scene_date(target_scene_id)
    start = (target_day - timedelta(days=_REFERENCE_WINDOW_DAYS)).date().isoformat()
    end = (target_day + timedelta(days=_REFERENCE_WINDOW_DAYS)).date().isoformat()
    scenes = list_scenes(bbox, start, end, max_cloud=90.0)
    by_id = {s.scene_id: s for s in scenes}
    target = by_id.get(target_scene_id)
    if target is None:
        raise RetrievalError(f"Target scene {target_scene_id!r} not found over this ROI.")

    reference: S2Scene | None = None
    if method == "mbmp":
        if reference_scene_id is not None:
            reference = by_id.get(reference_scene_id)
            if reference is None:
                raise RetrievalError(f"Reference scene {reference_scene_id!r} not found.")
        else:
            reference = pick_reference(target, scenes)
        if reference is not None and reference.relative_orbit != target.relative_orbit:
            flags.append("different_orbit_reference")

    # ── Step 2: fetch reference chip (skipped for MBSP) ──
    check_cancel()
    if method == "mbmp" and reference is not None:
        progress(2, "Fetching reference chip")
        ref_chip = fetch_chip(reference, bbox)
    else:
        progress(2, "skipped")
        ref_chip = None

    # ── Step 3: fetch target chip ──
    check_cancel()
    progress(3, "Fetching target chip")
    target_chip = fetch_chip(target, bbox)
    grid = target_chip.grid
    rgb = np.stack(
        [target_chip.bands["B4"], target_chip.bands["B3"], target_chip.bands["B2"]], axis=-1
    ).astype(np.float32)

    # ── Step 4: sample wind (×3) ──
    check_cancel()
    progress(4, "Sampling wind")
    wind, sigma_u10, fallback_used = _overpass_wind(bbox, target.time)
    if fallback_used:
        flags.append("wind_fallback_used")

    # ── Step 5: retrieve + invert (per-pass AMF/spacecraft) ──
    check_cancel()
    progress(5, "Retrieving + inverting")
    lo, hi = float(lut.delta_omega[0]), float(lut.delta_omega[-1])
    t_result = mbsp(
        target_chip.bands["B11"].astype(np.float64), target_chip.bands["B12"].astype(np.float64)
    )
    d_omega_t = invert_fractional_signal(t_result.delta_r, lut, target.spacecraft, target.amf)

    calibration = {
        "c_target": t_result.c,
        "c_ref": float("nan"),
        "n_excluded_target": float(t_result.n_excluded),
        "n_excluded_ref": 0.0,
    }
    if _clipped(d_omega_t, lo, hi):
        flags.append("clipped_inversion")

    if method == "mbmp" and reference is not None and ref_chip is not None:
        r_result = mbsp(
            ref_chip.bands["B11"].astype(np.float64), ref_chip.bands["B12"].astype(np.float64)
        )
        d_omega_r = invert_fractional_signal(
            r_result.delta_r, lut, reference.spacecraft, reference.amf
        )
        delta_omega = d_omega_t - d_omega_r
        delta_r = mbmp(t_result, r_result)
        calibration["c_ref"] = r_result.c
        calibration["n_excluded_ref"] = float(r_result.n_excluded)
        if "clipped_inversion" not in flags and _clipped(d_omega_r, lo, hi):
            flags.append("clipped_inversion")
    else:
        delta_omega = d_omega_t
        delta_r = t_result.delta_r

    delta_omega = np.asarray(delta_omega, dtype=np.float64)
    xch4_ppb = np.asarray(delta_omega_to_xch4_ppb(delta_omega), dtype=np.float64)

    # ── Step 6: mask ──
    check_cancel()
    progress(6, "Detecting plume")
    source_rc = _resolve_source_rc(source_lonlat, grid)

    # ── Step 7: Monte-Carlo quantification ──
    check_cancel()
    progress(7, "Quantifying (Monte Carlo)")
    emission, plume = quantify(
        delta_omega,
        grid,
        wind,
        sigma_u10,
        k_sigma=k_sigma,
        min_area_px=min_area_px,
        source_rc=source_rc,
        mc=mc,
    )
    if plume.n_pixels == 0:
        flags.append("no_plume")
    elif bool(np.isnan(delta_omega[plume.mask]).any()):
        flags.append("nan_in_mask")

    return DetectionResult(
        target=target,
        reference=reference if method == "mbmp" else None,
        method=method,
        grid=grid,
        delta_r=np.asarray(delta_r, dtype=np.float64),
        delta_omega=delta_omega,
        xch4_ppb=xch4_ppb,
        rgb=rgb,
        plume=plume,
        emission=emission,
        wind=wind,
        calibration=calibration,
        flags=flags,
    )
