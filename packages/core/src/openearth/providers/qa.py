"""Bit-packed QA masking shared by the HLS (Fmask) and Landsat (QA_PIXEL) providers.

Both sources encode cloud/shadow/snow state as bit flags in a single integer
band. The Earth Engine side masks with ``band.bitwiseAnd(bit_mask(bits)).eq(0)``;
the same :func:`bit_mask` feeds a pure NumPy :func:`clear_pixels` so the exact
bit arithmetic is unit-tested offline on synthetic QA arrays (no Earth Engine),
guaranteeing the server-side mask and the test agree bit-for-bit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


def bit_mask(bits: Iterable[int]) -> int:
    """Return the integer with exactly *bits* set (e.g. ``[1, 3]`` → ``0b1010`` = 10)."""
    mask = 0
    for bit in bits:
        if bit < 0:
            raise ValueError(f"bit index must be non-negative; got {bit}")
        mask |= 1 << bit
    return mask


def clear_pixels(qa: NDArray[np.integer], defect_bits: Iterable[int]) -> NDArray[np.bool_]:
    """Pure NumPy twin of the EE mask: True where NONE of *defect_bits* are set.

    Mirrors ``qa.bitwiseAnd(bit_mask(defect_bits)).eq(0)`` exactly, so the
    provider's cloud mask can be verified against a synthetic QA array offline.
    """
    return (qa & bit_mask(defect_bits)) == 0
