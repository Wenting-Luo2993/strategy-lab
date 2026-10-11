"""Operate the P12 durable research publisher and reconciliation workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from vibe.research_pipeline.paths import research_db_path
from vibe.research_pipeline.publishing import (
    ResearchPublisher,
    ResearchReconciler,
    ResearchSupabaseSettings,
    SupabaseResearchDestination,
)
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish and reconcile summary-only research records."
    )
    parser.add_argument(
        "--db",
        type=Path,
        help="Research SQLite path; defaults to RESEARCH__DB_PATH/OS-local storage.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    flush = subcommands.add_parser("flush", help="Publish ready outbox rows.")
    flush.add_argument("--max-batches", type=int, default=100)

    reconcile = subcommands.add_parser(
        "reconcile", help="Compare local checksums with the remote read model."
    )
    reconcile.add_argument(
        "--repair",
        action="store_true",
        help="Explicitly requeue missing/divergent local rows.",
    )
    reconcile.add_argument("--reason", help="Required operator reason with --repair.")

    status = subcommands.add_parser("status", help="Show durable outbox health.")
    status.add_argument("--stale-hours", type=float, default=24.0)

    requeue = subcommands.add_parser(
        "requeue-dead-letter", help="Explicitly requeue one dead-letter event."
    )
    requeue.add_argument("event_id")
    requeue.add_argument("--reason", required=True)

    replay = subcommands.add_parser(
        "replay", help="Explicitly replay one publication by idempotency key."
    )
    replay.add_argument("idempotency_key")
    replay.add_argument("--reason", required=True)
    return parser


def _destination() -> SupabaseResearchDestination:
    return SupabaseResearchDestination(ResearchSupabaseSettings.from_env())


async def _flush(store: SqliteResearchStore, max_batches: int) -> int:
    if max_batches <= 0:
        raise ValueError("--max-batches must be positive")
    publisher = ResearchPublisher(store, _destination())
    totals = {"claimed": 0, "published": 0, "failed": 0, "dead_lettered": 0}
    for _ in range(max_batches):
        result = await publisher.flush_once()
        for key in totals:
            totals[key] += int(getattr(result, key))
        if result.claimed == 0 or result.failed or result.dead_lettered:
            break
    print(json.dumps(totals, sort_keys=True))
    return 0 if totals["failed"] == 0 and totals["dead_lettered"] == 0 else 1


async def _reconcile(
    store: SqliteResearchStore, *, repair: bool, reason: Optional[str]
) -> int:
    if repair and not (reason or "").strip():
        raise ValueError("--reason is required with --repair")
    reconciler = ResearchReconciler(store, _destination())
    report = await reconciler.compare()
    repaired = reconciler.repair(report, reason=reason or "") if repair else 0
    print(
        json.dumps(
            {
                "items": [
                    {
                        "idempotency_key": item.idempotency_key,
                        "status": item.status.value,
                        "local_checksum": item.local_checksum,
                        "remote_checksum": item.remote_checksum,
                    }
                    for item in report.items
                ],
                "repaired": repaired,
            },
            sort_keys=True,
        )
    )
    return 0


async def _main() -> int:
    args = _parser().parse_args()
    path = args.db or research_db_path(create_parents=True)
    store = SqliteResearchStore(path)
    try:
        if args.command == "flush":
            return await _flush(store, args.max_batches)
        if args.command == "reconcile":
            return await _reconcile(
                store, repair=args.repair, reason=args.reason
            )
        if args.command == "status":
            print(
                json.dumps(
                    {
                        "counts": store.publication_status_counts(),
                        "pending_older_than_threshold": (
                            store.pending_publications_older_than(
                                timedelta(hours=args.stale_hours)
                            )
                        ),
                        "stale_hours": args.stale_hours,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "requeue-dead-letter":
            changed = store.requeue_dead_letter(
                args.event_id, reason=args.reason
            )
            print(json.dumps({"requeued": changed}))
            return 0 if changed else 1
        if args.command == "replay":
            changed = store.replay_publication(
                args.idempotency_key, reason=args.reason
            )
            print(json.dumps({"replayed": changed}))
            return 0 if changed else 1
        raise AssertionError(f"Unhandled command: {args.command}")
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
