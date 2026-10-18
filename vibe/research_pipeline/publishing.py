"""Durable, summary-only research publication and reconciliation.

The local SQLite database remains authoritative for execution. This module
drains its same-file outbox to a remote read model without ever accepting trade,
fill, bar, or equity-point payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from vibe.research_pipeline.hashing import canonical_json, sha256_hex
from vibe.research_pipeline.store import StoreError

logger = logging.getLogger(__name__)

PUBLICATION_SCHEMA_VERSION = 1


class PublicationKind(str, Enum):
    RUN_SUMMARY = "run_summary"
    REGISTRY_RECORD = "registry_record"


class PublicationStatus(str, Enum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    FAILED = "failed"
    PUBLISHED = "published"
    DEAD_LETTER = "dead_letter"


_ALLOWED_TOP_LEVEL_KEYS = {
    PublicationKind.RUN_SUMMARY: frozenset(
        {
            "identity",
            "state",
            "timestamps",
            "metrics",
            "evidence",
            "validation",
            "engine_integrity",
            "procedure_execution",
            "research_reliability",
        }
    ),
    PublicationKind.REGISTRY_RECORD: frozenset(
        {
            "identity",
            "status",
            "checksum",
            "lineage",
            "badges",
            "timestamps",
        }
    ),
}

_FORBIDDEN_KEY_ALIASES = frozenset(
    {
        "trade",
        "trades",
        "fill",
        "fills",
        "tradeledger",
        "individualfills",
        "equitycurve",
        "equitycurves",
        "equitypoint",
        "equitypoints",
        "rawbar",
        "rawbars",
        "pricebar",
        "pricebars",
        "barevents",
        "orderevents",
        "perevent",
        "events",
        "tradedata",
        "fillrecords",
        "equityhistory",
        "barseries",
    }
)
_APPROVED_CHECKSUM_KEYS = frozenset(
    {"tradeledgersha256", "equitycurvesha256", "payloadsha256", "canonicalsha256"}
)
_RUN_IDENTITY_KEYS = frozenset(
    {
        "run_id",
        "display_alias",
        "fingerprint",
        "fingerprint_version",
        "strategy_id",
        "methodology_version",
        "execution_model_version",
        "metric_calculation_version",
        "feature_set_version",
        "dataset_fingerprint",
        "split_manifest_hash",
        "code_commit",
        "code_dirty",
        "lineage_id",
        "origin_device_id",
        "badges",
    }
)
_RUN_EVIDENCE_KEYS = frozenset(
    {
        "aggregate_trade_count",
        "trade_ledger_sha256",
        "equity_curve_sha256",
        "equity_point_count",
        "retention_profile",
        "first_session",
        "last_session",
        "starting_equity",
        "ending_equity",
        "gross_pnl",
        "total_costs",
        "net_pnl",
        "max_observed_leverage",
        "min_cash",
    }
)
_RUN_VALIDATION_KEYS = frozenset(
    {
        "profile_name",
        "scope",
        "target_state",
        "registry_hash_at_execution",
        "current_registry_hash",
        "leakage_passed",
        "finding_counts",
    }
)
_REGISTRY_IDENTITY_KEYS = frozenset(
    {
        "record_type",
        "record_id",
        "methodology_version",
        "execution_model_version",
        "execution_identity",
        "record_origin",
        "origin_device_id",
    }
)
_EXECUTION_IDENTITY_KEYS = frozenset(
    {
        "execution_model_version",
        "intrabar_exit_resolution",
        "gap_fill_policy",
        "entry_fill_policy",
        "enforce_buying_power",
        "max_gross_leverage",
        "commission_model",
        "per_share",
        "minimum_per_order",
        "maximum_pct_of_notional",
        "other_per_share",
        "exit_slippage_model",
        "exit_slippage_tick_size",
        "exit_slippage_ticks",
        "exit_slippage_default_ticks",
    }
)
_EXIT_SLIPPAGE_REASON_KEYS = frozenset({"STOP", "EOD", "TP"})
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(authorization|apikey)\s*[:=]\s*(?:bearer\s+)?[^\s,}]+"
    ),
    re.compile(r"(?i)(service[_-]?role[_-]?key)\s*[:=]\s*[^\s,}]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def forbidden_payload_path(payload: Any, trail: str = "") -> Optional[str]:
    """Return the first forbidden event-level key path, including aliases."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            current = f"{trail}.{key}" if trail else str(key)
            normalized = _normalized_key(key)
            if (
                normalized in _FORBIDDEN_KEY_ALIASES
                and normalized not in _APPROVED_CHECKSUM_KEYS
            ):
                return current
            nested = forbidden_payload_path(value, current)
            if nested:
                return nested
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            nested = forbidden_payload_path(value, f"{trail}[{index}]")
            if nested:
                return nested
    return None


