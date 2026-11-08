#!/usr/bin/env python3
"""Recompute the live-vs-backtest parity report offline over a date range.

Live trades are rebuilt from the bot's durable IB execution journal (the same
source the cooldown report uses); each session is replayed through the
backtester's resting-stop path from 1-minute parquet data with the session's
start equity.

Usage:
    python scripts/live_backtest_parity.py --start 2026-11-09 --end 2026-11-20 \
        --executions-db deploy/ibkr-docker/data/ib_executions.db \
        --dashboard-db deploy/ibkr-docker/data/dashboard.db \
        [--ruleset orb_resting_stop_paper] [--data-dir PATH] [--output parity.json]

Session equity comes from the dashboard's ``session_start_equity`` snapshot for
each date (``--dashboard-db``) or, failing that, ``--session-equity``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas_market_calendars as mcal  # noqa: E402

from vibe.backtester.data.parquet_loader import ParquetLoader  # noqa: E402
from vibe.common.ruleset.loader import RuleSetLoader  # noqa: E402
from vibe.trading_bot.parity.report import (  # noqa: E402
    backtest_session,
    build_report,
    live_trades_from_executions,
)
from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore  # noqa: E402

ET = ZoneInfo("America/New_York")


def session_equity_from_dashboard(db_path: Path, session: date) -> Optional[float]:
    """The ``session_start_equity`` snapshot recorded at warm-up, if any."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT net_liquidation FROM equity_snapshots "
            "WHERE event_type = 'session_start_equity' AND substr(timestamp, 1, 10) = ? "
            "ORDER BY timestamp LIMIT 1",
            (session.isoformat(),),
        ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def load_minutes(data_dir: Optional[str], symbol: str, session: date):
    loader = ParquetLoader(data_dir, [symbol])
    start = datetime(session.year, session.month, session.day, tzinfo=ET)
    end = datetime(session.year, session.month, session.day, 23, 59, tzinfo=ET)
    return asyncio.run(loader.get_bars(symbol, start_time=start, end_time=end))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--executions-db", required=True, type=Path)
    parser.add_argument("--dashboard-db", type=Path)
    parser.add_argument("--session-equity", type=float)
    parser.add_argument("--ruleset", default="orb_resting_stop_paper")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not args.executions_db.is_file():
        parser.error(f"executions db not found: {args.executions_db}")
    ruleset = RuleSetLoader.from_name(args.ruleset)
    executions = IBExecutionStore(str(args.executions_db)).list_executions()
    sessions = [
        stamp.date()
        for stamp in mcal.get_calendar("XNYS")
        .schedule(start_date=args.start.isoformat(), end_date=args.end.isoformat())
        .index
    ]

    reports = []
    for session in sessions:
        equity = (
            session_equity_from_dashboard(args.dashboard_db, session)
            if args.dashboard_db is not None and args.dashboard_db.is_file()
            else None
        ) or args.session_equity
        live = live_trades_from_executions(executions, session, ET)
        if equity is None:
            print(f"{session}: skipped (no session equity)")
            continue
        backtests, errors = {}, {}
        for symbol in ruleset.instruments.symbols:
            try:
                backtests[symbol] = backtest_session(
                    ruleset, symbol, load_minutes(args.data_dir, symbol, session), equity
                )
            except Exception as exc:  # report and continue with other symbols
                errors[symbol] = str(exc)
        report = build_report(session, ruleset.instruments.symbols, live, backtests, errors)
        summary = report.to_dict()
        reports.append(summary)
        print(
            f"{session}: matched={summary['matched']} mismatched={summary['mismatched']} "
            f"{summary['mismatch_counts'] or ''} slippage_mean="
            f"{summary['mean_slippage_ticks']} gap_fills={summary['gap_fills']} "
            f"errors={summary['errors'] or ''}"
        )

    all_ticks = [tick for item in reports for tick in item["entry_slippage_ticks"]]
    print(
        f"TOTAL sessions={len(reports)} entry_fills={len(all_ticks)} "
        f"mean_slippage_ticks={sum(all_ticks) / len(all_ticks) if all_ticks else None}"
    )
    if args.output is not None:
        args.output.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
