"""SQLite implementation of the :class:`ResearchStore` protocol.

Design choices that matter:

* Run identity is a time-ordered UUIDv7 generated locally, not a directory-scan
  max. The old ``ResearchRegistry._next_id`` was unsafe the moment the same
  project was touched from two devices.
* Terminal-run immutability is enforced by a database trigger (see
  ``schema.py``); the Python guards here are a courteous early error, not the
  guarantee.
* Publication payloads are scanned for trade/equity keys and refused by
  construction, so per-trade data cannot leave the machine.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from vibe.research_pipeline.contracts import RunEvidence, ValidationFinding
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import (
    RunState,
    TERMINAL_STATES,
    assert_legal_transition,
)
from vibe.research_pipeline.paths import research_db_path
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.store import (
    ImmutableRecordError,
    LeaseError,
    StoreError,
    ValidationRequiredError,
)
from vibe.research_pipeline.validation import ValidationReport

__all__ = ["SqliteResearchStore", "SqliteRunRecord"]

# Keys whose presence anywhere in a publication payload means trade or equity
# data would leave the machine. Publication is summary-only, by construction.
_FORBIDDEN_PUBLICATION_KEYS = frozenset(
    {"trades", "trade_ledger", "equity_curve", "equity_points"}
)


def _uuid7() -> str:
    """Return a time-ordered UUIDv7 string using only the stdlib.

    Time ordering keeps run ids roughly sortable by creation, which the old
    integer ids gave for free and a bare uuid4 would lose.
    """
    ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def _forbidden_key_path(payload: Any, trail: str = "") -> Optional[str]:
    """Return the dotted path to the first trade/equity key found, or None."""
    if isinstance(payload, dict):
        for key, sub in payload.items():
            here = f"{trail}.{key}" if trail else str(key)
            if key in _FORBIDDEN_PUBLICATION_KEYS:
                return here
            found = _forbidden_key_path(sub, here)
            if found:
                return found
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            found = _forbidden_key_path(item, f"{trail}[{index}]")
            if found:
                return found
    return None


@dataclass(frozen=True)
class SqliteRunRecord:
    """Concrete read shape satisfying the ``RunRecord`` protocol."""

    run_id: str
    state: RunState
    fingerprint: str
    methodology_version: str
    display_alias: str
    strategy_id: str
    created_at: str
    lease_owner: Optional[str]
    lease_expires_at: Optional[str]


def _to_record(row: sqlite3.Row) -> SqliteRunRecord:
    return SqliteRunRecord(
        run_id=row["run_id"],
        state=RunState(row["state"]),
        fingerprint=row["fingerprint"],
        methodology_version=row["methodology_version"],
        display_alias=row["display_alias"],
        strategy_id=row["strategy_id"],
        created_at=row["created_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
    )


class SqliteResearchStore:
    """Durable local research store backed by a single SQLite file."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path if db_path is not None else research_db_path(create_parents=True)
        self._conn = sqlite3.connect(str(path), timeout=30.0)
        schema.configure_connection(self._conn)
        schema.migrate(self._conn)

    def close(self) -> None:
        self._conn.close()

    # -- lifecycle -------------------------------------------------------

    def register_run(
        self,
        *,
        fingerprint: RunFingerprint,
        methodology_version: str,
        notes: Optional[str] = None,
    ) -> str:
        """Create a run in ``REGISTERED``; idempotent on the fingerprint hash."""
        fp = fingerprint.fingerprint
        existing = self._conn.execute(
            "SELECT run_id FROM runs WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if existing:
            return existing["run_id"]

        run_id = _uuid7()
        now = schema.utc_now_iso()
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO run_display_ids (run_id) VALUES (?)", (run_id,)
                )
                display_seq = int(cur.lastrowid)
                display_alias = f"RUN-{display_seq:06d}"
                self._conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, display_seq, display_alias, fingerprint,
                        fingerprint_version, strategy_id, methodology_version,
                        state, notes, code_commit, code_dirty, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        display_seq,
                        display_alias,
                        fp,
                        fingerprint.fingerprint_version,
                        fingerprint.strategy_id,
                        methodology_version,
                        RunState.REGISTERED.value,
                        notes,
                        fingerprint.code_commit,
                        1 if fingerprint.code_dirty else 0,
                        now,
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO experiment_state_transitions
                        (run_id, from_state, to_state, reason, occurred_at)
                    VALUES (?, NULL, ?, ?, ?)
                    """,
                    (run_id, RunState.REGISTERED.value, "registered", now),
                )
        except sqlite3.IntegrityError:
            # A concurrent writer registered the same fingerprint first.
            row = self._conn.execute(
                "SELECT run_id FROM runs WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if row:
                return row["run_id"]
            raise
        return run_id

    def transition(
        self,
        run_id: str,
        *,
        target: RunState,
        reason: Optional[str] = None,
        lease_token: Optional[str] = None,
    ) -> None:
        current = self._require_state(run_id)
        if current in TERMINAL_STATES:
            raise ImmutableRecordError(
                f"Run {run_id} is terminal ({current.value}); cannot transition."
            )
        if target is RunState.RUNNING:
            raise LeaseError(
                "RUNNING requires an owned lease; call start_run() instead."
            )
        if current is RunState.RUNNING:
            self._assert_live_lease(run_id, lease_token)
        if current is RunState.VALIDATING and target in {
            RunState.COMPLETED,
            RunState.REVIEW_REQUIRED,
            RunState.VALIDATION_FAILED,
            RunState.INCONCLUSIVE,
        }:
            raise ValidationRequiredError(
                "A validating run can only be finalized with finalize_validation()."
            )
        if current is RunState.REVIEW_REQUIRED and target in {
            RunState.COMPLETED,
            RunState.VALIDATION_FAILED,
            RunState.INCONCLUSIVE,
        } and not reason:
            raise ValidationRequiredError(
                "Resolving REVIEW_REQUIRED requires a non-empty reviewer reason."
            )
        assert_legal_transition(current, target)
        now = schema.utc_now_iso()
        with self._conn:
            params: list[Any] = [target.value, now, run_id, current.value]
            lease_clause = ""
            if current is RunState.RUNNING:
                lease_clause = " AND lease_token = ? AND lease_expires_at > ?"
                params.extend([lease_token, now])
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET state = ?, updated_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE run_id = ? AND state = ?
                """
                + lease_clause,
                params,
            )
            if cursor.rowcount != 1:
                raise LeaseError(
                    f"Run {run_id!r} changed state or its lease expired."
                )
            self._conn.execute(
                """
                INSERT INTO experiment_state_transitions
                    (run_id, from_state, to_state, reason, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, current.value, target.value, reason, now),
            )

    def start_run(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> str:
        if not owner.strip():
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        current = self._require_state(run_id)
        assert_legal_transition(current, RunState.RUNNING)
        instant = self._utc(now)
        expires = instant + timedelta(seconds=lease_seconds)
        token = secrets.token_urlsafe(32)
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET state = ?, updated_at = ?, lease_owner = ?,
                    lease_token = ?, lease_expires_at = ?
                WHERE run_id = ? AND state = ?
                """,
                (
                    RunState.RUNNING.value,
                    instant.isoformat(),
                    owner,
                    token,
                    expires.isoformat(),
                    run_id,
                    current.value,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseError(
                    f"Run {run_id!r} was claimed by another worker."
                )
            self._conn.execute(
                """
                INSERT INTO experiment_state_transitions
                    (run_id, from_state, to_state, reason, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    current.value,
                    RunState.RUNNING.value,
                    f"lease acquired by {owner}",
                    instant.isoformat(),
                ),
            )
        return token

    def heartbeat(
        self,
        run_id: str,
        *,
        lease_token: str,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        instant = self._utc(now)
        expires = instant + timedelta(seconds=lease_seconds)
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE run_id = ? AND state = ? AND lease_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    expires.isoformat(),
                    instant.isoformat(),
                    run_id,
                    RunState.RUNNING.value,
                    lease_token,
                    instant.isoformat(),
                ),
            )
        if cursor.rowcount != 1:
            raise LeaseError(
                f"Run {run_id!r} has no live lease owned by the supplied token."
            )

    def sweep_stale_leases(self, *, now: Optional[datetime] = None) -> list[str]:
        instant = self._utc(now)
        now_iso = instant.isoformat()
        stale = self._conn.execute(
            """
            SELECT run_id FROM runs
            WHERE state = ? AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            ORDER BY display_seq
            """,
            (RunState.RUNNING.value, now_iso),
        ).fetchall()
        candidates = [row["run_id"] for row in stale]
        if not candidates:
            return []
        swept: list[str] = []
        with self._conn:
            for run_id in candidates:
                cursor = self._conn.execute(
                    """
                    UPDATE runs
                    SET state = ?, updated_at = ?, lease_owner = NULL,
                        lease_token = NULL, lease_expires_at = NULL
                    WHERE run_id = ? AND state = ?
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= ?
                    """,
                    (
                        RunState.EXECUTION_FAILED.value,
                        now_iso,
                        run_id,
                        RunState.RUNNING.value,
                        now_iso,
                    ),
                )
                if cursor.rowcount:
                    swept.append(run_id)
                    self._conn.execute(
                        """
                        INSERT INTO experiment_state_transitions
                            (run_id, from_state, to_state, reason, occurred_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            RunState.RUNNING.value,
                            RunState.EXECUTION_FAILED.value,
                            "execution lease expired",
                            now_iso,
                        ),
                    )
        return swept

    def finalize_validation(self, run_id: str, report: ValidationReport) -> None:
        current = self._require_state(run_id)
        if current is not RunState.VALIDATING:
            raise ValidationRequiredError(
                f"Run {run_id!r} is {current.value}, not validating."
            )
        assert_legal_transition(current, report.target_state)
        now = schema.utc_now_iso()
        finding_rows = [
            (
                run_id,
                finding.code,
                finding.category.value,
                finding.severity.value,
                finding.message,
                finding.metric_key,
                finding.model_dump_json(),
                now,
            )
            for finding in report.findings
        ]
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO validation_reports (
                    run_id, profile_name, scope, target_state,
                    registry_hash_at_execution, current_registry_hash,
                    leakage_passed, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    report.profile_name,
                    report.scope.value,
                    report.target_state.value,
                    report.registry_hash_at_execution,
                    report.current_registry_hash,
                    1 if report.leakage_passed else 0,
                    report.model_dump_json(),
                    now,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO run_findings
                    (run_id, code, category, severity, message, metric_key,
                     payload_json, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                finding_rows,
            )
            cursor = self._conn.execute(
                """
                UPDATE runs
                SET state = ?, updated_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE run_id = ? AND state = ?
                """,
                (
                    report.target_state.value,
                    now,
                    run_id,
                    RunState.VALIDATING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValidationRequiredError(
                    f"Run {run_id!r} changed state during validation finalization."
                )
            self._conn.execute(
                """
                INSERT INTO experiment_state_transitions
                    (run_id, from_state, to_state, reason, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    current.value,
                    report.target_state.value,
                    f"validation profile {report.profile_name}",
                    now,
                ),
            )

    # -- attachments -----------------------------------------------------

    def record_metrics(
        self, run_id: str, metrics: dict[str, float], *, calculation_version: int
    ) -> None:
        self._assert_not_terminal(run_id)
        now = schema.utc_now_iso()
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO run_metrics
                    (run_id, metric_key, metric_value, calculation_version, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (run_id, key, float(value), calculation_version, now)
                    for key, value in metrics.items()
                ],
            )

    def record_evidence(self, run_id: str, evidence: RunEvidence) -> None:
        self._assert_not_terminal(run_id)
        now = schema.utc_now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO run_evidence (
                    run_id, trade_count, trade_ledger_sha256, equity_curve_sha256,
                    equity_points, retention_profile, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    evidence.trade_count,
                    evidence.trade_ledger_sha256,
                    evidence.equity_curve_sha256,
                    evidence.equity_points,
                    evidence.retention_profile.value,
                    evidence.model_dump_json(),
                    now,
                ),
            )

    def record_findings(
        self, run_id: str, findings: Iterable[ValidationFinding]
    ) -> None:
        self._assert_not_terminal(run_id)
        now = schema.utc_now_iso()
        rows = [
            (
                run_id,
                finding.code,
                finding.category.value,
                finding.severity.value,
                finding.message,
                finding.metric_key,
                finding.model_dump_json(),
                now,
            )
            for finding in findings
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO run_findings
                    (run_id, code, category, severity, message, metric_key,
                     payload_json, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def enqueue_publication(self, run_id: str, payload: dict[str, Any]) -> None:
        """Queue a summary payload for remote publication.

        Refuses any payload containing trade or equity data at any depth, and is
        idempotent per run id (the second enqueue for a run is a no-op).
        """
        offending = _forbidden_key_path(payload)
        if offending is not None:
            raise StoreError(
                f"Refusing to publish payload containing trade/equity data at "
                f"{offending!r}. Per-trade and equity data never leave the machine."
            )
        self._require_state(run_id)  # FK/existence guard with a clear error
        now = schema.utc_now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO publication_outbox (run_id, payload_json, enqueued_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, json.dumps(payload, sort_keys=True), now),
            )

    # -- queries ---------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[SqliteRunRecord]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _to_record(row) if row else None

    def find_by_fingerprint(self, fingerprint: str) -> Optional[SqliteRunRecord]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return _to_record(row) if row else None

    def query_runs(
        self,
        *,
        state: Optional[RunState] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SqliteRunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM runs {where} ORDER BY display_seq LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_to_record(row) for row in rows]

    # -- read helpers (beyond the protocol, for inspection/tests) --------

    def get_evidence(self, run_id: str) -> Optional[RunEvidence]:
        row = self._conn.execute(
            "SELECT payload_json FROM run_evidence WHERE run_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return RunEvidence.model_validate_json(row["payload_json"]) if row else None

    def get_transitions(self, run_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT from_state, to_state, reason, occurred_at "
            "FROM experiment_state_transitions WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    def get_metrics(self, run_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT metric_key, metric_value, calculation_version "
            "FROM run_metrics WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    def get_outbox(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT run_id, payload_json, status FROM publication_outbox "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    def get_validation_report(self, run_id: str) -> Optional[ValidationReport]:
        row = self._conn.execute(
            "SELECT payload_json FROM validation_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return (
            ValidationReport.model_validate_json(row["payload_json"])
            if row
            else None
        )

    def get_findings(self, run_id: str) -> list[ValidationFinding]:
        rows = self._conn.execute(
            "SELECT payload_json FROM run_findings WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [
            ValidationFinding.model_validate_json(row["payload_json"])
            for row in rows
        ]

    # -- internals -------------------------------------------------------

    def _require_state(self, run_id: str) -> RunState:
        row = self._conn.execute(
            "SELECT state FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"Unknown run id {run_id!r}")
        return RunState(row["state"])

    def _assert_not_terminal(self, run_id: str) -> None:
        if self._require_state(run_id) in TERMINAL_STATES:
            raise ImmutableRecordError(
                f"Run {run_id} is terminal; cannot attach further records."
            )

    def _assert_live_lease(
        self, run_id: str, lease_token: Optional[str]
    ) -> None:
        now = datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT lease_token, lease_expires_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            row is None
            or not lease_token
            or row["lease_token"] != lease_token
            or row["lease_expires_at"] is None
            or datetime.fromisoformat(row["lease_expires_at"]) <= now
        ):
            raise LeaseError(
                f"Run {run_id!r} has no live lease owned by the supplied token."
            )

    @staticmethod
    def _utc(value: Optional[datetime]) -> datetime:
        instant = value or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("lease timestamps must be timezone-aware")
        return instant.astimezone(timezone.utc)
