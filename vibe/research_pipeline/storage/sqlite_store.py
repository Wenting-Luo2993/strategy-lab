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
import os
import secrets
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from vibe.research_pipeline.contracts import RunEvidence, ValidationFinding
from vibe.research_pipeline.contracts import RetentionProfile
from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.contracts import Severity, ValidationCategory
from vibe.research_pipeline.evidence import equity_curve_digest, trade_ledger_digest
from vibe.research_pipeline.holdout import load_lock, read_access_log
from vibe.research_pipeline.hashing import canonical_json, sha256_hex
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import (
    RunState,
    TERMINAL_STATES,
    assert_legal_transition,
)
from vibe.research_pipeline.paths import research_db_path
from vibe.research_pipeline.publishing import (
    PUBLICATION_SCHEMA_VERSION,
    PublicationKind,
    PublicationStatus,
    RemoteAcknowledgement,
    RemoteRecordState,
    ResearchPublication,
    redact_secrets,
    validate_publication_payload,
)
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.legacy_importer import upsert_registry_payload
from vibe.research_pipeline.store import (
    ImmutableRecordError,
    LeaseError,
    StoredTrade,
    StoreError,
    ValidationRequiredError,
)
from vibe.research_pipeline.validation import (
    ValidationReport,
    ValidationScope,
)

__all__ = ["SqliteResearchStore", "SqliteRunRecord"]

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
    lineage_id: Optional[str]


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
        lineage_id=row["lineage_id"],
    )


def _opt_float(value: Any) -> Optional[float]:
    """Coerce to float, preserving None rather than turning it into 0.0.

    An open trade has no ``exit_price``, and a run with no risk model has no
    ``initial_risk``. Collapsing those to zero would make "unknown" and
    "genuinely zero" indistinguishable in every downstream chart.
    """
    return None if value is None else float(value)


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _iso_or_str(value: Any) -> str:
    """Render a timestamp as ISO-8601 text, falling back to ``str``."""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _opt_iso(value: Any) -> Optional[str]:
    return None if value is None else _iso_or_str(value)


