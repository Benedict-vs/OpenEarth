"""Methane detection orchestrator: scenes → chips → wind → retrieval →
inversion → masking → quantification, in 7 labeled, cancellable steps.

Everything science-critical is delegated to the tested modules; this file wires
them and manages progress/cancellation. Offline tests fake ``list_scenes``,
``fetch_chip`` and ``sample_wind_at`` in this namespace.
"""

from __future__ import annotations

import re
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
from numpy.typing import NDArray

from openearth.errors import JobError, RetrievalError
from openearth.methane.constants import SIGMA_U10_FLOOR_MS
from openearth.methane.conversion import (
    delta_omega_to_xch4_ppb,
    edge_fractions,
    invert_fractional_signal,
    load_lut,
    load_mask_lut,
)
from openearth.methane.evidence import (
    SURFACE_CORRELATION_CUT,
    b12_dimming_ok,
    chip_flags,
    surface_correlation,
)
from openearth.methane.flare import nhi_hot_mask
from openearth.methane.ime import EmissionEstimate, McParams, quantify
from openearth.methane.plume import PlumeMask, detect_plume
from openearth.methane.retrieval import RetrievalChip, fetch_chip, mbsp
from openearth.methane.scenes import S2Scene, list_scenes, pick_reference, pick_reference_set
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

# ── Composite reference (opt-in, Phase 8) ──────────────────────────
# k same-orbit, same-spacecraft reference chips whose per-pixel median replaces
# the single reference (a 50 % breakdown point against an intermittent plume
# contaminating the background). Fewer than COMPOSITE_MIN eligible members →
# fall back to single (the run proceeds; the Lab says why).
COMPOSITE_SIZE = 5
COMPOSITE_MIN = 3
# The reporting LUT interpolates the forward curve on a 0.25-wide AMF grid
# (conversion.forward_signal). A composite's members span a ±120 d window, over
# which the solar zenith drifts; when their AMF max−min exceeds one grid step the
# median-AMF approximation smears across more than one interpolation interval, so
# we flag it (the run still proceeds).
AMF_SPREAD_MAX = 0.25

_DEFAULT_MC = McParams()

# Flag when >5% of masked pixels hit the reporting-LUT high edge (fix 3): the
# retrieved column is range-capped there, biasing a strong plume low.
_LUT_HI_CLIP_FLAG_FRACTION = 0.05
# Flag an unstable mask (fix 4c / Tier 1 F2) when the MC k-sweep's pixel count
# swings by ≥ this ratio, or any k empties a mask the display k did not — the
# order-of-magnitude mask noise made visible, not fixed.
_MASK_STABILITY_RATIO_MAX = 4.0
# MGRS tile in an S2 system:index, e.g. ..._T39RUN → "39RUN" (fix 4b / Tier 1 F5).
_MGRS_TILE_RE = re.compile(r"_T(\d{2}[A-Z]{3})")


@dataclass(frozen=True)
class ReferenceMember:
    """One member of a composite reference (for the Lab's reference block)."""

    scene_id: str
    days_from_target: float
    amf: float


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
    # Per-pass in-mask fractions on the reporting-LUT grid ends (fix 3):
    # target_lo/target_hi/ref_lo/ref_hi. Replaces the whole-chip clip flag.
    clip_fractions: dict[str, float]
    # Composite-reference provenance (Phase 8; "single" or "composite"). For a
    # composite: the members that were medianed, and their AMF max−min spread.
    reference_mode: str = "single"
    reference_members: list[ReferenceMember] = field(default_factory=list)
    composite_amf_spread: float = 0.0
    # NHI flare-hot pixel counts (Phase 9; post-floor, pre-dilation). Ride result_json
    # (no migration); ``flare_lit_*`` flags fire when either is ≥ 1.
    n_hot_target: int = 0
    n_hot_reference: int = 0


