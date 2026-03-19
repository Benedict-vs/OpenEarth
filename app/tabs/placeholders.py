"""Placeholder tabs: Compare, Animation, and Image (coming soon)."""

from __future__ import annotations

import streamlit as st


def render_compare(selected_key: str) -> None:
    st.subheader("Compare")
    st.info(
        "**Coming soon** \u2013 Compare two variables "
        "or two regions side by side over the same "
        "time period."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.selectbox(
            "Region / Variable A",
            options=[
                f"Current ROI \u2013 {selected_key}"
            ],
            disabled=True,
            key="cmp_a",
        )
        st.empty()
    with c2:
        st.selectbox(
            "Region / Variable B",
            options=["Select..."],
            disabled=True,
            key="cmp_b",
        )
        st.empty()


def render_animation() -> None:
    st.subheader("Animation")
    st.info(
        "**Coming soon** – Create and download an animated heatmap "
        "visualising the atmospheric flow"
    )

    st.button(
        "Download Animation",
        disabled=True,
        key="exp_anim",
    )


def render_image() -> None:
    st.subheader("Create Image")
    st.info(
        "**Coming soon** - Download heatmaps as GeoTIFF "
        "or other image file types"
    )
    img_type = st.selectbox(
        label="Select Image Type",
        options=["PNG", "JPEG", "GeoTIFF"],
    )

    if img_type == "GeoTIFF":
        st.button(
            "Download GeoTIFF composite",
            disabled=True,
            key="exp_tiff",
        )
    elif img_type == "PNG":
        st.button(
            "Download PNG composite",
            disabled=True,
            key="exp_png",
        )
    elif img_type == "JPEG":
        st.button(
            "Download JPEG composite",
            disabled=True,
            key="exp_jpeg",
        )
