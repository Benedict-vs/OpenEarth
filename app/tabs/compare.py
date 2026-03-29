"""Compare tab – side-by-side variable / region comparison."""

from __future__ import annotations

import streamlit as st


def render(selected_key: str) -> None:
    """Render the Compare tab (placeholder)."""
    st.subheader("Compare")
    st.info(
        "**Coming soon** – Compare two variables "
        "or two regions side by side over the same "
        "time period."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.selectbox(
            "Region / Variable A",
            options=[
                f"Current ROI – {selected_key}"
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
