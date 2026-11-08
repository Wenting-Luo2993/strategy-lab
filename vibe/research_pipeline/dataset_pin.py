"""Pin the exact input bar files a study consumed.

``dataset_manifest_hash`` is a single hash over one entry per symbol file:
``{path (relative to the data root, posix), size_bytes, sha256}``. Two runs
with the same hash read byte-identical inputs; a different hash means the data
drifted (e.g. a re-downloaded parquet adding or revising bars).

Content hashing a ~20 MB parquet costs tens of milliseconds, but studies are
re-run often, so content hashes are cached keyed by ``(absolute path, size,
mtime_ns)``. The cache only skips work: a stale entry can never be served,
because any rewrite changes size or mtime. ``mtime_ns`` is recorded in each
entry for diagnosis but is deliberately *not* part of the hash -- touching a
file must not look like data drift.

The cache is written next to the research DB (never into the data directory,
which may be read-only).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from vibe.research_pipeline.hashing import hash_file, hash_object

logger = logging.getLogger(__name__)

DATASET_PIN_SCHEMA_VERSION = 1
CACHE_FILENAME = "dataset_hash_cache.json"


def _load_cache(cache_path: Optional[Path]) -> dict[str, Any]:
    if cache_path is None or not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable dataset hash cache %s", cache_path)
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache_path: Optional[Path], cache: Mapping[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError:
        logger.warning("Could not write dataset hash cache %s", cache_path)


def file_entry(
    data_dir: Path, path: Path, cache: dict[str, Any]
) -> dict[str, Any]:
    """Describe one input file, using/refreshing ``cache`` for its content hash."""
    stat = path.stat()
    key = str(path.resolve())
    cached = cache.get(key)
    if (
        isinstance(cached, dict)
        and cached.get("size_bytes") == stat.st_size
        and cached.get("mtime_ns") == stat.st_mtime_ns
        and isinstance(cached.get("sha256"), str)
    ):
        digest = cached["sha256"]
    else:
        digest = hash_file(path)
        cache[key] = {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
        }
    try:
        rel = path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError:
        rel = path.name
    return {
        "path": rel,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
    }


def manifest_hash(entries: Iterable[Mapping[str, Any]]) -> str:
    """Hash the identity-bearing fields of the entries (order-independent)."""
    return hash_object(
        {
            "schema_version": DATASET_PIN_SCHEMA_VERSION,
            "files": sorted(
                (
                    {
                        "path": e["path"],
                        "size_bytes": e["size_bytes"],
                        "sha256": e["sha256"],
                    }
                    for e in entries
                ),
                key=lambda e: e["path"],
            ),
        }
    )


def dataset_pin(
    data_dir: Path | str,
    symbols: Iterable[str],
    *,
    cache_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Return ``{"dataset_manifest_hash", "dataset_files", ...}`` for ``symbols``.

    Symbols whose ``{symbol}.parquet`` is missing are listed under
    ``missing_symbols`` (and contribute nothing to the hash); the study itself
    reports them as errors.
    """
    root = Path(data_dir)
    cache_file = Path(cache_path) if cache_path is not None else None
    cache = _load_cache(cache_file)
    before = json.dumps(cache, sort_keys=True)
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol in sorted(set(symbols)):
        path = root / f"{symbol}.parquet"
        if not path.is_file():
            missing.append(symbol)
            continue
        entries.append(file_entry(root, path, cache))
    if json.dumps(cache, sort_keys=True) != before:
        _save_cache(cache_file, cache)
    pin: dict[str, Any] = {
        "dataset_manifest_hash": manifest_hash(entries),
        "dataset_pin_schema_version": DATASET_PIN_SCHEMA_VERSION,
        "dataset_files": entries,
    }
    if missing:
        pin["dataset_missing_symbols"] = missing
    return pin


def default_cache_path(research_db: Optional[Path | str]) -> Optional[Path]:
    if research_db is None:
        return None
    return Path(research_db).resolve().parent / CACHE_FILENAME
