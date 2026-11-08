"""Opt-in, content-addressed publication of safe research artifacts.

Artifact bytes are staged locally before any remote operation. The policy
rejects datasets, execution-level records, secrets, external paths, and files
that cannot be previewed safely. Supabase access is supplied through a protocol
so tests and local workflows never require network access or credentials.
"""

from __future__ import annotations

import csv
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Optional, Protocol, Sequence

from vibe.research_pipeline.hashing import canonical_json, hash_file, sha256_hex
from vibe.research_pipeline.publishing import BackoffPolicy, redact_secrets
from vibe.research_pipeline.paths import artifact_staging_path
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.store import StoreError

ARTIFACT_MANIFEST_VERSION = 1
ARTIFACT_SANITIZER_VERSION = 1


class ArtifactClass(str, Enum):
    IMAGE = "image"
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"
    TEXT = "text"
    PDF = "pdf"


class PreviewPolicy(str, Enum):
    INLINE = "inline"
    DOWNLOAD = "download"
    METADATA_ONLY = "metadata_only"
    BLOCKED = "blocked"


class ArtifactState(str, Enum):
    STAGED = "staged"
    PUBLISHING = "publishing"
    OBJECT_STORED = "object_stored"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class ArtifactFormat:
    artifact_class: ArtifactClass
    media_type: str
    maximum_bytes: int
    default_preview_policy: PreviewPolicy


SAFE_FORMATS: Mapping[str, ArtifactFormat] = {
    ".png": ArtifactFormat(ArtifactClass.IMAGE, "image/png", 5_000_000, PreviewPolicy.INLINE),
    ".jpg": ArtifactFormat(ArtifactClass.IMAGE, "image/jpeg", 5_000_000, PreviewPolicy.INLINE),
    ".jpeg": ArtifactFormat(ArtifactClass.IMAGE, "image/jpeg", 5_000_000, PreviewPolicy.INLINE),
    ".gif": ArtifactFormat(ArtifactClass.IMAGE, "image/gif", 5_000_000, PreviewPolicy.INLINE),
    ".webp": ArtifactFormat(ArtifactClass.IMAGE, "image/webp", 5_000_000, PreviewPolicy.INLINE),
    ".md": ArtifactFormat(ArtifactClass.MARKDOWN, "text/markdown", 1_000_000, PreviewPolicy.INLINE),
    ".csv": ArtifactFormat(ArtifactClass.CSV, "text/csv", 1_000_000, PreviewPolicy.INLINE),
    ".json": ArtifactFormat(ArtifactClass.JSON, "application/json", 1_000_000, PreviewPolicy.INLINE),
    ".txt": ArtifactFormat(ArtifactClass.TEXT, "text/plain", 1_000_000, PreviewPolicy.INLINE),
    ".log": ArtifactFormat(ArtifactClass.TEXT, "text/plain", 1_000_000, PreviewPolicy.INLINE),
    ".yaml": ArtifactFormat(ArtifactClass.TEXT, "application/yaml", 1_000_000, PreviewPolicy.INLINE),
    ".yml": ArtifactFormat(ArtifactClass.TEXT, "application/yaml", 1_000_000, PreviewPolicy.INLINE),
    ".xml": ArtifactFormat(ArtifactClass.TEXT, "text/plain", 1_000_000, PreviewPolicy.INLINE),
    ".html": ArtifactFormat(ArtifactClass.TEXT, "text/plain", 1_000_000, PreviewPolicy.INLINE),
    ".pdf": ArtifactFormat(ArtifactClass.PDF, "application/pdf", 10_000_000, PreviewPolicy.DOWNLOAD),
}

_FORBIDDEN_EXTENSIONS = frozenset(
    {
        ".arrow", ".db", ".db3", ".duckdb", ".feather", ".parquet",
        ".sqlite", ".sqlite3", ".zst",
    }
)
_FORBIDDEN_NAME = re.compile(
    r"(?i)(?:^|[._-])(secret|credential|service[_-]?role|api[_-]?key|"
    r"webhook|dataset|bars?|fills?|ledger|trades?)(?:[._-]|$)"
)
_SECRET_CONTENT = (
    re.compile(rb"(?i)(authorization|api[_-]?key|secret|password|credential|"
               rb"service[_-]?role|webhook)\s*[:=]\s*[^\s,;]{3,}"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
               rb"[A-Za-z0-9_-]{10,}\b"),
)
_EVENT_KEYS = frozenset(
    {
        "bar", "bars", "fill", "fills", "trade", "trades", "ledger",
        "event", "events", "equitypoint", "equitypoints", "pricebar",
        "pricebars", "rawbar", "rawbars", "order", "orders",
    }
)
_ROW_ID_KEYS = frozenset(
    {"barid", "fillid", "tradeid", "eventid", "orderid", "executionid"}
)
_OHLCV_KEYS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})
_SAFE_NAME_CHARACTER = re.compile(r"[^A-Za-z0-9._-]+")


