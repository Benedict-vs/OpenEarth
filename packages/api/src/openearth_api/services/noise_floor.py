"""Per-site empirical noise-floor context (Tier 1 fix 1 + fix 9b).

Loads the packaged ``noise_floor_v1.json`` (frozen by ``scripts/noise_floor.py``)
and resolves, for a detection, the floor it should be read against: the median Q
this pipeline retrieves from plume-free scene pairs at the detection's site (or a
pooled global floor for unknown/custom sites). Reported as display context and a
flag — never a gate, never folded into σ (that would double-count the MC-bootstrapped
noise and bury an empirical site number inside a model budget).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

_FLOOR_FILENAME = "noise_floor_v1.json"


@lru_cache(maxsize=4)
def _load_floor_cached(path_str: str | None) -> dict[str, Any]:
    if path_str is None:
        resource = files("openearth_api").joinpath("data", _FLOOR_FILENAME)
        try:
            text = resource.read_text()
        except (FileNotFoundError, OSError):
            return {}  # not frozen yet → no floor context (graceful)
        return json.loads(text)
    try:
        return json.loads(Path(path_str).read_text())
    except (FileNotFoundError, OSError):
        return {}


def load_floor(path: Path | None = None) -> dict[str, Any]:
    """Load the noise floor (cached). *path* defaults to the packaged v1 JSON;
    returns ``{}`` when the floor has not been frozen yet."""
    return _load_floor_cached(str(path) if path is not None else None)


def resolve_floor(
    floor: dict[str, Any], site_name: str | None, q_kg_h: float | None
) -> tuple[float | None, str | None, bool]:
    """Resolve ``(noise_floor_kg_h, floor_source, below_noise_floor)`` for a detection.

    Prefers the detection's own site floor; falls back to the pooled global floor
    for unknown/custom sites. ``below_noise_floor`` is ``q_kg_h ≤ floor`` — at or
    under the level indistinguishable from this pipeline's retrieval noise.
    """
    sites = floor.get("sites", {}) if floor else {}
    site_entry = sites.get(site_name) if site_name is not None else None
    site_floor = site_entry.get("floor_kg_h") if isinstance(site_entry, dict) else None

    floor_kg_h: float | None
    source: str | None
    if site_floor is not None:
        floor_kg_h, source = float(site_floor), "site"
    else:
        global_floor = (floor.get("global", {}) or {}).get("floor_kg_h") if floor else None
        floor_kg_h = float(global_floor) if global_floor is not None else None
        source = "global" if floor_kg_h is not None else None

    below = floor_kg_h is not None and q_kg_h is not None and q_kg_h <= floor_kg_h
    return floor_kg_h, source, below