def validate_publication_payload(
    kind: PublicationKind, payload: Mapping[str, Any]
) -> None:
    unexpected = set(payload) - _ALLOWED_TOP_LEVEL_KEYS[kind]
    if unexpected:
        raise StoreError(
            f"Publication {kind.value!r} contains unapproved top-level fields: "
            f"{sorted(unexpected)}"
        )
    offending = forbidden_payload_path(payload)
    if offending:
        raise StoreError(
            "Research publication refused event-level data at "
            f"{offending!r}; trades, fills, equity points, and bars remain local."
        )
    _validate_approved_shape(kind, payload)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StoreError(f"Publication field {path!r} must be an object")
    return value


def _approved_keys(
    value: Any, allowed: frozenset[str], path: str
) -> Mapping[str, Any]:
    mapping = _mapping(value, path)
    unexpected = set(mapping) - allowed
    if unexpected:
        raise StoreError(
            f"Publication field {path!r} contains unapproved keys: "
            f"{sorted(unexpected)}"
        )
    return mapping


def _validate_approved_shape(
    kind: PublicationKind, payload: Mapping[str, Any]
) -> None:
    timestamps = _approved_keys(
        payload.get("timestamps", {}),
        frozenset({"created_at", "updated_at", "recorded_at"}),
        "timestamps",
    )
    for key, value in timestamps.items():
        _require_scalar(value, f"timestamps.{key}")
    if kind is PublicationKind.RUN_SUMMARY:
        identity = _approved_keys(
            payload.get("identity", {}), _RUN_IDENTITY_KEYS, "identity"
        )
        for key, value in identity.items():
            if key == "badges":
                _require_scalar_list(value, "identity.badges")
            else:
                _require_scalar(value, f"identity.{key}")
        evidence = _approved_keys(
            payload.get("evidence", {}), _RUN_EVIDENCE_KEYS, "evidence"
        )
        for key, value in evidence.items():
            _require_scalar(value, f"evidence.{key}")
        validation = _approved_keys(
            payload.get("validation", {}), _RUN_VALIDATION_KEYS, "validation"
        )
        finding_counts = validation.get("finding_counts", {})
        _approved_keys(
            finding_counts,
            frozenset({"blocking", "review", "advisory"}),
            "validation.finding_counts",
        )
        for key, value in validation.items():
            if key != "finding_counts":
                _require_scalar(value, f"validation.{key}")
        for key, value in finding_counts.items():
            _require_scalar(value, f"validation.finding_counts.{key}")
        engine_integrity = _approved_keys(
            payload.get("engine_integrity", {}),
            frozenset({"status", "report"}),
            "engine_integrity",
        )
        _require_scalar(engine_integrity.get("status"), "engine_integrity.status")
        report = engine_integrity.get("report")
        if report is not None:
            report = _approved_keys(
                report,
                frozenset({
                    "status", "engine_version", "metric_calculation_version",
                    "checked_categories", "findings",
                }),
                "engine_integrity.report",
            )
            _require_scalar(report.get("status"), "engine_integrity.report.status")
            findings = report.get("findings", [])
            if not isinstance(findings, list):
                raise StoreError("Publication field 'engine_integrity.report.findings' must be a list")
            for index, finding in enumerate(findings):
                _approved_keys(
                    finding,
                    frozenset({
                        "check_id", "category", "passed", "observed", "expected",
                        "residual", "applicable", "context",
                    }),
                    f"engine_integrity.report.findings[{index}]",
                )
        _require_scalar(payload.get("procedure_execution"), "procedure_execution")
        _require_scalar(payload.get("research_reliability"), "research_reliability")
        metrics = payload.get("metrics", [])
        if not isinstance(metrics, list):
            raise StoreError("Publication field 'metrics' must be a list")
        for index, metric in enumerate(metrics):
            _approved_keys(
                metric,
                frozenset({"key", "value", "calculation_version"}),
                f"metrics[{index}]",
            )
            for key, value in metric.items():
                _require_scalar(value, f"metrics[{index}].{key}")
        _require_scalar(payload.get("state"), "state")
        return

    identity = _approved_keys(
        payload.get("identity", {}), _REGISTRY_IDENTITY_KEYS, "identity"
    )
    for key, value in identity.items():
        if key != "execution_identity":
            _require_scalar(value, f"identity.{key}")
    execution_identity = _approved_keys(
        identity.get("execution_identity", {}),
        _EXECUTION_IDENTITY_KEYS,
        "identity.execution_identity",
    )
    exit_ticks = execution_identity.get("exit_slippage_ticks", {})
    if not isinstance(exit_ticks, Mapping):
        raise StoreError(
            "Publication field 'identity.execution_identity.exit_slippage_ticks' "
            "must be an object"
        )
    unexpected_tick_keys = set(exit_ticks) - _EXIT_SLIPPAGE_REASON_KEYS
    if unexpected_tick_keys:
        raise StoreError(
            "Publication field "
            "'identity.execution_identity.exit_slippage_ticks' contains "
            f"unapproved exit reasons: {sorted(unexpected_tick_keys)}"
        )
    for reason, ticks in exit_ticks.items():
        if (
            isinstance(ticks, bool)
            or not isinstance(ticks, (int, float))
            or not math.isfinite(float(ticks))
            or float(ticks) < 0
            or float(ticks) > 1_000_000
        ):
            raise StoreError(
                "Publication field "
                f"'identity.execution_identity.exit_slippage_ticks.{reason}' "
                "must be a finite numeric scalar between 0 and 1000000"
            )
    for key, value in execution_identity.items():
        if key != "exit_slippage_ticks":
            _require_scalar(value, f"identity.execution_identity.{key}")
    checksum = _approved_keys(
        payload.get("checksum", {}),
        frozenset({"canonical_sha256"}),
        "checksum",
    )
    for key, value in checksum.items():
        _require_scalar(value, f"checksum.{key}")
    lineage = payload.get("lineage", [])
    if not isinstance(lineage, list):
        raise StoreError("Publication field 'lineage' must be a list")
    for index, relation in enumerate(lineage):
        _approved_keys(
            relation,
            frozenset({"relation", "record_type", "record_id"}),
            f"lineage[{index}]",
        )
        for key, value in relation.items():
            _require_scalar(value, f"lineage[{index}].{key}")
    _require_scalar(payload.get("status"), "status")
    _require_scalar_list(payload.get("badges", []), "badges")


