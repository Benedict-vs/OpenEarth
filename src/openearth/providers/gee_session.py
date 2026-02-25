"""Earth Engine session helpers."""

from __future__ import annotations
import ee


def initialize_ee(project_id: str,
                  authenticate: bool = True
                  ) -> str:
    """Initialize Earth Engine and return the resolved project ID.

    Args:
        project_id: Explicit GCP project ID for Earth Engine.
        authenticate: If True, run ee.Authenticate() and retry on auth/init \
        failure.
    """

    try:
        ee.Initialize(project=project_id)
    except Exception:
        if not authenticate:
            raise
        ee.Authenticate()
        ee.Initialize(project=project_id)

    return project_id
