#!/usr/bin/env python
"""Explicit local-detail retention management; never prunes automatically."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that trade/equity detail will be deleted.",
    )
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required; pruning is never automatic")
    store = SqliteResearchStore(args.db)
    try:
        counts = store.prune_run_detail(
            args.run_id,
            requested_by=args.requested_by,
            reason=args.reason,
        )
    finally:
        store.close()
    print(
        f"Pruned {counts['trades']} trade rows and "
        f"{counts['equity_points']} equity points; summary retained."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
