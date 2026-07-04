from __future__ import annotations

import pytest

from openearth.errors import (
    InvalidDateRangeError,
    InvalidROIError,
    classify_ee_error,
    is_transient_ee_error,
    validate_date_range,
    validate_roi_bbox,
)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Permission denied for project", "auth"),
        ("HTTP 403 Forbidden", "auth"),
        ("Too many concurrent aggregations", "quota"),
        ("User memory limit exceeded.", "quota"),
        ("Computation timed out.", "timeout"),
        ("Deadline exceeded while computing", "timeout"),
        ("ImageCollection.first: collection is empty", "empty"),
        ("Image has no bands.", "empty"),
        ("Something else entirely", "unknown"),
    ],
)
def test_classify_ee_error(message: str, category: str) -> None:
    cat, user_message = classify_ee_error(Exception(message))
    assert cat == category
    assert user_message  # never empty


def test_transient_categories_retry() -> None:
    assert is_transient_ee_error(Exception("rate limit"))
    assert is_transient_ee_error(Exception("timed out"))
    assert not is_transient_ee_error(Exception("permission denied"))
    assert not is_transient_ee_error(Exception("collection is empty"))
    assert not is_transient_ee_error(Exception("???"))


@pytest.mark.parametrize(
    ("west", "south", "east", "north"),
    [
        (-181, 0, 10, 10),  # lon out of range
        (0, -91, 10, 10),  # lat out of range
        (10, 0, 10, 10),  # zero width
        (12, 0, 10, 10),  # negative width
        (0, 10, 10, 10),  # zero height
        (0, 12, 10, 10),  # negative height
    ],
)
def test_validate_roi_bbox_rejects(west: float, south: float, east: float, north: float) -> None:
    with pytest.raises(InvalidROIError):
        validate_roi_bbox(west, south, east, north)


def test_validate_roi_bbox_accepts_globe() -> None:
    validate_roi_bbox(-180, -90, 180, 90)


def test_validate_date_range() -> None:
    validate_date_range("2024-01-01", "2024-01-02")
    with pytest.raises(InvalidDateRangeError):
        validate_date_range("2024-01-02", "2024-01-02")
    with pytest.raises(InvalidDateRangeError):
        validate_date_range("2024-01-03", "2024-01-02")
