"""SQLite engine and ``PRAGMA user_version`` migrations.

Deliberately hand-rolled, not Alembic (plan.md): migrations are a list of
DDL script batches applied in order, and the schema version is the SQLite
``user_version`` pragma — the index of the last batch reached. Adding a
table in a later phase means appending a batch, never editing an old one.

WAL mode lets the single event-loop writer coexist with concurrent readers
(the ``/config`` cache stats query, future read paths) without blocking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
