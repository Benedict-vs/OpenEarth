"""Error classification and display helpers."""

from __future__ import annotations

import urllib.error

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
    "collection.first",
    "no bands",
    "image collection is empty",
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
            "and time window. If your date range "
            "extends close to today, try an earlier "
            "end date \u2014 recent imagery may not be "
            "processed yet.",
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


# ── Image export error handling ──────────────────────────────

_SIZE_PHRASES = (
    "too large",
    "exceeds",
    "payload too large",
    "request entity too large",
    "output is too large",
    "total request size",
)

_NETWORK_PHRASES = (
    "urlopen",
    "connection refused",
    "connection reset",
    "name or service not known",
    "network is unreachable",
)


def _classify_image_error(
    exc: Exception,
) -> tuple[str, str]:
    """Classify an image export error.

    Returns (category, user_message) where category is
    one of: "size", "timeout", "network", "ee", "unknown".
    """
    if isinstance(exc, ee.EEException):
        return classify_ee_error(exc)

    message = str(exc).lower()

    # HTTP errors carry a status code.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 400:
            return (
                "size",
                "The requested image is too large. "
                "Reduce the image dimensions or use "
                "a smaller ROI.",
            )
        return (
            "network",
            f"Image download failed "
            f"(HTTP {exc.code}). "
            "Try again or reduce dimensions.",
        )

    if any(p in message for p in _SIZE_PHRASES):
        return (
            "size",
            "The requested image is too large. "
            "Reduce the image dimensions or use "
            "a smaller ROI.",
        )

    if any(p in message for p in _TIMEOUT_PHRASES):
        return (
            "timeout",
            "Image request timed out. "
            "Try reducing dimensions or ROI size.",
        )

    if (
        isinstance(exc, urllib.error.URLError)
        or any(p in message for p in _NETWORK_PHRASES)
    ):
        return (
            "network",
            "Could not download the image "
            "(network error). Check your "
            "connection and try again.",
        )

    return (
        "unknown",
        "Unexpected error during image export.",
    )


def show_image_error(
    exc: Exception,
    context: str,
) -> None:
    """Display an image export error."""
    category, user_message = _classify_image_error(exc)
    full_message = f"{context} {user_message}"

    if category in ("timeout", "network"):
        st.warning(full_message)
    else:
        st.error(full_message)

    with st.expander("Error details", expanded=False):
        st.exception(exc)
