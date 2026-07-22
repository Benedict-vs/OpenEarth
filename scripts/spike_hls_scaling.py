#!/usr/bin/env python
"""Phase 10 Stage 0 spike: are GEE HLS assets pre-scaled reflectance floats?

Decision 2 / Stage 1 ``providers/hls.py`` hinges on this. The HLS catalog vis
example (min 0.01 / max 0.18) implies float reflectance, and assets carry
REF_SCALE_FACTOR / REF_ADD_OFFSET properties — but whether GEE *delivers the
pixels already scaled* is UNDOCUMENTED. Guessing wrong silently washes out every
HLS frame (a ×0.0001 double-scale → black; skipping a needed scale → white).

This fetches one HLSL30 + one HLSS30 image live, prints the real GEE band names
(the provider needs them — HLS may use B04/B03/B02, not B4/B3/B2), the
scale/offset properties, and per-band min/mean/max over a small dry-region window
so the value magnitude answers "pre-scaled float?" empirically.

Live EE, run manually with real auth (never in CI):

    uv run python scripts/spike_hls_scaling.py

Result pasted into the "Stage 0 findings" block of docs/phase10-execution-plan.md.
"""

from __future__ import annotations

import ee

from openearth.ee.client import ee_call, initialize
from openearth.geometry import BBox

# A dry Permian-Basin sub-window: reliable clear-sky HLS coverage, both sensors.
REGION = BBox(-103.5, 31.7, -103.3, 31.9)
WINDOW = ("2023-07-01", "2023-09-30")

COLLECTIONS = {
    "HLSL30 (Landsat 8/9)": "NASA/HLS/HLSL30/v002",
    "HLSS30 (Sentinel-2)": "NASA/HLS/HLSS30/v002",
}


def _scale_props(image: ee.Image) -> dict[str, object]:
    """Return every property whose name mentions scale/offset (case-insensitive)."""
    names = ee_call(image.propertyNames().getInfo) or []
    hits = [n for n in names if "SCALE" in n.upper() or "OFFSET" in n.upper()]
    return {n: ee_call(image.get(n).getInfo) for n in hits}


def _band_stats(image: ee.Image, bands: list[str]) -> dict[str, dict[str, float]]:
    geom = REGION.to_ee_geometry()
    reducer = (
        ee.Reducer.min()
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.max(), sharedInputs=True)
    )
    stats = (
        ee_call(
            image.select(bands)
            .reduceRegion(reducer=reducer, geometry=geom, scale=30, bestEffort=True, maxPixels=1e8)
            .getInfo
        )
        or {}
    )
    out: dict[str, dict[str, float]] = {}
    for b in bands:
        out[b] = {
            "min": stats.get(f"{b}_min"),
            "mean": stats.get(f"{b}_mean"),
            "max": stats.get(f"{b}_max"),
        }
    return out


def main() -> None:
    project = initialize()
    print(f"EE project: {project}")
    print(f"Region: {REGION}  window: {WINDOW[0]}..{WINDOW[1]}\n")

    geom = REGION.to_ee_geometry()
    for label, cid in COLLECTIONS.items():
        coll = ee.ImageCollection(cid).filterDate(*WINDOW).filterBounds(geom).sort("CLOUD_COVERAGE")
        n = int(ee_call(coll.size().getInfo) or 0)
        print(f"── {label}  [{cid}]  {n} scenes in window")
        if n == 0:
            print("   (no scenes — widen the window)\n")
            continue
        img = ee.Image(coll.first())
        band_names = ee_call(img.bandNames().getInfo) or []
        cloud = ee_call(img.get("CLOUD_COVERAGE").getInfo)
        print(f"   selected scene CLOUD_COVERAGE={cloud}")
        print(f"   bandNames: {band_names}")
        print(f"   scale/offset properties: {_scale_props(img)}")
        # Sample the visible reflectance bands (detect padded vs unpadded naming).
        rgb = [b for b in ("B04", "B03", "B02", "B4", "B3", "B2") if b in band_names]
        stats = _band_stats(img, rgb)
        for b, s in stats.items():
            print(f"   {b}: min={s['min']}  mean={s['mean']}  max={s['max']}")
        print()

    print(
        "INTERPRETATION: reflectance bands with values ~0..1 (or small, e.g. 0.01–0.5)\n"
        "=> GEE delivers pre-scaled floats (provider skips ×scale). Large integers\n"
        "(hundreds–thousands) => raw DN (provider applies REF_SCALE_FACTOR + OFFSET)."
    )


if __name__ == "__main__":
    main()
