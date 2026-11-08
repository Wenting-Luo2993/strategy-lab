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
    ConclusionSyncResult,
    ResearchPublisher,
    ResearchReconciler,
    ResearchSupabaseSettings,
    SupabaseResearchDestination,
    sync_experiment_conclusions,
)
from vibe.research_pipeline.storage import run_summary_reports
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

    backfill = subcommands.add_parser(
        "backfill-experiments",
        help="Explicitly queue immutable study-level publications.",
    )
    backfill.add_argument(
        "--experiment-id",
        help="Queue one experiment; omit to queue all existing experiments.",
    )

    member_runs = subcommands.add_parser(
        "backfill-member-runs",
        help=(
            "List experiment member runs with no run_summary (dry run, "
            "read-only). --apply queues them in the LOCAL outbox only; "
            "publish later with 'flush'."
        ),
    )
    member_runs.add_argument("--experiment-id", help="Limit to one experiment.")
    member_runs.add_argument(
        "--apply",
        action="store_true",
        help="Enqueue eligible summaries locally (still no network access).",
    )

    orphans = subcommands.add_parser(
        "orphan-run-summaries",
        help=(
            "Read-only report of run_summary rows referenced by no experiment, "
            "grouped by source. Never deletes anything."
        ),
    )
    orphans.add_argument(
        "--show-run-ids", action="store_true", help="Include every run id."
    )
    orphans.add_argument(
        "--remote-sql",
        action="store_true",
        help=(
            "Print the equivalent read-only SQL for the Supabase SQL editor "
            "instead of querying the local DB."
        ),
    )

    conclusion = subcommands.add_parser(
        "conclusion",
        help="Set or show an experiment's editable one-line conclusion.",
    )
    conclusion_commands = conclusion.add_subparsers(
        dest="conclusion_command", required=True
    )
    conclusion_set = conclusion_commands.add_parser(
        "set", help="Create or overwrite the conclusion (updated in place)."
    )
    conclusion_set.add_argument("experiment_id")
    conclusion_set.add_argument("text", help="One line, at most 500 characters.")
    conclusion_set.add_argument(
        "--publish",
        action="store_true",
        help="Upload pending conclusions to Supabase immediately.",
    )
    conclusion_show = conclusion_commands.add_parser(
        "show", help="Show the stored conclusion and its sync state."
    )
    conclusion_show.add_argument("experiment_id")
    return parser


def _destination() -> SupabaseResearchDestination:
    return SupabaseResearchDestination(ResearchSupabaseSettings.from_env())


async def _flush(store: SqliteResearchStore, max_batches: int) -> int:
    if max_batches <= 0:
        raise ValueError("--max-batches must be positive")
    destination = _destination()
    publisher = ResearchPublisher(store, destination)
    totals = {"claimed": 0, "published": 0, "failed": 0, "dead_lettered": 0}
    for _ in range(max_batches):
        result = await publisher.flush_once()
        for key in totals:
            totals[key] += int(getattr(result, key))
        if result.claimed == 0 or result.failed or result.dead_lettered:
            break
    conclusions = await sync_experiment_conclusions(store, destination)
    totals["conclusions"] = _conclusion_summary(conclusions)
    print(json.dumps(totals, sort_keys=True))
    ok = (
        totals["failed"] == 0
        and totals["dead_lettered"] == 0
        and conclusions.failed == 0
    )
    return 0 if ok else 1


def _conclusion_summary(result: ConclusionSyncResult) -> dict[str, object]:
    return {
        "pending": result.pending,
        "synced": result.synced,
        "failed": result.failed,
        "errors": list(result.errors),
    }


async def _conclusion(store: SqliteResearchStore, args: argparse.Namespace) -> int:
    if args.conclusion_command == "set":
        stored = store.set_experiment_conclusion(args.experiment_id, args.text)
        output: dict[str, object] = {
            "experiment_id": stored.experiment_id,
            "conclusion": stored.conclusion,
            "updated_at": stored.updated_at,
            "pending_sync": stored.pending_sync,
        }
        status = 0
        if args.publish:
            result = await sync_experiment_conclusions(store, _destination())
            output["sync"] = _conclusion_summary(result)
            status = 0 if result.failed == 0 else 1
        print(json.dumps(output, sort_keys=True))
        return status
    stored = store.get_experiment_conclusion(args.experiment_id)
    if stored is None:
        print(json.dumps({"experiment_id": args.experiment_id, "conclusion": None}))
        return 1
    print(
        json.dumps(
            {
                "experiment_id": stored.experiment_id,
                "conclusion": stored.conclusion,
                "updated_at": stored.updated_at,
                "pending_sync": stored.pending_sync,
                "synced_at": stored.synced_at,
            },
            sort_keys=True,
        )
    )
    return 0


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


def _read_only_report(args: argparse.Namespace, path: Path) -> int:
    """Dry-run commands use a mode=ro connection: opening the store would migrate."""
    if args.command == "orphan-run-summaries" and args.remote_sql:
        print(run_summary_reports.REMOTE_ORPHAN_SQL)
        return 0
    if not Path(path).is_file():
        print(f"ERROR: research DB not found: {path}", file=sys.stderr)
        return 1
    conn = run_summary_reports.connect_read_only(path)
    try:
        if args.command == "orphan-run-summaries":
            report = run_summary_reports.orphan_run_summaries(conn)
            if not args.show_run_ids:
                for group in report["groups"]:
                    group.pop("run_ids")
            print(json.dumps({"db": str(path), **report}, indent=2, sort_keys=True))
            return 0
        status = run_summary_reports.member_run_summary_status(
            conn, args.experiment_id
        )
    finally:
        conn.close()
    eligible = [item for item in status if item["eligible"]]
    print(
        json.dumps(
            {
                "db": str(path),
                "dry_run": True,
                "missing_run_summary": len(status),
                "eligible": len(eligible),
                "ineligible": [item for item in status if not item["eligible"]],
                "eligible_runs": eligible,
                "next": (
                    "re-run with --apply to enqueue locally, then 'flush' to publish"
                    if eligible
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


async def _main() -> int:
    args = _parser().parse_args()
    path = args.db or research_db_path(create_parents=True)
    if args.command == "orphan-run-summaries" or (
        args.command == "backfill-member-runs" and not args.apply
    ):
        return _read_only_report(args, path)
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
        if args.command == "backfill-experiments":
            queued = store.backfill_experiment_publications(
                args.experiment_id
            )
            print(
                json.dumps(
                    {
                        "experiment_id": args.experiment_id,
                        "queued_rows": queued,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "backfill-member-runs":
            keys = store.backfill_member_run_summaries(args.experiment_id)
            print(
                json.dumps(
                    {
                        "queued_locally": len(keys),
                        "idempotency_keys": keys,
                        "published": False,
                        "next": "publish_research.py --db <db> flush",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "conclusion":
            return await _conclusion(store, args)
        raise AssertionError(f"Unhandled command: {args.command}")
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
