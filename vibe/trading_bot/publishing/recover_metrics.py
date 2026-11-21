"""Preview/requeue metric identity conflicts without deleting failure evidence.

Production entry point: deploy.sh recover-dashboard-metrics, after the approved
Supabase metric-identity migration. The existing publisher drains the successors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent, PublishOutboxStore


def _is_metric_identity_conflict(error: str) -> bool:
    prefix = "Supabase publish failed: status=409 body="
    if not error.startswith(prefix):
        return False
    try:
        body = json.loads(error[len(prefix):])
    except json.JSONDecodeError:
        return False
    if not isinstance(body, dict):
        return False
    return (
        body.get("code") == "23505"
        and any(
            f'"{name}"' in str(body.get("message", ""))
            for name in (
                "operational_metrics_pkey",
                "operational_metrics_legacy_name_timestamp_key",
            )
        )
        and re.search(
            r'Key \(metric_name, "?timestamp"?\)',
            str(body.get("details", "")),
        ) is not None
    )


def recovery_plan(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read only; include latest dead letters, never overwrite a newer payload."""
    if not path.is_file():
        raise ValueError("Outbox database does not exist")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM publish_outbox GROUP BY status"
        ).fetchall())
        rows = connection.execute("""
            SELECT original.*
            FROM publish_outbox AS original
            WHERE original.status = 'dead_letter'
              AND original.aggregate_type = 'metric'
              AND original.destination = 'supabase'
              AND NOT EXISTS (
                  SELECT 1 FROM publish_outbox AS newer
                  WHERE newer.logical_event_id = original.logical_event_id
                    AND newer.successor_version > original.successor_version
              )
              AND NOT EXISTS (
                  SELECT 1 FROM publication_ledger AS newer
                  WHERE newer.logical_event_id = original.logical_event_id
                    AND newer.successor_version > original.successor_version
              )
            ORDER BY original.event_id
        """).fetchall()
        candidates = [
            dict(row) for row in rows
            if _is_metric_identity_conflict(row["last_error"] or "")
        ]
    finally:
        connection.close()
    fingerprint = hashlib.sha256(
        json.dumps(candidates, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "eligible": len(candidates),
        "plan_sha": fingerprint,
        "status_counts": counts,
    }, candidates


def apply_recovery(path: Path, expected_plan_sha: str) -> int:
    summary, candidates = recovery_plan(path)
    if summary["plan_sha"] != expected_plan_sha:
        raise ValueError("Recovery plan changed; preview and approve the current plan")
    store = PublishOutboxStore(str(path))
    queued = 0
    try:
        for row in candidates:
            event = PublishOutboxEvent(
                event_id=row["logical_event_id"] or row["event_id"],
                event_type=row["event_type"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                destination=row["destination"],
                payload=json.loads(row["payload_json"]),
                original_event_timestamp=row["original_event_timestamp"],
            )
            if store.enqueue_event(event, expected_dead_letter_id=row["event_id"]):
                queued += 1
    finally:
        store.close()
    return queued


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox-db", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--schema-verified", action="store_true")
    parser.add_argument("--expected-plan-sha")
    args = parser.parse_args(argv)
    if args.apply and (not args.schema_verified or not args.expected_plan_sha):
        parser.error("--apply requires --schema-verified and --expected-plan-sha")
    if args.outbox_db is None:
        from vibe.trading_bot.config.settings import AppSettings

        args.outbox_db = Path(AppSettings(_env_file=None).dashboard.local_outbox_db_path)
    try:
        if args.apply:
            queued = apply_recovery(args.outbox_db, args.expected_plan_sha)
            print(json.dumps({"queued": queued, "published_by_this_command": 0}))
        else:
            summary, _ = recovery_plan(args.outbox_db)
            print(json.dumps(summary, sort_keys=True))
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Dashboard metric recovery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
