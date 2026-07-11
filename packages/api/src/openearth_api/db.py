"""SQLite engine and ``PRAGMA user_version`` migrations.

Deliberately hand-rolled, not Alembic (plan.md): migrations are a list of
DDL script batches applied in order, and the schema version is the SQLite
``user_version`` pragma — the index of the last batch reached. Adding a
table in a later phase means appending a batch, never editing an old one.

WAL mode lets the single event-loop writer coexist with concurrent readers
(the ``/config`` cache stats query, future read paths) without blocking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlmodel import create_engine

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine

# Each entry is one migration = a batch of DDL statements applied atomically.
# The list index + 1 becomes ``PRAGMA user_version``. NEVER edit a shipped
# entry; append a new one (Phase 3 adds sites/detections as migration 3+).
_MIGRATIONS: list[tuple[str, ...]] = [
    # 1 — jobs (Phase 2, stage 1)
    (
        """
        CREATE TABLE jobs (
            id             TEXT PRIMARY KEY,
            kind           TEXT NOT NULL,
            status         TEXT NOT NULL,
            params_json    TEXT NOT NULL,
            result_json    TEXT,
            error          TEXT,
            progress_done  INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            message        TEXT,
            created_at     TEXT NOT NULL,
            started_at     TEXT,
            finished_at    TEXT
        )
        """,
        "CREATE INDEX ix_jobs_created_at ON jobs (created_at)",
    ),
    # 2 — saved AOIs + workspaces (Phase 2, stage 8). Both name-unique so a
    # duplicate save surfaces as a 409, not a silent second row. Workspace
    # ``state_json`` is a versioned blob (see WorkspaceState) the API owns.
    (
        """
        CREATE TABLE aois (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            roi_json   TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE workspaces (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
    # 3 — methane sites, detections, reference events (Phase 3). Detections are
    # primary reviewable data: headline numbers are real columns so the feed
    # filters/sorts in SQL; everything else stays in the JSON blobs. Analyze
    # runners write their own detection row from the worker thread (WAL +
    # busy_timeout), so this schema is touched off the event loop too.
    (
        """
        CREATE TABLE sites (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            west REAL NOT NULL, south REAL NOT NULL, east REAL NOT NULL, north REAL NOT NULL,
            date_hint_start TEXT, date_hint_end TEXT,
            notes           TEXT,
            created_at      TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE detections (
            id              TEXT PRIMARY KEY,
            site_id         INTEGER REFERENCES sites(id) ON DELETE SET NULL,
            source          TEXT NOT NULL,
            status          TEXT NOT NULL,
            method          TEXT NOT NULL,
            scene_id        TEXT NOT NULL,
            scene_time_utc  TEXT NOT NULL,
            ref_scene_id    TEXT,
            q_kg_h REAL, q_sigma_kg_h REAL, xch4_max_ppb REAL, ime_kg REAL,
            u10_ms REAL, wind_from_deg REAL,
            params_json     TEXT NOT NULL,
            result_json     TEXT NOT NULL,
            mask_geojson    TEXT,
            array_path      TEXT NOT NULL,
            notes           TEXT,
            validation_json TEXT,
            created_at      TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX ix_detections_site   ON detections (site_id, created_at)",
        "CREATE INDEX ix_detections_status ON detections (status)",
        """
        CREATE TABLE reference_events (
            id             INTEGER PRIMARY KEY,
            source         TEXT NOT NULL,
            event_time_utc TEXT NOT NULL,
            lat REAL NOT NULL, lon REAL NOT NULL,
            q_kg_h REAL, q_sigma_kg_h REAL,
            raw_json       TEXT NOT NULL,
            imported_at    TEXT NOT NULL
        )
        """,
    ),
    # 4 — timelapse renders (Phase 4). A render is primary reviewable data (like
    # detections): the movie + frame PNGs + manifest live on disk, and this row
    # is the gallery index. The timelapse runner inserts and updates its own row
    # from the worker thread (WAL + busy_timeout, detections precedent); the
    # manager-owned ``jobs`` table stays event-loop-only.
    (
        """
        CREATE TABLE renders (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            dataset     TEXT NOT NULL,
            product     TEXT NOT NULL,
            params_json TEXT NOT NULL,
            roi_json    TEXT NOT NULL,
            status      TEXT NOT NULL,          -- running | succeeded | failed | cancelled
            frame_count INTEGER,
            fps         INTEGER NOT NULL,
            format      TEXT NOT NULL,          -- mp4 | gif | webm
            movie_bytes INTEGER,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX ix_renders_created_at ON renders (created_at)",
    ),
    # 5 — EMIT plume cross-match evidence (Phase 6). EMIT complexes are another
    # instrument's product attached to an existing detection, not a detection row
    # of ours (no detections.source touched). The match result — matched plumes,
    # query provenance, checked-at — is a JSON blob; rows predating this migration
    # keep emit_json IS NULL, meaning "never checked" (not "no match").
    ("ALTER TABLE detections ADD COLUMN emit_json TEXT",),
]


def create_db_engine(db_path: Path) -> Engine:
    """Open (creating if needed) the SQLite DB in WAL mode.

    ``check_same_thread=False``: all writes happen on the event-loop thread,
    but the connection pool may hand a pooled connection to a different
    thread context across await points, so the SQLite thread guard is
    relaxed. Cross-thread *write* discipline is enforced by design, not by
    this flag (worker threads never touch the DB — see ``jobs.py``).
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Analyze runners insert their detection row from a worker thread; a short
    # busy_timeout (set per raw connection, since it does not persist like WAL)
    # lets that writer wait out the event-loop writer instead of failing with
    # "database is locked".
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.commit()
    return engine


def migrate(engine: Engine) -> int:
    """Apply outstanding migrations in order; return the version reached."""
    with engine.begin() as conn:
        current = int(conn.exec_driver_sql("PRAGMA user_version").scalar_one())
        for index in range(current, len(_MIGRATIONS)):
            for statement in _MIGRATIONS[index]:
                conn.exec_driver_sql(statement)
            # user_version takes a literal, not a bound parameter; index is
            # an int we control, so interpolation is safe here.
            conn.exec_driver_sql(f"PRAGMA user_version = {index + 1}")
    return len(_MIGRATIONS)
