"""Error types, classification, and input validation.

Pure UI-independent module. The Streamlit app imports
from here to display errors; non-Streamlit callers
(scripts, notebooks) catch these exceptions directly.
"""

from __future__ import annotations

from datetime import date, datetime

# ── Exception hierarchy ──────────────────────────────────────


class OpenEarthError(Exception):
    """Base class for all OpenEarth library errors."""


class InvalidROIError(OpenEarthError, ValueError):
    """ROI bounding box is malformed or degenerate."""


class InvalidDateRangeError(OpenEarthError, ValueError):
    """Date range is invalid (end not after start, etc.)."""


class EmptyCollectionError(OpenEarthError):
    """No images found for the requested variable, ROI, and dates."""


# ── Earth Engine error classification ────────────────────────

_AUTH_PHRASES = (
    "not authorized",
    "access denied",
    "permission denied",
    "authenticate",
    "credentials",
    "forbidden",
    " 401",
    " 403",
)

_QUOTA_PHRASES = (
    "too many concurrent",
    "quota exceeded",
    "rate limit",
    "limit exceeded",
    " 429",
    "user memory limit exceeded",
)

_TIMEOUT_PHRASES = (
    "timed out",
    "timeout",
    "deadline exceeded",
)

_EMPTY_PHRASES = (
    "collection is empty",
    "no images",
    "contains no images",
    "empty collection",
    "0 elements",
    "no valid pixels",
    "collection.first",
    "no bands",
    "image collection is empty",
)


def classify_ee_error(
    exc: Exception,
) -> tuple[str, str]:
    """Classify an Earth Engine error by its message.

    Returns (category, user_message) where category is
    one of: "auth", "quota", "timeout", "empty",
    "unknown".
    """
    message = str(exc).lower()

    if any(p in message for p in _AUTH_PHRASES):
        return (
            "auth",
            "Earth Engine authentication or "
            "permissions failed. Check project "
            "access and sign in again.",
        )
    if any(p in message for p in _QUOTA_PHRASES):
        return (
            "quota",
            "Earth Engine quota or concurrency "
            "limit reached. Try a smaller "
            "ROI/date range or retry shortly.",
        )
    if any(p in message for p in _TIMEOUT_PHRASES):
        return (
            "timeout",
            "Earth Engine request timed out. "
            "Try a smaller ROI or date range.",
        )
    if any(p in message for p in _EMPTY_PHRASES):
        return (
            "empty",
            "No satellite observations are "
            "available for this variable, ROI, "
            "and time window. If your date range "
            "extends close to today, try an earlier "
            "end date \u2014 recent imagery may not be "
            "processed yet.",
        )

    return (
        "unknown",
        "Unexpected Earth Engine error.",
    )


# ── Input validation ─────────────────────────────────────────


def validate_roi_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Raise :class:`InvalidROIError` if the bbox is malformed.

    Checks longitude/latitude ranges and that the bbox
    has positive width and height. Runs in O(1) — no
    Earth Engine round-trip.
    """
    if not -180 <= west <= 180 or not -180 <= east <= 180:
        raise InvalidROIError(
            f"Longitudes must be in [-180, 180]; "
            f"got west={west}, east={east}."
        )
    if not -90 <= south <= 90 or not -90 <= north <= 90:
        raise InvalidROIError(
            f"Latitudes must be in [-90, 90]; "
            f"got south={south}, north={north}."
        )
    if west >= east:
        raise InvalidROIError(
            f"ROI has zero or negative width: "
            f"west={west} must be < east={east}."
        )
    if south >= north:
        raise InvalidROIError(
            f"ROI has zero or negative height: "
            f"south={south} must be < north={north}."
        )


def validate_date_range(
    start: str | date | datetime,
    end: str | date | datetime,
) -> None:
    """Raise :class:`InvalidDateRangeError` if ``end <= start``.

    Requires ``end`` to be strictly after ``start`` — a
    same-day range yields no daily buckets.
    """
    start_d = _coerce_date(start)
    end_d = _coerce_date(end)
    if end_d <= start_d:
        raise InvalidDateRangeError(
            f"End date must be after start date; "
            f"got start={start_d}, end={end_d}."
        )


def _coerce_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()