def _require_scalar(value: Any, path: str) -> None:
    if isinstance(value, (Mapping, list, tuple, set)):
        raise StoreError(f"Publication field {path!r} must be scalar")


def _require_scalar_list(value: Any, path: str) -> None:
    if not isinstance(value, list):
        raise StoreError(f"Publication field {path!r} must be a list")
    for index, item in enumerate(value):
        _require_scalar(item, f"{path}[{index}]")


def redact_secrets(message: Any, secrets: Sequence[str] = ()) -> str:
    text = str(message)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text


@dataclass(frozen=True)
class ResearchPublication:
    event_id: str
    idempotency_key: str
    kind: PublicationKind
    aggregate_id: str
    schema_version: int
    publication_version: int
    payload: Mapping[str, Any]
    payload_sha256: str
    claim_token: str
    sequence: int = 0
    attempts: int = 0

    def __post_init__(self) -> None:
        validate_publication_payload(self.kind, self.payload)
        expected = sha256_hex(canonical_json(self.payload))
        if self.payload_sha256 != expected:
            raise StoreError(
                f"Publication checksum mismatch for {self.idempotency_key!r}"
            )
        if not self.claim_token:
            raise StoreError("Claimed publication requires a claim token")


@dataclass(frozen=True)
class RemoteAcknowledgement:
    idempotency_key: str
    payload_sha256: str
    publication_version: int
    acknowledged_at: str


@dataclass(frozen=True)
class RemoteRecordState:
    idempotency_key: str
    payload_sha256: str
    publication_version: int


class ResearchPublishDestination(Protocol):
    async def publish(
        self, publication: ResearchPublication
    ) -> RemoteAcknowledgement:
        """Idempotently upsert one approved publication."""

    async def list_state(self) -> Sequence[RemoteRecordState]:
        """Return the remote acknowledgement/read-model state."""


