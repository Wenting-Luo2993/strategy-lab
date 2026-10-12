"""Immutable dataset identities and lifecycle metadata."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vibe.research_pipeline.hashing import canonical_json, hash_object, sha256_hex

MANIFEST_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    RETIRED = "retired"
    REVOKED = "revoked"


LEGAL_DATASET_TRANSITIONS = {
    DatasetStatus.CANDIDATE: frozenset(
        {DatasetStatus.APPROVED, DatasetStatus.REVOKED}
    ),
    DatasetStatus.APPROVED: frozenset(
        {DatasetStatus.RETIRED, DatasetStatus.REVOKED}
    ),
    DatasetStatus.RETIRED: frozenset({DatasetStatus.REVOKED}),
    DatasetStatus.REVOKED: frozenset(),
}


class ConverterMetadata(_Frozen):
    path: str = Field(..., min_length=1)
    code_commit: str = Field(..., min_length=7)
    pandas: str = Field(..., min_length=1)
    pyarrow: str = Field(..., min_length=1)
    zstandard: str = Field(..., min_length=1)
    compression: str = Field(..., min_length=1)

    @field_validator("code_commit")
    @classmethod
    def _validate_commit(cls, value: str) -> str:
        value = value.strip().lower()
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("Expected a hex git commit hash")
        return value


class DatasetValidation(_Frozen):
    null_values: int = Field(..., ge=0)
    duplicate_timestamps: int = Field(..., ge=0)
    hard_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return (
            self.null_values == 0
            and self.duplicate_timestamps == 0
            and not self.hard_errors
        )


class LicenseApproval(_Frozen):
    classification: str = Field(..., min_length=1)
    approved_by: str = ""
    approved_at: datetime | None = None
    evidence_reference: str = ""

    @field_validator("approved_at")
    @classmethod
    def _require_timezone(
        cls, value: datetime | None
    ) -> datetime | None:
        if (
            value is not None
            and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None)
        ):
            raise ValueError("License approval timestamp must be timezone-aware")
        return value

    @property
    def approved(self) -> bool:
        return bool(
            self.approved_by.strip()
            and self.approved_at is not None
            and self.evidence_reference.strip()
        )


class DatasetManifest(_Frozen):
    manifest_version: int = Field(default=MANIFEST_VERSION, ge=1)
    dataset_id: str
    status: DatasetStatus
    vendor: str = Field(..., min_length=1)
    vendor_job_id: str = Field(..., min_length=1)
    source_dataset: str = Field(..., min_length=1)
    source_schema: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    bar_interval: str = Field(..., min_length=1)
    session: str = Field(..., min_length=1)
    timezone: str = Field(..., min_length=1)
    first_timestamp: datetime
    last_timestamp: datetime
    row_count: int = Field(..., gt=0)
    trading_sessions: int = Field(..., gt=0)
    columns: tuple[str, ...]
    source_sha256: str
    source_bytes: int = Field(..., gt=0)
    object_sha256: str
    object_bytes: int = Field(..., gt=0)
    converter: ConverterMetadata
    validation: DatasetValidation
    license: LicenseApproval

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("source_sha256", "object_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Expected a lowercase hex SHA-256 digest")
        return value

    @field_validator("first_timestamp", "last_timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("Dataset timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _verify_identity_and_approval(self) -> "DatasetManifest":
        expected = self.calculate_dataset_id(self.logical_identity())
        if self.dataset_id != expected:
            raise ValueError(
                f"dataset_id mismatch: expected {expected}, got {self.dataset_id}"
            )
        if self.last_timestamp < self.first_timestamp:
            raise ValueError("last_timestamp precedes first_timestamp")
        if not self.columns:
            raise ValueError("columns must not be empty")
        if self.status is DatasetStatus.APPROVED:
            if not self.validation.valid:
                raise ValueError("Approved datasets require clean validation")
            if not self.license.approved:
                raise ValueError("Approved datasets require license approval evidence")
            versions = (
                self.converter.pandas,
                self.converter.pyarrow,
                self.converter.zstandard,
            )
            if "not-installed" in versions:
                raise ValueError(
                    "Approved datasets require installed converter versions"
                )
        return self

    def logical_identity(self) -> dict[str, Any]:
        """Identity of logical bars, independent of Parquet byte encoding."""
        return {
            "manifest_version": self.manifest_version,
            "vendor": self.vendor,
            "vendor_job_id": self.vendor_job_id,
            "source_dataset": self.source_dataset,
            "source_schema": self.source_schema,
            "symbol": self.symbol,
            "bar_interval": self.bar_interval,
            "session": self.session,
            "timezone": self.timezone,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "row_count": self.row_count,
            "trading_sessions": self.trading_sessions,
            "columns": self.columns,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
        }

    @staticmethod
    def calculate_dataset_id(identity: dict[str, Any]) -> str:
        return f"sha256:{hash_object(identity)}"

    @property
    def manifest_sha256(self) -> str:
        return sha256_hex(canonical_json(self))

    @property
    def object_key(self) -> str:
        return (
            f"v1/{self.dataset_id}/{self.symbol}/"
            f"{self.object_sha256}.parquet"
        )

    def transition(
        self,
        status: DatasetStatus,
        *,
        license: LicenseApproval | None = None,
    ) -> "DatasetManifest":
        if status not in LEGAL_DATASET_TRANSITIONS[self.status]:
            raise ValueError(
                f"Illegal dataset lifecycle transition: {self.status.value} "
                f"-> {status.value}"
            )
        if license is not None and not (
            self.status is DatasetStatus.CANDIDATE
            and status is DatasetStatus.APPROVED
        ):
            raise ValueError(
                "License evidence may only be attached when approving a candidate"
            )
        payload = self.model_dump(mode="python")
        payload["status"] = status
        if license is not None:
            payload["license"] = license
        return DatasetManifest.model_validate(payload)

    def canonical_json(self) -> str:
        return canonical_json(self)
