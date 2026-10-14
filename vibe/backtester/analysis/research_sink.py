"""Open a research-store sink for a backtest study.

This is the seam between the backtester's analysis runners and the research
pipeline. It lives on the backtester side on purpose: the sink needs
``EXECUTION_MODEL_VERSION`` and the on-disk layout of the parquet data
directory, and having ``research_pipeline`` reach back into ``backtester`` for
those would invert the dependency -- the store is meant to be readable by
tools that have no simulator at all.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["dataset_snapshot_id", "open_run_sink"]


def dataset_snapshot_id(data_dir: Path, symbol: str) -> str:
    """Identify the bar data a study consumed.

    A curated dataset manifest is preferred, because it names a dataset the
    pipeline can verify. When none exists -- the common case for a local
    parquet drop -- fall back to the file's content hash. The ``sha256:``
    prefix keeps the two distinguishable: a content hash proves the bytes were
    identical but carries none of a manifest's provenance, and conflating them
    would overstate what is known about the data.
    """
    from vibe.research_pipeline.datasets import read_manifest
    from vibe.research_pipeline.hashing import hash_file

    manifest_path = data_dir / f"{symbol}.manifest.json"
    if manifest_path.is_file():
        try:
            return read_manifest(manifest_path).dataset_id
        except Exception:
            logger.warning(
                "Could not read dataset manifest %s; falling back to a content "
                "hash, which pins the bytes but not their provenance.",
                manifest_path,
            )
    return f"sha256:{hash_file(data_dir / f'{symbol}.parquet')}"


def open_run_sink(
    *,
    data_dir: Path,
    symbol: str,
    strategy_id: str,
    starting_equity: float,
    methodology_version: str,
    retention: str = "full",
    owner: str = "research_study",
    db_path: Optional[Path] = None,
) -> Any:
    """Create a :class:`BacktestRunSink` writing to the local research store.

    Imports are deferred so that merely importing an analysis module does not
    pull in the storage layer; a sweep run without ``--record-runs`` should not
    need sqlite at all.
    """
    from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
    from vibe.research_pipeline.contracts import RetentionProfile
    from vibe.research_pipeline.paths import research_db_path
    from vibe.research_pipeline.recording import BacktestRunSink
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

    resolved_db = db_path or research_db_path(create_parents=True)
    store = SqliteResearchStore(resolved_db)
    logger.info("Recording runs into %s", resolved_db)

    sink = BacktestRunSink(
        store,
        strategy_id=strategy_id,
        symbol=symbol,
        data_snapshot_id=dataset_snapshot_id(Path(data_dir), symbol),
        execution_model_version=EXECUTION_MODEL_VERSION,
        methodology_version=methodology_version,
        repo_root=Path(__file__).resolve().parents[3],
        starting_equity=starting_equity,
        retention=RetentionProfile(retention),
        owner=owner,
    )
    if sink.code_dirty:
        logger.warning(
            "Working tree has uncommitted source changes; recorded runs are "
            "flagged code_dirty and cannot be reproduced from a commit alone."
        )
    return sink
