"""Tab 4: Statistics -- summary, distribution, seasonality, anomalies."""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from openearth.providers.s2_registry import (
    get_s2_index_config,
)
from openearth.providers.s5p_registry import get_gas_config


def _get_config(data_key: str, source: str):
    """Return the registry config for *data_key*."""
    if source == "s2":
        return get_s2_index_config(data_key)
    return get_gas_config(data_key)


def _fmt(value: float, cfg) -> str:
    """Format a value with display scale and unit."""
    return (
        f"{value * cfg.display_scale:.4g} "
        f"{cfg.display_unit}"
    )


def render(
    chart_df: pd.DataFrame,
    selected_key: str,
    source: str = "s5p",
) -> None:
    st.subheader("Statistics")
    cfg = _get_config(selected_key, source)

    # ── Prepare clean series ──────────────────────
    df = chart_df.copy()
    df["date"] = pd.to_datetime(
        df["date"], errors="coerce",
    )
    df["value"] = pd.to_numeric(
        df["value"], errors="coerce",
    )
    df = df.dropna(
        subset=["date", "value"],
    ).reset_index(drop=True)

    if df.empty:
        st.warning(
            "No valid observations for statistics."
        )
        return

    values = df["value"]
    n_obs = len(values)

    # ── 1. Metrics row ────────────────────────────
    mean_val = values.mean()
    median_val = values.median()
    std_val = values.std()
    min_idx = values.idxmin()
    max_idx = values.idxmax()
    min_val = float(values.loc[min_idx])
    max_val = float(values.loc[max_idx])
    min_date = df.loc[min_idx, "date"].date()
    max_date = df.loc[max_idx, "date"].date()

    m1, m2, m3 = st.columns(3)
    m1.metric("Mean", _fmt(mean_val, cfg))
    m2.metric("Median", _fmt(median_val, cfg))
    m3.metric("Std Dev", _fmt(std_val, cfg))

    m4, m5 = st.columns(2)
    m4.metric(
        "Min", _fmt(min_val, cfg),
        delta=str(min_date),
    )
    m5.metric(
        "Max", _fmt(max_val, cfg),
        delta=str(max_date),
    )

    # ── 2. Percentile breakdown ───────────────────
    st.markdown("#### Percentiles")
    st.caption(
        "Percentiles show the value below which a "
        "given percentage of days fall. For example, "
        "P25 means 25% of days had a value at or "
        "below that level. P50 equals the median."
    )
    pcts = [10, 25, 75, 90]
    pct_values = np.percentile(values, pcts)
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("P10", _fmt(pct_values[0], cfg))
    p2.metric("P25", _fmt(pct_values[1], cfg))
    p3.metric("P75", _fmt(pct_values[2], cfg))
    p4.metric("P90", _fmt(pct_values[3], cfg))

    # ── 3. Distribution histogram ─────────────────
    st.markdown("#### Distribution")
    hist_df = pd.DataFrame({
        "value": values * cfg.display_scale,
    })
    unit_label = (
        f"{selected_key} ({cfg.display_unit})"
    )
    hist_chart = (
        alt.Chart(hist_df)
        .mark_bar(
            cornerRadiusTopLeft=3,
            cornerRadiusTopRight=3,
        )
        .encode(
            alt.X(
                "value:Q",
                bin=alt.Bin(maxbins=40),
                title=unit_label,
            ),
            alt.Y("count():Q", title="Days"),
        )
        .properties(height=250)
    )
    rules = pd.DataFrame({
        "val": [
            mean_val * cfg.display_scale,
            median_val * cfg.display_scale,
        ],
        "label": ["Mean", "Median"],
    })
    rule_chart = (
        alt.Chart(rules)
        .mark_rule(
            strokeDash=[4, 4], strokeWidth=2,
        )
        .encode(
            x="val:Q",
            color=alt.Color(
                "label:N",
                scale=alt.Scale(
                    domain=["Mean", "Median"],
                    range=["#d53e4f", "#3288bd"],
                ),
                legend=alt.Legend(title=None),
            ),
        )
    )
    st.altair_chart(
        hist_chart + rule_chart,
        width="stretch",
    )

    # ── 4. Monthly box plot (seasonal pattern) ────
    st.markdown("#### Seasonal Pattern")
    df["month"] = df["date"].dt.month
    df["month_name"] = df["date"].dt.strftime("%b")
    df["display_value"] = (
        df["value"] * cfg.display_scale
    )

    month_order = (
        df.sort_values("month")["month_name"]
        .drop_duplicates()
        .tolist()
    )

    box_chart = (
        alt.Chart(df)
        .mark_boxplot(extent="min-max")
        .encode(
            x=alt.X(
                "month_name:N",
                title="Month",
                sort=month_order,
            ),
            y=alt.Y(
                "display_value:Q",
                title=unit_label,
            ),
        )
        .properties(height=300)
    )
    st.altair_chart(
        box_chart, width="stretch",
    )

    # ── 5. Anomaly detection (beyond 2 sigma) ────
    st.markdown("#### Anomalies")
    upper_bound = mean_val + 2 * std_val
    lower_bound = mean_val - 2 * std_val
    anomalies = df[
        (values > upper_bound)
        | (values < lower_bound)
    ].copy()

    if anomalies.empty:
        st.info(
            "No anomalous days detected "
            "(all values within 2 standard "
            "deviations)."
        )
    else:
        st.caption(
            f"**{len(anomalies)}** of {n_obs} days "
            f"({len(anomalies) / n_obs:.1%}) fall "
            f"outside 2\u03c3 of the mean "
            f"({_fmt(lower_bound, cfg)} \u2013 "
            f"{_fmt(upper_bound, cfg)})."
        )

        plot_df = df[
            ["date", "display_value"]
        ].copy()
        plot_df["anomaly"] = (
            (values > upper_bound)
            | (values < lower_bound)
        )

        base = (
            alt.Chart(plot_df)
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y(
                    "display_value:Q",
                    title=unit_label,
                ),
            )
        )
        line = base.mark_line(
            color="#999999", strokeWidth=1,
        )
        points = (
            base
            .transform_filter(
                alt.datum.anomaly == True  # noqa: E712
            )
            .mark_circle(
                size=50, color="#d53e4f",
            )
        )
        band_df = pd.DataFrame({
            "lower": [
                lower_bound * cfg.display_scale,
            ],
            "upper": [
                upper_bound * cfg.display_scale,
            ],
        })
        band = (
            alt.Chart(band_df)
            .mark_rect(opacity=0.1, color="#3288bd")
            .encode(y="lower:Q", y2="upper:Q")
        )
        st.altair_chart(
            (band + line + points).properties(
                height=250,
            ),
            width="stretch",
        )

        with st.expander(
            "Anomalous days", expanded=False,
        ):
            show_df = anomalies[
                ["date", "value", "coverage_fraction"]
            ].copy()
            show_df["value"] = (
                show_df["value"] * cfg.display_scale
            )
            show_df.columns = [
                "Date",
                f"Value ({cfg.display_unit})",
                "Coverage",
            ]
            st.dataframe(
                show_df,
                width="stretch",
                hide_index=True,
            )

    # ── 6. Year-over-year comparison ──────────────
    date_range = df["date"].max() - df["date"].min()
    if date_range > pd.Timedelta(days=365):
        st.markdown("#### Year-over-Year")
        df["year"] = df["date"].dt.year
        df["day_of_year"] = df["date"].dt.dayofyear

        yoy_chart = (
            alt.Chart(df)
            .mark_line(strokeWidth=1.5)
            .encode(
                x=alt.X(
                    "day_of_year:Q",
                    title="Day of Year",
                    scale=alt.Scale(
                        domain=[1, 366],
                    ),
                ),
                y=alt.Y(
                    "display_value:Q",
                    title=unit_label,
                ),
                color=alt.Color(
                    "year:N", title="Year",
                ),
            )
            .properties(height=300)
        )
        st.altair_chart(
            yoy_chart, width="stretch",
        )

        yearly = (
            df.groupby("year")["value"]
            .agg([
                "mean", "median", "std",
                "min", "max", "count",
            ])
            .reset_index()
        )
        for col in [
            "mean", "median", "std", "min", "max",
        ]:
            yearly[col] = yearly[col].apply(
                lambda v: _fmt(v, cfg),
            )
        yearly.columns = [
            "Year", "Mean", "Median", "Std Dev",
            "Min", "Max", "Days",
        ]
        st.dataframe(
            yearly,
            width="stretch",
            hide_index=True,
        )

    # ── 7. Data quality ───────────────────────────
    st.markdown("#### Data Quality")
    coverage = pd.to_numeric(
        chart_df["coverage_fraction"],
        errors="coerce",
    )
    mean_cov = coverage.mean()
    low_cov_days = int((coverage < 0.1).sum())

    q1, q2, q3 = st.columns(3)
    q1.metric("Observations", f"{n_obs} days")
    q2.metric("Mean Coverage", f"{mean_cov:.1%}")
    q3.metric(
        "Low-Coverage Days (<10%)",
        str(low_cov_days),
    )

    st.caption(
        f"Date range: {df['date'].min().date()} "
        f"to {df['date'].max().date()}"
    )
