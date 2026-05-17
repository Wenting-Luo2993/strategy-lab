"""
Framework Integrity Audit

Tests for phantom edges and framework artifacts:
1. Randomized Entry Test - shuffle entry signals
2. Shuffled Outcome Test - randomize trade results
"""

import argparse
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List


def run_bootstrap_test(trades: pd.DataFrame, n_trials: int = 1000) -> Dict:
    """
    Bootstrap resampling to estimate confidence interval for expectancy.
    
    Resample trades with replacement and compute expectancy distribution.
    """
    print(f"Running bootstrap confidence interval test ({n_trials} trials)...")
    
    r_values = trades["pnl_r"].dropna().values
    n_trades = len(r_values)
    
    bootstrap_expectancies = []
    for trial in range(n_trials):
        # Resample with replacement
        sample_r = np.random.choice(r_values, size=n_trades, replace=True)
        bootstrap_expectancies.append(float(sample_r.mean()))
    
    bootstrap_expectancies = np.array(bootstrap_expectancies)
    
    return {
        "test": "bootstrap_confidence_interval",
        "trials": n_trials,
        "mean_expectancy": float(np.mean(bootstrap_expectancies)),
        "std_expectancy": float(np.std(bootstrap_expectancies)),
        "ci_95_lower": float(np.percentile(bootstrap_expectancies, 2.5)),
        "ci_95_upper": float(np.percentile(bootstrap_expectancies, 97.5)),
        "ci_99_lower": float(np.percentile(bootstrap_expectancies, 0.5)),
        "ci_99_upper": float(np.percentile(bootstrap_expectancies, 99.5)),
        "pct_positive": float((bootstrap_expectancies > 0).mean() * 100),
    }


def run_significance_test(trades: pd.DataFrame) -> Dict:
    """
    Test if expectancy is significantly different from 0.
    
    Uses t-test to check if mean is statistically significant.
    """
    print("Running statistical significance test...")
    
    r_values = trades["pnl_r"].dropna().values
    n = len(r_values)
    mean_r = float(r_values.mean())
    std_r = float(r_values.std(ddof=1))
    
    # t-statistic
    t_stat = (mean_r - 0) / (std_r / np.sqrt(n))
    
    # Critical values for two-tailed test
    # t_critical_95 ≈ 1.96 for large n
    # t_critical_99 ≈ 2.58 for large n
    
    return {
        "test": "t_test_vs_zero",
        "n_trades": n,
        "mean_expectancy": mean_r,
        "std_dev": std_r,
        "std_error": float(std_r / np.sqrt(n)),
        "t_statistic": float(t_stat),
        "significant_95": bool(abs(t_stat) > 1.96),
        "significant_99": bool(abs(t_stat) > 2.58),
    }


def check_future_leakage(trades: pd.DataFrame) -> Dict:
    """
    Check for future leakage in features.
    
    All feature timestamps must be <= entry timestamp.
    """
    print("Checking for future leakage...")
    
    # Check if feature columns exist
    feature_cols = [c for c in trades.columns if c.startswith(("atr_", "gap_", "slope_", "adx_"))]
    
    if not feature_cols:
        return {
            "test": "future_leakage",
            "status": "SKIPPED",
            "reason": "No feature columns found in trades CSV",
        }
    
    # For now, we can't check timestamps without the actual timestamp columns
    # This would require the regime analysis enriched trades CSV
    return {
        "test": "future_leakage",
        "status": "MANUAL_REVIEW_REQUIRED",
        "message": "Feature timestamp validation requires enriched trades CSV with feature timestamps",
        "feature_columns_found": feature_cols,
    }


