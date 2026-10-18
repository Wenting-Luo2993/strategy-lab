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
import time
from datetime import datetime, timezone
from typing import Callable

from vibe.research_pipeline.lifecycle import TERMINAL_STATES

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "begin_immediate",
    "configure_connection",
    "migrate",
    "utc_now_iso",
]

# The highest migration version this module knows how to apply.
SCHEMA_VERSION = 13

_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_BASE_SECONDS = 0.01
_LOCK_BUSY_TIMEOUT_MS = 50

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
    previous_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.execute(f"PRAGMA busy_timeout = {_LOCK_BUSY_TIMEOUT_MS}")
    try:
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                if attempt == _LOCK_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_LOCK_RETRY_BASE_SECONDS * (attempt + 1))
    finally:
        conn.execute(f"PRAGMA busy_timeout = {previous_timeout}")
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

_MIGRATION_2 = """
ALTER TABLE runs ADD COLUMN lease_owner TEXT;
ALTER TABLE runs ADD COLUMN lease_token TEXT;
ALTER TABLE runs ADD COLUMN lease_expires_at TEXT;
CREATE INDEX idx_runs_lease_expiry ON runs(state, lease_expires_at);

CREATE TABLE validation_reports (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                     TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    profile_name               TEXT NOT NULL,
    scope                      TEXT NOT NULL,
    target_state               TEXT NOT NULL,
    registry_hash_at_execution TEXT NOT NULL,
    current_registry_hash      TEXT NOT NULL,
    leakage_passed             INTEGER NOT NULL,
    payload_json               TEXT NOT NULL,
    recorded_at                TEXT NOT NULL
);
CREATE INDEX idx_validation_reports_run ON validation_reports(run_id);

CREATE TRIGGER trg_runs_validation_required
BEFORE UPDATE OF state ON runs
FOR EACH ROW
WHEN OLD.state = 'validating'
 AND NEW.state IN ('completed', 'review_required', 'validation_failed', 'inconclusive')
 AND NOT EXISTS (
     SELECT 1 FROM validation_reports
     WHERE run_id = OLD.run_id AND target_state = NEW.state
 )
BEGIN
    SELECT RAISE(ABORT, 'validation report required before terminal outcome');
END;
"""

# --------------------------------------------------------------------------
# Migration 3: legacy registry compatibility and import audit
# --------------------------------------------------------------------------

_MIGRATION_3 = f"""
-- The YAML registry remains the public compatibility surface until P11.  This
-- table stores its complete canonical payload, rather than projecting only the
-- fields understood by the new run model and silently losing legacy data.
CREATE TABLE registry_records (
    record_type          TEXT NOT NULL,
    legacy_id            TEXT NOT NULL,
    payload_json         TEXT NOT NULL,
    canonical_sha256     TEXT NOT NULL,
    methodology_version  TEXT NOT NULL,
    status               TEXT,
    git_dirty            INTEGER NOT NULL DEFAULT 0,
    source_path          TEXT,
    imported_at          TEXT NOT NULL,
    PRIMARY KEY (record_type, legacy_id)
);
CREATE INDEX idx_registry_records_status
    ON registry_records(record_type, status);

CREATE TABLE registry_relationships (
    record_type  TEXT NOT NULL,
    legacy_id    TEXT NOT NULL,
    relation     TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    ordinal      INTEGER NOT NULL DEFAULT 0,
    resolved     INTEGER NOT NULL,
    PRIMARY KEY (record_type, legacy_id, relation, ordinal),
    FOREIGN KEY (record_type, legacy_id)
        REFERENCES registry_records(record_type, legacy_id)
);
CREATE INDEX idx_registry_relationship_target
    ON registry_relationships(target_type, target_id);

-- Preserve the legacy spelling while also exposing a normalized local path or
-- external URI.  This handles absolute paths without laundering them into a
-- repository-relative value that points at a different file.
CREATE TABLE registry_artifact_locations (
    record_type       TEXT NOT NULL,
    legacy_id         TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    original_location TEXT NOT NULL,
    repository_path   TEXT,
    external_uri      TEXT,
    PRIMARY KEY (record_type, legacy_id, ordinal),
    FOREIGN KEY (record_type, legacy_id)
        REFERENCES registry_records(record_type, legacy_id),
    CHECK ((repository_path IS NULL) != (external_uri IS NULL))
);

-- Historical absolute values are evidence, not usable baselines.  Keep the
-- original number for audit, but leave effective_value NULL until a clean run
-- re-baselines it under a controlled methodology.
CREATE TABLE legacy_metric_values (
    record_type     TEXT NOT NULL DEFAULT 'experiment'
                    CHECK (record_type = 'experiment'),
    experiment_id   TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    original_value  REAL NOT NULL,
    effective_value REAL,
    baseline_status TEXT NOT NULL,
    PRIMARY KEY (experiment_id, metric_key),
    FOREIGN KEY (record_type, experiment_id)
        REFERENCES registry_records(record_type, legacy_id)
);

CREATE TRIGGER trg_runs_terminal_delete_immutable
BEFORE DELETE ON runs
FOR EACH ROW
WHEN OLD.state IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'run is in a terminal state and is immutable');
END;

CREATE TRIGGER trg_registry_terminal_update_immutable
BEFORE UPDATE ON registry_records
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND OLD.status IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'legacy experiment is in a terminal state and is immutable');
END;

CREATE TRIGGER trg_registry_terminal_delete_immutable
BEFORE DELETE ON registry_records
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND OLD.status IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'legacy experiment is in a terminal state and is immutable');
END;
"""

