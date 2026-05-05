#!/usr/bin/env python3
"""
Compare QC baseline trades against our backtester trades.

Steps covered:
  3 — trade count / date alignment
  4 — entry price gap on matching trades
  5 — stop exit comparison

Usage:
    python scripts/compare_trades.py \
        --qc   reports/qc_trades.csv \
        --ours reports/our_trades_2023h1.csv \
        --start 2023-01-01 --end 2023-06-30

Output:
  - Summary printed to stdout
  - reports/trade_comparison.csv   (full day-by-day alignment)
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare QC vs our backtester trades")
    parser.add_argument("--qc",    required=True, help="QC trades CSV (from parse_qc_logs.py)")
    parser.add_argument("--ours",  required=True, help="Our trades CSV (from run_backtest.py --trades-csv)")
    parser.add_argument("--start", default=None,  help="Filter start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end",   default=None,  help="Filter end date YYYY-MM-DD (inclusive)")
    parser.add_argument("--output", default="reports/trade_comparison.csv", help="Output CSV path")
    args = parser.parse_args()

    qc_path   = Path(args.qc)
    ours_path = Path(args.ours)
    for p in (qc_path, ours_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    qc_raw   = load_csv(qc_path)
    ours_raw = load_csv(ours_path)

    def in_range(date_str):
        if args.start and date_str < args.start:
            return False
        if args.end   and date_str > args.end:
            return False
        return True

    qc_by_date   = {t["date"]: t for t in qc_raw   if in_range(t["date"])}
    ours_by_date = {t["date"]: t for t in ours_raw  if in_range(t["date"])}

    all_dates = sorted(set(qc_by_date) | set(ours_by_date))

    # ── Step 3: Date alignment ────────────────────────────────────────────────
    only_in_qc   = [d for d in all_dates if d in qc_by_date and d not in ours_by_date]
    only_in_ours = [d for d in all_dates if d in ours_by_date and d not in qc_by_date]
    matched      = [d for d in all_dates if d in qc_by_date and d in ours_by_date]

    print("=" * 60)
    print("STEP 3 — Trade count / date alignment")
    print("=" * 60)
    range_label = f"{args.start or 'all'} to {args.end or 'all'}"
    print(f"Date range  : {range_label}")
    print(f"QC trades   : {len(qc_by_date)}")
    print(f"Our trades  : {len(ours_by_date)}")
    print(f"Matched days: {len(matched)}")
    print(f"Only in QC  : {len(only_in_qc)}  (we missed these)")
    print(f"Only in ours: {len(only_in_ours)} (we have extra)")

    # Late-day QC entries (after 15:00) that we would miss due to entry cutoff
    qc_after_cutoff = [d for d in only_in_qc
                       if _float(qc_by_date[d].get("entry", "0")) is not None]
    print(f"\nSample dates we missed (first 10): {only_in_qc[:10]}")
    if only_in_ours:
        print(f"Sample dates we have extra (first 10): {only_in_ours[:10]}")

    # ── Step 4: Entry price gap on matched trades ────────────────────────────
    print()
    print("=" * 60)
    print("STEP 4 — Entry price gap (ours - QC) on matched trade days")
    print("=" * 60)
    entry_gaps = []
    direction_mismatches = []
    for d in matched:
        qc  = qc_by_date[d]
        our = ours_by_date[d]
        qc_entry  = _float(qc.get("entry"))
        our_entry = _float(our.get("entry_price"))
        if qc_entry and our_entry:
            gap = our_entry - qc_entry
            entry_gaps.append(gap)
        if qc.get("direction") != our.get("direction"):
            direction_mismatches.append({
                "date": d,
                "qc_dir": qc.get("direction"),
                "our_dir": our.get("direction"),
            })

    if entry_gaps:
        avg_gap = sum(entry_gaps) / len(entry_gaps)
        max_gap = max(entry_gaps)
        min_gap = min(entry_gaps)
        pos_gaps = [g for g in entry_gaps if g > 0.10]
        neg_gaps = [g for g in entry_gaps if g < -0.10]
        print(f"Avg entry gap : ${avg_gap:+.3f}")
        print(f"Max entry gap : ${max_gap:+.3f}")
        print(f"Min entry gap : ${min_gap:+.3f}")
        print(f"We enter >$0.10 higher than QC on {len(pos_gaps)}/{len(entry_gaps)} matched trades ({len(pos_gaps)/len(entry_gaps):.0%})")
        print(f"We enter >$0.10 lower  than QC on {len(neg_gaps)}/{len(entry_gaps)} matched trades ({len(neg_gaps)/len(entry_gaps):.0%})")
    else:
        print("No entry prices to compare.")

    if direction_mismatches:
        print(f"\nDirection mismatches: {len(direction_mismatches)}")
        for m in direction_mismatches[:5]:
            print(f"  {m['date']}: QC={m['qc_dir']}  ours={m['our_dir']}")

    # ── Step 5: Stop exit comparison on matched trades ───────────────────────
    print()
    print("=" * 60)
    print("STEP 5 — Stop exit comparison on matched trade days")
    print("=" * 60)
    stop_qc_stop_ours  = 0   # both stop
    stop_qc_eod_ours   = 0   # QC stopped, we held to EOD
    eod_qc_stop_ours   = 0   # QC held EOD, we stopped
    eod_qc_eod_ours    = 0   # both EOD
    exit_price_diffs   = []

    for d in matched:
        qc  = qc_by_date[d]
        our = ours_by_date[d]
        qr = qc.get("exit_reason")
        or_ = our.get("exit_reason", "").upper()
        if qr == "STOP" and or_ == "STOP":
            stop_qc_stop_ours += 1
        elif qr == "STOP" and or_ != "STOP":
            stop_qc_eod_ours += 1
        elif qr == "EOD" and or_ == "STOP":
            eod_qc_stop_ours += 1
        else:
            eod_qc_eod_ours += 1

        qc_exit  = _float(qc.get("exit"))
        our_exit = _float(our.get("exit_price"))
        if qc_exit and our_exit:
            exit_price_diffs.append(our_exit - qc_exit)

    total_matched = len(matched) or 1
    print(f"Both STOP        : {stop_qc_stop_ours:3d}  ({stop_qc_stop_ours/total_matched:.0%})")
    print(f"QC STOP, we EOD  : {stop_qc_eod_ours:3d}  ({stop_qc_eod_ours/total_matched:.0%})  [we missed the stop (held too long)]")
    print(f"QC EOD,  we STOP : {eod_qc_stop_ours:3d}  ({eod_qc_stop_ours/total_matched:.0%})  [we stopped early (intrabar wick)]")
    print(f"Both EOD         : {eod_qc_eod_ours:3d}  ({eod_qc_eod_ours/total_matched:.0%})")

    if exit_price_diffs:
        avg_exit_diff = sum(exit_price_diffs) / len(exit_price_diffs)
        print(f"\nAvg exit price gap (ours - QC): ${avg_exit_diff:+.3f}")

    # ── Overall P&L comparison ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("P&L summary (filtered range)")
    print("=" * 60)
    qc_pnl  = sum(_float(t.get("pnl")) or 0 for t in qc_by_date.values())
    our_pnl = sum(_float(t.get("pnl"))  or 0 for t in ours_by_date.values())
    qc_wins  = sum(1 for t in qc_by_date.values()  if (_float(t.get("pnl")) or 0) > 0)
    our_wins = sum(1 for t in ours_by_date.values() if (_float(t.get("pnl")) or 0) > 0)
    print(f"QC   total P&L: ${qc_pnl:,.0f}   win rate: {qc_wins/len(qc_by_date):.1%} ({qc_wins}/{len(qc_by_date)})")
    if ours_by_date:
        print(f"Ours total P&L: ${our_pnl:,.0f}   win rate: {our_wins/len(ours_by_date):.1%} ({our_wins}/{len(ours_by_date)})")

    # ── Write full comparison CSV ─────────────────────────────────────────────
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "qc_direction", "qc_entry", "qc_stop", "qc_exit", "qc_exit_reason", "qc_pnl",
        "our_direction", "our_entry", "our_stop", "our_exit", "our_exit_reason", "our_pnl",
        "entry_gap", "exit_gap", "exit_reason_match",
        "present_in_qc", "present_in_ours",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in all_dates:
            qc  = qc_by_date.get(d)
            our = ours_by_date.get(d)
            qc_entry  = _float(qc.get("entry"))     if qc  else None
            our_entry = _float(our.get("entry_price")) if our else None
            qc_exit_p  = _float(qc.get("exit"))      if qc  else None
            our_exit_p = _float(our.get("exit_price")) if our else None
            entry_gap = round(our_entry - qc_entry, 3) if (qc_entry and our_entry) else ""
            exit_gap  = round(our_exit_p - qc_exit_p, 3) if (qc_exit_p and our_exit_p) else ""
            qr  = qc.get("exit_reason")              if qc  else ""
            or_ = our.get("exit_reason", "").upper() if our else ""
            writer.writerow({
                "date":              d,
                "qc_direction":      qc.get("direction")   if qc  else "",
                "qc_entry":          qc.get("entry")        if qc  else "",
                "qc_stop":           qc.get("stop")         if qc  else "",
                "qc_exit":           qc.get("exit")         if qc  else "",
                "qc_exit_reason":    qr,
                "qc_pnl":            qc.get("pnl")          if qc  else "",
                "our_direction":     our.get("direction")    if our else "",
                "our_entry":         our.get("entry_price")  if our else "",
                "our_stop":          our.get("stop_price")   if our else "",
                "our_exit":          our.get("exit_price")   if our else "",
                "our_exit_reason":   or_,
                "our_pnl":           our.get("pnl")          if our else "",
                "entry_gap":         entry_gap,
                "exit_gap":          exit_gap,
                "exit_reason_match": (qr == or_) if (qr and or_) else "",
                "present_in_qc":     d in qc_by_date,
                "present_in_ours":   d in ours_by_date,
            })

    print(f"\nFull comparison: {out_path}")


if __name__ == "__main__":
    main()
