"""Dataset manifest generation and Parquet verification."""

from __future__ import annotations

import importlib.metadata
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from vibe.research_pipeline.datasets.models import (
    ConverterMetadata,
    DatasetManifest,
    DatasetStatus,
    DatasetValidation,
    LicenseApproval,
)
from vibe.research_pipeline.hashing import hash_file

PRICE_COLUMNS = ("open", "high", "low", "close")
BAR_COLUMNS = (*PRICE_COLUMNS, "volume")


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def converter_metadata(*, path: str, code_commit: str) -> ConverterMetadata:
    return ConverterMetadata(
        path=path,
        code_commit=code_commit,
        pandas=_version("pandas"),
        pyarrow=_version("pyarrow"),
        zstandard=_version("zstandard"),
        compression="snappy",
    )


def _read_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        timestamp_name = next(
            (name for name in ("ts_event", "timestamp") if name in frame.columns),
            None,
        )
        if timestamp_name is None:
            raise ValueError(
                "Parquet must have a DatetimeIndex or ts_event/timestamp column"
            )
        frame = frame.set_index(timestamp_name)
    if frame.index.tz is None:
        raise ValueError("Parquet timestamps must be timezone-aware")
    return frame.sort_index()


def inspect_parquet(path: str | Path) -> dict[str, Any]:
    parquet_path = Path(path)
    frame = _read_bars(parquet_path)
    if frame.empty:
        raise ValueError("Dataset contains no rows")
    columns = tuple(str(column) for column in frame.columns)
    errors: list[str] = []
    if columns != BAR_COLUMNS:
        errors.append(f"Expected columns {BAR_COLUMNS}, got {columns}")
    if set(PRICE_COLUMNS).issubset(frame.columns):
        if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
            errors.append("high is below open or close")
        if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
            errors.append("low is above open or close")
        if (frame["high"] < frame["low"]).any():
            errors.append("high is below low")
        if (frame[list(PRICE_COLUMNS)] <= 0).any(axis=None):
            errors.append("prices must be positive")
    null_values = int(frame.isna().sum().sum())
    duplicate_timestamps = int(frame.index.duplicated().sum())
    return {
        "first_timestamp": frame.index[0].to_pydatetime(),
        "last_timestamp": frame.index[-1].to_pydatetime(),
        "row_count": len(frame),
        "trading_sessions": int(frame.index.normalize().nunique()),
        "columns": columns,
        "timezone": str(frame.index.tz),
        "object_sha256": hash_file(parquet_path),
        "object_bytes": parquet_path.stat().st_size,
        "validation": DatasetValidation(
            null_values=null_values,
            duplicate_timestamps=duplicate_timestamps,
            hard_errors=tuple(errors),
        ),
    }


def generate_manifest(
    parquet_path: str | Path,
    *,
    status: DatasetStatus,
    vendor: str,
    vendor_job_id: str,
    source_dataset: str,
    source_schema: str,
    symbol: str,
    bar_interval: str,
    session: str,
    source_sha256: str,
    source_bytes: int,
    converter: ConverterMetadata,
    license: LicenseApproval,
) -> DatasetManifest:
    details = inspect_parquet(parquet_path)
    identity = {
        "manifest_version": 1,
        "vendor": vendor,
        "vendor_job_id": vendor_job_id,
        "source_dataset": source_dataset,
        "source_schema": source_schema,
        "symbol": symbol.upper(),
        "bar_interval": bar_interval,
        "session": session,
        "timezone": details["timezone"],
        "first_timestamp": details["first_timestamp"],
        "last_timestamp": details["last_timestamp"],
        "row_count": details["row_count"],
        "trading_sessions": details["trading_sessions"],
        "columns": details["columns"],
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
    }
    return DatasetManifest(
        dataset_id=DatasetManifest.calculate_dataset_id(identity),
        status=status,
        vendor=vendor,
        vendor_job_id=vendor_job_id,
        source_dataset=source_dataset,
        source_schema=source_schema,
        symbol=symbol,
        bar_interval=bar_interval,
        session=session,
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        converter=converter,
        license=license,
        **details,
    )


def verify_parquet(path: str | Path, manifest: DatasetManifest) -> None:
    observed = inspect_parquet(path)
    checks = {
        "object_sha256": manifest.object_sha256,
        "object_bytes": manifest.object_bytes,
        "first_timestamp": manifest.first_timestamp,
        "last_timestamp": manifest.last_timestamp,
        "row_count": manifest.row_count,
        "trading_sessions": manifest.trading_sessions,
        "columns": manifest.columns,
        "timezone": manifest.timezone,
        "validation": manifest.validation,
    }
    mismatches = [
        f"{name}: expected {expected!r}, got {observed[name]!r}"
        for name, expected in checks.items()
        if observed[name] != expected
    ]
    if mismatches:
        raise ValueError("Dataset verification failed: " + "; ".join(mismatches))


def read_manifest(path: str | Path) -> DatasetManifest:
    return DatasetManifest.model_validate_json(Path(path).read_text("utf-8"))


def write_manifest(path: str | Path, manifest: DatasetManifest) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(manifest.canonical_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
