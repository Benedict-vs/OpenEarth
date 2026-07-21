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
import math
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import h5py
import numpy as np
from numpy.typing import NDArray

from openearth.ee.pixels import _M_PER_DEG, GridSpec
from openearth.settings import get_settings

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=sorted(SITE_TILES), help="restrict to one base scene")
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
    print(f"{len(paths)} products under {root}")
    for site_key in sorted(SITE_TILES):
        n = sum(1 for p in paths if f"_T{SITE_TILES[site_key]}_" in p.name)
        if n:
            print(f"  {SITE_NAMES[site_key]:<16} ({SITE_TILES[site_key]}): {n} products")
    return 0


if __name__ == "__main__":
    sys.exit(main())
