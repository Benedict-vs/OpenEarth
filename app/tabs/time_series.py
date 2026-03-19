"""Tab 2: Time Series -- smoothing, line chart, coverage, CSV."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from openearth.analytics.smoothing import add_rolling_no2


def render(
    chart_df: pd.DataFrame,
    selected_key: str,
) -> None:
    ts_cfg, ts_plot = st.columns([1, 3])

    with ts_cfg:
        st.markdown("**Smoothing**")
        window_days = st.slider(
            "Window (days)",
            min_value=3,
            max_value=30,
            value=14,
            key="ts_window_days",
        )
        min_periods = st.slider(
            "Min valid days",
            min_value=1,
            max_value=window_days,
            value=min(4, window_days),
            key="ts_min_periods",
        )
        smoothing_method = st.selectbox(
            "Method",
            options=["mean", "median"],
            index=0,
            key="ts_method",
        )

        st.markdown("**Series**")
        show_raw = st.checkbox(
            "Raw values", value=True,
            key="ts_show_raw",
        )
        show_smooth = st.checkbox(
            "Smoothed", value=True,
            key="ts_show_smooth",
        )

    ts_df = chart_df.copy()
    if show_smooth:
        ts_df = add_rolling_no2(
            ts_df,
            value_col="value",
            window_days=window_days,
            min_periods=min_periods,
            method=smoothing_method,
            output_col="smoothed",
        )

    plot_cols: list[str] = []
    if show_raw:
        plot_cols.append("value")
    if show_smooth:
        plot_cols.append("smoothed")

    with ts_plot:
        st.subheader(
            f"Daily {selected_key} Time Series",
        )
        if not plot_cols:
            st.warning(
                "Select at least one series."
            )
        else:
            st.line_chart(
                ts_df.set_index("date")[plot_cols],
                use_container_width=True,
            )

    coverage_mean = pd.to_numeric(
        chart_df["coverage_fraction"],
        errors="coerce",
    ).mean()

    with st.expander("Coverage", expanded=False):
        st.area_chart(
            chart_df.set_index("date")[
                ["coverage_fraction"]
            ],
            use_container_width=True,
        )
        st.caption(
            f"Rows: {len(chart_df)} | "
            f"Mean coverage: {coverage_mean:.2%}"
        )

    with st.expander("Data", expanded=False):
        st.dataframe(
            chart_df, use_container_width=True,
        )
        export_df = chart_df.copy()
        export_df["date"] = pd.to_datetime(
            export_df["date"]
        ).dt.strftime("%Y-%m-%d")
        csv_data = export_df.to_csv(
            index=False
        ).encode("utf-8")

        date_min = export_df["date"].min()
        date_max = export_df["date"].max()
        file_name = (
            f"openearth_"
            f"{selected_key.lower()}_"
            f"{date_min}_{date_max}.csv"
        )

        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            key="exp_csv",
        )