# --------------------------------------------------------------------------
# Migration 4: P11 registry authority and promotion identity
# --------------------------------------------------------------------------

_MIGRATION_4 = f"""
-- Runtime registry IDs are allocated transactionally in SQLite.  The legacy
-- HYP/EXP/NOTE/RJ/ART spelling remains an API/display compatibility contract,
-- but directory scans are no longer an authority.
CREATE TABLE registry_id_counters (
    prefix      TEXT PRIMARY KEY,
    last_value INTEGER NOT NULL CHECK (last_value >= 0)
);

ALTER TABLE registry_records
    ADD COLUMN record_origin TEXT NOT NULL DEFAULT 'legacy_yaml';
ALTER TABLE registry_records
    ADD COLUMN execution_model_version INTEGER;
ALTER TABLE registry_records
    ADD COLUMN execution_identity_json TEXT;

CREATE INDEX idx_registry_records_origin
    ON registry_records(record_type, record_origin);

CREATE TABLE registry_import_audit (
    singleton             INTEGER PRIMARY KEY CHECK (singleton = 1),
    status                TEXT NOT NULL,
    canonical_tree_sha256 TEXT,
    record_count          INTEGER NOT NULL,
    completed_at          TEXT NOT NULL
);

-- A run lineage groups re-runs and the one final-holdout decision they share.
-- When omitted, the run id is used as its own lineage identity.
ALTER TABLE runs ADD COLUMN lineage_id TEXT;
CREATE INDEX idx_runs_lineage ON runs(lineage_id);

CREATE TRIGGER trg_run_metrics_terminal_insert
BEFORE INSERT ON run_metrics
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = NEW.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_metrics_terminal_update
BEFORE UPDATE ON run_metrics
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_metrics_terminal_delete
BEFORE DELETE ON run_metrics
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_evidence_terminal_insert
BEFORE INSERT ON run_evidence
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = NEW.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_evidence_terminal_update
BEFORE UPDATE ON run_evidence
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_evidence_terminal_delete
BEFORE DELETE ON run_evidence
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_findings_terminal_insert
BEFORE INSERT ON run_findings
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = NEW.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_findings_terminal_update
BEFORE UPDATE ON run_findings
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_run_findings_terminal_delete
BEFORE DELETE ON run_findings
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal run attachments are immutable');
END;

CREATE TRIGGER trg_registry_relationship_terminal_insert
BEFORE INSERT ON registry_relationships
FOR EACH ROW
WHEN NEW.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = NEW.record_type AND legacy_id = NEW.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment relationships are immutable');
END;

CREATE TRIGGER trg_registry_relationship_terminal_update
BEFORE UPDATE ON registry_relationships
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = OLD.record_type AND legacy_id = OLD.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
 AND (
     NEW.record_type != OLD.record_type
     OR NEW.legacy_id != OLD.legacy_id
     OR NEW.relation != OLD.relation
     OR NEW.target_type != OLD.target_type
     OR NEW.target_id != OLD.target_id
     OR NEW.ordinal != OLD.ordinal
 )
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment relationships are immutable');
END;

CREATE TRIGGER trg_registry_relationship_terminal_delete
BEFORE DELETE ON registry_relationships
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = OLD.record_type AND legacy_id = OLD.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment relationships are immutable');
END;

CREATE TRIGGER trg_registry_artifact_terminal_insert
BEFORE INSERT ON registry_artifact_locations
FOR EACH ROW
WHEN NEW.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = NEW.record_type AND legacy_id = NEW.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment artifact locations are immutable');
END;

CREATE TRIGGER trg_registry_artifact_terminal_update
BEFORE UPDATE ON registry_artifact_locations
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = OLD.record_type AND legacy_id = OLD.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment artifact locations are immutable');
END;

CREATE TRIGGER trg_registry_artifact_terminal_delete
BEFORE DELETE ON registry_artifact_locations
FOR EACH ROW
WHEN OLD.record_type = 'experiment'
 AND (SELECT status FROM registry_records
      WHERE record_type = OLD.record_type AND legacy_id = OLD.legacy_id)
     IN ('completed', 'failed', 'superseded', 'archived')
BEGIN
    SELECT RAISE(ABORT, 'terminal experiment artifact locations are immutable');
END;
"""

