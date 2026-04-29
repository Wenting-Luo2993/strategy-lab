#!/usr/bin/env python3
"""
Run a backtest from the command line.

Usage:
    python scripts/run_backtest.py --ruleset orb_production --symbol QQQ \
        --start 2023-01-01 --end 2024-12-31 --capital 10000 \
        --output reports/backtest.html
"""
import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ORB backtest")
    parser.add_argument("--ruleset", default="orb_production", help="Ruleset name")
    parser.add_argument("--symbol",  default="QQQ",           help="Symbol to test")
    parser.add_argument("--start",   default="2020-01-01",    help="Start date YYYY-MM-DD")
    parser.add_argument("--end",     default="2024-12-31",    help="End date YYYY-MM-DD")
    parser.add_argument("--capital", default=10_000.0, type=float, help="Initial capital")
    parser.add_argument("--slippage-ticks", default=5, type=int, help="Slippage in ticks")
    parser.add_argument("--output",  default="reports/backtest.html", help="Output HTML path")
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
