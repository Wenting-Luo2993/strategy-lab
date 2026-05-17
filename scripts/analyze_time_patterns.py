"""
Time-of-Day and Exit Reason Analysis

Analyzes entry time patterns and exit reason performance.
"""

import argparse
from pathlib import Path
import pandas as pd
import json


def analyze_entry_hour(trades: pd.DataFrame) -> dict:
    """Analyze performance by entry hour."""
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_localize(None)
    trades["entry_hour"] = trades["entry_time"].dt.hour
    trades["entry_minute"] = trades["entry_time"].dt.minute
    trades["entry_hhmm"] = trades["entry_hour"] * 100 + trades["entry_minute"]
    
    # Group by hour
    hour_stats = []
    for hour in sorted(trades["entry_hour"].unique()):
        hour_trades = trades[trades["entry_hour"] == hour]
        if len(hour_trades) > 0:
            r_multiples = hour_trades["pnl_r"].dropna()
            hour_stats.append({
                "hour": f"{hour:02d}:00",
                "count": len(hour_trades),
                "expectancy": float(r_multiples.mean()),
                "win_rate": float((r_multiples > 0).mean() * 100),
                "sharpe": float(r_multiples.mean() / r_multiples.std()) if r_multiples.std() > 0 else 0,
                "total_r": float(r_multiples.sum()),
            })
    
    return {"by_entry_hour": hour_stats}


def analyze_exit_reason(trades: pd.DataFrame) -> dict:
    """Analyze performance by exit reason."""
    if "exit_reason" not in trades.columns:
        return {"error": "No exit_reason column"}
    
    exit_stats = []
    for reason in trades["exit_reason"].unique():
        reason_trades = trades[trades["exit_reason"] == reason]
        if len(reason_trades) > 0:
            r_multiples = reason_trades["pnl_r"].dropna()
            exit_stats.append({
                "exit_reason": reason,
                "count": len(reason_trades),
                "pct_of_total": float(len(reason_trades) / len(trades) * 100),
                "expectancy": float(r_multiples.mean()),
                "win_rate": float((r_multiples > 0).mean() * 100),
                "sharpe": float(r_multiples.mean() / r_multiples.std()) if r_multiples.std() > 0 else 0,
                "total_r": float(r_multiples.sum()),
                "mean_r_winners": float(r_multiples[r_multiples > 0].mean()) if (r_multiples > 0).any() else 0,
                "mean_r_losers": float(r_multiples[r_multiples <= 0].mean()) if (r_multiples <= 0).any() else 0,
            })
    
    return {"by_exit_reason": exit_stats}