def _median_composite_chip(chips: list[RetrievalChip]) -> RetrievalChip:
    """Per-band, per-pixel median across same-orbit reference chips.

    A pixel that is NaN (masked) in some members medians over the rest; NaN in
    all of them stays NaN. The nearest member (``chips[0]``) is the display
    anchor — its scene/grid carry through unchanged.
    """
    anchor = chips[0]
    bands: dict[str, NDArray[np.float32]] = {}
    for name in anchor.bands:
        stack = np.stack([c.bands[name] for c in chips], axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slice → NaN, expected
            bands[name] = np.nanmedian(stack, axis=0).astype(np.float32)
    return RetrievalChip(scene=anchor.scene, grid=anchor.grid, bands=bands)


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


def _mgrs_tile(scene_id: str) -> str | None:
    """The MGRS tile id embedded in an S2 system:index, or None (fix 4b)."""
    match = _MGRS_TILE_RE.search(scene_id)
    return match.group(1) if match else None


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
    reference_mode: Literal["single", "composite"] = "single",
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
    ``reference_mode='composite'`` (opt-in, MBMP only, ignored when an explicit
    *reference_scene_id* is given) replaces the single reference with a per-pixel
    median over up to ``COMPOSITE_SIZE`` same-orbit/same-spacecraft scenes — a
    robust background for recurrent emitters; too few eligible members falls back
    to single (``flags`` gains ``'composite_reference_unavailable'``).
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
    # Frozen inversion for masking only — keeps the footprint invariant to reporting-LUT
    # recalibrations (Stage 2); the reported columns/IME use `lut`.
    mask_lut = load_mask_lut()

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
    # Members of a composite reference (empty ⇒ single-reference mode).
    composite_members: list[S2Scene] = []
    if method == "mbmp":
        if reference_scene_id is not None:
            # An explicit scene is single mode by construction (composite ignored).
            reference = by_id.get(reference_scene_id)
            if reference is None:
                raise RetrievalError(f"Reference scene {reference_scene_id!r} not found.")
        elif reference_mode == "composite":
            candidates = pick_reference_set(target, scenes, COMPOSITE_SIZE)
            if len(candidates) < COMPOSITE_MIN:
                flags.append("composite_reference_unavailable")
                reference = pick_reference(target, scenes)  # graceful single fallback
            else:
                composite_members = candidates
                reference = candidates[0]  # nearest member = the display anchor
        else:
            reference = pick_reference(target, scenes)
        if reference is not None and reference.relative_orbit != target.relative_orbit:
            flags.append("different_orbit_reference")
        if reference is not None:
            t_tile, r_tile = _mgrs_tile(target.scene_id), _mgrs_tile(reference.scene_id)
            if t_tile and r_tile and t_tile != r_tile:
                flags.append("cross_tile_reference")

    # ── Step 2: fetch reference chip(s) (skipped for MBSP) ──
    check_cancel()
    ref_chip: RetrievalChip | None = None
    # Reference-pass AMF: the member median for a composite (declared
    # approximation — solar zenith drifts over the member span), else the single
    # reference's own AMF. None when there is no reference pass.
    ref_amf: float | None = None
    reference_members: list[ReferenceMember] = []
    composite_amf_spread = 0.0
    if method == "mbmp" and composite_members:
        progress(2, f"Fetching {len(composite_members)} reference chips")
        # Serial fetches — each rides the shared ee_call semaphore; never a new
        # parallel EE path (k = COMPOSITE_SIZE round-trips per analyze).
        member_chips = [fetch_chip(m, bbox) for m in composite_members]
        ref_chip = _median_composite_chip(member_chips)
        member_amfs = [m.amf for m in composite_members]
        ref_amf = float(np.median(member_amfs))
        composite_amf_spread = float(max(member_amfs) - min(member_amfs))
        if composite_amf_spread > AMF_SPREAD_MAX:
            flags.append("composite_amf_spread")
        reference_members = [
            ReferenceMember(
                scene_id=m.scene_id,
                days_from_target=(m.time - target.time).total_seconds() / 86400.0,
                amf=m.amf,
            )
            for m in composite_members
        ]
    elif method == "mbmp" and reference is not None:
        progress(2, "Fetching reference chip")
        ref_chip = fetch_chip(reference, bbox)
        ref_amf = reference.amf
    else:
        progress(2, "skipped")

    # ── Step 3: fetch target chip ──
    check_cancel()
    progress(3, "Fetching target chip")
    target_chip = fetch_chip(target, bbox)
    grid = target_chip.grid
    rgb = np.stack(
        [target_chip.bands["B4"], target_chip.bands["B3"], target_chip.bands["B2"]], axis=-1
    ).astype(np.float32)
    # Chip-validity diagnostics (Phase 9; flag-only, nothing is gated on them).
    flags.extend(chip_flags(target_chip.bands))

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

    # NHI flare-hot exclusion (Phase 9): a lit gas flare's SWIR thermal signal
    # corrupts the B11/B12 retrieval, and a lit→unlit transition between target and
    # reference can mimic a plume at the stack. Drop the hot pixels (dilated) from
    # the calibration (exclude + robust_cut) and NaN them in ΔR before inversion, so
    # a hotspot neither biases c nor invents a plume. Post-floor count → flare_lit_*.
    t_hot = nhi_hot_mask(target_chip.bands, target.spacecraft)
    n_hot_target = int(nhi_hot_mask(target_chip.bands, target.spacecraft, dilate=False).sum())
    if n_hot_target > 0:
        flags.append("flare_lit_target")
    t_result = mbsp(
        target_chip.bands["B11"].astype(np.float64),
        target_chip.bands["B12"].astype(np.float64),
        robust_cut=True,
        exclude=t_hot,
    )
    delta_r_t = t_result.delta_r.copy()
    delta_r_t[t_hot] = np.nan
    d_omega_t = invert_fractional_signal(delta_r_t, lut, target.spacecraft, target.amf)
    mask_d_omega_t = invert_fractional_signal(delta_r_t, mask_lut, target.spacecraft, target.amf)

    calibration = {
        "c_target": t_result.c,
        "c_ref": float("nan"),
        "n_excluded_target": float(t_result.n_excluded),
        "n_excluded_ref": 0.0,
    }

    # Per-pass reporting-LUT ΔΩ, kept in scope for the in-mask clip diagnostics
    # (fix 3) and the reference-contamination check (fix 2-flag) below.
    d_omega_r: NDArray[np.float64] | None = None
    mask_d_omega_r: NDArray[np.float64] | None = None
    n_hot_reference = 0
    if method == "mbmp" and reference is not None and ref_chip is not None:
        assert ref_amf is not None  # set alongside ref_chip (single or composite)
        r_hot = nhi_hot_mask(ref_chip.bands, reference.spacecraft)
        n_hot_reference = int(
            nhi_hot_mask(ref_chip.bands, reference.spacecraft, dilate=False).sum()
        )
        if n_hot_reference > 0:
            flags.append("flare_lit_reference")
        r_result = mbsp(
            ref_chip.bands["B11"].astype(np.float64),
            ref_chip.bands["B12"].astype(np.float64),
            robust_cut=True,
            exclude=r_hot,
        )
        delta_r_r = r_result.delta_r.copy()
        delta_r_r[r_hot] = np.nan
        # Composite members are same-spacecraft (hard constraint), so the anchor's
        # spacecraft is the whole set's; the AMF is the member median.
        d_omega_r = invert_fractional_signal(delta_r_r, lut, reference.spacecraft, ref_amf)
        mask_d_omega_r = invert_fractional_signal(
            delta_r_r, mask_lut, reference.spacecraft, ref_amf
        )
        delta_omega = d_omega_t - d_omega_r
        mask_delta_omega = mask_d_omega_t - mask_d_omega_r
        delta_r = delta_r_t - delta_r_r
        calibration["c_ref"] = r_result.c
        calibration["n_excluded_ref"] = float(r_result.n_excluded)
    else:
        delta_omega = d_omega_t
        mask_delta_omega = mask_d_omega_t
        delta_r = delta_r_t

    delta_omega = np.asarray(delta_omega, dtype=np.float64)
    mask_delta_omega = np.asarray(mask_delta_omega, dtype=np.float64)
    xch4_ppb = np.asarray(delta_omega_to_xch4_ppb(delta_omega), dtype=np.float64)

    # ── Step 6: mask ──
    check_cancel()
    progress(6, "Detecting plume")
    source_rc = _resolve_source_rc(source_lonlat, grid)

    # Reference-contamination diagnostic (fix 2-flag / Tier 1 F4): a recurrent emitter
    # may have no plume-free reference. Run the same detector on the reference's OWN
    # mask-LUT ΔΩ — a surviving component means the reference itself shows an
    # enhancement near the source, which over-subtracts. Zero extra EE round-trips.
    if mask_d_omega_r is not None:
        ref_self = detect_plume(
            mask_d_omega_r, grid, k_sigma=k_sigma, min_area_px=min_area_px, source_rc=source_rc
        )
        if ref_self.n_pixels > 0:
            flags.append("possible_reference_contamination")

    # ── Step 7: Monte-Carlo quantification ──
    check_cancel()
    progress(7, "Quantifying (Monte Carlo)")
    # Threshold the plume footprint on the ΔΩ field from the FROZEN mask LUT, so the mask is
    # invariant to reporting-LUT recalibrations while still using the per-pass inversion that
    # actually localises the plume (raw −ΔR masking displaces the mask off-source for MBMP —
    # see docs/methane_methods.md §3/§8). Mass (IME) and the noise bootstrap use the reporting ΔΩ.
    emission, plume = quantify(
        delta_omega,
        grid,
        wind,
        sigma_u10,
        mask_field=mask_delta_omega,
        k_sigma=k_sigma,
        min_area_px=min_area_px,
        source_rc=source_rc,
        mc=mc,
    )
    if plume.n_pixels == 0:
        flags.append("no_plume")
    else:
        if bool(np.isnan(delta_omega[plume.mask]).any()):
            flags.append("nan_in_mask")
        # False-positive evidence checks (Phase 9; flag-only). Ehret dimming sign:
        # a real plume absorbs in B12, so the in-mask mean target-pass ΔR is < 0.
        if not b12_dimming_ok(delta_r_t, plume.mask):
            flags.append("not_b12_dimming")
        # S2 methane plumes are RGB-invisible; a mask correlated with B4/B3/B2 is
        # tracking a visible surface feature, not gas.
        blind = {b: target_chip.bands[b] for b in ("B4", "B3", "B2")}
        if surface_correlation(plume.mask, blind) > SURFACE_CORRELATION_CUT:
            flags.append("surface_correlated")

    # In-mask inversion-range diagnostics (fix 3): fraction of masked pixels on each
    # reporting-LUT grid end, per pass. Replaces the whole-chip clipped_inversion flag;
    # a strong high-clip biases the reported column low → lut_hi_clipped_mask.
    t_lo, t_hi = edge_fractions(d_omega_t, plume.mask, lo, hi)
    if d_omega_r is not None:
        r_lo, r_hi = edge_fractions(d_omega_r, plume.mask, lo, hi)
    else:
        r_lo, r_hi = 0.0, 0.0
    clip_fractions = {"target_lo": t_lo, "target_hi": t_hi, "ref_lo": r_lo, "ref_hi": r_hi}
    if t_hi > _LUT_HI_CLIP_FLAG_FRACTION:
        flags.append("lut_hi_clipped_mask")

    # Mask-stability diagnostic (fix 4c): the MC already labeled the mask at every k
    # in its grid — flag an order-of-magnitude swing or a k that empties the mask.
    npx = list(emission.mask_npx_by_k.values())
    if plume.n_pixels > 0 and npx:
        lo_n, hi_n = min(npx), max(npx)
        if lo_n == 0 or hi_n / lo_n >= _MASK_STABILITY_RATIO_MAX:
            flags.append("unstable_mask")

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
        clip_fractions=clip_fractions,
        reference_mode="composite" if composite_members else "single",
        reference_members=reference_members,
        composite_amf_spread=composite_amf_spread,
        n_hot_target=n_hot_target,
        n_hot_reference=n_hot_reference,
    )