class ResearchOutbox(Protocol):
    def reset_stale_publication_claims(self, *, now: datetime) -> int: ...

    def claim_publications(
        self,
        *,
        limit: int,
        claimed_by: str,
        lease_seconds: int,
        now: datetime,
    ) -> list[ResearchPublication]: ...

    def mark_publication_succeeded(
        self,
        publication: ResearchPublication,
        acknowledgement: RemoteAcknowledgement,
        *,
        now: datetime,
    ) -> bool: ...

    def mark_publication_failed(
        self,
        publication: ResearchPublication,
        *,
        error: str,
        next_attempt_at: Optional[datetime],
        dead_letter: bool,
        now: datetime,
    ) -> bool: ...

    def local_publication_state(self) -> Sequence[RemoteRecordState]: ...

    def record_reconciliation_state(
        self, remote: Sequence[RemoteRecordState], *, now: datetime
    ) -> None: ...

    def repair_publications(
        self, idempotency_keys: Sequence[str], *, reason: str, now: datetime
    ) -> int: ...


@dataclass(frozen=True)
class BackoffPolicy:
    base_seconds: float = 5.0
    maximum_seconds: float = 300.0
    jitter_fraction: float = 0.2
    random_value: Callable[[], float] = random.random

    def delay_seconds(self, attempts_after_failure: int) -> float:
        if attempts_after_failure <= 0:
            raise ValueError("attempts_after_failure must be positive")
        bounded = min(
            self.maximum_seconds,
            self.base_seconds * (2 ** (attempts_after_failure - 1)),
        )
        if self.jitter_fraction == 0:
            return bounded
        offset = ((self.random_value() * 2.0) - 1.0) * self.jitter_fraction
        return max(0.0, bounded * (1.0 + offset))


@dataclass
class PublishBatchResult:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0


