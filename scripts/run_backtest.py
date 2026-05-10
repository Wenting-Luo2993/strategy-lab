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
from vibe.backtester.reporting.report import ReportGenerator
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")


def _write_trades_csv(trades, path: Path) -> None:
    fieldnames = ["date", "exit_reason", "direction", "entry_price", "stop_price",
                  "exit_price", "qty", "initial_risk", "pnl"]
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
            writer.writerow({
                "date":         entry_time.strftime("%Y-%m-%d"),
                "exit_reason":  t.exit_reason or "",
                "direction":    "long" if t.side == "buy" else "short",
                "entry_price":  round(t.entry_price, 2),
                "stop_price":   round(stop_price, 2),
                "exit_price":   round(t.exit_price, 2) if t.exit_price is not None else "",
                "qty":          int(t.quantity),
                "initial_risk": round(t.initial_risk, 2) if t.initial_risk is not None else "",
                "pnl":          round(t.pnl, 2) if t.pnl is not None else "",
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
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    data_dir = Path(os.environ.get("BACKTEST__DATA_DIR", "vibe/data/parquet"))
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        sys.exit(1)

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


if __name__ == "__main__":
    main()
