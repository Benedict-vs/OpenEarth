"""Tab 4: Statistics – summary metrics and trend analysis."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from openearth.providers.gas_registry import get_gas_config


def render(
    chart_df: pd.DataFrame,
    selected_gas: str,
) -> None:
    st.subheader("Statistics")
    cfg = get_gas_config(selected_gas)
    stats_dates = pd.to_datetime(
        chart_df["date"], errors="coerce"
    )
    stats_values = pd.to_numeric(
        chart_df["value"], errors="coerce"
    )
    valid = stats_dates.notna() & stats_values.notna()

    if not valid.any():
        st.warning(
            "No valid values available for "
            "statistics in the selected range."
        )
        return

    valid_dates = stats_dates[valid]
    valid_values = stats_values[valid]

    mean_val = valid_values.mean()
    median_val = valid_values.median()
    max_idx = valid_values.idxmax()
    max_val = float(valid_values.loc[max_idx])
    max_date = pd.to_datetime(
        chart_df.loc[max_idx, "date"]
    ).date()

    x_days = (
        valid_dates - valid_dates.min()
    ).dt.days.astype(float)
    trend_value = "Stable"
    trend_delta = "n/a"

    if x_days.nunique() >= 2:
        x_centered = x_days - x_days.mean()
        y_centered = (
            valid_values - valid_values.mean()
        )
        denominator = (
            x_centered.pow(2).sum()
        )
        if denominator > 0:
            slope_per_day = float(
                (x_centered * y_centered).sum()
                / denominator
            )
            slope_per_week = slope_per_day * 7.0
            stability_tol = max(
                abs(float(mean_val)) * 0.005,
                1e-12,
            )

            if slope_per_week > stability_tol:
                trend_value = "Increasing"
            elif slope_per_week < -stability_tol:
                trend_value = "Decreasing"

            trend_delta = (
                f"{slope_per_week * cfg.display_scale:+.4g} "
                f"{cfg.display_unit}/week"
            )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Mean",
        f"{mean_val * cfg.display_scale:.4g} {cfg.display_unit}",
    )
    m2.metric(
        "Median",
        f"{median_val * cfg.display_scale:.4g} {cfg.display_unit}",
    )
    m3.metric(
        "Max",
        f"{max_val * cfg.display_scale:.4g} {cfg.display_unit}",
        delta=f"on {max_date.isoformat()}",
    )
    m4.metric(
        "Trend",
        trend_value,
        delta=trend_delta,
    )

    st.caption(
        f"Computed from {int(valid.sum())} valid "
        "daily observations."
    )
