"""
Trade Distribution & Convexity Analysis

Analyzes R-multiple distribution, tail contribution, and time-in-trade patterns
to validate convex payoff structure and understand edge mechanics.

Usage:
    python scripts/analyze_trade_distribution.py \
        --trades-csv reports/.../orb_trades_no_tp.csv \
        --output reports/.../distribution_analysis
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Warning: matplotlib not available. Plots will be skipped.")

try:
    from scipy import stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("Warning: scipy not available. Using numpy for basic stats.")


def load_trades(path: Path) -> pd.DataFrame:
    """Load trades CSV with proper datetime parsing."""
    trades = pd.read_csv(path)
    trades.columns = [c.lower().strip() for c in trades.columns]
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_localize(None)
    if "exit_time" in trades.columns:
        trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True).dt.tz_localize(None)
    return trades


def calculate_holding_duration(trades: pd.DataFrame) -> pd.DataFrame:
    """Calculate holding duration in minutes."""
    if "exit_time" in trades.columns and "entry_time" in trades.columns:
        trades["holding_minutes"] = (
            (trades["exit_time"] - trades["entry_time"]).dt.total_seconds() / 60
        )
    return trades


def analyze_r_distribution(trades: pd.DataFrame) -> dict:
    """Analyze R-multiple distribution statistics."""
    r_multiples = trades["pnl_r"].dropna()
    
    # Basic stats
    if HAS_SCIPY:
        skewness = float(stats.skew(r_multiples))
        kurtosis = float(stats.kurtosis(r_multiples))
    else:
        # Numpy fallback
        skewness = float(pd.Series(r_multiples).skew())
        kurtosis = float(pd.Series(r_multiples).kurtosis())
    
    stats_dict = {
        "count": len(r_multiples),
        "mean": float(r_multiples.mean()),
        "median": float(r_multiples.median()),
        "std": float(r_multiples.std()),
        "skewness": skewness,
        "kurtosis": kurtosis,
        "min": float(r_multiples.min()),
        "max": float(r_multiples.max()),
    }
    
    # Percentiles
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    for p in percentiles:
        stats_dict[f"p{p}"] = float(np.percentile(r_multiples, p))
    
    # Tail analysis
    total_r = r_multiples.sum()
    sorted_trades = trades.sort_values("pnl_r", ascending=False)
    
    # Calculate total from winners vs losers
    winning_r = sorted_trades[sorted_trades["pnl_r"] > 0]["pnl_r"].sum()
    losing_r = sorted_trades[sorted_trades["pnl_r"] <= 0]["pnl_r"].sum()
    
    for pct in [1, 5, 10, 20]:
        n_trades = max(1, int(len(sorted_trades) * pct / 100))
        top_contribution = sorted_trades.head(n_trades)["pnl_r"].sum()
        stats_dict[f"top_{pct}pct_r"] = float(top_contribution)
        stats_dict[f"top_{pct}pct_contribution_pct"] = float(top_contribution / total_r * 100) if total_r != 0 else 0
        stats_dict[f"top_{pct}pct_of_winners_pct"] = float(top_contribution / winning_r * 100) if winning_r > 0 else 0
        stats_dict[f"top_{pct}pct_count"] = n_trades
    
    # Also calculate bottom 90% for context
    bottom_90_contribution = sorted_trades.tail(len(sorted_trades) - max(1, int(len(sorted_trades) * 0.10))).loc[:, "pnl_r"].sum()
    stats_dict["bottom_90pct_r"] = float(bottom_90_contribution)
    stats_dict["total_r"] = float(total_r)
    stats_dict["winning_r"] = float(winning_r)
    stats_dict["losing_r"] = float(losing_r)
    
    return stats_dict


def analyze_time_in_trade(trades: pd.DataFrame) -> dict:
    """Analyze expectancy by holding duration."""
    if "holding_minutes" not in trades.columns:
        return {"error": "No holding duration data available"}
    
    # Bin by duration
    bins = [0, 30, 60, 120, 180, 240, 300, 360, 999]
    labels = ["0-30m", "30-60m", "1-2h", "2-3h", "3-4h", "4-5h", "5-6h", "6h+"]
    
    trades["duration_bin"] = pd.cut(trades["holding_minutes"], bins=bins, labels=labels)
    
    duration_stats = []
    for bin_label in labels:
        bin_trades = trades[trades["duration_bin"] == bin_label]
        if len(bin_trades) > 0:
            duration_stats.append({
                "duration": bin_label,
                "count": len(bin_trades),
                "expectancy": float(bin_trades["pnl_r"].mean()),
                "win_rate": float((bin_trades["pnl_r"] > 0).mean() * 100),
                "total_r": float(bin_trades["pnl_r"].sum()),
            })
    
    return {"by_duration": duration_stats}


def analyze_exit_time_distribution(trades: pd.DataFrame) -> dict:
    """Analyze when trades exit during the day."""
    if "exit_time" not in trades.columns:
        return {"error": "No exit time data available"}
    
    trades["exit_hour"] = trades["exit_time"].dt.hour
    trades["exit_minute"] = trades["exit_time"].dt.minute
    trades["exit_time_of_day"] = trades["exit_hour"] + trades["exit_minute"] / 60
    
    # Bin by hour
    hour_bins = list(range(10, 17))  # 10am to 4pm
    hour_stats = []
    
    for hour in hour_bins:
        hour_trades = trades[(trades["exit_hour"] == hour)]
        if len(hour_trades) > 0:
            hour_stats.append({
                "hour": f"{hour:02d}:00",
                "count": len(hour_trades),
                "expectancy": float(hour_trades["pnl_r"].mean()),
                "win_rate": float((hour_trades["pnl_r"] > 0).mean() * 100),
                "total_r": float(hour_trades["pnl_r"].sum()),
            })
    
    return {"by_exit_hour": hour_stats}


def analyze_long_vs_short(trades: pd.DataFrame) -> dict:
    """Decompose long vs short performance."""
    if "direction" not in trades.columns:
        return {"error": "No direction column available"}
    
    long_short_stats = []
    for direction in ["long", "short"]:
        dir_trades = trades[trades["direction"] == direction]
        if len(dir_trades) > 0:
            r_multiples = dir_trades["pnl_r"].dropna()
            if HAS_SCIPY:
                skewness = float(stats.skew(r_multiples))
            else:
                skewness = float(pd.Series(r_multiples).skew())
            
            long_short_stats.append({
                "direction": direction,
                "count": len(dir_trades),
                "expectancy": float(r_multiples.mean()),
                "win_rate": float((r_multiples > 0).mean() * 100),
                "sharpe": float(r_multiples.mean() / r_multiples.std()) if r_multiples.std() > 0 else 0,
                "total_r": float(r_multiples.sum()),
                "skewness": skewness,
                "max_r": float(r_multiples.max()),
                "min_r": float(r_multiples.min()),
            })
    
    return {"by_direction": long_short_stats}


def plot_r_distribution(trades: pd.DataFrame, output_dir: Path):
    """Generate R-multiple histogram."""
    if not HAS_MATPLOTLIB:
        return
    
    r_multiples = trades["pnl_r"].dropna()
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # Histogram
    ax1.hist(r_multiples, bins=50, alpha=0.7, edgecolor="black")
    ax1.axvline(x=0, color="red", linestyle="--", label="Breakeven")
    ax1.axvline(x=r_multiples.mean(), color="green", linestyle="--", label=f"Mean: {r_multiples.mean():.2f}R")
    ax1.set_xlabel("R-Multiple")
    ax1.set_ylabel("Frequency")
    ax1.set_title("R-Multiple Distribution")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Cumulative contribution
    sorted_trades = trades.sort_values("pnl_r", ascending=False).reset_index(drop=True)
    sorted_trades["cumulative_r"] = sorted_trades["pnl_r"].cumsum()
    total_r = sorted_trades["pnl_r"].sum()
    sorted_trades["cumulative_pct"] = sorted_trades["cumulative_r"] / total_r * 100 if total_r != 0 else 0
    
    ax2.plot(sorted_trades.index, sorted_trades["cumulative_pct"])
    ax2.axhline(y=50, color="red", linestyle="--", alpha=0.5, label="50% of profits")
    ax2.axhline(y=80, color="orange", linestyle="--", alpha=0.5, label="80% of profits")
    ax2.set_xlabel("Trade Rank (Best to Worst)")
    ax2.set_ylabel("Cumulative % of Total R")
    ax2.set_title("Cumulative Profit Contribution")
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / "r_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()


def plot_time_in_trade(trades: pd.DataFrame, output_dir: Path):
    """Generate time-in-trade analysis plots."""
    if not HAS_MATPLOTLIB or "holding_minutes" not in trades.columns:
        return
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # Holding duration histogram
    ax1.hist(trades["holding_minutes"].dropna(), bins=50, alpha=0.7, edgecolor="black")
    ax1.axvline(x=trades["holding_minutes"].mean(), color="red", linestyle="--", 
                label=f"Mean: {trades['holding_minutes'].mean():.0f} min")
    ax1.set_xlabel("Holding Duration (minutes)")
    ax1.set_ylabel("Frequency")
    ax1.set_title("Holding Duration Distribution")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Expectancy by duration bin
    if "duration_bin" in trades.columns:
        duration_exp = trades.groupby("duration_bin", observed=True)["pnl_r"].mean()
        ax2.bar(range(len(duration_exp)), duration_exp.values, alpha=0.7, edgecolor="black")
        ax2.set_xticks(range(len(duration_exp)))
        ax2.set_xticklabels(duration_exp.index, rotation=45)
        ax2.axhline(y=0, color="red", linestyle="--", alpha=0.5)
        ax2.set_xlabel("Holding Duration")
        ax2.set_ylabel("Expectancy (R)")
        ax2.set_title("Expectancy by Holding Duration")
        ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / "time_in_trade.png", dpi=150, bbox_inches="tight")
    plt.close()


def generate_markdown_report(all_stats: dict, output_dir: Path):
    """Generate markdown summary report."""
    report = []
    report.append("# Trade Distribution & Convexity Analysis\n")
    report.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.append("---\n")
    
    # R-distribution
    if "r_distribution" in all_stats:
        r_stats = all_stats["r_distribution"]
        report.append("## R-Multiple Distribution\n")
        report.append(f"- **Count:** {r_stats['count']}")
        report.append(f"- **Mean:** {r_stats['mean']:.3f}R")
        report.append(f"- **Median:** {r_stats['median']:.3f}R")
        report.append(f"- **Std Dev:** {r_stats['std']:.3f}R")
        report.append(f"- **Skewness:** {r_stats['skewness']:.3f} {'✅ (positive skew)' if r_stats['skewness'] > 0 else '❌ (negative skew)'}")
        report.append(f"- **Kurtosis:** {r_stats['kurtosis']:.3f}")
        report.append(f"- **Range:** {r_stats['min']:.2f}R to {r_stats['max']:.2f}R\n")
        
        report.append("### Percentiles\n")
        report.append("| Percentile | R-Multiple |")
        report.append("|------------|------------|")
        for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
            report.append(f"| {p}th | {r_stats[f'p{p}']:.2f}R |")
        report.append("")
        
        report.append("### Tail Contribution Analysis\n")
        report.append("| Top % | # Trades | R Contribution | % of Net R | % of Winner R |")
        report.append("|-------|----------|----------------|------------|---------------|")
        for pct in [1, 5, 10, 20]:
            r_contrib = r_stats.get(f'top_{pct}pct_r', 0)
            pct_contrib = r_stats.get(f'top_{pct}pct_contribution_pct', 0)
            pct_of_winners = r_stats.get(f'top_{pct}pct_of_winners_pct', 0)
            report.append(f"| Top {pct}% | {r_stats[f'top_{pct}pct_count']} | **{r_contrib:+.1f}R** | {pct_contrib:.1f}% | {pct_of_winners:.1f}% |")
        
        # Add bottom 90% and total for context
        bottom_90_r = r_stats.get('bottom_90pct_r', 0)
        total_r = r_stats.get('total_r', 0)
        winning_r = r_stats.get('winning_r', 0)
        losing_r = r_stats.get('losing_r', 0)
        bottom_90_pct = (bottom_90_r / total_r * 100) if total_r != 0 else 0
        report.append(f"| Bottom 90% | {r_stats['count'] - r_stats['top_10pct_count']} | **{bottom_90_r:+.1f}R** | {bottom_90_pct:.1f}% | — |")
        report.append(f"| **All trades** | **{r_stats['count']}** | **{total_r:+.1f}R** | **100.0%** | — |")
        report.append("")
        
        report.append("**📌 How to Read This Table:**\n")
        report.append("Two ways to understand the concentration:\n")
        report.append(f"\n**Method 1: Relative to NET profit** (% of Net R column)")
        report.append(f"- Total net profit: {total_r:.1f}R")
        top_10_r = r_stats.get('top_10pct_r', 0)
        top_10_pct = r_stats.get('top_10pct_contribution_pct', 0)
        report.append(f"- Top 10% contribute: {top_10_r:+.1f}R")
        report.append(f"- Bottom 90% contribute: {bottom_90_r:+.1f}R")
        report.append(f"- Ratio: {top_10_r:.1f}R ÷ {total_r:.1f}R = **{top_10_pct:.1f}%** (top 10% must overcome all losses AND create net profit)\n")
        
        report.append(f"**Method 2: Relative to ALL WINNING trades** (% of Winner R column)")
        report.append(f"- Total from all winners: {winning_r:+.1f}R")
        report.append(f"- Total from all losers: {losing_r:+.1f}R")
        top_10_winner_pct = r_stats.get('top_10pct_of_winners_pct', 0)
        report.append(f"- Top 10% of ALL trades contribute: {top_10_r:+.1f}R")
        report.append(f"- Percentage of winning trade profits: {top_10_r:.1f}R ÷ {winning_r:.1f}R = **{top_10_winner_pct:.1f}%**")
        report.append(f"- This means: Just {r_stats['top_10pct_count']} trades (~{r_stats['top_10pct_count'] * 100 / r_stats['count']:.0f}% of all) generate {top_10_winner_pct:.0f}% of winning profits\n")
        
        # Convexity assessment
        report.append("### Convexity Assessment\n")
        if top_10_pct > 400:
            report.append(f"⚠️ **EXTREME CONCENTRATION**: Top 10% contribute {top_10_r:+.1f}R while bottom 90% contribute {bottom_90_r:+.1f}R ({top_10_pct:.1f}% ratio).\n")
            report.append(f"This suggests extreme tail dependence. Edge exists but is highly concentrated in ~{r_stats['top_10pct_count']} best trades out of {r_stats['count']}.\n")
            report.append("**Live trading will experience long losing streaks (20-40 trades) before hitting occasional big winners.**\n")
        elif top_10_pct > 250:
            report.append(f"✅ **CONVEX PAYOFF**: Top 10% contribute {top_10_r:+.1f}R vs bottom 90% {bottom_90_r:+.1f}R ({top_10_pct:.1f}% ratio).\n")
            report.append("This is typical of trend-following systems. Edge appears structurally sound.\n")
        else:
            report.append(f"📊 **DISTRIBUTED**: Top 10% contribute {top_10_r:+.1f}R ({top_10_pct:.1f}% ratio).\n")
            report.append("Profits are broadly distributed across trades.\n")
    
    # Long vs Short
    if "long_vs_short" in all_stats and "by_direction" in all_stats["long_vs_short"]:
        report.append("## Long vs Short Performance\n")
        report.append("| Direction | Trades | Expectancy | Win Rate | Sharpe | Total R | Skewness |")
        report.append("|-----------|--------|-----------|----------|--------|---------|----------|")
        for stat in all_stats["long_vs_short"]["by_direction"]:
            report.append(
                f"| {stat['direction'].capitalize()} | {stat['count']} | "
                f"{stat['expectancy']:.3f}R | {stat['win_rate']:.1f}% | "
                f"{stat['sharpe']:.2f} | {stat['total_r']:.1f}R | {stat['skewness']:.2f} |"
            )
        report.append("")
    
    # Time in trade
    if "time_in_trade" in all_stats and "by_duration" in all_stats["time_in_trade"]:
        report.append("## Expectancy by Holding Duration\n")
        report.append("| Duration | Trades | Expectancy | Win Rate | Total R |")
        report.append("|----------|--------|-----------|----------|---------|")
        for stat in all_stats["time_in_trade"]["by_duration"]:
            report.append(
                f"| {stat['duration']} | {stat['count']} | "
                f"{stat['expectancy']:.3f}R | {stat['win_rate']:.1f}% | {stat['total_r']:.1f}R |"
            )
        report.append("")
    
    # Exit time
    if "exit_time" in all_stats and "by_exit_hour" in all_stats["exit_time"]:
        report.append("## Expectancy by Exit Hour\n")
        report.append("| Hour | Trades | Expectancy | Win Rate | Total R |")
        report.append("|------|--------|-----------|----------|---------|")
        for stat in all_stats["exit_time"]["by_exit_hour"]:
            report.append(
                f"| {stat['hour']} | {stat['count']} | "
                f"{stat['expectancy']:.3f}R | {stat['win_rate']:.1f}% | {stat['total_r']:.1f}R |"
            )
        report.append("")
    
    # Save report
    report_path = output_dir / "distribution_analysis.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    print(f"Report saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze trade distribution and convexity")
    parser.add_argument("--trades-csv", required=True, help="Path to trades CSV")
    parser.add_argument("--output", required=True, help="Output directory for reports")
    args = parser.parse_args()
    
    # Load trades
    print(f"Loading trades from {args.trades_csv}...")
    trades = load_trades(Path(args.trades_csv))
    print(f"Loaded {len(trades)} trades")
    
    # Calculate holding duration
    trades = calculate_holding_duration(trades)
    
    # Run analyses
    print("Analyzing R-multiple distribution...")
    r_stats = analyze_r_distribution(trades)
    
    print("Analyzing time-in-trade patterns...")
    time_stats = analyze_time_in_trade(trades)
    
    print("Analyzing exit time distribution...")
    exit_stats = analyze_exit_time_distribution(trades)
    
    print("Analyzing long vs short performance...")
    long_short_stats = analyze_long_vs_short(trades)
    
    # Combine all stats
    all_stats = {
        "r_distribution": r_stats,
        "time_in_trade": time_stats,
        "exit_time": exit_stats,
        "long_vs_short": long_short_stats,
    }
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save JSON
    json_path = output_dir / "distribution_stats.json"
    with open(json_path, "w") as f:
        json.dump(all_stats, f, indent=2)
    print(f"Statistics saved to: {json_path}")
    
    # Generate plots
    print("Generating plots...")
    plot_r_distribution(trades, output_dir)
    plot_time_in_trade(trades, output_dir)
    
    # Generate markdown report
    print("Generating markdown report...")
    generate_markdown_report(all_stats, output_dir)
    
    print("\n✅ Distribution analysis complete!")


if __name__ == "__main__":
    main()