# --------------------------------------------------------------------------
# Migration 5: immutable terminal validation evidence
# --------------------------------------------------------------------------

_MIGRATION_5 = f"""
CREATE TRIGGER trg_validation_reports_terminal_insert
BEFORE INSERT ON validation_reports
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = NEW.run_id) != 'validating'
BEGIN
    SELECT RAISE(ABORT, 'validation report requires a validating run');
END;

CREATE TRIGGER trg_validation_reports_terminal_update
BEFORE UPDATE ON validation_reports
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal validation evidence is immutable');
END;

CREATE TRIGGER trg_validation_reports_terminal_delete
BEFORE DELETE ON validation_reports
FOR EACH ROW
WHEN (SELECT state FROM runs WHERE run_id = OLD.run_id) IN ({_TERMINAL_SQL_LIST})
BEGIN
    SELECT RAISE(ABORT, 'terminal validation evidence is immutable');
END;
"""

# --------------------------------------------------------------------------
# Migration 6: P12 durable publication and reconciliation
# --------------------------------------------------------------------------

_MIGRATION_6 = """
ALTER TABLE runs ADD COLUMN identity_json TEXT;
ALTER TABLE runs ADD COLUMN origin_device_id TEXT;

ALTER TABLE publication_outbox RENAME TO publication_outbox_v1;

CREATE TABLE publication_outbox (
    sequence               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               TEXT NOT NULL UNIQUE,
    idempotency_key        TEXT NOT NULL UNIQUE,
    aggregate_type         TEXT NOT NULL,
    aggregate_id           TEXT NOT NULL,
    parent_idempotency_key TEXT,
    schema_version         INTEGER NOT NULL,
    publication_version    INTEGER NOT NULL DEFAULT 1,
    payload_json           TEXT NOT NULL,
    payload_sha256         TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN (
                               'pending', 'publishing', 'failed',
                               'published', 'dead_letter'
                           )),
    attempts               INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error             TEXT,
    next_attempt_at        TEXT,
    claimed_by             TEXT,
    claimed_at             TEXT,
    lease_expires_at       TEXT,
    enqueued_at            TEXT NOT NULL,
    published_at           TEXT,
    remote_checksum        TEXT,
    FOREIGN KEY (parent_idempotency_key)
        REFERENCES publication_outbox(idempotency_key)
);
CREATE INDEX idx_publication_outbox_ready
    ON publication_outbox(status, sequence, next_attempt_at);
CREATE INDEX idx_publication_outbox_lease
    ON publication_outbox(status, lease_expires_at);

INSERT INTO publication_outbox (
    event_id, idempotency_key, aggregate_type, aggregate_id,
    schema_version, payload_json, payload_sha256, status, enqueued_at
)
SELECT
    'legacy-' || run_id,
    'legacy-untrusted:' || run_id,
    'run_summary',
    run_id,
    1,
    payload_json,
    '',
    'dead_letter',
    enqueued_at
FROM publication_outbox_v1;
DROP TABLE publication_outbox_v1;

CREATE TABLE publication_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES publication_outbox(event_id),
    attempt_number  INTEGER NOT NULL,
    outcome         TEXT NOT NULL,
    error           TEXT,
    attempted_at    TEXT NOT NULL
);
CREATE INDEX idx_publication_attempts_event
    ON publication_attempts(event_id, attempt_number);

CREATE TABLE publication_dead_letters (
    event_id         TEXT PRIMARY KEY REFERENCES publication_outbox(event_id),
    idempotency_key  TEXT NOT NULL,
    payload_sha256   TEXT NOT NULL,
    attempts         INTEGER NOT NULL,
    last_error       TEXT NOT NULL,
    dead_lettered_at TEXT NOT NULL,
    replay_count     INTEGER NOT NULL DEFAULT 0
);

INSERT INTO publication_dead_letters (
    event_id, idempotency_key, payload_sha256, attempts,
    last_error, dead_lettered_at
)
SELECT
    event_id, idempotency_key, payload_sha256, attempts,
    'legacy arbitrary payload quarantined during P12 migration', enqueued_at
FROM publication_outbox
WHERE idempotency_key LIKE 'legacy-untrusted:%';

CREATE TABLE publication_acknowledgements (
    idempotency_key     TEXT PRIMARY KEY,
    remote_checksum    TEXT NOT NULL,
    publication_version INTEGER NOT NULL,
    acknowledged_at    TEXT NOT NULL,
    last_reconciled_at TEXT NOT NULL
);

CREATE TRIGGER trg_publication_payload_immutable_after_delivery
BEFORE UPDATE ON publication_outbox
FOR EACH ROW
WHEN OLD.status IN ('published', 'dead_letter')
 AND (
     NEW.event_id != OLD.event_id
     OR NEW.idempotency_key != OLD.idempotency_key
     OR NEW.aggregate_type != OLD.aggregate_type
     OR NEW.aggregate_id != OLD.aggregate_id
     OR COALESCE(NEW.parent_idempotency_key, '') !=
        COALESCE(OLD.parent_idempotency_key, '')
     OR NEW.schema_version != OLD.schema_version
     OR NEW.payload_json != OLD.payload_json
     OR NEW.payload_sha256 != OLD.payload_sha256
 )
BEGIN
    SELECT RAISE(ABORT, 'delivered publication payload is immutable');
END;

CREATE TRIGGER trg_publication_dead_letters_immutable_update
BEFORE UPDATE ON publication_dead_letters
FOR EACH ROW
WHEN NEW.event_id != OLD.event_id
 OR NEW.idempotency_key != OLD.idempotency_key
 OR NEW.payload_sha256 != OLD.payload_sha256
 OR NEW.attempts != OLD.attempts
 OR NEW.last_error != OLD.last_error
 OR NEW.dead_lettered_at != OLD.dead_lettered_at
BEGIN
    SELECT RAISE(ABORT, 'dead-letter evidence is immutable');
END;

CREATE TRIGGER trg_publication_dead_letters_immutable_delete
BEFORE DELETE ON publication_dead_letters
BEGIN
    SELECT RAISE(ABORT, 'dead-letter evidence is append-only');
END;
"""