class ResearchPublisher:
    """Deterministically drain the durable research outbox."""

    def __init__(
        self,
        outbox: ResearchOutbox,
        destination: ResearchPublishDestination,
        *,
        batch_size: int = 25,
        max_attempts: int = 5,
        lease_seconds: int = 120,
        claimed_by: str = "research-publisher",
        backoff: BackoffPolicy = BackoffPolicy(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if batch_size <= 0 or max_attempts <= 0 or lease_seconds <= 0:
            raise ValueError("batch_size, max_attempts, and lease_seconds must be positive")
        self.outbox = outbox
        self.destination = destination
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.claimed_by = claimed_by
        self.backoff = backoff
        self.clock = clock
        self._flush_lock = asyncio.Lock()

    async def flush_once(self) -> PublishBatchResult:
        async with self._flush_lock:
            now = self.clock()
            self.outbox.reset_stale_publication_claims(now=now)
            publications = self.outbox.claim_publications(
                limit=self.batch_size,
                claimed_by=self.claimed_by,
                lease_seconds=self.lease_seconds,
                now=now,
            )
            result = PublishBatchResult(claimed=len(publications))
            for publication in publications:
                try:
                    acknowledgement = await self.destination.publish(publication)
                    if acknowledgement.idempotency_key != publication.idempotency_key:
                        raise RuntimeError("remote acknowledgement key mismatch")
                    if acknowledgement.payload_sha256 != publication.payload_sha256:
                        raise RuntimeError("remote acknowledgement checksum mismatch")
                    if self.outbox.mark_publication_succeeded(
                        publication, acknowledgement, now=self.clock()
                    ):
                        result.published += 1
                except Exception as exc:
                    attempts = publication.attempts + 1
                    dead_letter = attempts >= self.max_attempts
                    retry_at = None
                    if not dead_letter:
                        retry_at = self.clock() + timedelta(
                            seconds=self.backoff.delay_seconds(attempts)
                        )
                    error = redact_secrets(exc)
                    if self.outbox.mark_publication_failed(
                        publication,
                        error=error,
                        next_attempt_at=retry_at,
                        dead_letter=dead_letter,
                        now=self.clock(),
                    ):
                        if dead_letter:
                            result.dead_lettered += 1
                        else:
                            result.failed += 1
                    logger.warning(
                        "Research publication failed: key=%s attempt=%s dead_letter=%s error=%s",
                        publication.idempotency_key,
                        attempts,
                        dead_letter,
                        error,
                    )
                    break
            return result


class ReconciliationStatus(str, Enum):
    MATCHED = "matched"
    MISSING_REMOTE = "missing_remote"
    DIVERGENT = "divergent"
    ORPHANED_REMOTE = "orphaned_remote"


@dataclass(frozen=True)
class ReconciliationItem:
    idempotency_key: str
    status: ReconciliationStatus
    local_checksum: Optional[str] = None
    remote_checksum: Optional[str] = None


@dataclass(frozen=True)
class ReconciliationReport:
    items: tuple[ReconciliationItem, ...]

    @property
    def missing(self) -> tuple[ReconciliationItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status is ReconciliationStatus.MISSING_REMOTE
        )

    @property
    def divergent(self) -> tuple[ReconciliationItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status is ReconciliationStatus.DIVERGENT
        )

    @property
    def orphaned(self) -> tuple[ReconciliationItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status is ReconciliationStatus.ORPHANED_REMOTE
        )


class ResearchReconciler:
    def __init__(
        self,
        outbox: ResearchOutbox,
        destination: ResearchPublishDestination,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.outbox = outbox
        self.destination = destination
        self.clock = clock

    async def compare(self) -> ReconciliationReport:
        local = {item.idempotency_key: item for item in self.outbox.local_publication_state()}
        remote_rows = await self.destination.list_state()
        remote = {item.idempotency_key: item for item in remote_rows}
        self.outbox.record_reconciliation_state(remote_rows, now=self.clock())
        items: list[ReconciliationItem] = []
        for key in sorted(local.keys() | remote.keys()):
            local_item = local.get(key)
            remote_item = remote.get(key)
            if local_item is None:
                status = ReconciliationStatus.ORPHANED_REMOTE
            elif remote_item is None:
                status = ReconciliationStatus.MISSING_REMOTE
            elif local_item.payload_sha256 != remote_item.payload_sha256:
                status = ReconciliationStatus.DIVERGENT
            else:
                status = ReconciliationStatus.MATCHED
            items.append(
                ReconciliationItem(
                    idempotency_key=key,
                    status=status,
                    local_checksum=(
                        local_item.payload_sha256 if local_item is not None else None
                    ),
                    remote_checksum=(
                        remote_item.payload_sha256 if remote_item is not None else None
                    ),
                )
            )
        return ReconciliationReport(tuple(items))

    def repair(
        self, report: ReconciliationReport, *, reason: str
    ) -> int:
        keys = [
            item.idempotency_key
            for item in report.items
            if item.status is ReconciliationStatus.MISSING_REMOTE
        ]
        return self.outbox.repair_publications(keys, reason=reason, now=self.clock())


@dataclass(frozen=True)
class ResearchSupabaseSettings:
    url: str
    service_role_key: str = field(repr=False)
    table: str = "research_publications"
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "ResearchSupabaseSettings":
        env = os.environ if environ is None else environ
        url = env.get("RESEARCH_SUPABASE_URL", "").strip()
        key = env.get("RESEARCH_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise ValueError(
                "RESEARCH_SUPABASE_URL and RESEARCH_SUPABASE_SERVICE_ROLE_KEY "
                "are required for trusted local research publication"
            )
        if env.get("NEXT_PUBLIC_RESEARCH_SUPABASE_SERVICE_ROLE_KEY"):
            raise ValueError("Service-role credentials must never use NEXT_PUBLIC_*")
        for live_name in (
            "SUPABASE_SERVICE_ROLE_KEY",
            "DASHBOARD_SUPABASE_SERVICE_ROLE_KEY",
            "TRADING_SUPABASE_SERVICE_ROLE_KEY",
        ):
            if env.get(live_name) and env[live_name] == key:
                raise ValueError(
                    "Research Supabase credentials must be separate from "
                    f"live-trading credentials ({live_name})"
                )
        return cls(
            url=url,
            service_role_key=key,
            table=env.get("RESEARCH_SUPABASE_TABLE", "research_publications"),
            timeout_seconds=float(
                env.get("RESEARCH_SUPABASE_TIMEOUT_SECONDS", "10")
            ),
        )


class ResearchHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout_seconds: float,
    ) -> "ResearchHttpResponse": ...


@dataclass(frozen=True)
class ResearchHttpResponse:
    status: int
    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)


class AiohttpResearchTransport:
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout_seconds: float,
    ) -> ResearchHttpResponse:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError(
                "aiohttp is required for Supabase research publication"
            ) from exc
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                url,
                headers=dict(headers),
                params=dict(params or {}),
                json=json_body,
            ) as response:
                text = await response.text()
                try:
                    body = json.loads(text) if text else None
                except json.JSONDecodeError:
                    body = text
                return ResearchHttpResponse(
                    status=response.status,
                    body=body,
                    headers=dict(response.headers),
                )


