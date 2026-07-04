"""ROI models: bounding box or polygon, with pure-Python validation.

All geometric bookkeeping (validation, global-coverage checks, centers,
aspect ratios) happens client-side so it is unit-testable and costs no
Earth Engine round-trips. ``to_ee_geometry()`` is the only EE touchpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import ee

from openearth.errors import InvalidROIError, validate_roi_bbox


@dataclass(frozen=True)
class BBox:
    """Geographic bounding box in WGS84 degrees. Validates on construction."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        validate_roi_bbox(self.west, self.south, self.east, self.north)

    @property
    def is_global(self) -> bool:
        """True if the box covers (essentially) the whole Earth.

        Used to skip server-side ``.clip()`` calls that choke Earth Engine
        when the ROI is the entire planet.
        """
        return self.west <= -179 and self.east >= 179 and self.south <= -89 and self.north >= 89

    @property
    def center(self) -> tuple[float, float]:
        """(lat, lon) center."""
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    @property
    def width_deg(self) -> float:
        return self.east - self.west

    @property
    def height_deg(self) -> float:
        return self.north - self.south

    def aspect_ratio(self) -> float:
        """Real-world width/height ratio, cosine-corrected for latitude."""
        mid_lat_rad = math.radians((self.south + self.north) / 2)
        width = self.width_deg * math.cos(mid_lat_rad)
        if width <= 0 or self.height_deg <= 0:
            return 1.0
        return width / self.height_deg

    def rounded(self, ndigits: int = 5) -> BBox:
        """Round coordinates (~1 m at 5 dp) for stable cache keys."""
        return BBox(
            round(self.west, ndigits),
            round(self.south, ndigits),
            round(self.east, ndigits),
            round(self.north, ndigits),
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    def to_ee_geometry(self) -> ee.Geometry:
        return ee.Geometry.Rectangle([self.west, self.south, self.east, self.north])


@dataclass(frozen=True)
class PolygonROI:
    """Single-ring polygon ROI as an (lon, lat) coordinate sequence.

    The ring may be open (first point not repeated); it is treated as
    closed. Holes are intentionally unsupported.
    """

    coordinates: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        ring = list(self.coordinates)
        if len(ring) >= 2 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            raise InvalidROIError(f"Polygon needs at least 3 distinct points; got {len(ring)}.")
        for lon, lat in ring:
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise InvalidROIError(f"Polygon vertex out of range: lon={lon}, lat={lat}.")
        bbox = self.bounds
        if bbox.width_deg == 0 or bbox.height_deg == 0:
            raise InvalidROIError("Polygon is degenerate (zero width or height).")

    @property
    def ring(self) -> list[tuple[float, float]]:
        """Closed exterior ring (first point repeated at the end)."""
        ring = list(self.coordinates)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring

    @property
    def bounds(self) -> BBox:
        lons = [p[0] for p in self.coordinates]
        lats = [p[1] for p in self.coordinates]
        return BBox(min(lons), min(lats), max(lons), max(lats))

    @property
    def is_global(self) -> bool:
        return self.bounds.is_global

    @property
    def center(self) -> tuple[float, float]:
        return self.bounds.center

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "Polygon", "coordinates": [[list(p) for p in self.ring]]}

    def to_ee_geometry(self) -> ee.Geometry:
        return ee.Geometry.Polygon([[list(p) for p in self.ring]])


ROI = BBox | PolygonROI