# --------------------------------------------------------------------------
# Migration 7: publication claim ownership fencing
# --------------------------------------------------------------------------

_MIGRATION_7 = """
ALTER TABLE publication_outbox ADD COLUMN claim_token TEXT;
CREATE UNIQUE INDEX idx_publication_outbox_claim_token
    ON publication_outbox(claim_token)
    WHERE claim_token IS NOT NULL;
"""

# --------------------------------------------------------------------------
# Migration 8: explicit artifact staging and publication
# --------------------------------------------------------------------------

_MIGRATION_8 = """
CREATE TABLE artifact_publication_outbox (
    sequence               INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id            TEXT NOT NULL UNIQUE,
    owning_publication_key TEXT NOT NULL,
    object_key             TEXT NOT NULL,
    staging_path           TEXT NOT NULL,
    manifest_json          TEXT NOT NULL,
    manifest_sha256        TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    content_sha256         TEXT NOT NULL CHECK (length(content_sha256) = 64),
    size_bytes             INTEGER NOT NULL CHECK (size_bytes > 0),
    media_type             TEXT NOT NULL,
    preview_policy         TEXT NOT NULL
                           CHECK (preview_policy IN (
                               'inline', 'download', 'metadata_only'
                           )),
    status                 TEXT NOT NULL DEFAULT 'staged'
                           CHECK (status IN (
                               'staged', 'publishing', 'object_stored',
                               'published', 'failed', 'dead_letter'
                           )),
    attempts               INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error             TEXT,
    next_attempt_at        TEXT,
    claimed_by             TEXT,
    claimed_at             TEXT,
    lease_expires_at       TEXT,
    claim_token            TEXT,
    remote_object_sha256   TEXT,
    enqueued_at            TEXT NOT NULL,
    updated_at             TEXT,
    published_at           TEXT
);
CREATE INDEX idx_artifact_object_identity
    ON artifact_publication_outbox(object_key);
CREATE INDEX idx_artifact_publication_ready
    ON artifact_publication_outbox(status, sequence, next_attempt_at);
CREATE INDEX idx_artifact_publication_owner
    ON artifact_publication_outbox(owning_publication_key, sequence);
CREATE UNIQUE INDEX idx_artifact_publication_claim
    ON artifact_publication_outbox(claim_token)
    WHERE claim_token IS NOT NULL;

CREATE TRIGGER trg_artifact_identity_immutable
BEFORE UPDATE ON artifact_publication_outbox
FOR EACH ROW
WHEN NEW.artifact_id != OLD.artifact_id
 OR NEW.owning_publication_key != OLD.owning_publication_key
 OR NEW.object_key != OLD.object_key
 OR NEW.staging_path != OLD.staging_path
 OR NEW.manifest_json != OLD.manifest_json
 OR NEW.manifest_sha256 != OLD.manifest_sha256
 OR NEW.content_sha256 != OLD.content_sha256
 OR NEW.size_bytes != OLD.size_bytes
 OR NEW.media_type != OLD.media_type
 OR NEW.preview_policy != OLD.preview_policy
BEGIN
    SELECT RAISE(ABORT, 'artifact publication identity is immutable');
END;

CREATE TRIGGER trg_artifact_delivered_delete_immutable
BEFORE DELETE ON artifact_publication_outbox
FOR EACH ROW
WHEN OLD.status IN ('published', 'dead_letter')
BEGIN
    SELECT RAISE(ABORT, 'delivered artifact publication evidence is immutable');
END;
"""