class SupabaseResearchDestination:
    """Service-role Supabase adapter for trusted local Python only."""

    def __init__(
        self,
        settings: ResearchSupabaseSettings,
        *,
        transport: Optional[ResearchHttpTransport] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.settings = settings
        self.transport = transport or AiohttpResearchTransport()
        self.clock = clock

    def _headers(self) -> dict[str, str]:
        service_role_key = self.settings.service_role_key
        return {
            "apikey": service_role_key,
            "Authorization": "Bearer " + service_role_key,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        }

    async def _request(self, *args: Any, **kwargs: Any) -> ResearchHttpResponse:
        try:
            return await self.transport.request(*args, **kwargs)
        except Exception as exc:
            safe = redact_secrets(exc, (self.settings.service_role_key,))
            raise RuntimeError(f"Supabase research transport failed: {safe}") from None
        return {
            "apikey": self.settings.service_role_key,
            "Authorization": f"Bearer {self.settings.service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        }

    async def publish(
        self, publication: ResearchPublication
    ) -> RemoteAcknowledgement:
        endpoint = f"{self.settings.url.rstrip('/')}/rest/v1/{self.settings.table}"
        body = {
            "idempotency_key": publication.idempotency_key,
            "aggregate_type": publication.kind.value,
            "aggregate_id": publication.aggregate_id,
            "schema_version": publication.schema_version,
            "publication_version": publication.publication_version,
            "payload": publication.payload,
            "payload_sha256": publication.payload_sha256,
            "acknowledged_at": self.clock().isoformat(),
        }
        response = await self._request(
            "POST",
            endpoint,
            headers=self._headers(),
            params={"on_conflict": "idempotency_key"},
            json_body=body,
            timeout_seconds=self.settings.timeout_seconds,
        )
        if not 200 <= response.status < 300:
            safe = redact_secrets(
                response.body, (self.settings.service_role_key,)
            )
            raise RuntimeError(
                "Supabase research publish failed: "
                f"status={response.status} body={safe}"
            )
        body = response.body
        row = body[0] if isinstance(body, list) and body else body
        if not isinstance(row, Mapping):
            raise RuntimeError("Supabase research publish returned no acknowledgement")
        return RemoteAcknowledgement(
            idempotency_key=str(row["idempotency_key"]),
            payload_sha256=str(row["payload_sha256"]),
            publication_version=int(row["publication_version"]),
            acknowledged_at=str(row["acknowledged_at"]),
        )

    async def list_state(self) -> Sequence[RemoteRecordState]:
        endpoint = f"{self.settings.url.rstrip('/')}/rest/v1/{self.settings.table}"
        page_size = 1000
        offset = 0
        records: list[RemoteRecordState] = []
        while True:
            response = await self._request(
                "GET",
                endpoint,
                headers={
                    **self._headers(),
                    "Range": f"{offset}-{offset + page_size - 1}",
                    "Prefer": "count=exact",
                },
                params={
                    "select": (
                        "idempotency_key,payload_sha256,publication_version"
                    ),
                    "order": "idempotency_key.asc",
                },
                timeout_seconds=self.settings.timeout_seconds,
            )
            if not 200 <= response.status < 300:
                safe = redact_secrets(
                    response.body, (self.settings.service_role_key,)
                )
                raise RuntimeError(
                    "Supabase research reconciliation failed: "
                    f"status={response.status} body={safe}"
                )
            if not isinstance(response.body, list):
                raise RuntimeError(
                    "Supabase research state response must be a list"
                )
            page = [
                RemoteRecordState(
                    idempotency_key=str(row["idempotency_key"]),
                    payload_sha256=str(row["payload_sha256"]),
                    publication_version=int(row["publication_version"]),
                )
                for row in response.body
            ]
            records.extend(page)
            content_range = (
                response.headers.get("Content-Range")
                or response.headers.get("content-range")
            )
            total = self._content_range_total(content_range)
            offset += len(page)
            if (
                not page
                or len(page) < page_size
                or (total is not None and offset >= total)
            ):
                break
        return tuple(records)

    @staticmethod
    def _content_range_total(content_range: Optional[str]) -> Optional[int]:
        if not content_range or "/" not in content_range:
            return None
        total = content_range.rsplit("/", 1)[1]
        return None if total == "*" else int(total)
