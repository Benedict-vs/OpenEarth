#!/usr/bin/env python
"""Curate same-scene Sentinel-2 calibration events from an IMEO plume export.

Phase 3.5, Stage 1a. Reads a *downloaded* plume export (never fetches it — the
IMEO portals bot-wall automation), keeps the Sentinel-2 rows, selects a
region-diverse candidate set, resolves each plume to its exact Earth Engine
``COPERNICUS/S2_HARMONIZED`` ``system:index`` and runs the practicality gates
live, then writes a candidate JSON for human review. A human prunes the
candidates into the committed ``scripts/data/calibration_events.json`` — the
committed file with per-row provenance is the deliverable, not this script's
raw output.

    OPENEARTH_EE_TESTS=1 uv run python scripts/curate_calibration_events.py \
        --input <mars_s2l_plumes.csv> --output <candidates.json>

Default source: the UNEP-IMEO/MARS-S2L dataset (``validated_images_plumes.csv``
on Hugging Face), IMEO's own validated-plume table with per-scene emission
quantifications. Its ``ch4_fluxrate`` is in **kg/h** (converted to t/h here),
``tile`` is the full S2 L1C product id (its datatake datetime + MGRS tile
resolve the EE scene), and ``ch4_fluxrate_std`` carries the published σ. The
IMEO *Eye on Methane* portal export uses different column names; extend
``_MARS_COLUMNS`` / add a reader if you feed a different export.

Same-scene principle (non-negotiable): every kept event's published rate must
derive from the *same* S2 acquisition we analyze. MARS rows satisfy this by
construction (the rate is quantified from that scene). Cross-instrument /
cross-day pairs measure source variability, not our calibration, and are
rejected upstream by keeping only S2A/S2B rows and matching the exact scene.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from openearth.ee.client import initialize
from openearth.geometry import BBox
from openearth.methane.conversion import CH4Lut, invert_fractional_signal, load_lut
from openearth.methane.detect import DetectionResult, analyze
from openearth.methane.ime import McParams
from openearth.methane.plume import detect_plume
from openearth.methane.retrieval import fetch_chip, mbsp
from openearth.methane.scenes import S2Scene, list_scenes, pick_reference

# MARS-S2L column names (verified against validated_images_plumes.csv, 2026-07-06).
_MARS_S2_SATELLITES = ("S2A", "S2B")
_RATE_COL = "ch4_fluxrate"  # kg/h
_SIGMA_COL = "ch4_fluxrate_std"  # kg/h

# Region labels by (lon_min, lon_max, lat_min, lat_max) box — coarse, human-reviewable;
# the final committed file gets hand-checked region names. Order matters (first hit wins).
_REGION_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    ("Turkmenistan", 50.0, 60.0, 35.0, 45.0),
    ("Algeria", -5.0, 12.0, 25.0, 38.0),
    ("Libya", 12.0, 26.0, 24.0, 34.0),
    ("Persian Gulf", 44.0, 60.0, 24.0, 34.0),
    ("Yemen/Arabia", 40.0, 50.0, 12.0, 22.0),
    ("US Permian", -110.0, -95.0, 28.0, 38.0),
    ("Mexico", -98.0, -88.0, 16.0, 24.0),
    ("Turkmenistan-N/Kazakhstan", 50.0, 65.0, 45.0, 55.0),
    ("SE Asia offshore", 95.0, 120.0, 0.0, 15.0),
    ("Argentina", -72.0, -62.0, -42.0, -32.0),
    ("Venezuela", -70.0, -60.0, 6.0, 14.0),
)

# A ~4.4 km analysis box (matches the Korpezhe precedent, ~220 px at 20 m ≪ 1024²).
_BBOX_HALF_LON_DEG = 0.025
_BBOX_HALF_LAT_DEG = 0.020
_VALID_FRACTION_MIN = 0.95  # B11/B12 finite fraction over the bbox (chip-level cloud gate)


def _region_of(lat: float, lon: float) -> str:
    for name, lon_min, lon_max, lat_min, lat_max in _REGION_BOXES:
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            return name
    return f"other({round(lat)},{round(lon)})"


def _bbox_for(lat: float, lon: float) -> BBox:
    return BBox(
        west=lon - _BBOX_HALF_LON_DEG,
        south=lat - _BBOX_HALF_LAT_DEG,
        east=lon + _BBOX_HALF_LON_DEG,
        north=lat + _BBOX_HALF_LAT_DEG,
    )


@dataclass
class Candidate:
    """One plume row promoted to a calibration-event candidate (pre-gate)."""

    id_plume: str
    id_source: str
    region: str
    lat: float
    lon: float
    published_q_t_h: float
    published_sigma_t_h: float
    datatake_dt: str  # e.g. "20180619T070619" — from the MARS product id
    mgrs_tile: str  # e.g. "T39SYC"
    acquisition_date: str  # tile_date (YYYY-MM-DD)
    satellite: str
    # Filled by the live gates:
    scene_id: str | None = None
    scene_time_utc: str | None = None
    amf: float | None = None
    cloud_pct: float | None = None
    valid_fraction: float | None = None
    gate_pass: bool = False
    gate_reason: str = ""
    notes: list[str] = field(default_factory=list)


def load_candidates(
    csv_path: str,
    *,
    min_t_h: float,
    max_t_h: float,
    sources_per_region: int,
    per_source: int,
) -> list[Candidate]:
    """Filter the MARS export to S2, then pick a region-diverse candidate set.

    One representative plume per source by default (``per_source``), the row(s)
    nearest the source's median rate, taking the ``sources_per_region`` largest
    (best-characterised, most-recurrent) sources in each region. Deterministic.
    """
    df = pd.read_csv(csv_path)
    df = df[df["satellite"].isin(_MARS_S2_SATELLITES)].copy()
    df = df[df[_RATE_COL].notna() & df[_SIGMA_COL].notna()]
    df["t_h"] = df[_RATE_COL] / 1000.0
    df["sigma_t_h"] = df[_SIGMA_COL] / 1000.0
    df = df[(df["t_h"] >= min_t_h) & (df["t_h"] <= max_t_h)]
    df["region"] = [_region_of(a, b) for a, b in zip(df["lat"], df["lon"], strict=True)]

    candidates: list[Candidate] = []
    for _region, region_df in sorted(df.groupby("region"), key=lambda kv: str(kv[0])):
        # Largest sources first (most overpasses ⇒ recurrent, well-characterised).
        source_sizes = region_df.groupby("id_source").size().sort_values(ascending=False)
        for id_source in source_sizes.index[:sources_per_region]:
            src = region_df[region_df["id_source"] == id_source]
            median_rate = float(src["t_h"].median())
            picks = src.iloc[(src["t_h"] - median_rate).abs().argsort()[:per_source]]
            for _, row in picks.iterrows():
                parts = str(row["tile"]).split("_")
                candidates.append(
                    Candidate(
                        id_plume=str(row["id_plume"]),
                        id_source=str(row["id_source"]),
                        region=str(row["region"]),
                        lat=float(row["lat"]),
                        lon=float(row["lon"]),
                        published_q_t_h=round(float(row["t_h"]), 3),
                        published_sigma_t_h=round(float(row["sigma_t_h"]), 3),
                        datatake_dt=parts[2],
                        mgrs_tile=parts[5],
                        acquisition_date=str(row["tile_date"])[:10],
                        satellite=str(row["satellite"]),
                    )
                )
    return candidates


def _match_scene(cand: Candidate, scenes: list[S2Scene]) -> S2Scene | None:
    """The EE scene whose id shares the plume's datatake datetime and MGRS tile."""
    for scene in scenes:
        if scene.scene_id.startswith(cand.datatake_dt) and scene.scene_id.endswith(cand.mgrs_tile):
            return scene
    return None


