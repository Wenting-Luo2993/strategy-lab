#!/usr/bin/env python
"""Bind a blocked research registration to immutable local data identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vibe.research_pipeline.registration import _split_binding, load_registration


def _as_date(value: str) -> date:
    return date.fromisoformat(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--dev-start", type=_as_date, required=True)
    parser.add_argument("--dev-end", type=_as_date, required=True)
    args = parser.parse_args()

    registration = load_registration(args.registration)
    split_policy = registration["split_policy"]
    split = _split_binding(
        dev_start=args.dev_start,
        dev_end=args.dev_end,
        oos_start=date.fromisoformat(registration["final_holdout"]["start"]),
        oos_end=date.fromisoformat(registration["final_holdout"]["end"]),
        train_sessions=int(split_policy["train_sessions"]),
        validation_sessions=int(split_policy["validation_sessions"]),
        test_sessions=int(split_policy["test_sessions"]),
        step_sessions=int(split_policy["step_sessions"]),
        warmup_sessions=int(split_policy["warmup_sessions"]),
    )

    parquet_path = args.data_dir / f"{args.symbol}.parquet"
    frame = pd.read_parquet(parquet_path)
    index = pd.DatetimeIndex(frame.index)
    if index.empty:
        raise ValueError(f"Cannot bind empty parquet file: {parquet_path}")
    if index.max().date() > args.dev_end:
        raise ValueError(
            "Refusing to bind data containing rows after --dev-end; "
            "create a physically separate development-only snapshot first"
        )
    sessions = sorted({instant.date().isoformat() for instant in index})
    session_hash = hashlib.sha256(
        json.dumps(sessions, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    parquet_hash = _sha256(parquet_path)

    manifest_core: dict[str, Any] = {
        "manifest_version": 1,
        "symbols": [args.symbol],
        "sessions": sessions,
        "session_sequence_sha256": session_hash,
        "files": {
            args.symbol: {
                "path": parquet_path.name,
                "sha256": parquet_hash,
                "row_count": len(frame),
                "first_timestamp": index.min().isoformat(),
                "last_timestamp": index.max().isoformat(),
            }
        },
    }
    manifest_id = hashlib.sha256(
        json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {"manifest_id": f"sha256:{manifest_id}", **manifest_core}
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    split_policy.update(
        dev_start=args.dev_start.isoformat(),
        dev_end=args.dev_end.isoformat(),
        exact_calendar_boundaries=split["boundaries"],
        fold_count=split["fold_count"],
        plan_hash=split["plan_hash"],
    )
    registration["data_binding"] = {
        "manifest_id": manifest["manifest_id"],
        "manifest_path": args.manifest_output.as_posix(),
        "manifest_sha256": _sha256(args.manifest_output),
        "session_sequence_sha256": session_hash,
        "data_directory": args.data_dir.as_posix(),
    }
    registration["research_database"] = {"path": str(args.research_db.resolve())}
    candidate_count = 1
    for axis in registration["candidate_axes"]:
        candidate_count *= len(axis["values"])
    registration["expected_counts"] = {
        "folds": split["fold_count"],
        "candidates_per_fold": candidate_count,
        "train_memberships": split["fold_count"] * candidate_count,
        "validation_memberships": split["fold_count"] * candidate_count,
        "test_memberships": split["fold_count"],
        "total_memberships": split["fold_count"] * (2 * candidate_count + 1),
    }
    registration["execution_gate"] = {"state": "ready", "reasons": []}
    args.registration.write_text(
        json.dumps(registration, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Bound {args.symbol}: {len(frame)} rows, {len(sessions)} sessions")
    print(f"Manifest: {args.manifest_output}")
    print(f"Folds: {split['fold_count']} ({split['plan_hash']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