def _normalized_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def sanitize_display_name(name: str) -> str:
    normalized = _SAFE_NAME_CHARACTER.sub("-", Path(name).name).strip(".-_")
    if not normalized:
        normalized = "artifact"
    stem, suffix = os.path.splitext(normalized)
    stem = stem[:100] or "artifact"
    suffix = suffix[:16].lower()
    return f"{stem}{suffix}"


def resolve_repository_artifact(repository_root: Path, repository_path: str) -> Path:
    """Resolve a registry reference beneath the checkout after symlink resolution."""
    if not repository_path or "\x00" in repository_path:
        raise StoreError("Artifact path must be a non-empty repository-relative path")
    relative = Path(repository_path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise StoreError("Artifact path must be repository-relative without traversal")
    root = repository_root.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise StoreError("Artifact path escapes the repository after symlink resolution") from None
    if not candidate.is_file():
        raise StoreError("Artifact reference must resolve to a regular file")
    return candidate


@dataclass(frozen=True)
class ArtifactPublicationRequest:
    owning_publication_key: str
    repository_path: str
    source_registry_checksum: str
    preview_policy: Optional[PreviewPolicy] = None

    def __post_init__(self) -> None:
        if not self.owning_publication_key.strip():
            raise ValueError("owning_publication_key must be non-empty")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_registry_checksum):
            raise ValueError("source_registry_checksum must be a lowercase SHA-256")


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    owning_publication_key: str
    sha256: str
    size_bytes: int
    media_type: str
    display_name: str
    artifact_class: ArtifactClass
    object_key: str
    preview_policy: PreviewPolicy
    sanitizer_version: int
    manifest_version: int
    source_registry_checksum: str

    @property
    def manifest_sha256(self) -> str:
        return sha256_hex(canonical_json(asdict(self)))


@dataclass(frozen=True)
class StagedArtifact:
    manifest: ArtifactManifest
    staging_path: Path