def gate_candidate(cand: Candidate) -> None:
    """Resolve the scene id and run the practicality gates (mutates *cand*)."""
    bbox = _bbox_for(cand.lat, cand.lon)
    acq = date.fromisoformat(cand.acquisition_date)
    try:
        scenes = list_scenes(
            bbox, acq - timedelta(days=1), acq + timedelta(days=1), max_cloud=100.0
        )
    except Exception as exc:
        cand.gate_reason = f"list_scenes failed: {exc}"
        return
    scene = _match_scene(cand, scenes)
    if scene is None:
        cand.gate_reason = f"no S2_HARMONIZED scene matching {cand.datatake_dt}/{cand.mgrs_tile}"
        return
    cand.scene_id = scene.scene_id
    cand.scene_time_utc = scene.time.isoformat()
    cand.amf = round(scene.amf, 4)
    cand.cloud_pct = round(scene.cloud_pct, 2)
    try:
        chip = fetch_chip(scene, bbox)
    except Exception as exc:
        cand.gate_reason = f"fetch_chip failed: {exc}"
        return
    b11, b12 = chip.bands["B11"], chip.bands["B12"]
    valid = float(np.mean(np.isfinite(b11) & np.isfinite(b12)))
    cand.valid_fraction = round(valid, 4)
    if valid < _VALID_FRACTION_MIN:
        cand.gate_reason = f"B11/B12 valid fraction {valid:.3f} < {_VALID_FRACTION_MIN}"
        return
    cand.gate_pass = True
    cand.gate_reason = "ok"


