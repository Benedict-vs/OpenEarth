"""Animation tab – animated heatmap export."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Render the Animation tab (placeholder)."""
    st.subheader("Animation")
    st.info(
        "**Coming soon** – Create and download an "
        "animated heatmap visualising the "
        "atmospheric flow"
    )

    st.button(
        "Download Animation",
        disabled=True,
        key="exp_anim",
    )
