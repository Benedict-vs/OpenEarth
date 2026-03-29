"""Image export tab – PNG, JPEG, and GeoTIFF generation."""

from __future__ import annotations

import urllib.request

import ee
import streamlit as st

from app.analysis import (
    cached_thumb_url,
    cached_download_url,
    show_ee_error,
)


def render() -> None:
    """Render the Image export tab."""
    st.subheader("Create Image")

    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
        return

    hp = st.session_state["heatmap_params"]
    data_key = hp["data_key"]
    source = hp.get("source", "s5p")

    st.caption(
        f"Export the mean composite of **{data_key}** "
        f"over the current ROI "
        f"({hp['start_date']} to {hp['end_date']})."
    )

    img_type = st.selectbox(
        label="Select Image Type",
        options=["PNG", "JPEG", "GeoTIFF"],
    )

    if img_type in ("PNG", "JPEG"):
        dimensions = st.slider(
            "Image size (longest edge, px)",
            min_value=256,
            max_value=4096,
            value=1024,
            step=256,
            key="img_dimensions",
        )

        fmt_map = {"PNG": "png", "JPEG": "jpg"}

        fmt = fmt_map[img_type]
        mime = (
            "image/png" if fmt == "png"
            else "image/jpeg"
        )
        ext = fmt_map[img_type]
        if ext == "jpg":
            ext = "jpeg"

        if st.button(
            f"Generate {img_type}",
            key=f"gen_{fmt}",
        ):
            try:
                with st.spinner(
                    "Generating image..."
                ):
                    thumb_url = cached_thumb_url(
                        data_key,
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source=source,
                        dimensions=dimensions,
                        img_format=fmt,
                    )
                    with urllib.request.urlopen(
                        thumb_url, timeout=60,
                    ) as resp:
                        img_bytes = resp.read()

                st.image(
                    img_bytes,
                    caption=(
                        f"{data_key} mean composite"
                    ),
                )
                fname = (
                    f"openearth_{data_key}"
                    f"_{hp['start_date']}"
                    f"_{hp['end_date']}"
                    f".{ext}"
                )
                st.download_button(
                    label=f"Download {img_type}",
                    data=img_bytes,
                    file_name=fname,
                    mime=mime,
                    key=f"dl_{fmt}",
                )
            except ee.EEException as exc:
                show_ee_error(
                    exc,
                    "Could not generate image.",
                )

    elif img_type == "GeoTIFF":
        st.info(
            "GeoTIFF exports the raw raster data "
            "(single band) for use in GIS software."
        )
        if st.button(
            "Generate GeoTIFF link",
            key="gen_tiff",
        ):
            try:
                with st.spinner(
                    "Preparing GeoTIFF..."
                ):
                    dl_url = cached_download_url(
                        data_key,
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source=source,
                    )
                st.markdown(
                    f"[Download GeoTIFF]({dl_url})"
                )
            except ee.EEException as exc:
                show_ee_error(
                    exc,
                    "Could not generate GeoTIFF.",
                )
