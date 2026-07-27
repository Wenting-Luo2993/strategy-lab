"""Validate live dashboard Stage 5 local and optional Supabase state.

This script checks local dashboard SQLite files first, then can optionally query
the Supabase read model using the browser-safe anonymous key. The optional
Supabase publish probe writes only a synthetic dashboard-safe account row.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Stage 5 live dashboard state")
    parser.add_argument("--price-db", default=os.getenv("DASHBOARD_PRICE_DB", "./data/market_data.db"))
    parser.add_argument("--dashboard-db", default=os.getenv("DASHBOARD_DB", "./data/dashboard.db"))
    parser.add_argument("--outbox-db", default=os.getenv("DASHBOARD_OUTBOX_DB", "./data/local/publish_outbox.db"))
    parser.add_argument("--trades-db", default=os.getenv("TRADES_DB", "./data/trades.db"))
    parser.add_argument("--metrics-db", default=os.getenv("OPERATIONAL_METRICS_DB", "./data/local/operational_metrics.db"))
    parser.add_argument("--symbols", default=os.getenv("DASHBOARD_SYMBOLS", "QQQ"), help="Comma-separated symbols expected in price_bars")
    parser.add_argument("--timeframe", default=os.getenv("DASHBOARD_TIMEFRAME", "5m"))
    parser.add_argument("--require-order", action="store_true", help="Require order, trade, metric, equity, and position rows")
    parser.add_argument("--require-supabase", action="store_true", help="Fail unless Supabase read-model rows are reachable")
    parser.add_argument("--supabase-only", action="store_true", help="Skip local SQLite checks and validate only Supabase connectivity")
    parser.add_argument("--probe-supabase-publish", action="store_true", help="Upsert a synthetic account row with the service key and read it back with the anon key")
    parser.add_argument("--supabase-url", default=os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL"))
    parser.add_argument("--supabase-anon-key", default=os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY"))
    parser.add_argument("--supabase-service-key", default=os.getenv("DASHBOARD__SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_KEY"))
    parser.add_argument("--probe-account-id", default=os.getenv("STAGE5_PROBE_ACCOUNT_ID", "stage5-validation-probe"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    results: list[CheckResult] = []

    if not args.supabase_only:
        results.extend(validate_price_db(Path(args.price_db), symbols, args.timeframe))
        results.extend(validate_dashboard_db(Path(args.dashboard_db), require_order=args.require_order))
        results.extend(validate_trades_db(Path(args.trades_db), require_order=args.require_order))
        results.extend(validate_metrics_db(Path(args.metrics_db), require_order=args.require_order))
        results.extend(validate_outbox_db(Path(args.outbox_db)))
    if args.probe_supabase_publish:
        results.extend(
            validate_supabase_publish_probe(
                args.supabase_url,
                args.supabase_service_key,
                args.supabase_anon_key,
                args.probe_account_id,
            )
        )
    results.extend(validate_supabase(args.supabase_url, args.supabase_anon_key, args.require_supabase))

    for result in results:
      status = "PASS" if result.passed else "FAIL"
      print(f"[{status}] {result.name}: {result.detail}")

    failed = [result for result in results if not result.passed]
    if failed:
        print(f"\nStage 5 validation incomplete: {len(failed)} check(s) failed.")
        return 1
    print("\nStage 5 validation passed.")
    return 0


def validate_price_db(db_path: Path, symbols: list[str], timeframe: str) -> list[CheckResult]:
    if not db_path.exists():
        return [CheckResult("local price DB", False, f"missing {db_path}")]
    results = [CheckResult("local price DB", True, str(db_path))]
    with connect_readonly(db_path) as conn:
        total = count_rows(conn, "price_bars")
        results.append(CheckResult("price_bars rows", total > 0, f"{total} row(s)"))
        for symbol in symbols:
            count = scalar(
                conn,
                "SELECT COUNT(*) FROM price_bars WHERE symbol = ? AND timeframe = ? AND is_complete = 1",
                (symbol, timeframe),
            )
            results.append(CheckResult(f"completed {timeframe} bar for {symbol}", count > 0, f"{count} row(s)"))
    return results


def validate_dashboard_db(db_path: Path, require_order: bool) -> list[CheckResult]:
    if not db_path.exists():
        return [CheckResult("local dashboard DB", False, f"missing {db_path}")]
    checks = {
        "accounts": 1,
        "equity_snapshots": 1 if require_order else 0,
        "positions": 1 if require_order else 0,
        "order_events": 1 if require_order else 0,
    }
    return validate_table_counts(db_path, "local dashboard DB", checks)


def validate_trades_db(db_path: Path, require_order: bool) -> list[CheckResult]:
    if not db_path.exists():
        return [CheckResult("local trades DB", False, f"missing {db_path}")]
    minimum = 1 if require_order else 0
    return validate_table_counts(db_path, "local trades DB", {"trades": minimum})


def validate_metrics_db(db_path: Path, require_order: bool) -> list[CheckResult]:
    if not db_path.exists():
        return [CheckResult("local metrics DB", False, f"missing {db_path}")]
    minimum = 1 if require_order else 0
    return validate_table_counts(db_path, "local metrics DB", {"metrics": minimum})


def validate_outbox_db(db_path: Path) -> list[CheckResult]:
    if not db_path.exists():
        return [CheckResult("local outbox DB", False, f"missing {db_path}")]
    results = validate_table_counts(db_path, "local outbox DB", {"publish_outbox": 1, "publish_failures": 0})
    with connect_readonly(db_path) as conn:
        statuses = dict(conn.execute("SELECT status, COUNT(*) FROM publish_outbox GROUP BY status").fetchall())
        unresolved = sum(statuses.get(status, 0) for status in ("pending", "failed", "publishing", "dead_letter"))
        published = statuses.get("published", 0)
        results.append(
            CheckResult(
                "outbox publication state",
                unresolved == 0 and published > 0,
                f"published={published}, unresolved={unresolved}, statuses={statuses}",
            )
        )
    return results


def validate_supabase(url: str | None, anon_key: str | None, required: bool) -> list[CheckResult]:
    if not url or not anon_key:
        return [CheckResult("Supabase read model", not required, "not configured")]
    tables = [
        "accounts",
        "trades",
        "order_events",
        "price_bars",
        "equity_snapshots",
        "positions",
        "operational_metrics",
        "strategy_annotations",
    ]
    results: list[CheckResult] = []
    for table in tables:
        try:
            rows = query_supabase_count(url, anon_key, table)
            minimum = 1 if required and table not in {"positions", "strategy_annotations"} else 0
            results.append(CheckResult(f"Supabase {table}", rows >= minimum, f"{rows} row(s) visible to anon"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            results.append(CheckResult(f"Supabase {table}", False, str(exc)))
    return results


def validate_supabase_publish_probe(
    url: str | None,
    service_key: str | None,
    anon_key: str | None,
    account_id: str,
) -> list[CheckResult]:
    if not url or not service_key or not anon_key:
        return [CheckResult("Supabase publish probe", False, "URL, service key, and anon key are required")]
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "account_id": account_id,
        "broker": "stage5-validator",
        "display_name": "Stage 5 Validation Probe",
        "currency": "USD",
        "mode": "paper",
        "updated_at": now,
    }
    try:
        upsert_supabase_row(url, service_key, "accounts", "account_id", payload)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return [CheckResult("Supabase publish probe write", False, str(exc))]

    try:
        rows = query_supabase_count(url, anon_key, "accounts", {"account_id": f"eq.{account_id}"})
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return [
            CheckResult("Supabase publish probe write", True, f"upserted account_id={account_id}"),
            CheckResult("Supabase publish probe anon read", False, str(exc)),
        ]
    return [
        CheckResult("Supabase publish probe write", True, f"upserted account_id={account_id}"),
        CheckResult("Supabase publish probe anon read", rows == 1, f"{rows} row(s) visible to anon for account_id={account_id}"),
    ]


def validate_table_counts(db_path: Path, label: str, checks: dict[str, int]) -> list[CheckResult]:
    results = [CheckResult(label, True, str(db_path))]
    with connect_readonly(db_path) as conn:
        for table, minimum in checks.items():
            rows = count_rows(conn, table)
            results.append(CheckResult(f"{table} rows", rows >= minimum, f"{rows} row(s), expected >= {minimum}"))
    return results


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(scalar(conn, f"SELECT COUNT(*) FROM {table}"))


def scalar(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def query_supabase_count(url: str, anon_key: str, table: str, filters: dict[str, str] | None = None) -> int:
    query = {"select": "*"}
    if filters:
        query.update(filters)
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}?{urlencode(query)}"
    request = Request(
        endpoint,
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Prefer": "count=exact",
            "Range": "0-0",
        },
    )
    with urlopen(request, timeout=10) as response:
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[1]
            return 0 if total == "*" else int(total)
        payload = json.loads(response.read().decode("utf-8"))
        return len(payload) if isinstance(payload, list) else 0


def upsert_supabase_row(url: str, service_key: str, table: str, on_conflict: str, payload: dict[str, Any]) -> None:
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}?{urlencode({'on_conflict': on_conflict})}"
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        method="POST",
    )
    with urlopen(request, timeout=10):
        return


if __name__ == "__main__":
    raise SystemExit(main())