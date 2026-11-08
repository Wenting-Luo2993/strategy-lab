"""Verified content-addressed dataset cache and atomic materialization."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from vibe.backtester.data.paths import resolve_market_data_dir
from vibe.research_pipeline.datasets.manifest import verify_parquet, write_manifest
from vibe.research_pipeline.datasets.models import DatasetManifest, DatasetStatus


class DatasetSource(Protocol):
    def fetch_manifest(self, dataset_id: str) -> DatasetManifest: ...

    def iter_object(
        self, object_key: str, *, chunk_size: int = 1 << 20
    ) -> Iterable[bytes]: ...


def default_cache_dir() -> Path:
    override = os.environ.get("RESEARCH_DATASET_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".strategy-lab" / "research" / "datasets"


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copyfile(source, temporary_path)
        with temporary_path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


class DatasetSynchronizer:
    def __init__(
        self, source: DatasetSource, *, cache_dir: str | Path | None = None
    ) -> None:
        self.source = source
        self.cache_dir = Path(cache_dir or default_cache_dir()).expanduser()

    def sync(
        self, dataset_id: str, *, data_dir: str | Path | None = None
    ) -> Path:
        manifest = self.source.fetch_manifest(dataset_id)
        if manifest.dataset_id != dataset_id:
            raise ValueError(
                f"Requested {dataset_id}, remote returned {manifest.dataset_id}"
            )
        if manifest.status is not DatasetStatus.APPROVED:
            raise PermissionError(
                f"Dataset {dataset_id} is {manifest.status.value}; only approved "
                "datasets may be synchronized"
            )

        object_path = (
            self.cache_dir / "objects" / f"{manifest.object_sha256}.parquet"
        )
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if object_path.is_symlink():
            raise ValueError("Refusing symlink in content-addressed dataset cache")
        if object_path.exists():
            verify_parquet(object_path, manifest)
        else:
            self._download_verified(manifest, object_path)

        manifest_path = (
            self.cache_dir
            / "manifests"
            / f"{manifest.dataset_id.removeprefix('sha256:')}.json"
        )
        write_manifest(manifest_path, manifest)
        verification_path = manifest_path.with_suffix(".verified.json")
        _atomic_write_text(
            verification_path,
            json.dumps(
                {
                    "dataset_id": manifest.dataset_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "object_sha256": manifest.object_sha256,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
            )
            + "\n",
        )

        target_dir = (
            Path(data_dir)
            if data_dir is not None
            else resolve_market_data_dir(require_exists=False)
        )
        target = target_dir / f"{manifest.symbol}.parquet"
        _atomic_copy(object_path, target)
        write_manifest(target.with_suffix(".dataset.json"), manifest)
        verify_materialized_dataset(
            target_dir, symbol=manifest.symbol, dataset_id=dataset_id
        )
        return target

    def _download_verified(
        self, manifest: DatasetManifest, destination: Path
    ) -> None:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{manifest.object_sha256}.", suffix=".tmp",
            dir=destination.parent,
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in self.source.iter_object(manifest.object_key):
                    if not chunk:
                        continue
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if digest.hexdigest() != manifest.object_sha256:
                raise ValueError("Downloaded dataset object hash does not match manifest")
            if size != manifest.object_bytes:
                raise ValueError("Downloaded dataset object size does not match manifest")
            verify_parquet(temporary, manifest)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)


def verify_materialized_dataset(
    data_dir: str | Path, *, symbol: str, dataset_id: str
) -> DatasetManifest:
    directory = Path(data_dir)
    manifest_path = directory / f"{symbol.upper()}.dataset.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"No verified dataset manifest beside {symbol.upper()}.parquet"
        )
    manifest = DatasetManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    if manifest.dataset_id != dataset_id:
        raise ValueError(
            f"Run fingerprint expects {dataset_id}, but materialized "
            f"{symbol.upper()} is {manifest.dataset_id}"
        )
    if manifest.status is not DatasetStatus.APPROVED:
        raise PermissionError(
            f"Materialized dataset is {manifest.status.value}, not approved"
        )
    verify_parquet(directory / f"{symbol.upper()}.parquet", manifest)
    return manifest