# P15b. Local-only row-level retention for the ``diagnostic`` and ``full``
# profiles. ``RetentionProfile`` has promised these since P0, but nothing could
# honour the promise because there was nowhere to put the rows: ``run_evidence``
# keeps ``equity_points`` as a count, not a series. Without them the research
# store can state that a run produced 214 trades and prove the ledger digest,
# yet never show a single trade, which is what left the dashboards unable to
# answer "how did this run actually behave".
#
# These tables are local-only by construction, not by convention. The
# publication allowlist in ``publishing.py`` recursively refuses trades, fills,
# ledgers, equity series, and bars, so no payload builder can reach them.
#
# ``sequence`` is explicit and unique per run because both digests are
# order-sensitive: ``trade_ledger_digest`` hashes rows in execution order, so a
# ledger that round-trips through storage in a different order is a different
# ledger. Relying on rowid or on an unordered SELECT would let a reordering
# defect survive the checksum that exists to catch it.
_MIGRATION_9 = """
CREATE TABLE run_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    sequence     INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    quantity     REAL NOT NULL,
    entry_price  REAL NOT NULL,
    exit_price   REAL,
    entry_time   TEXT NOT NULL,
    exit_time    TEXT,
    pnl          REAL,
    commission   REAL,
    initial_risk REAL,
    exit_reason  TEXT,
    recorded_at  TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);
CREATE INDEX idx_run_trades_run ON run_trades(run_id, sequence);

CREATE TABLE run_equity_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    sequence    INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    equity      REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);
CREATE INDEX idx_run_equity_points_run ON run_equity_points(run_id, sequence);
"""