class ArtifactPolicy:
    """Strict publication policy; registry references are never implicit opt-in."""

    def inspect(
        self, request: ArtifactPublicationRequest, repository_root: Path
    ) -> tuple[Path, ArtifactFormat, int]:
        path = resolve_repository_artifact(repository_root, request.repository_path)
        artifact_format = self.classify(path, request.preview_policy)
        size = self.validate_staged_copy(path, artifact_format)
        return path, artifact_format, size

    def classify(
        self, path: Path, preview_policy: Optional[PreviewPolicy]
    ) -> ArtifactFormat:
        suffix = path.suffix.lower()
        if suffix in _FORBIDDEN_EXTENSIONS:
            raise StoreError(f"Artifact type {suffix!r} is forbidden")
        artifact_format = SAFE_FORMATS.get(suffix)
        if artifact_format is None:
            raise StoreError(f"Artifact type {suffix or '<none>'!r} is not allowlisted")
        if _FORBIDDEN_NAME.search(path.name):
            raise StoreError("Artifact filename indicates forbidden data or credentials")
        requested = preview_policy or artifact_format.default_preview_policy
        if artifact_format.artifact_class is ArtifactClass.PDF and requested is PreviewPolicy.INLINE:
            raise StoreError("PDF artifacts cannot use inline preview")
        if requested is PreviewPolicy.BLOCKED:
            raise StoreError("Blocked artifacts cannot be staged for publication")
        return artifact_format

    def validate_staged_copy(
        self, path: Path, artifact_format: ArtifactFormat
    ) -> int:
        size = path.stat().st_size
        if size <= 0 or size > artifact_format.maximum_bytes:
            raise StoreError(
                f"Artifact size {size} is outside the allowed range for {path.suffix.lower()}"
            )
        self._validate_signature(path, artifact_format)
        self._scan_content(path, artifact_format)
        return size

    @staticmethod
    def _validate_signature(path: Path, artifact_format: ArtifactFormat) -> None:
        if artifact_format.artifact_class not in {ArtifactClass.IMAGE, ArtifactClass.PDF}:
            return
        with path.open("rb") as handle:
            prefix = handle.read(16)
        signatures = {
            "image/png": (b"\x89PNG\r\n\x1a\n",),
            "image/jpeg": (b"\xff\xd8\xff",),
            "image/gif": (b"GIF87a", b"GIF89a"),
            "image/webp": (b"RIFF",),
            "application/pdf": (b"%PDF-",),
        }
        if not any(prefix.startswith(signature) for signature in signatures[artifact_format.media_type]):
            raise StoreError(
                f"Artifact bytes do not match {artifact_format.media_type}"
            )
        if artifact_format.media_type == "image/webp" and prefix[8:12] != b"WEBP":
            raise StoreError("Artifact bytes do not match image/webp")

    def _scan_content(self, path: Path, artifact_format: ArtifactFormat) -> None:
        payload = path.read_bytes()
        if any(pattern.search(payload) for pattern in _SECRET_CONTENT):
            raise StoreError("Artifact content contains credential-shaped data")
        if artifact_format.artifact_class is ArtifactClass.JSON:
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StoreError(f"Artifact JSON is invalid: {exc}") from None
            offending = self._event_path(decoded)
            if offending:
                raise StoreError(f"Artifact contains event-level data at {offending}")
        elif artifact_format.artifact_class is ArtifactClass.CSV:
            try:
                header = next(csv.reader(payload.decode("utf-8-sig").splitlines()))
            except (UnicodeDecodeError, StopIteration, csv.Error) as exc:
                raise StoreError(f"Artifact CSV is invalid: {exc}") from None
            normalized = {_normalized_key(value) for value in header}
            if normalized & _ROW_ID_KEYS or _OHLCV_KEYS.issubset(normalized):
                raise StoreError("Artifact CSV contains event-level rows")

    @classmethod
    def _event_path(cls, value: object, trail: str = "$") -> Optional[str]:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = _normalized_key(key)
                current = f"{trail}.{key}"
                if normalized in _EVENT_KEYS or normalized in _ROW_ID_KEYS:
                    return current
                offending = cls._event_path(nested, current)
                if offending:
                    return offending
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                offending = cls._event_path(nested, f"{trail}[{index}]")
                if offending:
                    return offending
        return None