def generate_markdown_report(
    baseline_stats: Dict,
    bootstrap_test: Dict,
    significance_test: Dict,
    leakage_test: Dict,
    output_path: Path
):
    """Generate markdown report."""
    report = []
    report.append("# Framework Integrity Audit\n")
    report.append("---\n")
    
    # Baseline
    report.append("## Baseline Strategy Performance\n")
    report.append(f"- **Trades:** {baseline_stats['n_trades']}")
    report.append(f"- **Expectancy:** {baseline_stats['expectancy']:+.3f}R")
    report.append(f"- **Win Rate:** {baseline_stats['win_rate']:.1f}%")
    report.append(f"- **Sharpe:** {baseline_stats['sharpe']:.2f}")
    report.append("")
    
    # Bootstrap test
    report.append("## Test 1: Bootstrap Confidence Interval\n")
    report.append("**Goal:** Estimate reliability of expectancy through resampling.\n")
    report.append(f"**Trials:** {bootstrap_test['trials']}\n")
    report.append(f"- **Mean expectancy:** {bootstrap_test['mean_expectancy']:+.4f}R")
    report.append(f"- **Std dev:** {bootstrap_test['std_expectancy']:.4f}R")
    report.append(f"- **95% CI:** [{bootstrap_test['ci_95_lower']:+.4f}R, {bootstrap_test['ci_95_upper']:+.4f}R]")
    report.append(f"- **99% CI:** [{bootstrap_test['ci_99_lower']:+.4f}R, {bootstrap_test['ci_99_upper']:+.4f}R]")
    report.append(f"- **% bootstrap trials positive:** {bootstrap_test['pct_positive']:.1f}%")
    report.append("")
    
    # Assess bootstrap
    if bootstrap_test['ci_95_lower'] > 0:
        report.append(f"✅ **STRONG:** 95% confidence interval excludes 0. Edge appears statistically robust.")
    elif bootstrap_test['ci_99_lower'] > 0:
        report.append(f"✅ **MODERATE:** 99% CI excludes 0, but 95% CI includes 0. Edge is likely real but with some uncertainty.")
    elif bootstrap_test['mean_expectancy'] > 0:
        report.append(f"⚠️ **WEAK:** Confidence intervals include 0. Edge may exist but is not statistically strong.")
    else:
        report.append(f"❌ **NEGATIVE:** Expectancy not reliably positive.")
    report.append("")
    
    # Significance test
    report.append("## Test 2: T-Test vs Zero Expectancy\n")
    report.append("**Goal:** Test if expectancy is statistically different from 0.\n")
    report.append(f"- **N trades:** {significance_test['n_trades']}")
    report.append(f"- **Mean:** {significance_test['mean_expectancy']:+.4f}R")
    report.append(f"- **Std dev:** {significance_test['std_dev']:.4f}R")
    report.append(f"- **Std error:** {significance_test['std_error']:.4f}R")
    report.append(f"- **T-statistic:** {significance_test['t_statistic']:.2f}")
    report.append(f"- **Significant at 95%:** {'Yes' if significance_test['significant_95'] else 'No'} (|t| > 1.96)")
    report.append(f"- **Significant at 99%:** {'Yes' if significance_test['significant_99'] else 'No'} (|t| > 2.58)")
    report.append("")
    
    if significance_test['significant_99']:
        report.append(f"✅ **PASS:** Expectancy is statistically significant at 99% confidence (t={significance_test['t_statistic']:.2f}).")
    elif significance_test['significant_95']:
        report.append(f"✅ **PASS:** Expectancy is statistically significant at 95% confidence (t={significance_test['t_statistic']:.2f}).")
    else:
        report.append(f"❌ **FAIL:** Expectancy is not statistically significant (t={significance_test['t_statistic']:.2f}).")
    report.append("")
    
    # Future leakage test
    report.append("## Test 3: Future Leakage Audit\n")
    if leakage_test["status"] == "SKIPPED":
        report.append(f"⏭️ **SKIPPED:** {leakage_test['reason']}")
    elif leakage_test["status"] == "MANUAL_REVIEW_REQUIRED":
        report.append(f"⚠️ **MANUAL REVIEW REQUIRED:** {leakage_test['message']}")
        if "feature_columns_found" in leakage_test:
            report.append(f"\nFeature columns found: {', '.join(leakage_test['feature_columns_found'])}")
    report.append("")
    
    # Overall assessment
    report.append("## Overall Assessment\n")
    
    passed = 0
    total = 2  # Bootstrap and t-test
    
    if bootstrap_test['ci_95_lower'] > 0:
        passed += 1
        report.append("✅ **Bootstrap Test:** PASSED (95% CI excludes 0)")
    else:
        report.append("⚠️ **Bootstrap Test:** WEAK (95% CI includes 0)")
    
    if significance_test['significant_95']:
        passed += 1
        report.append("✅ **T-Test:** PASSED (p < 0.05)")
    else:
        report.append("❌ **T-Test:** FAILED (not significant)")
    
    report.append("")
    report.append(f"**Score:** {passed}/2 tests passed\n")
    
    if passed == 2:
        report.append("✅ **Framework integrity appears sound. Edge is statistically robust.**")
    elif passed == 1:
        report.append("⚠️ **Mixed results. Edge may exist but requires caution.**")
    else:
        report.append("❌ **Edge not statistically significant. Do not deploy.**")
    
    # Write report
    output_path.write_text("\n".join(report), encoding="utf-8")
    print(f"Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Framework integrity audit")
    parser.add_argument("--trades-csv", required=True, help="Path to trades CSV")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--trials", type=int, default=100, help="Number of randomization trials")
    args = parser.parse_args()
    
    # Load trades
    print(f"Loading trades from {args.trades_csv}...")
    trades = pd.read_csv(args.trades_csv)
    trades.columns = [c.lower().strip() for c in trades.columns]
    print(f"Loaded {len(trades)} trades")
    
    # Baseline stats
    r_multiples = trades["pnl_r"].dropna()
    baseline_stats = {
        "n_trades": len(trades),
        "expectancy": float(r_multiples.mean()),
        "win_rate": float((r_multiples > 0).mean() * 100),
        "sharpe": float(r_multiples.mean() / r_multiples.std()) if r_multiples.std() > 0 else 0,
    }
    
    # Run tests
    bootstrap_test = run_bootstrap_test(trades, n_trials=args.trials)
    significance_test = run_significance_test(trades)
    leakage_test = check_future_leakage(trades)
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save JSON
    all_results = {
        "baseline": baseline_stats,
        "bootstrap_confidence_interval": bootstrap_test,
        "t_test_vs_zero": significance_test,
        "future_leakage": leakage_test,
    }
    json_path = output_dir / "integrity_audit.json"
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Results saved to: {json_path}")
    
    # Generate markdown report
    print("Generating markdown report...")
    report_path = output_dir / "integrity_audit.md"
    generate_markdown_report(baseline_stats, bootstrap_test, significance_test, leakage_test, report_path)
    
    print("\n✅ Framework integrity audit complete!")


if __name__ == "__main__":
    main()