# ``segment_role`` was previously accepted by the recording API but only ever
# folded into ``split_manifest_hash``. That made fingerprints distinct but left
# the store unable to answer the question a research viewer most needs to ask:
# "which of these runs are out-of-sample?" Reviewers had to parse free-text
# notes, which is exactly how an in-sample number gets read as evidence.
#
# Existing rows are backfilled to NULL rather than guessed. NULL means
# "recorded before the role was persisted", which is honest; defaulting them to
# 'test' would manufacture out-of-sample evidence that was never established,
# and defaulting them to 'train' would silently rewrite history the other way.
_MIGRATION_10 = """
ALTER TABLE runs ADD COLUMN segment_role TEXT;
CREATE INDEX idx_runs_segment_role ON runs(segment_role);
"""

# Forward-only: (version, ddl). Append new migrations; never edit an applied one.
_MIGRATION_11 = """
CREATE TABLE research_experiments (
    experiment_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'inconclusive', 'execution_failed')),
    definition_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE research_experiment_runs (
    experiment_id TEXT NOT NULL REFERENCES research_experiments(experiment_id),
    fold_index INTEGER NOT NULL CHECK (fold_index >= 0),
    candidate_id TEXT NOT NULL,
    segment_role TEXT NOT NULL CHECK (segment_role IN ('train', 'validation', 'test')),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY (experiment_id, fold_index, segment_role, candidate_id)
);
CREATE INDEX idx_experiment_runs_run ON research_experiment_runs(run_id);
CREATE TRIGGER immutable_finished_experiment
BEFORE UPDATE ON research_experiments
WHEN OLD.state != 'running'
BEGIN
    SELECT RAISE(ABORT, 'Finished experiment is immutable');
END;
CREATE TRIGGER experiment_membership_guard
BEFORE INSERT ON research_experiment_runs
WHEN (SELECT state FROM research_experiments WHERE experiment_id = NEW.experiment_id) != 'running'
    OR (SELECT segment_role FROM runs WHERE run_id = NEW.run_id) IS NOT NEW.segment_role
BEGIN
    SELECT RAISE(ABORT, 'Experiment membership requires a running experiment and matching run role');
END;
"""

_MIGRATION_12 = """
CREATE TABLE engine_integrity_reports (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    engine_version INTEGER NOT NULL,
    metric_calculation_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE reliability_policies (
    policy_hash TEXT PRIMARY KEY,
    policy_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE study_reliability_reports (
    experiment_id TEXT PRIMARY KEY REFERENCES research_experiments(experiment_id),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('reliable', 'rejected', 'inconclusive', 'review_required')
    ),
    policy_hash TEXT NOT NULL REFERENCES reliability_policies(policy_hash),
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE study_reliability_criteria (
    experiment_id TEXT NOT NULL REFERENCES research_experiments(experiment_id),
    criterion_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (experiment_id, criterion_id)
);

CREATE TABLE robustness_evidence (
    experiment_id TEXT PRIMARY KEY REFERENCES research_experiments(experiment_id),
    provenance_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE frozen_study_definitions (
    experiment_id TEXT PRIMARY KEY REFERENCES research_experiments(experiment_id),
    definition_hash TEXT NOT NULL UNIQUE,
    policy_hash TEXT NOT NULL REFERENCES reliability_policies(policy_hash),
    final_holdout_lock_hash TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    frozen_by TEXT NOT NULL CHECK (frozen_by = 'Bloom'),
    payload_json TEXT NOT NULL
);

CREATE TABLE final_holdout_executions (
    experiment_id TEXT PRIMARY KEY REFERENCES frozen_study_definitions(experiment_id),
    authorization_sha256 TEXT NOT NULL,
    requested_by TEXT NOT NULL CHECK (requested_by = 'Bloom'),
    executed_at TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('passed', 'failed', 'inconclusive')),
    payload_json TEXT NOT NULL
);

CREATE TABLE retention_metadata (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    detail_disposition TEXT NOT NULL DEFAULT 'retained'
        CHECK (detail_disposition IN ('retained', 'pruned', 'protected')),
    protected_reason TEXT,
    pruned_at TEXT,
    pruned_by TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER immutable_engine_integrity
BEFORE UPDATE ON engine_integrity_reports
BEGIN
    SELECT RAISE(ABORT, 'engine integrity report is immutable');
END;
CREATE TRIGGER immutable_reliability_policy
BEFORE UPDATE ON reliability_policies
BEGIN
    SELECT RAISE(ABORT, 'reliability policy is immutable');
END;
CREATE TRIGGER immutable_reliability_report
BEFORE UPDATE ON study_reliability_reports
BEGIN
    SELECT RAISE(ABORT, 'study reliability report is immutable');
END;
CREATE TRIGGER immutable_frozen_study
BEFORE UPDATE ON frozen_study_definitions
BEGIN
    SELECT RAISE(ABORT, 'frozen study definition is immutable');
END;
CREATE TRIGGER immutable_final_holdout
BEFORE UPDATE ON final_holdout_executions
BEGIN
    SELECT RAISE(ABORT, 'final holdout execution is exactly-once and immutable');
END;
"""

