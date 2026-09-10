"""SQLite DDL and forward-only migrations for the research store.

Immutability of terminal runs is enforced *in the database* with a
``BEFORE UPDATE`` trigger that ``RAISE(ABORT)``s, not in Python. Application
guards are bypassable; a trigger is not. Every connection turns on foreign keys
(SQLite defaults them OFF) and WAL journalling (so the local viewer can read
while a run writes).

Timestamps are UTC ISO-8601 text everywhere. The legacy code stamped completion
in local time (``Experiment.mark_completed``); that is the bug being corrected.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

from vibe.research_pipeline.lifecycle import TERMINAL_STATES

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "configure_connection",
    "migrate",
    "utc_now_iso",
]

# The highest migration version this module knows how to apply.
SCHEMA_VERSION = 1

# Rendered into the trigger below so the database's notion of "terminal" can
# never drift from lifecycle.py.
_TERMINAL_SQL_LIST = ", ".join(
    f"'{state.value}'" for state in sorted(TERMINAL_STATES, key=lambda s: s.value)
)


def utc_now_iso() -> str:
    """Return the current instant as UTC ISO-8601 text."""
    return datetime.now(timezone.utc).isoformat()


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the per-connection PRAGMAs the store relies on.

    ``foreign_keys`` and ``journal_mode`` are connection-scoped, so this must run
    on every connection, not once at creation.
    """
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")


def connect(db_path: str) -> sqlite3.Connection:
    """Open a configured, migrated connection to ``db_path``."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    configure_connection(conn)
    migrate(conn)
    return conn


# --------------------------------------------------------------------------
# Migration 1: initial schema
# --------------------------------------------------------------------------

_MIGRATION_1 = f"""
CREATE TABLE run_display_ids (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL UNIQUE
);

CREATE TABLE runs (
    run_id               TEXT PRIMARY KEY,
    display_seq          INTEGER NOT NULL UNIQUE,
    display_alias        TEXT NOT NULL UNIQUE,
    fingerprint          TEXT NOT NULL UNIQUE,
    fingerprint_version  INTEGER NOT NULL,
    strategy_id          TEXT NOT NULL,
    methodology_version  TEXT NOT NULL,
    state                TEXT NOT NULL,
    notes                TEXT,
    code_commit          TEXT,
    code_dirty           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX idx_runs_state ON runs(state);
CREATE INDEX idx_runs_strategy ON runs(strategy_id);

CREATE TABLE run_metrics (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL REFERENCES runs(run_id),
    metric_key           TEXT NOT NULL,
    metric_value         REAL NOT NULL,
    calculation_version  INTEGER NOT NULL,
    recorded_at          TEXT NOT NULL
);
CREATE INDEX idx_run_metrics_run ON run_metrics(run_id);

CREATE TABLE run_evidence (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL REFERENCES runs(run_id),
    trade_count          INTEGER NOT NULL,
    trade_ledger_sha256  TEXT NOT NULL,
    equity_curve_sha256  TEXT NOT NULL,
    equity_points        INTEGER NOT NULL,
    retention_profile    TEXT NOT NULL,
    payload_json         TEXT NOT NULL,
    recorded_at          TEXT NOT NULL
);
CREATE INDEX idx_run_evidence_run ON run_evidence(run_id);

CREATE TABLE run_findings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    code         TEXT NOT NULL,
    category     TEXT NOT NULL,
    severity     TEXT NOT NULL,
    message      TEXT NOT NULL,
    metric_key   TEXT,
    payload_json TEXT NOT NULL,
    recorded_at  TEXT NOT NULL
);
CREATE INDEX idx_run_findings_run ON run_findings(run_id);

CREATE TABLE experiment_state_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    reason      TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX idx_transitions_run ON experiment_state_transitions(run_id);

CREATE TABLE publication_outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    payload_json TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    enqueued_at  TEXT NOT NULL
);

-- Terminal runs are append-only. Any UPDATE to a row already in a terminal
-- state is rejected by the engine itself, so no Python path can rewrite a
-- completed or failed result.
CREATE TRIGGER trg_runs_terminal_immutable
BEFORE UPDATE ON runs
FOR EACH ROW
WHEN OLD.state IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'run is in a terminal state and is immutable');
END;
"""

# Forward-only: (version, ddl). Append new migrations; never edit an applied one.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def migrate(conn: sqlite3.Connection, *, now: Callable[[], str] = utc_now_iso) -> int:
    """Apply pending migrations in order. Idempotent; returns the resulting version.

    Running this twice is a no-op: already-recorded versions are skipped.
    """
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    for version, ddl in sorted(_MIGRATIONS):
        if version in applied:
            continue
        with conn:  # one transaction per migration
            conn.executescript(ddl)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, now()),
            )
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])
