"""Time-series smoothing utilities."""

from __future__ import annotations

import pandas as pd


def add_rolling_smooth(
    df: pd.DataFrame,
    value_col: str = "value",
    window_days: int = 14,
    min_periods: int = 4,
    method: str = "mean",
    output_col: str | None = None,
) -> pd.DataFrame:
    """Return a copy of *df* with a rolling-smoothed column.

    Expected input includes:
    - ``date`` column (datetime-like or parseable)
    - *value_col* column with numeric values
    """
    if "date" not in df.columns:
        raise ValueError("Input DataFrame must contain a `date` column.")
    if value_col not in df.columns:
        raise ValueError(f"Input DataFrame must contain `{value_col}`.")
    if window_days < 1:
        raise ValueError("`window_days` must be >= 1.")
    if min_periods < 1:
        raise ValueError("`min_periods` must be >= 1.")
    if min_periods > window_days:
        raise ValueError("`min_periods` cannot exceed `window_days`.")
    if method not in {"mean", "median"}:
        raise ValueError("`method` must be either `mean` or `median`.")

    smoothed_col = output_col or f"{value_col}_{method}_{window_days}d"

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").reset_index(drop=True)

    roller = out[value_col].rolling(window=window_days,
                                    min_periods=min_periods)
    if method == "median":
        out[smoothed_col] = roller.median()
    else:
        out[smoothed_col] = roller.mean()

    return out
