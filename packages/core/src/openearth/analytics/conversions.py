from __future__ import annotations

from datetime import date, datetime

import ee


def to_ee_date(value: str | date | datetime) -> ee.Date:
    """Convert Python date-like values to ee.Date."""
    if isinstance(value, datetime | date):
        return ee.Date(value.isoformat())
    return ee.Date(value)
