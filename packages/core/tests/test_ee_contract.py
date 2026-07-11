"""Live Earth Engine *contract* probes (fix 14 / Tier 3 P1+P2).

These freeze two upstream-EE behaviours the whole pipeline silently assumes, so
a change in EE's semantics surfaces here in the live suite instead of as quiet
georeferencing or resampling drift:

  A. ``computePixels`` interprets our ``grid_for`` affine's translateX/Y as the
     *top-left corner* of pixel (0, 0) — i.e. EE samples at our pixel centres
     ``(x0 + (col+0.5)·xscale, y0 − (row+0.5)·yscale)``. Half-pixel errors would
     be common-mode across target/reference (same grid), but this pins zero.
  B. S2 20 m SWIR bands resample by *nearest-neighbour* (EE default), not
     interpolation: fetched at 10 m they show ≥ 40 % adjacent-pixel duplication;
     at native 20 m nearly every pixel is distinct. This is the mechanism behind
     Tier 1 F5's cross-tile registration noise.

Run explicitly with real per-user auth (never in CI):

    OPENEARTH_EE_TESTS=1 uv run pytest -m ee packages/core/tests/test_ee_contract.py
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from openearth.geometry import BBox

pytestmark = [
    pytest.mark.ee,
    pytest.mark.skipif(
        os.environ.get("OPENEARTH_EE_TESTS") != "1",
        reason="live EE tests need OPENEARTH_EE_TESTS=1 and real auth",
    ),
]

# A small all-land box over the Korpezhe well-pad belt (Turkmenistan).
_BBOX = BBox(53.95, 38.48, 53.98, 38.50)


@pytest.fixture(scope="module", autouse=True)
def _ee_session() -> None:
    from openearth.ee.client import initialize

    initialize()


def test_grid_affine_samples_at_pixel_centers() -> None:
    """Probe A: pixelLonLat through our grid returns our pixel centres."""
    import ee

    from openearth.ee.pixels import PixelWindow, fetch_window, grid_for

    spec = grid_for(_BBOX, scale_m=100)
    grid = spec.window_grid(PixelWindow(row_off=0, col_off=0, width=spec.width, height=spec.height))
    lonlat = fetch_window(ee.Image.pixelLonLat(), grid, ["longitude", "latitude"])

    cols = np.arange(spec.width)
    rows = np.arange(spec.height)
    exp_lon = spec.x0 + (cols + 0.5) * spec.xscale  # (W,)
    exp_lat = spec.y0 - (rows + 0.5) * spec.yscale  # (H,)

    max_lon_err = float(np.max(np.abs(lonlat[..., 0] - exp_lon[None, :])))
    max_lat_err = float(np.max(np.abs(lonlat[..., 1] - exp_lat[:, None])))
    assert max_lon_err < 1e-5, f"lon center drift {max_lon_err:.2e} deg — grid convention changed"
    assert max_lat_err < 1e-5, f"lat center drift {max_lat_err:.2e} deg — grid convention changed"


def _adjacent_equal_fraction(band: np.ndarray) -> float:
    """Fraction of horizontally-adjacent finite pixel pairs that are exactly equal."""
    left, right = band[:, :-1], band[:, 1:]
    both_finite = np.isfinite(left) & np.isfinite(right)
    equal = both_finite & (left == right)
    n = int(both_finite.sum())
    return float(equal.sum()) / n if n else 0.0


def test_b11_default_resampling_is_nearest_neighbor() -> None:
    """Probe B: B11 at 10 m duplicates neighbours (NN); at native 20 m it doesn't."""
    from openearth.ee.pixels import PixelWindow, fetch_window, grid_for
    from openearth.methane.retrieval import _build_scene_image
    from openearth.methane.scenes import list_scenes

    scenes = list_scenes(_BBOX, "2018-06-18", "2018-06-21", max_cloud=90.0)
    assert scenes, "expected a Korpezhe 2018-06-19 scene"
    image = _build_scene_image(scenes[0].scene_id).select("B11")

    def fetch_b11(scale_m: int) -> np.ndarray:
        spec = grid_for(_BBOX, scale_m)
        grid = spec.window_grid(
            PixelWindow(row_off=0, col_off=0, width=spec.width, height=spec.height)
        )
        cube = fetch_window(image, grid, ["B11"])[..., 0]
        cube[cube == -9999.0] = np.nan  # _build_scene_image unmask fill
        return cube

    dup_10m = _adjacent_equal_fraction(fetch_b11(10))
    dup_20m = _adjacent_equal_fraction(fetch_b11(20))
    assert dup_10m >= 0.40, f"10 m duplication {dup_10m:.2f} — expected NN block duplication"
    assert dup_20m < 0.20, f"20 m duplication {dup_20m:.2f} — native band should be distinct"