def _to_event_json(cand: Candidate) -> dict[str, object]:
    """A candidate rendered in the committed calibration_events.json shape (MBSP)."""
    bbox = _bbox_for(cand.lat, cand.lon)
    return {
        "id": f"{cand.region.lower().replace(' ', '-').replace('/', '-')}-{cand.acquisition_date}",
        "region": cand.region,
        "surface": "arid",
        "source": "mars_s2l",
        "source_ref": (
            "UNEP-IMEO/MARS-S2L validated_images_plumes.csv "
            f"(id_plume={cand.id_plume}, id_source={cand.id_source}); "
            "huggingface.co/datasets/UNEP-IMEO/MARS-S2L"
        ),
        "published_q_t_h": cand.published_q_t_h,
        "published_sigma_t_h": cand.published_sigma_t_h,
        "published_instrument": "Sentinel-2",
        "published_time_utc": cand.scene_time_utc,
        "lat": cand.lat,
        "lon": cand.lon,
        "bbox": [
            round(bbox.west, 5),
            round(bbox.south, 5),
            round(bbox.east, 5),
            round(bbox.north, 5),
        ],
        "method": "mbsp",
        "target_scene_id": cand.scene_id,
        "reference_scene_id": None,
        "source_lonlat": [cand.lon, cand.lat],
        "notes": f"MARS-S2L same-scene; valid_frac={cand.valid_fraction}; cloud={cand.cloud_pct}%",
    }


# ── Method resolution: prefer MBMP with a plume-free reference (Varon et al. 2021) ──
#
# MBSP has no reference to cancel static surface structure, so over heterogeneous
# terrain a coherent dark/bright region inverts to the clamped LUT ΔΩ edge and the
# connected-component step engulfs it into a multi-thousand-pixel "plume" (hundreds
# of t/h). MBMP subtracts the reference pass, so co-located saturation cancels in the
# ΔΩ difference. We therefore DEFAULT every event to MBMP with a pinned, plume-free
# reference and fall back to MBSP only where no clean reference exists and the MBSP
# retrieval is itself valid (homogeneous arid surface).

_LUT_SAT_FRACTION_MAX = (
    0.20  # a retrieval whose mask exceeds this LUT-saturated fraction is invalid
)
_MC_CURATE = McParams(n=40, seed=0)  # light MC — the display mask/ΔΩ don't depend on n
_MAX_REF_TRIES = 6


def lut_saturated_fraction(delta_omega: np.ndarray, mask: np.ndarray, lut: CH4Lut) -> float:
    """Fraction of masked pixels whose inverted ΔΩ landed on a LUT grid edge (saturated)."""
    in_mask = delta_omega[mask]
    if in_mask.size == 0:
        return 0.0
    lo, hi = float(lut.delta_omega[0]), float(lut.delta_omega[-1])
    return float(np.mean((in_mask <= lo) | (in_mask >= hi)))


