from __future__ import annotations

import pandas as pd
import pytest

from openearth.analytics.smoothing import add_rolling_smooth


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=30, freq="D"),
            "value": list(range(30)),
        }
    )


def test_adds_named_column_sorted() -> None:
    out = add_rolling_smooth(_df().sample(frac=1, random_state=0), window_days=7, min_periods=1)
    assert "value_mean_7d" in out.columns
    assert out["date"].is_monotonic_increasing
    # mean of a linear ramp lags the raw value
    assert out["value_mean_7d"].iloc[-1] == pytest.approx((23 + 29) / 2)


def test_median_method_and_custom_output() -> None:
    out = add_rolling_smooth(_df(), method="median", output_col="smooth")
    assert "smooth" in out.columns


@pytest.mark.parametrize(
    "kwargs",
    [
        {"value_col": "missing"},
        {"window_days": 0},
        {"min_periods": 0},
        {"window_days": 3, "min_periods": 5},
        {"method": "mode"},
    ],
)
def test_rejects_bad_arguments(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - message varies by argument
        add_rolling_smooth(_df(), **kwargs)  # type: ignore[arg-type]
