"""One cache tier: diskcache at ``{data_dir}/cache/``.

Keys are sha256 hashes of canonical JSON so that logically identical
requests (dict order, float noise beyond 5 dp in the ROI) hit the same
entry. ``ALGO_VERSION`` is baked into every key — bump it whenever the
science behind a cached artifact changes, and stale entries simply stop
being found.

Tile URLs are deliberately never cached (they expire ~4 h after minting);
thumbnails and other derived artifacts are.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import diskcache

from openearth.geometry import BBox, PolygonROI

if TYPE_CHECKING:
    from openearth.geometry import ROI
    from openearth.settings import Settings

# Bump when cached artifact semantics change (rendering math, defaults, …).
# 2: CH4 LUT v2 (Curtis–Godson effective T/p) changed methane ΔΩ/ΔXCH4 outputs.
# 4: plume footprint thresholded on the frozen mask-LUT ΔΩ (invariant to reporting-LUT swaps).
# 5: CH4 LUT v4 (interfering H2O/CO2 + TSIS-1 solar weighting) changed methane ΔΩ/ΔXCH4 outputs.
ALGO_VERSION = 5

# Open-ended date ranges keep collecting new scenes upstream; closed
# historical ranges are immutable.
OPEN_ENDED_TTL_SECONDS = 6 * 3600

_ROI_NDIGITS = 5


def make_cache(settings: Settings) -> diskcache.Cache:
    """Open (creating if needed) the single on-disk cache."""
    return diskcache.Cache(str(settings.data_dir / "cache"))


def roi_key_part(roi: ROI | None) -> dict[str, Any] | None:
    """Canonical, JSON-able ROI representation rounded to 5 dp (~1 m)."""
    if roi is None:
        return None
    if isinstance(roi, BBox):
        rounded = roi.rounded(_ROI_NDIGITS)
        return {"kind": "bbox", "bounds": rounded.as_tuple()}
    if isinstance(roi, PolygonROI):
        return {
            "kind": "polygon",
            "ring": [(round(lon, _ROI_NDIGITS), round(lat, _ROI_NDIGITS)) for lon, lat in roi.ring],
        }
    raise TypeError(f"Unsupported ROI type: {type(roi).__name__}")


def cache_key(op: str, **parts: Any) -> str:
    """sha256 of canonical JSON over *op*, *parts*, and ``ALGO_VERSION``."""
    payload = {"op": op, "algo_version": ALGO_VERSION, **parts}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def ttl_for(end_date: str | date) -> int | None:
    """Cache TTL for a query ending at *end_date*.

    ``None`` (never expire) for closed historical ranges; a short TTL when
    the range reaches into the present and new scenes may still arrive.
    """
    end = datetime.fromisoformat(end_date).date() if isinstance(end_date, str) else end_date
    today = datetime.now(tz=UTC).date()
    return None if end < today else OPEN_ENDED_TTL_SECONDS