def _parse_iso(value: Any) -> Any:
    """Restore a stored timestamp to a ``datetime``.

    Returned as a real datetime rather than the stored text because the
    canonical encoder normalizes datetimes to UTC before hashing but treats a
    string as an opaque literal. A curve read back as ISO text therefore
    digests to a different value than the one recorded, and every integrity
    check against it would fail for a reason that has nothing to do with the
    data. Unparseable text is passed through unchanged so a malformed row
    surfaces as a checksum mismatch rather than an exception during a read.
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return value


class SqliteResearchStore:
    """Durable local research store backed by a single SQLite file."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        holdout_config_path: Optional[Path] = None,
        holdout_access_log_path: Optional[Path] = None,
        origin_device_id: Optional[str] = None,
    ) -> None:
        path = db_path if db_path is not None else research_db_path(create_parents=True)
        self._conn = sqlite3.connect(str(path), timeout=30.0)
        schema.configure_connection(self._conn)
        schema.migrate(self._conn)
        self._holdout_config_path = holdout_config_path
        self._holdout_access_log_path = holdout_access_log_path
        self._origin_device_id = (
            origin_device_id
            or os.getenv("RESEARCH_ORIGIN_DEVICE_ID")
            or socket.gethostname()
        )
        self.backfill_publications()

    def close(self) -> None:
        self._conn.close()

    # -- lifecycle -------------------------------------------------------

    def register_run(
        self,
        *,
        fingerprint: RunFingerprint,
        methodology_version: str,
        notes: Optional[str] = None,
        lineage_id: Optional[str] = None,
        segment_role: Optional[SegmentRole] = None,
    ) -> str:
        """Create a run in ``REGISTERED``; idempotent on the fingerprint hash.

        ``segment_role`` is persisted as a queryable column rather than left
        inside the fingerprint hash. A research viewer has to be able to filter
        out-of-sample runs directly; recovering that from free-text notes is
        how an in-sample number ends up being read as evidence. ``None`` is
        preserved as NULL -- "role not recorded" -- because inventing a role
        for a run that never declared one is the failure this column exists to
        prevent.
        """
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
                        state, notes, code_commit, code_dirty, created_at, updated_at,
                        lineage_id, identity_json, origin_device_id, segment_role
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        lineage_id,
                        fingerprint.model_dump_json(),
                        self._origin_device_id,
                        None if segment_role is None else SegmentRole(segment_role).value,
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
        if (
            current is RunState.REVIEW_REQUIRED
            and target is RunState.COMPLETED
            and self._is_promotion_report(run_id)
            and self._holdout_lineage_touch_count(run_id) > 1
        ):
            raise ValidationRequiredError(
                "Promotion cannot be resolved to COMPLETED after more than one "
                "final-holdout touch for the research lineage."
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
        report = self._apply_holdout_promotion_gate(run_id, report)
        self._assert_completed_report_is_valid(report)
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
            self._enqueue_run_publication_tx(run_id, now=now)
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

    # -- local-only row-level retention (P15b) ---------------------------
    #
    # These two methods are the only writers for ``run_trades`` and
    # ``run_equity_points``. Both refuse to store anything the run did not
    # declare it would keep, and both verify the rows against the digest
    # already recorded in ``run_evidence``.
    #
    # The digest check is the point. Storing a ledger that does not reproduce
    # ``trade_ledger_sha256`` would be worse than storing nothing: the
    # dashboards would show rows that disagree with the checksum the pipeline
    # uses to prove reproducibility, and the disagreement would be invisible
    # because the summary row would still look correct.

    def _retention_context(self, run_id: str) -> tuple[RetentionProfile, str, str]:
        """Return the declared profile and both digests for ``run_id``.

        Raises:
            StoreError: If no evidence row exists. ``record_evidence`` is
                required under *every* profile, so its absence means the caller
                is writing detail before the summary it must agree with.
        """
        row = self._conn.execute(
            """
            SELECT retention_profile, trade_ledger_sha256, equity_curve_sha256
            FROM run_evidence WHERE run_id = ? ORDER BY id DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise StoreError(
                f"Run {run_id} has no evidence row; record_evidence must run "
                "before row-level detail, which is verified against it."
            )
        return (
            RetentionProfile(row["retention_profile"]),
            row["trade_ledger_sha256"],
            row["equity_curve_sha256"],
        )

    def record_trades(self, run_id: str, trades: Sequence[Any]) -> None:
        """Persist the local trade ledger for a ``diagnostic``/``full`` run.

        Args:
            run_id: Target run. Must be non-terminal and carry an evidence row.
            trades: Completed trades **in execution order**. Order is part of
                the identity of the ledger, so it is preserved verbatim and
                stored in an explicit ``sequence`` column.

        Raises:
            StoreError: If the run declared ``summary`` retention, or if the
                supplied trades do not reproduce the recorded ledger digest.
        """
        self._assert_not_terminal(run_id)
        profile, ledger_digest, _ = self._retention_context(run_id)
        if profile is RetentionProfile.SUMMARY:
            raise StoreError(
                f"Run {run_id} declared retention profile 'summary'; it keeps "
                "parameters and metrics only. Re-run under 'diagnostic' or "
                "'full' to retain the trade ledger."
            )

        actual = trade_ledger_digest(trades)
        if actual != ledger_digest:
            raise StoreError(
                f"Trade ledger for run {run_id} does not match its recorded "
                f"evidence: expected {ledger_digest}, computed {actual}. "
                "Storing it would contradict the checksum used to prove the "
                "run is reproducible."
            )

        now = schema.utc_now_iso()
        rows = [
            (
                run_id,
                index,
                str(getattr(trade, "symbol", "")),
                str(getattr(trade, "side", "")),
                float(getattr(trade, "quantity", 0.0) or 0.0),
                float(getattr(trade, "entry_price", 0.0) or 0.0),
                _opt_float(getattr(trade, "exit_price", None)),
                _iso_or_str(getattr(trade, "entry_time", None)),
                _opt_iso(getattr(trade, "exit_time", None)),
                _opt_float(getattr(trade, "pnl", None)),
                _opt_float(getattr(trade, "commission", None)),
                _opt_float(getattr(trade, "initial_risk", None)),
                _opt_str(getattr(trade, "exit_reason", None)),
                now,
            )
            for index, trade in enumerate(trades)
        ]
        with self._conn:
            self._conn.execute("DELETE FROM run_trades WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                """
                INSERT INTO run_trades (
                    run_id, sequence, symbol, side, quantity, entry_price,
                    exit_price, entry_time, exit_time, pnl, commission,
                    initial_risk, exit_reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def record_equity_curve(self, run_id: str, points: Any) -> None:
        """Persist the local equity curve for a ``full`` run.

        Args:
            run_id: Target run. Must be non-terminal and carry an evidence row.
            points: A pandas Series indexed by timestamp, or an iterable of
                ``(timestamp, equity)`` pairs. Both shapes are accepted because
                the engine carries the curve in both at different stages.

        Raises:
            StoreError: If the run did not declare ``full`` retention, or if
                the curve does not reproduce the recorded digest.
        """
        self._assert_not_terminal(run_id)
        profile, _, curve_digest = self._retention_context(run_id)
        if profile is not RetentionProfile.FULL:
            raise StoreError(
                f"Run {run_id} declared retention profile {profile.value!r}; "
                "the equity curve is retained only under 'full'."
            )

        if hasattr(points, "items") and hasattr(points, "index"):
            pairs = list(points.items())
        else:
            pairs = [tuple(pair) for pair in points]

        actual = equity_curve_digest(pairs)
        if actual != curve_digest:
            raise StoreError(
                f"Equity curve for run {run_id} does not match its recorded "
                f"evidence: expected {curve_digest}, computed {actual}."
            )

        now = schema.utc_now_iso()
        rows = [
            (run_id, index, _iso_or_str(timestamp), float(value), now)
            for index, (timestamp, value) in enumerate(pairs)
        ]
        with self._conn:
            self._conn.execute(
                "DELETE FROM run_equity_points WHERE run_id = ?", (run_id,)
            )
            self._conn.executemany(
                """
                INSERT INTO run_equity_points
                    (run_id, sequence, observed_at, equity, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_trades(self, run_id: str) -> list["StoredTrade"]:
        """Return the stored trade ledger in execution order.

        Rows are returned as :class:`StoredTrade` rather than plain dicts for a
        specific reason: ``trade_ledger_digest`` reads its fields by attribute
        and canonicalizes datetimes to UTC. A dict of ISO strings hashes to a
        completely different value, so a caller could not check retrieved rows
        against the evidence digest at all. Returning a shape that reproduces
        the digest is what makes :meth:`verify_retained_detail` possible, and
        with it any downstream claim that a chart shows the run's real trades.

        An empty list means either that the run kept no ledger or that it took
        no trades. Callers that must tell those apart should read
        ``retention_profile`` and ``trade_count`` from the evidence row, which
        are recorded under every profile.
        """
        return [
            StoredTrade(
                sequence=int(row["sequence"]),
                symbol=row["symbol"],
                side=row["side"],
                quantity=row["quantity"],
                entry_price=row["entry_price"],
                exit_price=row["exit_price"],
                entry_time=_parse_iso(row["entry_time"]),
                exit_time=_parse_iso(row["exit_time"]),
                pnl=row["pnl"],
                commission=row["commission"],
                initial_risk=row["initial_risk"],
                exit_reason=row["exit_reason"],
            )
            for row in self._conn.execute(
                """
                SELECT sequence, symbol, side, quantity, entry_price,
                       exit_price, entry_time, exit_time, pnl, commission,
                       initial_risk, exit_reason
                FROM run_trades WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        ]

    def get_equity_curve(self, run_id: str) -> list[tuple[Any, float]]:
        """Return the stored equity curve as chronological ``(time, value)``.

        Pairs rather than dicts, and real datetimes rather than ISO text, for
        the same reason as :meth:`get_trades`: this is the exact shape
        ``equity_curve_digest`` consumes, so the retrieved curve can be checked
        against the evidence it claims to come from.
        """
        return [
            (_parse_iso(row["observed_at"]), float(row["equity"]))
            for row in self._conn.execute(
                """
                SELECT observed_at, equity
                FROM run_equity_points WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        ]

    def verify_retained_detail(self, run_id: str) -> None:
        """Re-check stored rows against the run's evidence checksums.

        The writers verify on the way in, but that only proves the rows were
        correct when written. This proves they still are, which is the claim a
        dashboard implicitly makes every time it renders them. Silent drift --
        a partial delete, a botched migration, a hand-edited row -- would
        otherwise be invisible, because the summary row would continue to show
        the original, correct-looking totals.

        Raises:
            StoreError: If retained rows do not reproduce the recorded digest.
        """
        profile, ledger_digest, curve_digest = self._retention_context(run_id)

        if profile in (RetentionProfile.DIAGNOSTIC, RetentionProfile.FULL):
            actual = trade_ledger_digest(self.get_trades(run_id))
            if actual != ledger_digest:
                raise StoreError(
                    f"Stored trade ledger for run {run_id} no longer matches "
                    f"its evidence: expected {ledger_digest}, computed "
                    f"{actual}. The retained rows have drifted from the run "
                    "they claim to describe."
                )

        if profile is RetentionProfile.FULL:
            actual = equity_curve_digest(self.get_equity_curve(run_id))
            if actual != curve_digest:
                raise StoreError(
                    f"Stored equity curve for run {run_id} no longer matches "
                    f"its evidence: expected {curve_digest}, computed "
                    f"{actual}."
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
        """Reject arbitrary caller-authored payloads.

        P12 publication is built from allowlisted database columns only. Keeping
        this legacy method as a hard failure prevents older callers from
        bypassing that boundary.
        """
        del run_id, payload
        raise StoreError(
            "Arbitrary research publication payloads are disabled. Terminal run "
            "summaries are enqueued atomically by finalize_validation()."
        )

    def enqueue_run_publication(self, run_id: str) -> None:
        """Explicitly enqueue or refresh one terminal run summary."""
        state = self._require_state(run_id)
        if state not in TERMINAL_STATES:
            raise StoreError("Only terminal runs are eligible for publication")
        now = schema.utc_now_iso()
        with self._conn:
            self._enqueue_run_publication_tx(run_id, now=now)

    def backfill_publications(self) -> int:
        """Queue trusted summaries for records predating schema v6.

        The original P7 outbox accepted arbitrary JSON. Migration v6 preserves
        those rows as quarantined dead letters and rebuilds publications from
        authoritative allowlisted columns instead.
        """
        before = self._conn.total_changes
        now = schema.utc_now_iso()
        with self._conn:
            terminal_runs = self._conn.execute(
                """
                SELECT run_id FROM runs
                WHERE state IN ({})
                ORDER BY display_seq
                """.format(
                    ", ".join("?" for _ in TERMINAL_STATES)
                ),
                tuple(state.value for state in TERMINAL_STATES),
            ).fetchall()
            for row in terminal_runs:
                self._enqueue_run_publication_tx(row["run_id"], now=now)
            registry_rows = self._conn.execute(
                """
                SELECT record_type, legacy_id FROM registry_records
                ORDER BY imported_at, record_type, legacy_id
                """
            ).fetchall()
            for row in registry_rows:
                self._enqueue_registry_publication_tx(
                    row["record_type"], row["legacy_id"], now=now
                )
        return self._conn.total_changes - before

    def _insert_publication_tx(
        self,
        *,
        idempotency_key: str,
        kind: PublicationKind,
        aggregate_id: str,
        payload: dict[str, Any],
        now: str,
        parent_idempotency_key: Optional[str] = None,
    ) -> None:
        validate_publication_payload(kind, payload)
        payload_json = canonical_json(payload)
        payload_sha256 = sha256_hex(payload_json)
        self._conn.execute(
            """
            INSERT INTO publication_outbox (
                event_id, idempotency_key, aggregate_type, aggregate_id,
                parent_idempotency_key, schema_version, payload_json,
                payload_sha256, status, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                _uuid7(),
                idempotency_key,
                kind.value,
                aggregate_id,
                parent_idempotency_key,
                PUBLICATION_SCHEMA_VERSION,
                payload_json,
                payload_sha256,
                now,
            ),
        )

    def _enqueue_run_publication_tx(self, run_id: str, *, now: str) -> None:
        row = self._conn.execute(
            """
            SELECT run_id, display_alias, fingerprint, fingerprint_version,
                   strategy_id, methodology_version, state, code_commit,
                   code_dirty, created_at, updated_at, lineage_id,
                   identity_json, origin_device_id
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise StoreError(f"Unknown run {run_id!r}")
        if RunState(row["state"]) not in TERMINAL_STATES:
            raise StoreError("Only terminal runs are eligible for publication")

        identity = json.loads(row["identity_json"]) if row["identity_json"] else {}
        execution_version = identity.get("execution_model_version")
        execution_badge = (
            "unknown-pre-p2b"
            if execution_version is None
            else (
                "pre-p2b"
                if int(execution_version) < 6
                else f"execution-model-v{int(execution_version)}"
            )
        )
        metrics = [
            {
                "key": metric["metric_key"],
                "value": metric["metric_value"],
                "calculation_version": metric["calculation_version"],
            }
            for metric in self._conn.execute(
                """
                SELECT metric_key, metric_value, calculation_version
                FROM run_metrics WHERE run_id = ? ORDER BY metric_key, id
                """,
                (run_id,),
            ).fetchall()
        ]
        evidence_row = self._conn.execute(
            """
            SELECT trade_count, trade_ledger_sha256, equity_curve_sha256,
                   equity_points, retention_profile, payload_json
            FROM run_evidence WHERE run_id = ? ORDER BY id DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        evidence: dict[str, Any] = {}
        if evidence_row:
            raw_evidence = json.loads(evidence_row["payload_json"])
            evidence = {
                "aggregate_trade_count": evidence_row["trade_count"],
                "trade_ledger_sha256": evidence_row["trade_ledger_sha256"],
                "equity_curve_sha256": evidence_row["equity_curve_sha256"],
                "equity_point_count": evidence_row["equity_points"],
                "retention_profile": evidence_row["retention_profile"],
                "first_session": raw_evidence.get("first_session"),
                "last_session": raw_evidence.get("last_session"),
                "starting_equity": raw_evidence.get("starting_equity"),
                "ending_equity": raw_evidence.get("ending_equity"),
                "gross_pnl": raw_evidence.get("gross_pnl"),
                "total_costs": raw_evidence.get("total_costs"),
                "net_pnl": raw_evidence.get("net_pnl"),
                "max_observed_leverage": raw_evidence.get(
                    "max_observed_leverage"
                ),
                "min_cash": raw_evidence.get("min_cash"),
            }
        report = self._conn.execute(
            """
            SELECT profile_name, scope, target_state,
                   registry_hash_at_execution, current_registry_hash,
                   leakage_passed
            FROM validation_reports WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        finding_counts = {
            item["severity"]: item["count"]
            for item in self._conn.execute(
                """
                SELECT severity, COUNT(*) AS count
                FROM run_findings WHERE run_id = ?
                GROUP BY severity ORDER BY severity
                """,
                (run_id,),
            ).fetchall()
        }
        validation = (
            {
                "profile_name": report["profile_name"],
                "scope": report["scope"],
                "target_state": report["target_state"],
                "registry_hash_at_execution": report[
                    "registry_hash_at_execution"
                ],
                "current_registry_hash": report["current_registry_hash"],
                "leakage_passed": bool(report["leakage_passed"]),
                "finding_counts": finding_counts,
            }
            if report
            else {"finding_counts": finding_counts}
        )
        payload = {
            "identity": {
                "run_id": row["run_id"],
                "display_alias": row["display_alias"],
                "fingerprint": row["fingerprint"],
                "fingerprint_version": row["fingerprint_version"],
                "strategy_id": row["strategy_id"],
                "methodology_version": row["methodology_version"],
                "execution_model_version": execution_version,
                "metric_calculation_version": identity.get(
                    "metric_calculation_version"
                ),
                "feature_set_version": identity.get("feature_set_version"),
                "dataset_fingerprint": identity.get("data_snapshot_id"),
                "split_manifest_hash": identity.get("split_manifest_hash"),
                "code_commit": row["code_commit"],
                "code_dirty": bool(row["code_dirty"]),
                "lineage_id": row["lineage_id"] or row["run_id"],
                "origin_device_id": row["origin_device_id"] or "unknown",
                "badges": [
                    (
                        "legacy-uncontrolled"
                        if row["methodology_version"] == "legacy-uncontrolled"
                        else row["methodology_version"]
                    ),
                    execution_badge,
                ],
            },
            "state": row["state"],
            "timestamps": {
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
            "metrics": metrics,
            "evidence": evidence,
            "validation": validation,
        }
        self._insert_publication_tx(
            idempotency_key=f"run:{run_id}",
            kind=PublicationKind.RUN_SUMMARY,
            aggregate_id=run_id,
            payload=payload,
            now=now,
        )

    def _enqueue_registry_publication_tx(
        self,
        record_type: str,
        legacy_id: str,
        *,
        now: str,
        visited: Optional[set[tuple[str, str]]] = None,
    ) -> Optional[str]:
        visited = visited or set()
        identity = (record_type, legacy_id)
        if identity in visited:
            return None
        visited.add(identity)
        row = self._conn.execute(
            """
            SELECT canonical_sha256, methodology_version, status, git_dirty,
                   imported_at, record_origin, execution_model_version,
                   execution_identity_json
            FROM registry_records
            WHERE record_type = ? AND legacy_id = ?
            """,
            identity,
        ).fetchone()
        if row is None:
            return None
        relations = self._conn.execute(
            """
            SELECT relation, target_type, target_id
            FROM registry_relationships
            WHERE record_type = ? AND legacy_id = ? AND resolved = 1
            ORDER BY ordinal, relation
            """,
            identity,
        ).fetchall()
        parent_key: Optional[str] = None
        lineage: list[dict[str, str]] = []
        for relation in relations:
            target_key = self._enqueue_registry_publication_tx(
                relation["target_type"],
                relation["target_id"],
                now=now,
                visited=visited,
            )
            lineage.append(
                {
                    "relation": relation["relation"],
                    "record_type": relation["target_type"],
                    "record_id": relation["target_id"],
                }
            )
            parent_key = parent_key or target_key

        checksum = row["canonical_sha256"]
        idempotency_key = f"registry:{record_type}:{legacy_id}:{checksum}"
        execution_version = row["execution_model_version"]
        execution_identity = (
            json.loads(row["execution_identity_json"])
            if row["execution_identity_json"]
            else {}
        )
        badges = [
            (
                "legacy-uncontrolled"
                if row["methodology_version"] == "legacy-uncontrolled"
                else row["methodology_version"]
            ),
            (
                "unknown-pre-p2b"
                if execution_version is None
                else (
                    "pre-p2b"
                    if int(execution_version) < 6
                    else f"execution-model-v{int(execution_version)}"
                )
            ),
        ]
        payload = {
            "identity": {
                "record_type": record_type,
                "record_id": legacy_id,
                "methodology_version": row["methodology_version"],
                "execution_model_version": execution_version,
                "execution_identity": execution_identity,
                "record_origin": row["record_origin"],
                "origin_device_id": self._origin_device_id,
            },
            "status": row["status"],
            "checksum": {"canonical_sha256": checksum},
            "lineage": lineage,
            "badges": badges,
            "timestamps": {"recorded_at": row["imported_at"]},
        }
        self._insert_publication_tx(
            idempotency_key=idempotency_key,
            kind=PublicationKind.REGISTRY_RECORD,
            aggregate_id=f"{record_type}:{legacy_id}",
            payload=payload,
            now=now,
            parent_idempotency_key=parent_key,
        )
        return idempotency_key

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
            """
            SELECT aggregate_id AS run_id, payload_json, status, attempts,
                   last_error, next_attempt_at, payload_sha256
            FROM publication_outbox
            WHERE idempotency_key = ?
            """,
            (f"run:{run_id}",),
        ).fetchone()

    def reset_stale_publication_claims(self, *, now: datetime) -> int:
        now_iso = self._utc(now).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE publication_outbox
                SET status = 'failed', claimed_by = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, claim_token = NULL,
                    next_attempt_at = ?,
                    last_error = COALESCE(
                        last_error, 'publisher lease expired before acknowledgement'
                    )
                WHERE status = 'publishing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (now_iso, now_iso),
            )
        return cursor.rowcount

    def claim_publications(
        self,
        *,
        limit: int,
        claimed_by: str,
        lease_seconds: int,
        now: datetime,
    ) -> list[ResearchPublication]:
        if limit <= 0 or lease_seconds <= 0 or not claimed_by.strip():
            raise ValueError("claim arguments must be positive/non-empty")
        instant = self._utc(now)
        now_iso = instant.isoformat()
        lease_iso = (instant + timedelta(seconds=lease_seconds)).isoformat()
        claim_token = secrets.token_urlsafe(32)
        schema.begin_immediate(self._conn)
        try:
            row = self._conn.execute(
                """
                SELECT *
                FROM publication_outbox
                WHERE status IN ('pending', 'failed', 'publishing')
                ORDER BY sequence
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                self._conn.commit()
                return []
            if row["status"] == PublicationStatus.PUBLISHING.value:
                self._conn.commit()
                return []
            if row["next_attempt_at"] and row["next_attempt_at"] > now_iso:
                self._conn.commit()
                return []
            if row["parent_idempotency_key"]:
                parent = self._conn.execute(
                    "SELECT status FROM publication_outbox WHERE idempotency_key = ?",
                    (row["parent_idempotency_key"],),
                ).fetchone()
                if parent is not None and parent["status"] != PublicationStatus.PUBLISHED.value:
                    self._conn.commit()
                    return []
            cursor = self._conn.execute(
                """
                UPDATE publication_outbox
                SET status = 'publishing', claimed_by = ?, claimed_at = ?,
                    lease_expires_at = ?, claim_token = ?
                WHERE event_id = ? AND status IN ('pending', 'failed')
                """,
                (
                    claimed_by,
                    now_iso,
                    lease_iso,
                    claim_token,
                    row["event_id"],
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return []
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        payload = json.loads(row["payload_json"])
        return [
            ResearchPublication(
                event_id=row["event_id"],
                idempotency_key=row["idempotency_key"],
                kind=PublicationKind(row["aggregate_type"]),
                aggregate_id=row["aggregate_id"],
                schema_version=int(row["schema_version"]),
                publication_version=int(row["publication_version"]),
                payload=payload,
                payload_sha256=row["payload_sha256"],
                claim_token=claim_token,
                sequence=int(row["sequence"]),
                attempts=int(row["attempts"]),
            )
        ]

    def mark_publication_succeeded(
        self,
        publication: ResearchPublication,
        acknowledgement: RemoteAcknowledgement,
        *,
        now: datetime,
    ) -> bool:
        now_iso = self._utc(now).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE publication_outbox
                SET status = 'published', attempts = attempts + 1,
                    last_error = NULL, next_attempt_at = NULL,
                    claimed_by = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, claim_token = NULL,
                    published_at = ?,
                    remote_checksum = ?
                WHERE event_id = ? AND status = 'publishing'
                  AND publication_version = ?
                  AND claim_token = ?
                """,
                (
                    now_iso,
                    acknowledgement.payload_sha256,
                    publication.event_id,
                    publication.publication_version,
                    publication.claim_token,
                ),
            )
            if cursor.rowcount:
                self._conn.execute(
                    """
                    INSERT INTO publication_attempts (
                        event_id, attempt_number, outcome, error, attempted_at
                    ) VALUES (?, ?, 'published', NULL, ?)
                    """,
                    (
                        publication.event_id,
                        publication.attempts + 1,
                        now_iso,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO publication_acknowledgements (
                        idempotency_key, remote_checksum, publication_version,
                        acknowledged_at, last_reconciled_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        remote_checksum = excluded.remote_checksum,
                        publication_version = excluded.publication_version,
                        acknowledged_at = excluded.acknowledged_at,
                        last_reconciled_at = excluded.last_reconciled_at
                    """,
                    (
                        acknowledgement.idempotency_key,
                        acknowledgement.payload_sha256,
                        acknowledgement.publication_version,
                        acknowledgement.acknowledged_at,
                        now_iso,
                    ),
                )
        return bool(cursor.rowcount)

    def mark_publication_failed(
        self,
        publication: ResearchPublication,
        *,
        error: str,
        next_attempt_at: Optional[datetime],
        dead_letter: bool,
        now: datetime,
    ) -> bool:
        now_iso = self._utc(now).isoformat()
        safe_error = redact_secrets(error)
        status = (
            PublicationStatus.DEAD_LETTER.value
            if dead_letter
            else PublicationStatus.FAILED.value
        )
        retry_iso = (
            self._utc(next_attempt_at).isoformat()
            if next_attempt_at is not None
            else None
        )
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE publication_outbox
                SET status = ?, attempts = attempts + 1, last_error = ?,
                    next_attempt_at = ?, claimed_by = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, claim_token = NULL
                WHERE event_id = ? AND status = 'publishing'
                  AND publication_version = ?
                  AND claim_token = ?
                """,
                (
                    status,
                    safe_error,
                    retry_iso,
                    publication.event_id,
                    publication.publication_version,
                    publication.claim_token,
                ),
            )
            if cursor.rowcount:
                self._conn.execute(
                    """
                    INSERT INTO publication_attempts (
                        event_id, attempt_number, outcome, error, attempted_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        publication.event_id,
                        publication.attempts + 1,
                        status,
                        safe_error,
                        now_iso,
                    ),
                )
                if dead_letter:
                    self._conn.execute(
                        """
                        INSERT INTO publication_dead_letters (
                            event_id, idempotency_key, payload_sha256, attempts,
                            last_error, dead_lettered_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id) DO UPDATE SET
                            replay_count = publication_dead_letters.replay_count + 1
                        """,
                        (
                            publication.event_id,
                            publication.idempotency_key,
                            publication.payload_sha256,
                            publication.attempts + 1,
                            safe_error,
                            now_iso,
                        ),
                    )
        return bool(cursor.rowcount)

    def local_publication_state(self) -> list[RemoteRecordState]:
        rows = self._conn.execute(
            """
            SELECT idempotency_key, payload_sha256, publication_version
            FROM publication_outbox
            WHERE idempotency_key NOT LIKE 'legacy-untrusted:%'
            ORDER BY idempotency_key
            """
        ).fetchall()
        return [
            RemoteRecordState(
                idempotency_key=row["idempotency_key"],
                payload_sha256=row["payload_sha256"],
                publication_version=int(row["publication_version"]),
            )
            for row in rows
        ]

    def record_reconciliation_state(
        self, remote: Iterable[RemoteRecordState], *, now: datetime
    ) -> None:
        now_iso = self._utc(now).isoformat()
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO publication_acknowledgements (
                    idempotency_key, remote_checksum, publication_version,
                    acknowledged_at, last_reconciled_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    remote_checksum = excluded.remote_checksum,
                    publication_version = excluded.publication_version,
                    last_reconciled_at = excluded.last_reconciled_at
                """,
                [
                    (
                        item.idempotency_key,
                        item.payload_sha256,
                        item.publication_version,
                        now_iso,
                        now_iso,
                    )
                    for item in remote
                ],
            )

    def repair_publications(
        self,
        idempotency_keys: Iterable[str],
        *,
        reason: str,
        now: datetime,
    ) -> int:
        if not reason.strip():
            raise ValueError("repair reason must be non-empty")
        keys = tuple(dict.fromkeys(idempotency_keys))
        if not keys:
            return 0
        now_iso = self._utc(now).isoformat()
        repaired = 0
        with self._conn:
            for key in keys:
                cursor = self._conn.execute(
                    """
                    UPDATE publication_outbox
                    SET status = 'pending',
                        publication_version = publication_version + 1,
                        last_error = ?, next_attempt_at = ?,
                        claimed_by = NULL, claimed_at = NULL,
                        lease_expires_at = NULL, claim_token = NULL,
                        published_at = NULL,
                        remote_checksum = NULL
                    WHERE idempotency_key = ?
                      AND idempotency_key NOT LIKE 'legacy-untrusted:%'
                    """,
                    (f"explicit repair: {reason}", now_iso, key),
                )
                repaired += cursor.rowcount
                if cursor.rowcount:
                    self._conn.execute(
                        """
                        UPDATE publication_dead_letters
                        SET replay_count = replay_count + 1
                        WHERE idempotency_key = ?
                        """,
                        (key,),
                    )
        return repaired

    def requeue_dead_letter(
        self,
        event_id: str,
        *,
        reason: str,
        now: Optional[datetime] = None,
    ) -> bool:
        row = self._conn.execute(
            """
            SELECT idempotency_key FROM publication_outbox
            WHERE event_id = ? AND status = 'dead_letter'
              AND idempotency_key NOT LIKE 'legacy-untrusted:%'
            """,
            (event_id,),
        ).fetchone()
        return bool(
            row
            and self.repair_publications(
                [row["idempotency_key"]],
                reason=reason,
                now=self._utc(now),
            )
        )

    def replay_publication(
        self,
        idempotency_key: str,
        *,
        reason: str,
        now: Optional[datetime] = None,
    ) -> bool:
        return bool(
            self.repair_publications(
                [idempotency_key],
                reason=reason,
                now=self._utc(now),
            )
        )

    def publication_status_counts(self) -> dict[str, int]:
        counts = {
            row["status"]: int(row["count"])
            for row in self._conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM publication_outbox GROUP BY status
                """
            ).fetchall()
        }
        return {
            status.value: counts.get(status.value, 0)
            for status in PublicationStatus
        }

    def pending_publications_older_than(
        self, age: timedelta, *, now: Optional[datetime] = None
    ) -> int:
        if age.total_seconds() < 0:
            raise ValueError("age must be non-negative")
        instant = self._utc(now)
        cutoff = (instant - age).isoformat()
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS count FROM publication_outbox
            WHERE status IN ('pending', 'failed', 'publishing')
              AND enqueued_at <= ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["count"])

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

    def upsert_registry_record(
        self,
        *,
        record_type: str,
        payload: dict[str, Any],
        source_path: str,
        repository_root: Path,
    ) -> str:
        """Dual-write one legacy registry record during the compatibility period."""
        return upsert_registry_payload(
            self._conn,
            record_type=record_type,
            payload=payload,
            source_path=source_path,
            repository_root=repository_root,
        )

    def allocate_registry_id(self, *, record_type: str, prefix: str) -> str:
        """Allocate a prefix-local sequence under SQLite's write lock."""
        if not record_type.strip() or not prefix.strip():
            raise ValueError("record_type and prefix must be non-empty")
        with self._conn:
            row = self._conn.execute(
                """
                INSERT INTO registry_id_counters (prefix, last_value)
                VALUES (
                    ?,
                    COALESCE((
                        SELECT MAX(
                            CAST(substr(legacy_id, instr(legacy_id, '-') + 1) AS INTEGER)
                        )
                        FROM registry_records
                        WHERE legacy_id GLOB ? || '-[0-9]*'
                    ), 0) + 1
                )
                ON CONFLICT(prefix) DO UPDATE
                    SET last_value = registry_id_counters.last_value + 1
                RETURNING last_value
                """,
                (prefix, prefix),
            ).fetchone()
        return f"{prefix}-{int(row['last_value']):03d}"

    def create_registry_record(
        self,
        *,
        record_type: str,
        payload: dict[str, Any],
        methodology_version: str,
        execution_identity: Optional[dict[str, Any]] = None,
    ) -> str:
        legacy_id = payload.get("id")
        if not isinstance(legacy_id, str):
            raise StoreError("Registry payload has no string id")
        stored_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        payload_hash = sha256_hex(canonical_json(payload))
        identity = execution_identity or {}
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO registry_records (
                    record_type, legacy_id, payload_json, canonical_sha256,
                    methodology_version, status, git_dirty, source_path,
                    imported_at, record_origin, execution_model_version,
                    execution_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'research_store', ?, ?)
                """,
                (
                    record_type,
                    legacy_id,
                    stored_json,
                    payload_hash,
                    methodology_version,
                    None,
                    int(
                        bool(
                            (payload.get("execution_metadata") or {}).get(
                                "git_dirty", False
                            )
                        )
                    ),
                    schema.utc_now_iso(),
                    identity.get("execution_model_version"),
                    canonical_json(identity) if identity else None,
                ),
            )
            self._replace_registry_relationships(record_type, legacy_id, payload)
            self._conn.execute(
                """
                UPDATE registry_records
                SET status = ?
                WHERE record_type = ? AND legacy_id = ?
                """,
                (payload.get("status"), record_type, legacy_id),
            )
            self._enqueue_registry_publication_tx(
                record_type, legacy_id, now=schema.utc_now_iso()
            )
        return legacy_id

    def update_registry_record(
        self,
        *,
        record_type: str,
        legacy_id: str,
        payload: dict[str, Any],
    ) -> str:
        if payload.get("id") != legacy_id:
            raise StoreError("Registry payload id does not match update target")
        stored_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        payload_hash = sha256_hex(canonical_json(payload))
        with self._conn:
            self._conn.execute(
                "DELETE FROM registry_relationships "
                "WHERE record_type = ? AND legacy_id = ?",
                (record_type, legacy_id),
            )
            self._replace_registry_relationships(record_type, legacy_id, payload)
            cursor = self._conn.execute(
                """
                UPDATE registry_records
                SET payload_json = ?, canonical_sha256 = ?, status = ?,
                    git_dirty = ?, imported_at = ?
                WHERE record_type = ? AND legacy_id = ?
                """,
                (
                    stored_json,
                    payload_hash,
                    payload.get("status"),
                    int(
                        bool(
                            (payload.get("execution_metadata") or {}).get(
                                "git_dirty", False
                            )
                        )
                    ),
                    schema.utc_now_iso(),
                    record_type,
                    legacy_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError(
                    f"Unknown registry record {record_type}:{legacy_id}"
                )
            self._enqueue_registry_publication_tx(
                record_type, legacy_id, now=schema.utc_now_iso()
            )
        return payload_hash

    def get_registry_record(
        self, *, record_type: str, legacy_id: str
    ) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            """
            SELECT payload_json, methodology_version, execution_model_version,
                   execution_identity_json, record_origin, imported_at
            FROM registry_records
            WHERE record_type = ? AND legacy_id = ?
            """,
            (record_type, legacy_id),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload.setdefault("methodology_version", row["methodology_version"])
        if row["execution_model_version"] is not None:
            payload.setdefault(
                "execution_model_version", row["execution_model_version"]
            )
        if row["execution_identity_json"]:
            payload.setdefault(
                "execution_identity", json.loads(row["execution_identity_json"])
            )
        payload["_record_origin"] = row["record_origin"]
        payload["_imported_at"] = row["imported_at"]
        if record_type == "artifact":
            location = self._conn.execute(
                """
                SELECT repository_path, external_uri
                FROM registry_artifact_locations
                WHERE record_type = 'artifact' AND legacy_id = ?
                ORDER BY ordinal LIMIT 1
                """,
                (legacy_id,),
            ).fetchone()
            if location:
                payload["path"] = location["repository_path"]
                payload["external_uri"] = location["external_uri"]
        return payload

    def query_registry_records(
        self, *, record_type: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT legacy_id FROM registry_records
            WHERE record_type = ?
            ORDER BY
                CASE
                    WHEN instr(legacy_id, '-') > 0
                    THEN CAST(substr(legacy_id, instr(legacy_id, '-') + 1) AS INTEGER)
                    ELSE 0
                END,
                legacy_id
            """,
            (record_type,),
        ).fetchall()
        return [
            self.get_registry_record(
                record_type=record_type, legacy_id=row["legacy_id"]
            )
            for row in rows
        ]

    def _replace_registry_relationships(
        self, record_type: str, legacy_id: str, payload: dict[str, Any]
    ) -> None:
        relationships: list[tuple[str, str, str, int]] = []
        if record_type == "experiment":
            if payload.get("hypothesis_id"):
                relationships.append(
                    ("hypothesis", "hypothesis", payload["hypothesis_id"], 0)
                )
            if payload.get("parent_experiment_id"):
                relationships.append(
                    ("parent", "experiment", payload["parent_experiment_id"], 0)
                )
        elif record_type == "note" and payload.get("related_experiment_id"):
            relationships.append(
                ("experiment", "experiment", payload["related_experiment_id"], 0)
            )
        elif record_type == "artifact" and payload.get("experiment_id"):
            relationships.append(
                ("experiment", "experiment", payload["experiment_id"], 0)
            )
        elif record_type == "rejected_idea":
            relationships.extend(
                ("evidence", "experiment", target, index)
                for index, target in enumerate(payload.get("evidence") or [])
            )
        for relation, target_type, target_id, ordinal in relationships:
            resolved = self._conn.execute(
                "SELECT 1 FROM registry_records "
                "WHERE record_type = ? AND legacy_id = ?",
                (target_type, target_id),
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO registry_relationships (
                    record_type, legacy_id, relation, target_type, target_id,
                    ordinal, resolved
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_type,
                    legacy_id,
                    relation,
                    target_type,
                    target_id,
                    ordinal,
                    int(resolved is not None),
                ),
            )
        self._conn.execute(
            """
            UPDATE registry_relationships AS rel
            SET resolved = EXISTS (
                SELECT 1 FROM registry_records AS target
                WHERE target.record_type = rel.target_type
                  AND target.legacy_id = rel.target_id
            )
            """
        )

    def _apply_holdout_promotion_gate(
        self, run_id: str, report: ValidationReport
    ) -> ValidationReport:
        if (
            report.scope is not ValidationScope.PROMOTION
            or report.target_state is not RunState.COMPLETED
        ):
            return report
        lock = load_lock(self._holdout_config_path)
        touches = self._holdout_lineage_touch_count(run_id, lock_hash=lock.lock_hash)
        if touches <= 1:
            return report
        row = self._conn.execute(
            "SELECT COALESCE(lineage_id, run_id) AS lineage_id "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        lineage_id = row["lineage_id"]
        finding = ValidationFinding(
            code="OOS-001",
            category=ValidationCategory.RESEARCH_ACCEPTANCE,
            severity=Severity.BLOCKING,
            message=(
                "Promotion blocked: this research lineage touched the final "
                f"holdout {touches} times; at most one touch is permitted."
            ),
            observed=touches,
            expected="final-holdout touch count <= 1",
            context={"lineage_id": lineage_id, "lock_hash": lock.lock_hash},
        )
        return report.model_copy(
            update={
                "target_state": RunState.VALIDATION_FAILED,
                "findings": (*report.findings, finding),
            }
        )

    @staticmethod
    def _assert_completed_report_is_valid(report: ValidationReport) -> None:
        if report.target_state is not RunState.COMPLETED:
            return
        missing = set(ValidationCategory) - set(report.checked_categories)
        if missing:
            names = ", ".join(sorted(category.value for category in missing))
            raise ValidationRequiredError(
                f"Completed validation report omitted categories: {names}"
            )
        if not report.leakage_passed:
            raise ValidationRequiredError(
                "Completed validation report requires passing leakage evidence."
            )
        if report.registry_hash_at_execution != report.current_registry_hash:
            raise ValidationRequiredError(
                "Completed validation report requires an unchanged feature registry."
            )

    def _holdout_lineage_touch_count(
        self, run_id: str, *, lock_hash: Optional[str] = None
    ) -> int:
        if lock_hash is None:
            lock_hash = load_lock(self._holdout_config_path).lock_hash
        access_rows = read_access_log(self._holdout_access_log_path)
        touched_run_ids = {
            str(item.get("run_id"))
            for item in access_rows
            if item.get("lock_hash") == lock_hash
        }
        if not touched_run_ids:
            return 0
        row = self._conn.execute(
            "SELECT COALESCE(lineage_id, run_id) AS lineage_id "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        lineage_id = row["lineage_id"]
        lineage_runs = {
            result["run_id"]
            for result in self._conn.execute(
                "SELECT run_id FROM runs "
                "WHERE COALESCE(lineage_id, run_id) = ?",
                (lineage_id,),
            )
        } & touched_run_ids
        return sum(
            1
            for item in access_rows
            if item.get("lock_hash") == lock_hash
            and str(item.get("run_id")) in lineage_runs
        )

    def _is_promotion_report(self, run_id: str) -> bool:
        row = self._conn.execute(
            "SELECT scope FROM validation_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return bool(row and row["scope"] == ValidationScope.PROMOTION.value)

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
