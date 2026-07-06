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


class Aoi(SQLModel, table=True):
    __tablename__ = "aois"

    # ``id`` is the SQLite rowid alias (autoincrement); None until inserted.
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    roi_json: str
    created_at: str


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    state_json: str
    created_at: str
    updated_at: str


# ── Phase 3 (methane) ────────────────────────────────────────

# A detection's review lifecycle. 'candidate' until a human accepts/rejects it.
DetectionStatus = Literal["candidate", "accepted", "rejected"]


class Site(SQLModel, table=True):
    __tablename__ = "sites"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    west: float
    south: float
    east: float
    north: float
    date_hint_start: str | None = None
    date_hint_end: str | None = None
    notes: str | None = None
    created_at: str


class Detection(SQLModel, table=True):
    __tablename__ = "detections"

    id: str = Field(primary_key=True)  # uuid4 hex
    site_id: int | None = None
    source: str  # 'physics' now; 'ml'|'emit' later
    status: str  # DetectionStatus
    method: str  # 'mbmp'|'mbsp'
    scene_id: str
    scene_time_utc: str
    ref_scene_id: str | None = None
    q_kg_h: float | None = None
    q_sigma_kg_h: float | None = None
    xch4_max_ppb: float | None = None
    ime_kg: float | None = None
    u10_ms: float | None = None
    wind_from_deg: float | None = None
    params_json: str
    result_json: str
    mask_geojson: str | None = None
    array_path: str
    notes: str | None = None
    validation_json: str | None = None
    created_at: str
    updated_at: str


class ReferenceEvent(SQLModel, table=True):
    __tablename__ = "reference_events"

    id: int | None = Field(default=None, primary_key=True)
    source: str  # 'imeo'|'sron'|'manual'
    event_time_utc: str
    lat: float
    lon: float
    q_kg_h: float | None = None
    q_sigma_kg_h: float | None = None
    raw_json: str
    imported_at: str
