"""Streamlit display helpers for OpenEarth errors.

Classification logic lives in :mod:`openearth.errors` so
that non-Streamlit callers share the same categories.
This module only handles rendering — it imports the
classifiers and maps them to ``st.error`` / ``st.warning``
/ ``st.info``.
"""

from __future__ import annotations

import urllib.error

import ee
import streamlit as st

from openearth.errors import (
    _TIMEOUT_PHRASES,
    EmptyCollectionError,
    InvalidDateRangeError,
    InvalidROIError,
    classify_ee_error,
)

# ── EE error display ─────────────────────────────────────────


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


# ── Validation + empty-collection errors ─────────────────────


def show_validation_error(
    exc: InvalidROIError | InvalidDateRangeError,
    context: str,
) -> None:
    """Display a library validation error (ROI / dates).

    These are user-input failures — shown as a red
    ``st.error`` with the exception's own message
    (e.g. "End date must be after start date; ...").
    """
    st.error(f"{context} {exc}")
    with st.expander("Error details", expanded=False):
        st.exception(exc)


def show_empty_collection(
    exc: EmptyCollectionError,
    context: str,
) -> None:
    """Display an empty-collection result as info, not error.

    No satellite data is not a failure — it's a benign
    outcome that the user may want to widen the query
    to fix.
    """
    st.info(f"{context} {exc}")


# ── Unexpected / non-EE error handling ───────────────────────


def show_unexpected_error(
    exc: Exception,
    context: str,
) -> None:
    """Display a non-EE exception with a collapsed traceback.

    Use as the fallback branch when an EE-specific handler
    (``show_ee_error``) does not apply. Prefer
    ``show_ee_error`` when the exception is known to be EE.
    """
    st.error(f"{context} An unexpected error occurred.")
    with st.expander("Error details", expanded=False):
        st.exception(exc)