def generate_markdown_report(entry_stats: dict, exit_stats: dict, output_path: Path):
    """Generate markdown report."""
    report = []
    report.append("# Time-of-Day & Exit Reason Analysis\n")
    report.append("---\n")
    
    # Entry hour analysis
    if "by_entry_hour" in entry_stats:
        report.append("## Performance by Entry Hour\n")
        report.append("| Hour | Trades | Expectancy | Win Rate | Sharpe | Total R |")
        report.append("|------|--------|-----------|----------|--------|---------|")
        for stat in entry_stats["by_entry_hour"]:
            report.append(
                f"| {stat['hour']} | {stat['count']} | {stat['expectancy']:+.3f}R | "
                f"{stat['win_rate']:.1f}% | {stat['sharpe']:.2f} | {stat['total_r']:+.1f}R |"
            )
        report.append("")
        
        # Key insights
        best_hour = max(entry_stats["by_entry_hour"], key=lambda x: x["expectancy"])
        worst_hour = min(entry_stats["by_entry_hour"], key=lambda x: x["expectancy"])
        report.append("### Key Insights\n")
        report.append(f"- **Best entry hour:** {best_hour['hour']} ({best_hour['expectancy']:+.3f}R expectancy)")
        report.append(f"- **Worst entry hour:** {worst_hour['hour']} ({worst_hour['expectancy']:+.3f}R expectancy)")
        report.append("")
    
    # Exit reason analysis
    if "by_exit_reason" in exit_stats:
        report.append("## Performance by Exit Reason\n")
        report.append("| Exit Reason | Trades | % of Total | Expectancy | Win Rate | Total R | Avg Winner | Avg Loser |")
        report.append("|-------------|--------|-----------|-----------|----------|---------|------------|-----------|")
        for stat in exit_stats["by_exit_reason"]:
            report.append(
                f"| {stat['exit_reason']} | {stat['count']} | {stat['pct_of_total']:.1f}% | "
                f"{stat['expectancy']:+.3f}R | {stat['win_rate']:.1f}% | {stat['total_r']:+.1f}R | "
                f"{stat['mean_r_winners']:+.2f}R | {stat['mean_r_losers']:+.2f}R |"
            )
        report.append("")
        
        # Key insights
        if len(exit_stats["by_exit_reason"]) > 0:
            total_trades = sum(s["count"] for s in exit_stats["by_exit_reason"])
            eod_trades = next((s for s in exit_stats["by_exit_reason"] if s["exit_reason"] == "EOD"), None)
            stop_trades = next((s for s in exit_stats["by_exit_reason"] if s["exit_reason"] == "STOP"), None)
            
            report.append("### Key Insights\n")
            if eod_trades:
                report.append(f"- **EOD exits:** {eod_trades['count']} trades ({eod_trades['pct_of_total']:.1f}%) with {eod_trades['expectancy']:+.3f}R expectancy")
                report.append(f"  - These are the **winners that ran** until market close")
                report.append(f"  - Win rate: {eod_trades['win_rate']:.1f}%")
                report.append(f"  - Average winner: {eod_trades['mean_r_winners']:+.2f}R")
            if stop_trades:
                report.append(f"- **STOP exits:** {stop_trades['count']} trades ({stop_trades['pct_of_total']:.1f}%) with {stop_trades['expectancy']:+.3f}R expectancy")
                report.append(f"  - These are the **stopped out trades** (mostly losers)")
                report.append(f"  - Win rate: {stop_trades['win_rate']:.1f}%")
                report.append(f"  - Average loser: {stop_trades['mean_r_losers']:+.2f}R")
            report.append("")
            
            if eod_trades and stop_trades:
                eod_contribution = eod_trades["total_r"]
                stop_contribution = stop_trades["total_r"]
                total_r = eod_contribution + stop_contribution
                report.append(f"**Contribution to Total R:**")
                report.append(f"- EOD exits contribute: {eod_contribution:+.1f}R ({eod_contribution/total_r*100:.1f}% of total)")
                report.append(f"- STOP exits contribute: {stop_contribution:+.1f}R ({stop_contribution/total_r*100:.1f}% of total)")
                report.append("")
    
    # Write report
    output_path.write_text("\n".join(report), encoding="utf-8")
    print(f"Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze time-of-day and exit reason patterns")
    parser.add_argument("--trades-csv", required=True, help="Path to trades CSV")
    parser.add_argument("--output", required=True, help="Output directory")
    args = parser.parse_args()
    
    # Load trades
    print(f"Loading trades from {args.trades_csv}...")
    trades = pd.read_csv(args.trades_csv)
    trades.columns = [c.lower().strip() for c in trades.columns]
    print(f"Loaded {len(trades)} trades")
    
    # Analyze
    print("Analyzing entry hour patterns...")
    entry_stats = analyze_entry_hour(trades)
    
    print("Analyzing exit reason patterns...")
    exit_stats = analyze_exit_reason(trades)
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save JSON
    all_stats = {
        "entry_hour": entry_stats,
        "exit_reason": exit_stats,
    }
    json_path = output_dir / "time_analysis.json"
    with open(json_path, "w") as f:
        json.dump(all_stats, f, indent=2)
    print(f"Statistics saved to: {json_path}")
    
    # Generate markdown report
    print("Generating markdown report...")
    report_path = output_dir / "time_analysis.md"
    generate_markdown_report(entry_stats, exit_stats, report_path)
    
    print("\n✅ Time-of-day analysis complete!")


if __name__ == "__main__":
    main()
