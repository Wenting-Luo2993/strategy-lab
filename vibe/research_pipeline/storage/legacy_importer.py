"""Lossless importer for the legacy YAML research registry.

P7 deliberately imports the old tree before P11 replaces its public API.  The
canonical payload is retained in full, while indexed relationships, artifact
locations, and contaminated absolute metrics are projected into audit tables.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping, Optional

import yaml

from vibe.research_pipeline.hashing import canonical_json, sha256_hex
from vibe.research_pipeline.storage import schema

LEGACY_METHODOLOGY_VERSION = "legacy-uncontrolled"

_RECORD_FOLDERS = {
    "hypothesis": ("hypotheses", "HYP-*", ".yaml"),
    "experiment": ("experiments", "EXP-*", ".yaml"),
    "note": ("notes", "NOTE-*", ".md"),
    "rejected_idea": ("rejected", "RJ-*", ".yaml"),
    "artifact": ("artifacts", "ART-*", ".yaml"),
}
_TERMINAL_EXPERIMENT_STATUSES = frozenset(
    {"completed", "failed", "superseded", "archived"}
)
_ABSOLUTE_METRIC_TERMS = frozenset(
    {
        "capital",
        "cash",
        "commission",
        "cost",
        "dollar",
        "drawdown",
        "equity",
        "pnl",
        "profit",
        "return",
        "slippage",
    }
)


class LegacyImportError(RuntimeError):
    """Raised when importing would lose or silently change legacy data."""


class LegacyParityError(LegacyImportError):
    """Raised when source records and imported rows do not have exact parity."""


@dataclass(frozen=True)
class LegacyRecord:
    record_type: str
    legacy_id: str
    payload: dict[str, Any]
    canonical_payload: str
    canonical_sha256: str
    source_path: str


@dataclass(frozen=True)
class ImportVerification:
    source_counts: dict[str, int]
    database_counts: dict[str, int]
    record_count: int
    relationship_count: int
    terminal_experiment_count: int
    canonical_tree_sha256: str


def _parse_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, re.DOTALL)
    if match is None:
        # Two early notes predate the frontmatter contract. Preserve their
        # Markdown byte-for-byte (apart from newline normalization performed by
        # text decoding) instead of rejecting or heuristically restructuring it.
        return {
            "id": path.stem,
            "legacy_format": "markdown",
            "content": text.rstrip("\r\n"),
        }
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        raise LegacyImportError(f"Note frontmatter is not a mapping in {path}")
    return {**frontmatter, "content": match.group(2).rstrip("\r\n")}


def _load_payload(path: Path, record_type: str) -> dict[str, Any]:
    if record_type == "note":
        return _parse_note(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LegacyImportError(f"Legacy record is not a mapping: {path}")
    return payload


def load_legacy_records(research_root: Path) -> list[LegacyRecord]:
    """Load every supported legacy record in deterministic type/ID order."""
    root = Path(research_root)
    records: list[LegacyRecord] = []
    for record_type, (folder, pattern, suffix) in _RECORD_FOLDERS.items():
        for path in sorted((root / folder).glob(f"{pattern}{suffix}")):
            payload = _load_payload(path, record_type)
            legacy_id = payload.get("id")
            if not isinstance(legacy_id, str) or legacy_id != path.stem:
                raise LegacyImportError(
                    f"Record ID/path mismatch for {path}: {legacy_id!r}"
                )
            encoded = canonical_json(payload)
            records.append(
                LegacyRecord(
                    record_type=record_type,
                    legacy_id=legacy_id,
                    payload=payload,
                    canonical_payload=encoded,
                    canonical_sha256=sha256_hex(encoded),
                    source_path=path.relative_to(root).as_posix(),
                )
            )
    return records


def _relationships(record: LegacyRecord) -> list[tuple[str, str, str, int]]:
    payload = record.payload
    result: list[tuple[str, str, str, int]] = []

    def add(relation: str, target_type: str, target_id: Any, ordinal: int = 0) -> None:
        if isinstance(target_id, str) and target_id:
            result.append((relation, target_type, target_id, ordinal))

    if record.record_type == "experiment":
        add("hypothesis", "hypothesis", payload.get("hypothesis_id"))
        add("parent", "experiment", payload.get("parent_experiment_id"))
        for index, value in enumerate(payload.get("artifacts") or []):
            if isinstance(value, str) and re.fullmatch(r"ART-\d{3,}", value):
                add("artifact", "artifact", value, index)
    elif record.record_type == "note":
        add("experiment", "experiment", payload.get("related_experiment_id"))
    elif record.record_type == "rejected_idea":
        for index, value in enumerate(payload.get("evidence") or []):
            add("evidence", "experiment", value, index)
    elif record.record_type == "artifact":
        add("experiment", "experiment", payload.get("experiment_id"))
    return result


def _is_absolute_metric(metric_key: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", metric_key.lower()))
    if "profit" in tokens and "factor" in tokens:
        return False
    return bool(tokens & _ABSOLUTE_METRIC_TERMS)


def _numeric_metrics(
    value: Any, prefix: str = ""
) -> Iterable[tuple[str, float, bool]]:
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _numeric_metrics(value[key], path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield prefix, float(value), _is_absolute_metric(prefix)


def _normalize_artifact_location(
    location: str, repository_root: Path
) -> tuple[Optional[str], Optional[str]]:
    original = location
    windows_path = PureWindowsPath(location)
    is_windows_absolute = windows_path.is_absolute()
    native_path = Path(location)

    if not is_windows_absolute and not native_path.is_absolute():
        normalized = location.replace("\\", "/").lstrip("./")
        return normalized, None

    if native_path.is_absolute():
        try:
            relative = native_path.resolve().relative_to(repository_root.resolve())
            return relative.as_posix(), None
        except ValueError:
            pass

    # Legacy records may point to an older checkout.  A path below a directory
    # with the same repository name can still be normalized deterministically.
    parts = list(windows_path.parts if is_windows_absolute else native_path.parts)
    repository_names = {repository_root.name.lower()}
    git_marker = repository_root / ".git"
    if git_marker.is_file():
        marker = git_marker.read_text(encoding="utf-8").strip()
        if marker.lower().startswith("gitdir:"):
            git_dir = Path(marker.split(":", 1)[1].strip())
            # <main checkout>/.git/worktrees/<worktree> -> main checkout name
            if git_dir.parent.name == "worktrees":
                repository_names.add(git_dir.parent.parent.parent.name.lower())

    matches = [
        index
        for index, part in enumerate(parts)
        if str(part).lower() in repository_names
    ]
    if matches and matches[-1] + 1 < len(parts):
        return "/".join(str(part) for part in parts[matches[-1] + 1 :]), None

    if is_windows_absolute:
        return None, windows_path.as_uri()
    return None, native_path.as_uri()


def _artifact_locations(record: LegacyRecord) -> list[str]:
    if record.record_type == "artifact":
        location = record.payload.get("path")
        return [location] if isinstance(location, str) and location else []
    if record.record_type == "experiment":
        return [
            value
            for value in (record.payload.get("artifacts") or [])
            if isinstance(value, str) and not re.fullmatch(r"ART-\d{3,}", value)
        ]
    return []


def _record_status(record: LegacyRecord) -> Optional[str]:
    status = record.payload.get("status")
    return str(status) if status is not None else None


def _git_dirty(record: LegacyRecord) -> bool:
    metadata = record.payload.get("execution_metadata")
    return bool(metadata.get("git_dirty")) if isinstance(metadata, dict) else False


def _write_record(
    conn: sqlite3.Connection,
    record: LegacyRecord,
    *,
    repository_root: Path,
    strict_existing: bool,
    now: str,
) -> None:
    existing = conn.execute(
        "SELECT canonical_sha256, status FROM registry_records "
        "WHERE record_type = ? AND legacy_id = ?",
        (record.record_type, record.legacy_id),
    ).fetchone()
    if existing is not None and existing["canonical_sha256"] != record.canonical_sha256:
        if strict_existing or existing["status"] in _TERMINAL_EXPERIMENT_STATUSES:
            raise LegacyParityError(
                f"Existing SQLite row differs from {record.source_path}: "
                f"{existing['canonical_sha256']} != {record.canonical_sha256}"
            )

    if existing is None:
        conn.execute(
            """
            INSERT INTO registry_records (
                record_type, legacy_id, payload_json, canonical_sha256,
                methodology_version, status, git_dirty, source_path, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_type,
                record.legacy_id,
                record.canonical_payload,
                record.canonical_sha256,
                LEGACY_METHODOLOGY_VERSION,
                _record_status(record),
                int(_git_dirty(record)),
                record.source_path,
                now,
            ),
        )
    elif existing["canonical_sha256"] == record.canonical_sha256:
        return
    else:
        conn.execute(
            """
            UPDATE registry_records
            SET payload_json = ?, canonical_sha256 = ?, status = ?,
                git_dirty = ?, source_path = ?, imported_at = ?
            WHERE record_type = ? AND legacy_id = ?
            """,
            (
                record.canonical_payload,
                record.canonical_sha256,
                _record_status(record),
                int(_git_dirty(record)),
                record.source_path,
                now,
                record.record_type,
                record.legacy_id,
            ),
        )
        conn.execute(
            "DELETE FROM registry_relationships "
            "WHERE record_type = ? AND legacy_id = ?",
            (record.record_type, record.legacy_id),
        )
        conn.execute(
            "DELETE FROM registry_artifact_locations "
            "WHERE record_type = ? AND legacy_id = ?",
            (record.record_type, record.legacy_id),
        )
        if record.record_type == "experiment":
            conn.execute(
                "DELETE FROM legacy_metric_values WHERE experiment_id = ?",
                (record.legacy_id,),
            )

    for relation, target_type, target_id, ordinal in _relationships(record):
        resolved = conn.execute(
            "SELECT 1 FROM registry_records WHERE record_type = ? AND legacy_id = ?",
            (target_type, target_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO registry_relationships (
                record_type, legacy_id, relation, target_type, target_id,
                ordinal, resolved
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_type,
                record.legacy_id,
                relation,
                target_type,
                target_id,
                ordinal,
                int(resolved is not None),
            ),
        )

    for ordinal, location in enumerate(_artifact_locations(record)):
        repository_path, external_uri = _normalize_artifact_location(
            location, repository_root
        )
        conn.execute(
            """
            INSERT INTO registry_artifact_locations (
                record_type, legacy_id, ordinal, original_location,
                repository_path, external_uri
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_type,
                record.legacy_id,
                ordinal,
                location,
                repository_path,
                external_uri,
            ),
        )

    if record.record_type == "experiment":
        for key, value, is_absolute in _numeric_metrics(
            record.payload.get("results_summary") or {}
        ):
            conn.execute(
                """
                INSERT INTO legacy_metric_values (
                    experiment_id, metric_key, original_value,
                    effective_value, baseline_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.legacy_id,
                    key,
                    value,
                    None if is_absolute else value,
                    "requires-rebaseline" if is_absolute else "legacy-uncontrolled",
                ),
            )


def import_legacy_tree(
    research_root: Path,
    conn: sqlite3.Connection,
    *,
    repository_root: Optional[Path] = None,
) -> ImportVerification:
    """Import a complete YAML tree atomically, then verify exact parity."""
    schema.configure_connection(conn)
    schema.migrate(conn)
    root = Path(research_root)
    repo_root = Path(repository_root) if repository_root else root.parent
    records = load_legacy_records(root)
    now = schema.utc_now_iso()

    conn.execute("BEGIN IMMEDIATE")
    try:
        for record in records:
            _write_record(
                conn,
                record,
                repository_root=repo_root,
                strict_existing=True,
                now=now,
            )
        # Relationships to records imported later in the transaction become
        # resolved only after the complete tree is present.
        conn.execute(
            """
            UPDATE registry_relationships AS rel
            SET resolved = EXISTS (
                SELECT 1 FROM registry_records AS target
                WHERE target.record_type = rel.target_type
                  AND target.legacy_id = rel.target_id
            )
            """
        )
        verification = verify_legacy_import(root, conn)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    return verification


def upsert_registry_payload(
    conn: sqlite3.Connection,
    *,
    record_type: str,
    payload: Mapping[str, Any],
    source_path: str,
    repository_root: Path,
) -> str:
    """Dual-write one newly created compatibility-registry payload."""
    legacy_id = payload.get("id")
    if not isinstance(legacy_id, str):
        raise LegacyImportError("Registry payload has no string id")
    encoded = canonical_json(dict(payload))
    record = LegacyRecord(
        record_type=record_type,
        legacy_id=legacy_id,
        payload=dict(payload),
        canonical_payload=encoded,
        canonical_sha256=sha256_hex(encoded),
        source_path=source_path,
    )
    with conn:
        _write_record(
            conn,
            record,
            repository_root=Path(repository_root),
            strict_existing=False,
            now=schema.utc_now_iso(),
        )
        conn.execute(
            """
            UPDATE registry_relationships AS rel
            SET resolved = EXISTS (
                SELECT 1 FROM registry_records AS target
                WHERE target.record_type = rel.target_type
                  AND target.legacy_id = rel.target_id
            )
            """
        )
    return record.canonical_sha256


def verify_legacy_import(
    research_root: Path, conn: sqlite3.Connection
) -> ImportVerification:
    """Verify count, ID, hash, relationship, and terminal-state parity."""
    records = load_legacy_records(Path(research_root))
    expected = {(record.record_type, record.legacy_id): record for record in records}
    expected_counts = Counter(record.record_type for record in records)

    placeholders = ",".join("(?, ?)" for _ in expected)
    params = [value for key in expected for value in key]
    rows = (
        conn.execute(
            "SELECT record_type, legacy_id, canonical_sha256, status "
            f"FROM registry_records WHERE (record_type, legacy_id) IN ({placeholders})",
            params,
        ).fetchall()
        if expected
        else []
    )
    actual = {(row["record_type"], row["legacy_id"]): row for row in rows}
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise LegacyParityError(f"Record ID parity failed; missing={missing}, extra={extra}")

    for key, record in expected.items():
        if actual[key]["canonical_sha256"] != record.canonical_sha256:
            raise LegacyParityError(f"Canonical hash parity failed for {record.source_path}")
        if actual[key]["status"] != _record_status(record):
            raise LegacyParityError(f"Status parity failed for {record.source_path}")

    expected_relationships = {
        (record.record_type, record.legacy_id, *relationship)
        for record in records
        for relationship in _relationships(record)
    }
    db_relationships = {
        (
            row["record_type"],
            row["legacy_id"],
            row["relation"],
            row["target_type"],
            row["target_id"],
            row["ordinal"],
        )
        for row in conn.execute(
            "SELECT record_type, legacy_id, relation, target_type, target_id, ordinal "
            "FROM registry_relationships"
        )
        if (row["record_type"], row["legacy_id"]) in expected
    }
    if db_relationships != expected_relationships:
        raise LegacyParityError("Relationship parity failed")

    terminal_count = sum(
        record.record_type == "experiment"
        and _record_status(record) in _TERMINAL_EXPERIMENT_STATUSES
        for record in records
    )
    database_counts = Counter(row["record_type"] for row in rows)
    tree_hash = sha256_hex(
        canonical_json(
            [
                [record.record_type, record.legacy_id, record.canonical_sha256]
                for record in records
            ]
        )
    )
    return ImportVerification(
        source_counts=dict(sorted(expected_counts.items())),
        database_counts=dict(sorted(database_counts.items())),
        record_count=len(records),
        relationship_count=len(expected_relationships),
        terminal_experiment_count=terminal_count,
        canonical_tree_sha256=tree_hash,
    )
