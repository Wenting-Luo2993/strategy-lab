#!/usr/bin/env python3
"""
Parse QuantConnect ORB trade logs into a clean CSV.

Usage:
    python scripts/parse_qc_logs.py \
        --log-dir data/QuantConnect \
        --output reports/qc_trades.csv

Log format per line:
    <timestamp> T|<reason>|<dir>|<entry>|<sl>|<exit>|<qty>|<ir>|<pnl>

  reason : S = stop exit   E = EOD exit
  dir    : L = long        R = short
"""
import argparse
import csv
import re
import sys
from pathlib import Path

_TRADE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+\S+\s+T\|([SE])\|([LR])"
    r"\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)\|([\d.]+)\|([-\d.]+)"
)


def parse_logs(log_dir: Path) -> list[dict]:
    trades = []
    for path in sorted(log_dir.glob("v0_ORB_logs_*.txt")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                m = _TRADE_RE.search(line)
                if not m:
                    continue
                date, reason, direction, entry, stop, exit_, qty, ir, pnl = m.groups()
                trades.append({
                    "date":          date,
                    "exit_reason":   "STOP" if reason == "S" else "EOD",
                    "direction":     "long" if direction == "L" else "short",
                    "entry":         float(entry),
                    "stop":          float(stop),
                    "exit":          float(exit_),
                    "qty":           int(qty),
                    "initial_risk":  float(ir),
                    "pnl":           float(pnl),
                })
    trades.sort(key=lambda t: t["date"])
    return trades


def write_csv(trades: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "exit_reason", "direction", "entry", "stop", "exit",
                  "qty", "initial_risk", "pnl"]
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse QC ORB logs to CSV")
    parser.add_argument("--log-dir", default="data/QuantConnect", help="Directory with v0_ORB_logs_*.txt files")
    parser.add_argument("--output",  default="reports/qc_trades.csv",    help="Output CSV path")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"ERROR: log dir not found: {log_dir}", file=sys.stderr)
        sys.exit(1)

    trades = parse_logs(log_dir)
    if not trades:
        print("ERROR: no trades parsed — check log file format", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output)
    write_csv(trades, output)

    n_stop = sum(1 for t in trades if t["exit_reason"] == "STOP")
    n_eod  = sum(1 for t in trades if t["exit_reason"] == "EOD")
    total_pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    print(f"Parsed {len(trades)} trades  ({n_stop} stop / {n_eod} EOD)")
    print(f"Win rate : {wins/len(trades):.1%}  ({wins}W / {len(trades)-wins}L)")
    print(f"Total P&L: ${total_pnl:,.0f}")
    print(f"Written  : {output}")


if __name__ == "__main__":
    main()
