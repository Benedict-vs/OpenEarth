"""Persisted table models (SQLModel).

The table *schema* is owned by the DDL migrations in ``db.py`` — these
models are the typed read/write view over those tables and MUST mirror the
DDL. We never call ``SQLModel.metadata.create_all``; the migration list is
the single source of truth for the shape on disk.

Timestamps are stored as ISO-8601 UTC strings (SQLite ``TEXT``): trivially
JSON-serialisable, human-readable in the DB, and ordered lexicographically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlmodel import Field, SQLModel

# The lifecycle of a job. Terminal states never transition again.
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (second precision is enough)."""
    return datetime.now(tz=UTC).isoformat()


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    kind: str
    status: str
    params_json: str
    result_json: str | None = None
    error: str | None = None
    progress_done: int = 0
    progress_total: int = 0
    message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
