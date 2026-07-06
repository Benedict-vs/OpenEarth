"""Render a detection's ΔXCH4 array to an RGBA overlay PNG (Pillow).

Pure pixel work: load the ``.npz``, colour-map the ΔXCH4 field over the
catalog's CH4 diverging palette, and emit PNG bytes with below-``vmin`` (and
NaN) pixels transparent. Array row 0 is the grid's north row = image top — no
flips (MapLibre image-source corners go [top-left, …]).
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from openearth.catalog.registry import get_product

if TYPE_CHECKING:
    from pathlib import Path

# The CH4 diverging palette (hex stops), reused from the catalog quicklook.
_PALETTE_HEX: list[str] = get_product("s2", "CH4_ANOMALY").palette


def _palette_lut() -> NDArray[np.float64]:
    """Palette hex stops as an ``(n, 3)`` float array in [0, 255]."""
    stops = [[int(h.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)] for h in _PALETTE_HEX]
    return np.asarray(stops, dtype=np.float64)


def _colormap(norm: NDArray[np.float64]) -> NDArray[np.uint8]:
    """Map [0, 1] values to RGB by linear interpolation over the palette."""
    stops = _palette_lut()
    positions = np.linspace(0.0, 1.0, len(stops))
    clamped = np.clip(norm, 0.0, 1.0)
    channels = [np.interp(clamped, positions, stops[:, c]) for c in range(3)]
    return np.stack(channels, axis=-1).round().astype(np.uint8)


def default_vmax(array: NDArray[np.float64]) -> float:
    """Default upper bound: the 98th percentile of finite values (fallback 1)."""
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return 1.0
    vmax = float(np.percentile(finite, 98))
    return vmax if vmax > 0.0 else 1.0


def render_overlay_png(
    array_path: Path, vmin: float | None = None, vmax: float | None = None
) -> bytes:
    """Render the detection's ΔXCH4 field to RGBA PNG bytes."""
    with np.load(array_path, allow_pickle=False) as npz:
        array = np.asarray(npz["xch4_ppb"], dtype=np.float64)

    lo = 0.0 if vmin is None else vmin
    hi = default_vmax(array) if vmax is None else vmax
    span = hi - lo if hi > lo else 1.0

    norm = (array - lo) / span
    rgb = _colormap(np.where(np.isfinite(norm), norm, 0.0))
    alpha = np.where(np.isfinite(array) & (array >= lo), 255, 0).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])

    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return buffer.getvalue()
