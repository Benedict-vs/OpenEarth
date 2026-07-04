"""Earth Engine session and guarded call wrapper.

ALL blocking Earth Engine round-trips (``getInfo``, ``getMapId``,
``getThumbURL``, ``computePixels``, …) go through :func:`ee_call` so the
whole process shares one concurrency budget and one retry policy:

- a global semaphore bounds concurrent requests (EE allows ~40/user
  across tiles + compute; we self-limit compute),
- transient failures (quota, timeout — see ``classify_ee_error``) retry
  with exponential backoff and jitter; everything else raises immediately.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import ee
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from openearth.errors import is_transient_ee_error
from openearth.settings import get_settings

_semaphore: threading.BoundedSemaphore | None = None
_semaphore_lock = threading.Lock()


def _get_semaphore() -> threading.BoundedSemaphore:
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.BoundedSemaphore(get_settings().ee_max_concurrency)
        return _semaphore


def initialize(project: str | None = None, *, authenticate: bool = False) -> str:
    """Initialize Earth Engine and return the resolved project ID.

    Args:
        project: GCP project ID; defaults to ``OPENEARTH_EE_PROJECT``.
        authenticate: If True, run ``ee.Authenticate()`` and retry once on
            auth/init failure (interactive — terminal or notebook only).
    """
    resolved = project or get_settings().ee_project
    if not resolved:
        raise ValueError(
            "No Earth Engine project configured. Set OPENEARTH_EE_PROJECT or pass project=."
        )
    try:
        ee.Initialize(project=resolved)
    except ee.EEException:
        if not authenticate:
            raise
        ee.Authenticate()
        ee.Initialize(project=resolved)
    return resolved


@retry(
    retry=retry_if_exception(is_transient_ee_error),
    wait=wait_random_exponential(multiplier=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _call_with_retry[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    return fn(*args, **kwargs)


def ee_call[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking Earth Engine call under the global semaphore with retry.

    Usage: ``ee_call(image.getMapId, params)`` — pass the bound method,
    not its result.
    """
    with _get_semaphore():
        return _call_with_retry(fn, *args, **kwargs)
