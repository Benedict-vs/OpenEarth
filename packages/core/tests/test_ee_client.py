"""ee_call retry/semaphore behavior with fake callables (no Earth Engine)."""

from __future__ import annotations

import pytest

from openearth.ee.client import ee_call


def test_passes_through_result_and_arguments() -> None:
    assert ee_call(lambda a, b=0: a + b, 2, b=3) == 5


def test_retries_transient_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Too many concurrent aggregations. rate limit")
        return "ok"

    assert ee_call(flaky) == "ok"
    assert calls["n"] == 3


def test_does_not_retry_permanent_errors() -> None:
    calls = {"n": 0}

    def denied() -> None:
        calls["n"] += 1
        raise RuntimeError("Earth Engine: permission denied.")

    with pytest.raises(RuntimeError, match="permission denied"):
        ee_call(denied)
    assert calls["n"] == 1


def test_gives_up_after_max_attempts() -> None:
    calls = {"n": 0}

    def always_throttled() -> None:
        calls["n"] += 1
        raise RuntimeError("quota exceeded")

    with pytest.raises(RuntimeError, match="quota exceeded"):
        ee_call(always_throttled)
    assert calls["n"] == 5
