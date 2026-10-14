#!/usr/bin/env python3
"""
Run a backtest from the command line.

Usage:
    python scripts/run_backtest.py --ruleset orb_production --symbol QQQ \
        --start 2023-01-01 --end 2024-12-31 --capital 10000 \
        --output reports/backtest.html \
        --trades-csv reports/our_trades.csv
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.data.paths import (
    MarketDataNotFoundError,
    resolve_market_data_dir,
)
from vibe.backtester.reporting.report import ReportGenerator
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")

RETENTION_CHOICES = ("summary", "diagnostic", "full")


def _dataset_snapshot_id(data_dir: Path, symbol: str) -> str:
    """Identify the bar data this run consumed.

    A curated dataset manifest is preferred, because it names a dataset the
    pipeline can verify. When none exists -- the common case for a local
    parquet drop -- fall back to the file's content hash. The ``sha256:``
    prefix keeps the two distinguishable: a content hash proves the bytes were
    identical but carries none of a manifest's provenance, and conflating them
    would overstate what is known about the data.
    """
    from vibe.research_pipeline.datasets import read_manifest
    from vibe.research_pipeline.hashing import hash_file

    parquet = data_dir / f"{symbol}.parquet"
    manifest_path = data_dir / f"{symbol}.manifest.json"
    if manifest_path.is_file():
        try:
            return read_manifest(manifest_path).dataset_id
        except Exception:
            pass
    return f"sha256:{hash_file(parquet)}"


def _record_run(result, *, args, ruleset_path: Path, data_dir: Path,
                retention_name: str) -> None:
    """Persist the finished run so it is visible to the research dashboards."""
    from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
    from vibe.research_pipeline.contracts import RetentionProfile
    from vibe.research_pipeline.paths import research_db_path
    from vibe.research_pipeline.recording import RunRecorder, build_run_fingerprint
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

    retention = RetentionProfile(retention_name)
    first = datetime.strptime(result.overall.first_date[:10], "%Y-%m-%d").date()
    last = datetime.strptime(result.overall.last_date[:10], "%Y-%m-%d").date()

    fingerprint = build_run_fingerprint(
        strategy_id=args.ruleset,
        ruleset_path=ruleset_path,
        parameters={
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "initial_capital": args.capital,
            "slippage_ticks": args.slippage_ticks,
        },
        symbol=args.symbol,
        first_session=first,
        last_session=last,
        session_count=int(result.equity.n_sessions),
        data_snapshot_id=_dataset_snapshot_id(data_dir, args.symbol),
        execution_model_version=EXECUTION_MODEL_VERSION,
        metric_calculation_version=int(result.overall.calculation_version),
        repo_root=Path(__file__).resolve().parent.parent,
    )

    db_path = research_db_path(create_parents=True)
    store = SqliteResearchStore(db_path)
    # The simulation has already finished, so the recorder's crash handling has
    # nothing left to protect. The lifecycle is still walked in full so the run
    # is indistinguishable from one recorded around a live execution.
    with RunRecorder(
        store,
        fingerprint=fingerprint,
        methodology_version="run_backtest/v1",
        retention=retention,
    ) as recorder:
        run_id = recorder.record(result, starting_equity=args.capital)

    print(f"Recorded: {run_id}  ({retention.value} retention)")
    print(f"Store  : {db_path}")


def _write_trades_csv(trades, path: Path) -> None:
    fieldnames = ["entry_time", "date", "exit_reason", "direction", "entry_price",
                  "stop_price", "exit_price", "qty", "initial_risk", "pnl", "pnl_r"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            entry_time = t.entry_time
            if hasattr(entry_time, "astimezone"):
                entry_time = entry_time.astimezone(ET)
            risk_per_share = (t.initial_risk / t.quantity) if (t.initial_risk and t.quantity) else 0
            if t.side == "buy":
                stop_price = t.entry_price - risk_per_share
            else:
                stop_price = t.entry_price + risk_per_share
            pnl_r = round(t.pnl / t.initial_risk, 6) if (t.pnl is not None and t.initial_risk) else ""
            writer.writerow({
                "entry_time":   entry_time.isoformat(),
                "date":         entry_time.strftime("%Y-%m-%d"),
                "exit_reason":  t.exit_reason or "",
                "direction":    "long" if t.side == "buy" else "short",
                "entry_price":  round(t.entry_price, 2),
                "stop_price":   round(stop_price, 2),
                "exit_price":   round(t.exit_price, 2) if t.exit_price is not None else "",
                "qty":          int(t.quantity),
                "initial_risk": round(t.initial_risk, 2) if t.initial_risk is not None else "",
                "pnl":          round(t.pnl, 2) if t.pnl is not None else "",
                "pnl_r":        pnl_r,
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ORB backtest")
    parser.add_argument("--ruleset", default="orb_production", help="Ruleset name")
    parser.add_argument("--symbol",  default="QQQ",           help="Symbol to test")
    parser.add_argument("--start",   default="2020-01-01",    help="Start date YYYY-MM-DD")
    parser.add_argument("--end",     default="2024-12-31",    help="End date YYYY-MM-DD")
    parser.add_argument("--capital", default=10_000.0, type=float, help="Initial capital")
    parser.add_argument("--slippage-ticks", default=5, type=int, help="Slippage in ticks")
    parser.add_argument("--output",     default="reports/backtest.html", help="Output HTML path")
    parser.add_argument("--trades-csv", default=None, help="Optional path to dump trade list as CSV")
    parser.add_argument(
        "--retention", default="full", choices=RETENTION_CHOICES,
        help="How much of the run to keep locally. 'summary' keeps metrics and "
             "checksums only; 'diagnostic' adds the trade ledger; 'full' adds "
             "the equity curve. Never leaves this machine.",
    )
    parser.add_argument(
        "--no-record", action="store_true",
        help="Skip writing the run to the local research store.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    # Resolve through the shared helper rather than a literal relative path.
    # Market data is gitignored, so a worktree does not have its own copy; the
    # resolver finds the checkout that does, which is how the golden tests
    # already locate it. The hardcoded "vibe/data/parquet" only ever worked
    # from the primary checkout's root.
    try:
        data_dir = resolve_market_data_dir()
    except MarketDataNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        sys.exit(1)

    ruleset_path = RuleSetLoader.RULESETS_DIR / f"{args.ruleset}.yaml"
    ruleset = RuleSetLoader.from_name(args.ruleset)
    engine  = BacktestEngine(ruleset=ruleset, data_dir=data_dir,
                              initial_capital=args.capital,
                              slippage_ticks=args.slippage_ticks)

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=ET)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=ET)

    print(f"Running {args.ruleset} on {args.symbol} from {args.start} to {args.end}...")
    result = engine.run(symbol=args.symbol, start_date=start, end_date=end)

    cm = result.overall
    print(f"Trades: {cm.n_trades}  Win: {cm.win_rate:.1%}  "
          f"Expectancy: {cm.expectancy_r:.2f}R  P&L: ${cm.total_pnl:,.0f}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    ReportGenerator().generate_html(result, out)
    print(f"Report: {out}")

    if args.trades_csv:
        trades_path = Path(args.trades_csv)
        trades_path.parent.mkdir(parents=True, exist_ok=True)
        _write_trades_csv(result.trades, trades_path)
        print(f"Trades : {trades_path}")

    if not args.no_record:
        _record_run(
            result,
            args=args,
            ruleset_path=ruleset_path,
            data_dir=data_dir,
            retention_name=args.retention,
        )


if __name__ == "__main__":
    main()