class ArtifactStager:
    def __init__(
        self,
        staging_root: Optional[Path] = None,
        *,
        policy: Optional[ArtifactPolicy] = None,
    ) -> None:
        self.staging_root = staging_root or artifact_staging_path(create_parents=True)
        self.policy = policy or ArtifactPolicy()

    def stage(
        self, request: ArtifactPublicationRequest, repository_root: Path
    ) -> StagedArtifact:
        source = resolve_repository_artifact(repository_root, request.repository_path)
        artifact_format = self.policy.classify(source, request.preview_policy)
        display_name = sanitize_display_name(source.name)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.staging_root, prefix=".artifact-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(source, flags)
            try:
                source_info = os.fstat(descriptor)
                path_info = source.lstat()
                if (
                    not stat.S_ISREG(source_info.st_mode)
                    or stat.S_ISLNK(path_info.st_mode)
                    or (
                        source_info.st_ino
                        and path_info.st_ino
                        and source_info.st_ino != path_info.st_ino
                    )
                ):
                    raise StoreError("Artifact source changed or became a symlink")
                with os.fdopen(descriptor, "rb", closefd=False) as source_handle:
                    shutil.copyfileobj(source_handle, temporary)
            finally:
                os.close(descriptor)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            size = self.policy.validate_staged_copy(temporary_path, artifact_format)
            digest = hash_file(temporary_path)
            object_key = str(PurePosixPath("v1", digest, display_name))
            preview_policy = request.preview_policy or artifact_format.default_preview_policy
            artifact_id = "artifact:" + sha256_hex(
                canonical_json(
                    {
                        "owner": request.owning_publication_key,
                        "sha256": digest,
                        "name": display_name,
                        "preview_policy": preview_policy.value,
                        "source_registry_checksum": request.source_registry_checksum,
                    }
                )
            )
            manifest = ArtifactManifest(
                artifact_id=artifact_id,
                owning_publication_key=request.owning_publication_key,
                sha256=digest,
                size_bytes=size,
                media_type=artifact_format.media_type,
                display_name=display_name,
                artifact_class=artifact_format.artifact_class,
                object_key=object_key,
                preview_policy=preview_policy,
                sanitizer_version=ARTIFACT_SANITIZER_VERSION,
                manifest_version=ARTIFACT_MANIFEST_VERSION,
                source_registry_checksum=request.source_registry_checksum,
            )
            target = self.staging_root.joinpath(*PurePosixPath(object_key).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hash_file(target) != digest:
                    raise StoreError("Content-addressed staging object has divergent bytes")
                return StagedArtifact(manifest, target)
            os.replace(temporary_path, target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)
        return StagedArtifact(manifest, target)


@dataclass(frozen=True)
class ClaimedArtifact:
    staged: StagedArtifact
    claim_token: str
    attempts: int
    object_already_stored: bool


@dataclass(frozen=True)
class RemoteArtifactState:
    artifact_id: str
    sha256: str
    object_key: str
    manifest_present: bool
    object_present: bool
    staged_at: Optional[str] = None


class ArtifactDestination(Protocol):
    async def store_object(self, artifact: StagedArtifact) -> str:
        """Store without overwrite and return the verified remote SHA-256."""

    async def publish_manifest(self, manifest: ArtifactManifest) -> RemoteArtifactState:
        """Insert immutable metadata after object verification."""

    async def list_state(self) -> Sequence[RemoteArtifactState]: ...

    async def delete_unreferenced_staged_object(
        self, object_key: str, sha256: str, *, older_than: datetime
    ) -> bool:
        """Conditionally delete an old staged object after rechecking references."""


class SqliteArtifactOutbox:
    """Durable artifact staging and claim state in the local research database."""

    def __init__(
        self, db_path: Path, staging_root: Optional[Path] = None
    ) -> None:
        self._conn = schema.connect(str(db_path))
        self.staging_root = (
            staging_root or artifact_staging_path(create_parents=True)
        ).resolve()

    def close(self) -> None:
        self._conn.close()

    def stage_and_enqueue(
        self,
        stager: ArtifactStager,
        request: ArtifactPublicationRequest,
        repository_root: Path,
    ) -> StagedArtifact:
        if stager.staging_root.resolve() != self.staging_root:
            raise ValueError("Stager and outbox must use the same staging root")
        staged = stager.stage(request, repository_root)
        self.enqueue(staged)
        return staged

    def enqueue(self, staged: StagedArtifact) -> None:
        relative = staged.staging_path.resolve().relative_to(self.staging_root).as_posix()
        manifest_json = canonical_json(asdict(staged.manifest))
        now = schema.utc_now_iso()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO artifact_publication_outbox (
                    artifact_id, owning_publication_key, object_key, staging_path,
                    manifest_json, manifest_sha256, content_sha256, size_bytes,
                    media_type, preview_policy, status, enqueued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?)
                ON CONFLICT(artifact_id) DO NOTHING
                """,
                (
                    staged.manifest.artifact_id,
                    staged.manifest.owning_publication_key,
                    staged.manifest.object_key,
                    relative,
                    manifest_json,
                    staged.manifest.manifest_sha256,
                    staged.manifest.sha256,
                    staged.manifest.size_bytes,
                    staged.manifest.media_type,
                    staged.manifest.preview_policy.value,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                row = self._conn.execute(
                    """
                    SELECT manifest_sha256 FROM artifact_publication_outbox
                    WHERE artifact_id = ?
                    """,
                    (staged.manifest.artifact_id,),
                ).fetchone()
                if row is None or row["manifest_sha256"] != staged.manifest.manifest_sha256:
                    raise StoreError("Artifact id already exists with a divergent manifest")

    def reset_stale_claims(self, *, now: datetime) -> int:
        now_iso = _utc(now).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE artifact_publication_outbox
                SET status = CASE WHEN remote_object_sha256 IS NULL
                                  THEN 'failed' ELSE 'object_stored' END,
                    claim_token = NULL, claimed_at = NULL, lease_expires_at = NULL,
                    next_attempt_at = ?, last_error = COALESCE(
                        last_error, 'artifact publisher lease expired'
                    )
                WHERE status = 'publishing' AND lease_expires_at <= ?
                """,
                (now_iso, now_iso),
            )
        return cursor.rowcount

    def claim(
        self, *, claimed_by: str, lease_seconds: int, now: datetime
    ) -> Optional[ClaimedArtifact]:
        if not claimed_by.strip() or lease_seconds <= 0:
            raise ValueError("claim owner and lease_seconds must be valid")
        instant = _utc(now)
        token = secrets.token_urlsafe(32)
        schema.begin_immediate(self._conn)
        try:
            row = self._conn.execute(
                """
                SELECT * FROM artifact_publication_outbox
                WHERE status IN ('staged', 'failed', 'object_stored')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY sequence LIMIT 1
                """,
                (instant.isoformat(),),
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            try:
                manifest = _manifest_from_json(row["manifest_json"])
                if (
                    manifest.manifest_sha256 != row["manifest_sha256"]
                    or manifest.artifact_id != row["artifact_id"]
                    or manifest.object_key != row["object_key"]
                    or manifest.sha256 != row["content_sha256"]
                    or manifest.size_bytes != row["size_bytes"]
                ):
                    raise StoreError("Artifact outbox row and manifest diverge")
                path = (self.staging_root / row["staging_path"]).resolve()
                path.relative_to(self.staging_root)
                if (
                    not path.is_file()
                    or path.stat().st_size != manifest.size_bytes
                    or hash_file(path) != manifest.sha256
                ):
                    raise StoreError("Artifact staging object is missing or corrupt")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, StoreError) as exc:
                self._conn.execute(
                    """
                    UPDATE artifact_publication_outbox
                    SET status = 'dead_letter', attempts = attempts + 1,
                        last_error = ?, updated_at = ?
                    WHERE artifact_id = ?
                    """,
                    (redact_secrets(exc), instant.isoformat(), row["artifact_id"]),
                )
                self._conn.commit()
                return None
            cursor = self._conn.execute(
                """
                UPDATE artifact_publication_outbox
                SET status = 'publishing', claimed_by = ?, claimed_at = ?,
                    lease_expires_at = ?, claim_token = ?
                WHERE artifact_id = ? AND status = ?
                """,
                (
                    claimed_by,
                    instant.isoformat(),
                    (instant + timedelta(seconds=lease_seconds)).isoformat(),
                    token,
                    row["artifact_id"],
                    row["status"],
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                return None
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return ClaimedArtifact(
            StagedArtifact(manifest, path),
            token,
            int(row["attempts"]),
            row["remote_object_sha256"] == manifest.sha256,
        )

    def mark_object_stored(
        self, claimed: ClaimedArtifact, remote_sha256: str, *, now: datetime
    ) -> bool:
        if remote_sha256 != claimed.staged.manifest.sha256:
            raise StoreError("Remote artifact checksum does not match staged bytes")
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE artifact_publication_outbox
                SET remote_object_sha256 = ?, updated_at = ?
                WHERE artifact_id = ? AND status = 'publishing'
                  AND claim_token = ?
                """,
                (
                    remote_sha256,
                    _utc(now).isoformat(),
                    claimed.staged.manifest.artifact_id,
                    claimed.claim_token,
                ),
            )
        return bool(cursor.rowcount)

    def mark_published(
        self, claimed: ClaimedArtifact, remote: RemoteArtifactState, *, now: datetime
    ) -> bool:
        manifest = claimed.staged.manifest
        if (
            remote.artifact_id != manifest.artifact_id
            or remote.sha256 != manifest.sha256
            or remote.object_key != manifest.object_key
            or not remote.object_present
            or not remote.manifest_present
        ):
            raise StoreError("Remote artifact acknowledgement is incomplete or divergent")
        now_iso = _utc(now).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE artifact_publication_outbox
                SET status = 'published', attempts = attempts + 1,
                    published_at = ?, updated_at = ?, last_error = NULL,
                    next_attempt_at = NULL, claimed_by = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, claim_token = NULL
                WHERE artifact_id = ? AND status = 'publishing'
                  AND claim_token = ?
                """,
                (now_iso, now_iso, manifest.artifact_id, claimed.claim_token),
            )
        return bool(cursor.rowcount)

    def mark_failed(
        self,
        claimed: ClaimedArtifact,
        *,
        error: str,
        dead_letter: bool,
        next_attempt_at: Optional[datetime],
        now: datetime,
    ) -> bool:
        status = ArtifactState.DEAD_LETTER if dead_letter else ArtifactState.FAILED
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE artifact_publication_outbox
                SET status = ?, attempts = attempts + 1, last_error = ?,
                    next_attempt_at = ?, updated_at = ?, claimed_by = NULL,
                    claimed_at = NULL, lease_expires_at = NULL, claim_token = NULL
                WHERE artifact_id = ? AND status = 'publishing'
                  AND claim_token = ?
                """,
                (
                    status.value,
                    redact_secrets(error),
                    _utc(next_attempt_at).isoformat() if next_attempt_at else None,
                    _utc(now).isoformat(),
                    claimed.staged.manifest.artifact_id,
                    claimed.claim_token,
                ),
            )
        return bool(cursor.rowcount)

    def local_state(self) -> tuple[RemoteArtifactState, ...]:
        rows = self._conn.execute(
            """
            SELECT artifact_id, content_sha256, object_key, status,
                   remote_object_sha256
            FROM artifact_publication_outbox ORDER BY sequence
            """
        ).fetchall()
        return tuple(
            RemoteArtifactState(
                artifact_id=row["artifact_id"],
                sha256=row["content_sha256"],
                object_key=row["object_key"],
                manifest_present=row["status"] == ArtifactState.PUBLISHED.value,
                object_present=row["remote_object_sha256"] == row["content_sha256"],
            )
            for row in rows
        )

    def unreferenced_staging_files(
        self,
        *,
        older_than: datetime,
    ) -> tuple[Path, ...]:
        cutoff = _utc(older_than).timestamp()
        referenced = {
            row["staging_path"]
            for row in self._conn.execute(
                "SELECT staging_path FROM artifact_publication_outbox"
            ).fetchall()
        }
        unreferenced: list[Path] = []
        if not self.staging_root.exists():
            return ()
        for candidate in self.staging_root.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(self.staging_root).as_posix()
            if relative not in referenced and candidate.stat().st_mtime <= cutoff:
                unreferenced.append(candidate)
        return tuple(sorted(unreferenced))

    def garbage_collect_local_staging(
        self,
        *,
        older_than: datetime,
    ) -> int:
        candidates = self.unreferenced_staging_files(older_than=older_than)
        for candidate in candidates:
            candidate.unlink()
        return len(candidates)

    def requeue_missing(
        self,
        artifact_ids: Sequence[str],
        *,
        missing_objects: Sequence[str] = (),
        now: datetime,
    ) -> int:
        changed = 0
        object_missing = set(missing_objects)
        with self._conn:
            for artifact_id in dict.fromkeys(artifact_ids):
                cursor = self._conn.execute(
                    """
                    UPDATE artifact_publication_outbox
                    SET status = CASE WHEN remote_object_sha256 IS NULL
                                           OR artifact_id IN ({})
                                      THEN 'staged' ELSE 'object_stored' END,
                        remote_object_sha256 = CASE WHEN artifact_id IN ({})
                                                   THEN NULL ELSE remote_object_sha256 END,
                        next_attempt_at = ?, updated_at = ?, published_at = NULL
                    WHERE artifact_id = ? AND status != 'dead_letter'
                    """.format(
                        ",".join("?" for _ in object_missing) or "NULL",
                        ",".join("?" for _ in object_missing) or "NULL",
                    ),
                    (
                        *object_missing,
                        *object_missing,
                        _utc(now).isoformat(),
                        _utc(now).isoformat(),
                        artifact_id,
                    ),
                )
                changed += cursor.rowcount
        return changed


class ArtifactPublisher:
    def __init__(
        self,
        outbox: SqliteArtifactOutbox,
        destination: ArtifactDestination,
        *,
        max_attempts: int = 5,
        lease_seconds: int = 120,
        claimed_by: str = "artifact-publisher",
        backoff: BackoffPolicy = BackoffPolicy(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.outbox = outbox
        self.destination = destination
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.claimed_by = claimed_by
        self.backoff = backoff
        self.clock = clock

    async def flush_once(self) -> bool:
        now = self.clock()
        self.outbox.reset_stale_claims(now=now)
        claimed = self.outbox.claim(
            claimed_by=self.claimed_by,
            lease_seconds=self.lease_seconds,
            now=now,
        )
        if claimed is None:
            return False
        try:
            if claimed.object_already_stored:
                remote_sha256 = claimed.staged.manifest.sha256
            else:
                remote_sha256 = await self.destination.store_object(claimed.staged)
                if not self.outbox.mark_object_stored(
                    claimed, remote_sha256, now=self.clock()
                ):
                    return False
            remote = await self.destination.publish_manifest(claimed.staged.manifest)
            return self.outbox.mark_published(claimed, remote, now=self.clock())
        except Exception as exc:
            attempts = claimed.attempts + 1
            dead_letter = attempts >= self.max_attempts
            retry_at = None if dead_letter else self.clock() + timedelta(
                seconds=self.backoff.delay_seconds(attempts)
            )
            self.outbox.mark_failed(
                claimed,
                error=str(exc),
                dead_letter=dead_letter,
                next_attempt_at=retry_at,
                now=self.clock(),
            )
            return False


@dataclass(frozen=True)
class ArtifactReconciliation:
    missing_manifests: tuple[str, ...]
    missing_objects: tuple[str, ...]
    divergent: tuple[str, ...]
    unreferenced_staged_objects: tuple[tuple[str, str, str], ...]


class ArtifactReconciler:
    def __init__(
        self,
        outbox: SqliteArtifactOutbox,
        destination: ArtifactDestination,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.outbox = outbox
        self.destination = destination
        self.clock = clock

    async def compare(self) -> ArtifactReconciliation:
        local = {item.artifact_id: item for item in self.outbox.local_state()}
        remote_rows = tuple(await self.destination.list_state())
        remote = {item.artifact_id: item for item in remote_rows}
        remote_objects = {
            item.object_key: item
            for item in remote_rows
            if item.object_present
        }
        missing_manifests: list[str] = []
        missing_objects: list[str] = []
        divergent: list[str] = []
        unreferenced: list[tuple[str, str, str]] = []
        for artifact_id, local_item in local.items():
            remote_item = remote.get(artifact_id)
            if remote_item is None or not remote_item.manifest_present:
                missing_manifests.append(artifact_id)
            stored_object = remote_objects.get(local_item.object_key)
            if stored_object is None:
                missing_objects.append(artifact_id)
            if (
                remote_item
                and remote_item.manifest_present
                and (
                    remote_item.sha256 != local_item.sha256
                    or remote_item.object_key != local_item.object_key
                )
            ) or (
                stored_object is not None
                and stored_object.sha256 != local_item.sha256
            ):
                divergent.append(artifact_id)
        local_object_keys = {item.object_key for item in local.values()}
        for artifact_id, remote_item in remote.items():
            if (
                artifact_id not in local
                and remote_item.object_key not in local_object_keys
                and remote_item.object_present
                and not remote_item.manifest_present
                and remote_item.staged_at is not None
            ):
                unreferenced.append(
                    (remote_item.object_key, remote_item.sha256, remote_item.staged_at)
                )
        return ArtifactReconciliation(
            tuple(sorted(missing_manifests)),
            tuple(sorted(missing_objects)),
            tuple(sorted(divergent)),
            tuple(sorted(unreferenced)),
        )

    def repair(self, report: ArtifactReconciliation) -> int:
        repairable = set(report.missing_manifests) | set(report.missing_objects)
        repairable -= set(report.divergent)
        return self.outbox.requeue_missing(
            sorted(repairable),
            missing_objects=report.missing_objects,
            now=self.clock(),
        )

    async def garbage_collect(
        self, report: ArtifactReconciliation, *, grace_period: timedelta = timedelta(hours=24)
    ) -> int:
        cutoff = self.clock() - grace_period
        deleted = 0
        for object_key, digest, staged_at in report.unreferenced_staged_objects:
            staged = datetime.fromisoformat(staged_at)
            if _utc(staged) > cutoff:
                continue
            if await self.destination.delete_unreferenced_staged_object(
                object_key, digest, older_than=cutoff
            ):
                deleted += 1
        return deleted


def _manifest_from_json(payload: str) -> ArtifactManifest:
    value = json.loads(payload)
    value["artifact_class"] = ArtifactClass(value["artifact_class"])
    value["preview_policy"] = PreviewPolicy(value["preview_policy"])
    return ArtifactManifest(**value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Artifact publication timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
