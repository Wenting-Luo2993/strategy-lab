#!/usr/bin/env python3
"""Recompute the live-vs-backtest parity report offline over a date range.

Live trades are rebuilt from the bot's durable IB execution journal (the same
source the cooldown report uses); each session is replayed through the
backtester's resting-stop path with the session's start equity.

Usage:
    python scripts/live_backtest_parity.py --start 2026-11-09 --end 2026-11-20 \
        --ruleset orb_resting_stop_paper_5sym \
        --executions-db deploy/ibkr-docker/data/ib_executions.db \
        --dashboard-db deploy/ibkr-docker/data/dashboard.db \
        --ib-bars-dir deploy/ibkr-docker/data/parity_reports/bars \
        [--account DU123] [--output parity.json]

Bars: ``--ib-bars-dir`` replays the IBKR 1-minute bars the in-bot report
saved (``{SYMBOL}_{YYYY-MM-DD}.csv``), so results are comparable with the
daily report. Without it, 1-minute parquet data (``--data-dir``) is used and
the output is labelled "parquet bars — not comparable to in-bot report".

Session equity comes from the dashboard's ``session_start_equity`` snapshot for
each date (``--dashboard-db``) or, failing that, ``--session-equity``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import pandas_market_calendars as mcal  # noqa: E402

from vibe.backtester.data.parquet_loader import ParquetLoader  # noqa: E402
from vibe.common.ruleset.loader import RuleSetLoader  # noqa: E402
from vibe.trading_bot.parity.report import (  # noqa: E402
    FILL_LATE_PLACEMENT,
    FILL_MARKET_GAP,
    FILL_NORMAL,
    backtest_session,
    build_report,
    dedupe_executions,
    live_trades_from_executions,
)
from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore  # noqa: E402

ET = ZoneInfo("America/New_York")
PARQUET_LABEL = "parquet bars — not comparable to in-bot report"


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


def load_parquet_minutes(data_dir: Optional[str], symbol: str, session: date):
    loader = ParquetLoader(data_dir, [symbol])
    start = datetime(session.year, session.month, session.day, tzinfo=ET)
    end = datetime(session.year, session.month, session.day, 23, 59, tzinfo=ET)
    return asyncio.run(loader.get_bars(symbol, start_time=start, end_time=end))


def load_ib_minutes(bars_dir: Path, symbol: str, session: date) -> pd.DataFrame:
    """An IBKR 1-minute bar dump as saved by the in-bot report."""
    path = bars_dir / f"{symbol}_{session.isoformat()}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"no IBKR bar dump {path.name}")
    frame = pd.read_csv(path)
    index = pd.to_datetime(frame.pop("timestamp"), utc=True).dt.tz_convert(ET)
    frame.index = pd.DatetimeIndex(index)
    return frame.sort_index()


def _session_of(execution: dict) -> Optional[date]:
    filled_at = execution.get("filled_at")
    if not filled_at:
        return None
    stamp = datetime.fromisoformat(str(filled_at))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    return stamp.astimezone(ET).date()


def select_executions(
    executions: list[dict],
    start: date,
    end: date,
    symbols: list[str],
    account: Optional[str],
) -> list[dict]:
    """Executions in range for one account; raise on ambiguity or foreign symbols."""
    in_range = [
        item for item in dedupe_executions(executions)
        if (session := _session_of(item)) is not None and start <= session <= end
    ]
    if account is not None:
        in_range = [item for item in in_range if item.get("account_id") == account]
    else:
        accounts = sorted({str(item["account_id"]) for item in in_range if item.get("account_id")})
        if len(accounts) > 1:
            raise ValueError(
                f"executions span several accounts ({', '.join(accounts)}); pass --account"
            )
    foreign = sorted({str(item["symbol"]) for item in in_range} - set(symbols))
    if foreign:
        raise ValueError(
            f"executions include symbols the ruleset does not list: {', '.join(foreign)}"
        )
    return in_range


def _mean(values: list[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--executions-db", required=True, type=Path)
    parser.add_argument("--account", default=None)
    parser.add_argument("--dashboard-db", type=Path)
    parser.add_argument("--session-equity", type=float)
    parser.add_argument("--ib-bars-dir", type=Path, default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not args.executions_db.is_file():
        parser.error(f"executions db not found: {args.executions_db}")
    if args.ib_bars_dir is not None and not args.ib_bars_dir.is_dir():
        parser.error(f"IBKR bar directory not found: {args.ib_bars_dir}")
    ruleset = RuleSetLoader.from_name(args.ruleset)
    symbols = list(ruleset.instruments.symbols)
    try:
        executions = select_executions(
            IBExecutionStore(str(args.executions_db)).list_executions(),
            args.start, args.end, symbols, args.account,
        )
    except ValueError as exc:
        parser.error(str(exc))
    bars_source = "ibkr" if args.ib_bars_dir is not None else "parquet"
    if bars_source == "parquet":
        print(f"NOTE: {PARQUET_LABEL}")
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
        for symbol in symbols:
            try:
                minutes = (
                    load_ib_minutes(args.ib_bars_dir, symbol, session)
                    if bars_source == "ibkr"
                    else load_parquet_minutes(args.data_dir, symbol, session)
                )
                backtests[symbol] = backtest_session(ruleset, symbol, minutes, equity, session)
            except Exception as exc:  # report and continue with other symbols
                errors[symbol] = str(exc)
        report = build_report(
            session, symbols, live, backtests, errors, ruleset=ruleset, session_equity=equity
        )
        summary = report.to_dict()
        summary["bars_source"] = bars_source
        if bars_source == "parquet":
            summary["label"] = PARQUET_LABEL
        reports.append(summary)
        print(
            f"{session}: matched={summary['matched']} mismatched={summary['mismatched']} "
            f"{summary['mismatch_counts'] or ''} normal_slippage_mean="
            f"{summary['mean_slippage_ticks']} market_gaps={summary['gap_fills']} "
            f"late_placements={summary['late_placements']} errors={summary['errors'] or ''}"
        )

    fills = [fill for item in reports for fill in item["fills"]]

    def ticks(fill_class: str) -> list[float]:
        return [
            fill["slippage_ticks"] for fill in fills
            if fill["fill_class"] == fill_class and fill["slippage_ticks"] is not None
        ]

    normal, gaps, late = ticks(FILL_NORMAL), ticks(FILL_MARKET_GAP), ticks(FILL_LATE_PLACEMENT)
    print(
        f"TOTAL sessions={len(reports)} entry_fills={len(fills)} "
        f"normal_fills={len(normal)} mean_slippage_ticks={_mean(normal)} "
        f"median_slippage_ticks={statistics.median(normal) if normal else None} | "
        f"market_gaps={len(gaps)} mean_vs_backtest_base={_mean(gaps)} | "
        f"late_placements={len(late)} mean_vs_trigger={_mean(late)}"
        + (f" [{PARQUET_LABEL}]" if bars_source == "parquet" else "")
    )
    if args.output is not None:
        args.output.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
