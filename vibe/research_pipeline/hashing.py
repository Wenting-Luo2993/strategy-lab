"""Deterministic canonical encoding and hashing for research run identity.

Every identity in the research pipeline (run fingerprints, split manifests,
universe definitions) is a SHA-256 over a *canonical* encoding of a Python
object. "Canonical" means two semantically equal objects always produce
byte-identical output, regardless of dict insertion order, float formatting,
or timezone representation.

This module is the single source of truth for that encoding. Nothing else in
the pipeline may hand-roll a hash, because divergent encodings silently break
cache correctness and reproducibility claims.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any

__all__ = [
    "CANONICAL_ENCODING_VERSION",
    "canonical_encode",
    "canonical_json",
    "sha256_hex",
    "hash_object",
    "hash_file",
    "NonCanonicalValueError",
]

# Bump when the encoding rules below change in a way that alters output bytes.
# Stored alongside hashes so old hashes can be recognized as incomparable.
CANONICAL_ENCODING_VERSION = 1

_FLOAT_FORMAT = ".12g"


class NonCanonicalValueError(TypeError):
    """Raised when a value has no deterministic canonical representation."""


def _encode_float(value: float) -> str:
    """Format a float deterministically.

    NaN and infinities are rejected: they are almost always a symptom of a
    broken metric calculation, and silently hashing them would let a corrupt
    run masquerade as a valid identity.
    """
    if math.isnan(value) or math.isinf(value):
        raise NonCanonicalValueError(
            f"Refusing to hash non-finite float {value!r}. "
            "A non-finite value in an identity payload indicates a "
            "calculation defect, not a valid run."
        )
    if value == 0.0:
        value = 0.0  # normalize -0.0 so sign of zero cannot split identities
    return format(value, _FLOAT_FORMAT)


def _encode_datetime(value: datetime) -> str:
    """Encode a datetime as UTC ISO-8601 with explicit offset.

    Naive datetimes are rejected. The repository has an explicit
    timezone-awareness rule (ADR-002), and a naive timestamp inside an identity
    payload means the same instant can hash two different ways depending on the
    machine's local timezone, which is exactly the cross-device failure this
    pipeline exists to eliminate.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NonCanonicalValueError(
            f"Refusing to hash naive datetime {value!r}. "
            "Attach a timezone (UTC preferred) before hashing."
        )
    return value.astimezone(timezone.utc).isoformat()


def canonical_encode(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-safe canonical primitives.

    Ordering rules:
      * ``dict`` keys are sorted after being coerced to ``str``.
      * ``list`` and ``tuple`` preserve order (order is meaningful).
      * ``set`` and ``frozenset`` are sorted by encoded representation,
        because set iteration order is not stable across processes.
    """
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, Enum):
        return canonical_encode(value.value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return _encode_float(value)

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise NonCanonicalValueError(
                f"Refusing to hash non-finite Decimal {value!r}."
            )
        return format(value.normalize(), "f")

    if isinstance(value, str):
        return value

    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()

    if isinstance(value, datetime):
        return _encode_datetime(value)

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, PurePath):
        # Always POSIX-style: the same repo checked out on Windows and Linux
        # must produce the same hash.
        return value.as_posix()

    if isinstance(value, dict):
        encoded: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = raw_key.value if isinstance(raw_key, Enum) else raw_key
            if not isinstance(key, str):
                key = str(key)
            if key in encoded:
                raise NonCanonicalValueError(
                    f"Duplicate key {key!r} after canonical key coercion; "
                    "identity would be ambiguous."
                )
            encoded[key] = canonical_encode(raw_val)
        return {k: encoded[k] for k in sorted(encoded)}

    if isinstance(value, (list, tuple)):
        return [canonical_encode(item) for item in value]

    if isinstance(value, (set, frozenset)):
        items = [canonical_encode(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))

    # Pydantic models expose their own dict conversion; use it rather than
    # reaching into __dict__, so field aliases and exclusions apply.
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return canonical_encode(dump(mode="python"))

    raise NonCanonicalValueError(
        f"No canonical encoding for type {type(value).__name__!r}. "
        "Add an explicit rule rather than relying on repr()."
    )


def canonical_json(value: Any) -> str:
    """Return the canonical JSON string for ``value``."""
    return json.dumps(
        canonical_encode(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(payload: str | bytes) -> str:
    """SHA-256 of ``payload`` as lowercase hex."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def hash_object(value: Any) -> str:
    """Canonical SHA-256 of an arbitrary Python object."""
    return sha256_hex(canonical_json(value))


def hash_file(path: str | PurePath, *, chunk_size: int = 1 << 20) -> str:
    """Stream a file through SHA-256.

    Used for ruleset content hashing and trade/equity ledger checksums, where
    the file may be too large to hold in memory.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