_MIGRATION_13 = """
ALTER TABLE research_experiments ADD COLUMN reliability_policy_hash TEXT
    REFERENCES reliability_policies(policy_hash);

CREATE TABLE final_holdout_authorizations (
    experiment_id TEXT PRIMARY KEY REFERENCES frozen_study_definitions(experiment_id),
    authorization_sha256 TEXT NOT NULL UNIQUE,
    requested_by TEXT NOT NULL CHECK (requested_by = 'Bloom'),
    reason TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('authorized', 'claimed', 'completed', 'interrupted')
    ),
    authorized_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    claim_id TEXT UNIQUE,
    interruption_reason TEXT
);

ALTER TABLE final_holdout_executions ADD COLUMN run_id TEXT REFERENCES runs(run_id);
ALTER TABLE final_holdout_executions ADD COLUMN verified_identities_json TEXT;

CREATE TRIGGER immutable_engine_integrity_delete
BEFORE DELETE ON engine_integrity_reports BEGIN
    SELECT RAISE(ABORT, 'engine integrity report is immutable');
END;
CREATE TRIGGER immutable_reliability_policy_delete
BEFORE DELETE ON reliability_policies BEGIN
    SELECT RAISE(ABORT, 'reliability policy is immutable');
END;
CREATE TRIGGER immutable_reliability_report_delete
BEFORE DELETE ON study_reliability_reports BEGIN
    SELECT RAISE(ABORT, 'study reliability report is immutable');
END;
CREATE TRIGGER immutable_reliability_criteria_update
BEFORE UPDATE ON study_reliability_criteria BEGIN
    SELECT RAISE(ABORT, 'study reliability criteria are immutable');
END;
CREATE TRIGGER immutable_reliability_criteria_delete
BEFORE DELETE ON study_reliability_criteria BEGIN
    SELECT RAISE(ABORT, 'study reliability criteria are immutable');
END;
CREATE TRIGGER immutable_robustness_update
BEFORE UPDATE ON robustness_evidence BEGIN
    SELECT RAISE(ABORT, 'robustness evidence is immutable');
END;
CREATE TRIGGER immutable_robustness_delete
BEFORE DELETE ON robustness_evidence BEGIN
    SELECT RAISE(ABORT, 'robustness evidence is immutable');
END;
CREATE TRIGGER immutable_frozen_study_delete
BEFORE DELETE ON frozen_study_definitions BEGIN
    SELECT RAISE(ABORT, 'frozen study definition is immutable');
END;
CREATE TRIGGER immutable_final_holdout_delete
BEFORE DELETE ON final_holdout_executions BEGIN
    SELECT RAISE(ABORT, 'final holdout execution is exactly-once and immutable');
END;
CREATE TRIGGER immutable_final_authorization_delete
BEFORE DELETE ON final_holdout_authorizations BEGIN
    SELECT RAISE(ABORT, 'final holdout authorization is immutable');
END;
CREATE TRIGGER final_authorization_transition_guard
BEFORE UPDATE ON final_holdout_authorizations
WHEN NOT (
    (OLD.state = 'authorized' AND NEW.state = 'claimed'
        AND NEW.claim_id IS NOT NULL AND NEW.claimed_at IS NOT NULL
        AND NEW.experiment_id = OLD.experiment_id
        AND NEW.authorization_sha256 = OLD.authorization_sha256
        AND NEW.requested_by = OLD.requested_by
        AND NEW.reason = OLD.reason
        AND NEW.authorized_at = OLD.authorized_at)
    OR
    (OLD.state = 'claimed' AND NEW.state IN ('completed', 'interrupted')
        AND NEW.claim_id = OLD.claim_id
        AND NEW.claimed_at = OLD.claimed_at
        AND NEW.experiment_id = OLD.experiment_id
        AND NEW.authorization_sha256 = OLD.authorization_sha256
        AND NEW.requested_by = OLD.requested_by
        AND NEW.reason = OLD.reason
        AND NEW.authorized_at = OLD.authorized_at)
)
BEGIN
    SELECT RAISE(ABORT, 'illegal final holdout authorization transition');
END;

CREATE TRIGGER protect_frozen_run_trades
BEFORE DELETE ON run_trades
WHEN EXISTS (
    SELECT 1 FROM research_experiment_runs er
    JOIN frozen_study_definitions fs ON fs.experiment_id = er.experiment_id
    WHERE er.run_id = OLD.run_id
)
OR EXISTS (
    SELECT 1 FROM final_holdout_executions fh WHERE fh.run_id = OLD.run_id
)
OR EXISTS (
    SELECT 1 FROM retention_metadata rm
    WHERE rm.run_id = OLD.run_id AND rm.detail_disposition = 'protected'
)
BEGIN
    SELECT RAISE(ABORT, 'protected run detail is retained indefinitely');
END;
CREATE TRIGGER protect_frozen_run_equity
BEFORE DELETE ON run_equity_points
WHEN EXISTS (
    SELECT 1 FROM research_experiment_runs er
    JOIN frozen_study_definitions fs ON fs.experiment_id = er.experiment_id
    WHERE er.run_id = OLD.run_id
)
OR EXISTS (
    SELECT 1 FROM final_holdout_executions fh WHERE fh.run_id = OLD.run_id
)
OR EXISTS (
    SELECT 1 FROM retention_metadata rm
    WHERE rm.run_id = OLD.run_id AND rm.detail_disposition = 'protected'
)
BEGIN
    SELECT RAISE(ABORT, 'protected run detail is retained indefinitely');
END;
"""

_MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
    (3, _MIGRATION_3),
    (4, _MIGRATION_4),
    (5, _MIGRATION_5),
    (6, _MIGRATION_6),
    (7, _MIGRATION_7),
    (8, _MIGRATION_8),
    (9, _MIGRATION_9),
    (10, _MIGRATION_10),
    (11, _MIGRATION_11),
    (12, _MIGRATION_12),
    (13, _MIGRATION_13),
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


def begin_immediate(
    conn: sqlite3.Connection,
    *,
    attempts: int = _LOCK_RETRY_ATTEMPTS,
    base_delay_seconds: float = _LOCK_RETRY_BASE_SECONDS,
) -> None:
    """Acquire SQLite's write lock with bounded startup contention retries."""
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    previous_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.execute(f"PRAGMA busy_timeout = {_LOCK_BUSY_TIMEOUT_MS}")
    try:
        for attempt in range(attempts):
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                if attempt == attempts - 1:
                    raise
                time.sleep(base_delay_seconds * (attempt + 1))
    finally:
        conn.execute(f"PRAGMA busy_timeout = {previous_timeout}")


def _execute_ddl(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute a migration script without ``executescript`` auto-commits."""
    statement = ""
    for line in ddl.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            if sql:
                conn.execute(sql)
            statement = ""
    if statement.strip():
        raise RuntimeError("Incomplete SQL statement in research schema migration")


def migrate(conn: sqlite3.Connection, *, now: Callable[[], str] = utc_now_iso) -> int:
    """Apply pending migrations in order. Idempotent; returns the resulting version.

    Running this twice is a no-op: already-recorded versions are skipped.
    """
    begin_immediate(conn)
    try:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        if applied and applied != set(range(1, max(applied) + 1)):
            raise RuntimeError(
                f"Non-contiguous research schema history: {sorted(applied)}"
            )
        if applied and max(applied) > SCHEMA_VERSION:
            raise RuntimeError(
                f"Research schema version {max(applied)} is newer than this "
                f"binary supports ({SCHEMA_VERSION})."
            )
        for version, ddl in sorted(_MIGRATIONS):
            if version in applied:
                continue
            _execute_ddl(conn, ddl)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, now()),
            )
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        resulting_version = int(row[0])
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
        return resulting_version