def find_clean_reference(
    target: S2Scene,
    scenes: list[S2Scene],
    bbox: BBox,
    source_rc: tuple[int, int] | None,
    d_omega_t: np.ndarray,
    grid: object,
    lut: CH4Lut,
) -> tuple[str, float] | None:
    """A reference that yields a *valid MBMP retrieval* — the published-value-blind test.

    Ranks candidates by ``pick_reference`` (geometry), then for each forms the per-pass
    ΔΩ difference (``d_omega_t − invert(ΔR_ref)``) and accepts the first whose plume mask
    is present and NOT LUT-saturated. Static surface structure is shared by both passes,
    so it cancels in the difference (even if each pass saturates on its own); only a
    reference carrying a transient plume at the source, or one leaving residual
    saturation, is rejected. Returns ``(reference_scene_id, sat_fraction)`` or ``None``.
    """
    tried: set[str] = set()
    for _ in range(_MAX_REF_TRIES):
        remaining = [s for s in scenes if s.scene_id not in tried and s.scene_id != target.scene_id]
        ref = pick_reference(target, remaining)
        if ref is None:
            return None
        tried.add(ref.scene_id)
        try:
            chip = fetch_chip(ref, bbox)
        except Exception:
            continue
        r_result = mbsp(chip.bands["B11"].astype(np.float64), chip.bands["B12"].astype(np.float64))
        d_omega_r = invert_fractional_signal(r_result.delta_r, lut, ref.spacecraft, ref.amf)
        delta_omega = np.asarray(d_omega_t - d_omega_r, dtype=np.float64)
        pm = detect_plume(delta_omega, grid, k_sigma=2.0, source_rc=source_rc)  # type: ignore[arg-type]
        if pm.n_pixels == 0:
            continue  # this reference over-cancels the plume — try the next date
        sat = lut_saturated_fraction(delta_omega, pm.mask, lut)
        if sat <= _LUT_SAT_FRACTION_MAX:
            return ref.scene_id, sat
    return None


def _saturation_verdict(result: DetectionResult, lut: CH4Lut) -> tuple[bool, float]:
    """(valid, sat_fraction): valid when a plume exists and isn't LUT-saturated."""
    if result.plume.n_pixels == 0:
        return False, 0.0
    sat = lut_saturated_fraction(result.delta_omega, result.plume.mask, lut)
    return sat <= _LUT_SAT_FRACTION_MAX, sat


def resolve_method(event: dict[str, object], lut: CH4Lut) -> dict[str, object]:
    """Decide method + reference for one event, live. MBMP-preferred; MBSP fallback.

    Returns a verdict dict: chosen method, reference_scene_id (pinned), q_t_h,
    sat_fraction, and a reason. ``no_plume`` and saturation are recorded outcomes.
    """
    bbox = BBox(*event["bbox"])  # type: ignore[misc]
    src = event.get("source_lonlat")
    source_lonlat = tuple(src) if src else None  # type: ignore[arg-type]
    target_id = str(event["target_scene_id"])
    out: dict[str, object] = {"id": event["id"], "method": None, "reference_scene_id": None}

    # Gather the scene window once to find a clean reference.
    target_day = date.fromisoformat(str(event["published_time_utc"])[:10])
    scenes = list_scenes(
        bbox,
        target_day - timedelta(days=130),
        target_day + timedelta(days=130),
        max_cloud=90.0,
    )
    target = next((s for s in scenes if s.scene_id == target_id), None)
    if target is None:
        out["reason"] = "target scene not found"
        return out

    # Target inversion once (reused across every candidate reference).
    target_chip = fetch_chip(target, bbox)
    grid = target_chip.grid
    t_result = mbsp(
        target_chip.bands["B11"].astype(np.float64), target_chip.bands["B12"].astype(np.float64)
    )
    d_omega_t = invert_fractional_signal(t_result.delta_r, lut, target.spacecraft, target.amf)
    source_rc = None
    if source_lonlat is not None:
        lon, lat = source_lonlat
        col = round((lon - grid.x0) / grid.xscale)
        row = round((grid.y0 - lat) / grid.yscale)
        if 0 <= row < grid.height and 0 <= col < grid.width:
            source_rc = (row, col)

    found = find_clean_reference(target, scenes, bbox, source_rc, d_omega_t, grid, lut)
    if found is not None:
        ref_id, _ = found
        result = analyze(
            bbox,
            target_id,
            reference_scene_id=ref_id,
            method="mbmp",
            source_lonlat=source_lonlat,
            mc=_MC_CURATE,  # type: ignore[arg-type]
        )
        valid, sat = _saturation_verdict(result, lut)
        if valid:
            out.update(
                method="mbmp",
                reference_scene_id=ref_id,
                q_t_h=round(result.emission.q_kg_h / 1000.0, 3),
                sat_fraction=round(sat, 3),
                reason="mbmp with pinned reference (co-located saturation cancels)",
            )
            return out

    # Fall back to MBSP (valid only over homogeneous surfaces).
    result = analyze(
        bbox,
        target_id,
        method="mbsp",
        source_lonlat=source_lonlat,
        mc=_MC_CURATE,  # type: ignore[arg-type]
    )
    valid, sat = _saturation_verdict(result, lut)
    if valid:
        out.update(
            method="mbsp",
            reference_scene_id=None,
            q_t_h=round(result.emission.q_kg_h / 1000.0, 3),
            sat_fraction=round(sat, 3),
            reason="mbsp fallback (no clean reference; retrieval valid)",
        )
        return out
    out.update(
        method=None,
        q_t_h=None,
        sat_fraction=round(sat, 3),
        reason=(
            "no_plume under both methods"
            if result.plume.n_pixels == 0
            else f"excluded_lut_saturated (sat={sat:.2f}); no clean reference"
        ),
    )
    return out


