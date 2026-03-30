"""Earth Engine error classification and display helpers."""

from __future__ import annotations

import ee
import streamlit as st

# ── EE error handling ─────────────────────────────────────────

_AUTH_PHRASES = (
    "not authorized",
    "access denied",
    "permission denied",
    "authenticate",
    "credentials",
    "forbidden",
    " 401",
    " 403",
)

_QUOTA_PHRASES = (
    "too many concurrent",
    "quota exceeded",
    "rate limit",
    "limit exceeded",
    " 429",
    "user memory limit exceeded",
)

_TIMEOUT_PHRASES = (
    "timed out",
    "timeout",
    "deadline exceeded",
)

_EMPTY_PHRASES = (
    "collection is empty",
    "no images",
    "contains no images",
    "empty collection",
    "0 elements",
    "no valid pixels",
)


def classify_ee_error(
    exc: Exception,
) -> tuple[str, str]:
    """Classify an Earth Engine error by its message.

    Returns (category, user_message) where category is
    one of: "auth", "quota", "timeout", "empty",
    "unknown".
    """
    message = str(exc).lower()

    if any(p in message for p in _AUTH_PHRASES):
        return (
            "auth",
            "Earth Engine authentication or "
            "permissions failed. Check project "
            "access and sign in again.",
        )
    if any(p in message for p in _QUOTA_PHRASES):
        return (
            "quota",
            "Earth Engine quota or concurrency "
            "limit reached. Try a smaller "
            "ROI/date range or retry shortly.",
        )
    if any(p in message for p in _TIMEOUT_PHRASES):
        return (
            "timeout",
            "Earth Engine request timed out. "
            "Try a smaller ROI or date range.",
        )
    if any(p in message for p in _EMPTY_PHRASES):
        return (
            "empty",
            "No satellite observations are "
            "available for this variable, ROI, "
            "and time window.",
        )

    return (
        "unknown",
        "Unexpected Earth Engine error.",
    )


def show_ee_error(
    exc: Exception,
    context: str,
) -> None:
    """Display an EE error with Streamlit severity."""
    if not isinstance(exc, ee.EEException):
        raise exc

    category, user_message = classify_ee_error(exc)
    full_message = f"{context} {user_message}"

    if category == "auth":
        st.error(full_message)
    elif category in ("quota", "timeout"):
        st.warning(full_message)
    elif category == "empty":
        st.info(full_message)
    else:
        st.error(full_message)

    with st.expander("Error details", expanded=False):
        st.exception(exc)