def recurate(events_path: str) -> list[dict[str, object]]:
    """Resolve method + reference for every event in a committed events file (live)."""
    initialize()
    lut = load_lut()
    with open(events_path) as fh:
        events = json.load(fh)["events"]
    verdicts: list[dict[str, object]] = []
    for i, event in enumerate(events, 1):
        try:
            v = resolve_method(event, lut)
        except Exception as exc:
            v = {"id": event["id"], "method": None, "reason": f"error: {exc}"}
        verdicts.append(v)
        m = v.get("method") or "EXCLUDED"
        q = v.get("q_t_h")
        qs = f"{q:>7.1f}" if isinstance(q, (int, float)) else "      —"
        print(
            f"  [{i:>2}/{len(events)}] {event['id']!s:<30} {m:<5} {qs}  {v.get('reason')}",
            file=sys.stderr,
        )
    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recurate",
        metavar="EVENTS_JSON",
        help="resolve method+reference for an existing events file (live)",
    )
    parser.add_argument("--input", help="path to a downloaded MARS-S2L plume CSV")
    parser.add_argument("--output", help="candidate/verdict JSON to write for human review")
    parser.add_argument("--min-th", type=float, default=5.0, help="min published rate (t/h)")
    parser.add_argument("--max-th", type=float, default=30.0, help="max published rate (t/h)")
    parser.add_argument("--sources-per-region", type=int, default=2)
    parser.add_argument("--per-source", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="select only; skip live EE gates")
    args = parser.parse_args()

    if args.recurate:
        verdicts = recurate(args.recurate)
        quantified = [v for v in verdicts if v.get("method")]
        print(
            f"\n{len(quantified)}/{len(verdicts)} events quantified "
            f"({sum(v['method'] == 'mbmp' for v in quantified)} mbmp, "
            f"{sum(v['method'] == 'mbsp' for v in quantified)} mbsp)",
            file=sys.stderr,
        )
        if args.output:
            with open(args.output, "w") as fh:
                json.dump(verdicts, fh, indent=2, default=str)
        return 0

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --recurate is given")

    candidates = load_candidates(
        args.input,
        min_t_h=args.min_th,
        max_t_h=args.max_th,
        sources_per_region=args.sources_per_region,
        per_source=args.per_source,
    )
    print(
        f"selected {len(candidates)} candidates across "
        f"{len({c.region for c in candidates})} regions",
        file=sys.stderr,
    )

    if not args.dry_run:
        initialize()
        for i, cand in enumerate(candidates, 1):
            gate_candidate(cand)
            flag = "✓" if cand.gate_pass else "✗"
            print(
                f"  [{i:>2}/{len(candidates)}] {flag} {cand.region:<20} "
                f"{cand.published_q_t_h:>5.1f} t/h  {cand.gate_reason}",
                file=sys.stderr,
            )

    passing = [c for c in candidates if c.gate_pass] if not args.dry_run else candidates
    payload = {
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "input": args.input,
        "n_selected": len(candidates),
        "n_passing": len(passing),
        "candidates": [vars(c) for c in candidates],
        "events_template": [_to_event_json(c) for c in passing],
    }
    with open(args.output, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(
        f"wrote {args.output}: {len(passing)} passing / {len(candidates)} selected", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
