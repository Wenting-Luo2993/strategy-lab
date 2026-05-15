"""
Stage 4 tests — Filter Evaluator.

Exit gate: all P0 + P1 tests must pass. P2 tests must pass before promoting
any filter to production use.

Run: pytest vibe/tests/backtester/regime_research/test_filter.py -v
"""

import math

import numpy as np
import pandas as pd
import pytest

from vibe.backtester.analysis.regime_research.filter_evaluator import FilterEvaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trades(
    n: int = 100,
    seed: int = 0,
    atr_pctile: np.ndarray | None = None,
    regime: list[str] | None = None,
    entry_start: str = "2022-01-03",
) -> pd.DataFrame:
    """Synthetic enriched trade DataFrame."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(entry_start, periods=n, freq="B")

    if atr_pctile is None:
        atr_pctile = rng.uniform(0, 1, n)
    if regime is None:
        regimes = ["trending_up", "trending_down", "ranging"]
        regime = rng.choice(regimes, size=n).tolist()

    return pd.DataFrame(
        {
            "entry_time": idx,
            "pnl_r":      rng.normal(0.1, 1.0, n),
            "atr_pctile": atr_pctile,
            "regime":     regime,
        }
    )


def _make_known_trades() -> pd.DataFrame:
    """Trades with deterministic pnl_r values for metric verification."""
    idx = pd.date_range("2022-01-03", periods=6, freq="B")
    return pd.DataFrame(
        {
            "entry_time": idx,
            "pnl_r":      [1.0, -0.5, 2.0, -1.0, 0.5, 1.5],
            "atr_pctile": [0.2,  0.3, 0.7,  0.8, 0.4, 0.6],
            "regime":     ["trending_up", "ranging", "trending_up",
                           "ranging", "trending_up", "trending_up"],
        }
    )


# ---------------------------------------------------------------------------
# P0 Tests
# ---------------------------------------------------------------------------

class TestP0:
    def test_filter_removes_correct_trades(self):
        """atr_pctile < 0.5 → only trades with atr_pctile < 0.5 remain."""
        trades = _make_trades(200, seed=1)
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "atr_pctile < 0.5")

        expected = trades.query("atr_pctile < 0.5")
        assert report.filtered.trade_count == len(expected), (
            f"Expected {len(expected)} trades, got {report.filtered.trade_count}"
        )

    def test_regime_filter_works(self):
        """regime == 'trending_up' → only trades on trending_up days remain."""
        trades = _make_trades(200, seed=2)
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "regime == 'trending_up'")

        expected = trades.query("regime == 'trending_up'")
        assert report.filtered.trade_count == len(expected)

    def test_no_filter_baseline_equivalence(self):
        """Empty filter must reproduce original metrics exactly."""
        trades = _make_trades(100, seed=3)
        evaluator = FilterEvaluator()

        report_no_filter = evaluator.evaluate(trades, "")
        report_full = evaluator.evaluate(trades, "")

        assert report_no_filter.filtered.trade_count == len(trades)
        assert math.isclose(
            report_no_filter.filtered.expectancy,
            report_no_filter.baseline.expectancy,
            rel_tol=1e-9,
        ), "No-filter expectancy must match baseline exactly"
        assert math.isclose(
            report_no_filter.filtered.sharpe,
            report_no_filter.baseline.sharpe,
            rel_tol=1e-9,
        )


# ---------------------------------------------------------------------------
# P1 Tests
# ---------------------------------------------------------------------------

class TestP1:
    def test_filter_cannot_increase_trade_count(self):
        """Filtered trade count must always be <= original."""
        trades = _make_trades(150, seed=4)
        evaluator = FilterEvaluator()

        for expr in ["atr_pctile < 0.5", "regime == 'ranging'", ""]:
            report = evaluator.evaluate(trades, expr)
            assert report.filtered.trade_count <= report.baseline.trade_count, (
                f"Filter '{expr}' increased trade count"
            )

    def test_extreme_filter_returns_zero_trades(self):
        """atr_pctile > 2 → 0 trades, no crash."""
        trades = _make_trades(100, seed=5)
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "atr_pctile > 2")

        assert report.filtered.trade_count == 0
        assert math.isnan(report.filtered.expectancy)

    def test_filter_metrics_recomputed_from_filtered(self):
        """Metrics must reflect the filtered trade set, not the full set."""
        trades = _make_known_trades()
        evaluator = FilterEvaluator()

        # Filter to trending_up only: pnl_r = [1.0, 2.0, 0.5, 1.5] → mean = 1.25
        report = evaluator.evaluate(trades, "regime == 'trending_up'")

        expected_expectancy = (1.0 + 2.0 + 0.5 + 1.5) / 4
        assert math.isclose(report.filtered.expectancy, expected_expectancy, rel_tol=1e-9), (
            f"Filtered expectancy {report.filtered.expectancy} != expected {expected_expectancy}"
        )
        assert report.filtered.trade_count == 4

    def test_random_strategy_no_regime_edge(self):
        """Random R-multiples should not show consistent regime edge across seeds."""
        evaluator = FilterEvaluator()
        improvements = 0
        trials = 20

        for seed in range(trials):
            rng = np.random.default_rng(seed)
            n = 200
            idx = pd.date_range("2022-01-03", periods=n, freq="B")
            # Completely random R-multiples (zero edge)
            pnl = rng.standard_normal(n)
            regimes = rng.choice(["trending_up", "trending_down", "ranging"], n)
            trades = pd.DataFrame(
                {"entry_time": idx, "pnl_r": pnl, "atr_pctile": rng.uniform(0, 1, n),
                 "regime": regimes}
            )
            report = evaluator.evaluate(trades, "regime == 'trending_up'")
            if (
                report.filtered.trade_count >= 10
                and not math.isnan(report.filtered.expectancy)
                # Absolute lift of >0.4R on random data is a suspicious false discovery
                and (report.filtered.expectancy - report.baseline.expectancy) > 0.4
            ):
                improvements += 1

        # On random data, strong apparent improvement should be rare (< 20% of trials)
        assert improvements / trials <= 0.20, (
            f"Random strategy showed regime edge in {improvements}/{trials} trials — "
            "possible data mining bug"
        )

    def test_shuffled_outcomes_weaken_regime_effect(self):
        """Shuffling pnl_r should reduce any regime-based performance difference."""
        rng = np.random.default_rng(7)
        n = 300
        idx = pd.date_range("2022-01-03", periods=n, freq="B")

        # Build trades where trending_up genuinely has higher returns
        regimes = rng.choice(["trending_up", "ranging"], n, p=[0.4, 0.6])
        pnl = np.where(regimes == "trending_up",
                        rng.normal(0.3, 1.0, n),
                        rng.normal(-0.1, 1.0, n))
        trades = pd.DataFrame(
            {"entry_time": idx, "pnl_r": pnl, "atr_pctile": rng.uniform(0, 1, n),
             "regime": regimes}
        )

        evaluator = FilterEvaluator()
        original_report = evaluator.evaluate(trades, "regime == 'trending_up'")
        original_lift = (
            original_report.filtered.expectancy - original_report.baseline.expectancy
        )

        # Shuffle pnl_r (breaks any regime relationship)
        trades_shuffled = trades.copy()
        trades_shuffled["pnl_r"] = rng.permutation(trades_shuffled["pnl_r"].values)

        shuffled_report = evaluator.evaluate(trades_shuffled, "regime == 'trending_up'")
        shuffled_lift = (
            shuffled_report.filtered.expectancy - shuffled_report.baseline.expectancy
        )

        assert abs(shuffled_lift) < abs(original_lift), (
            "Shuffled outcomes should reduce regime effect, but the lift did not decrease"
        )


# ---------------------------------------------------------------------------
# P2 Tests
# ---------------------------------------------------------------------------

class TestP2:
    def test_narrow_threshold_warning_emitted(self):
        """Filter on a tiny numeric range → NARROW_THRESHOLD warning."""
        trades = _make_trades(200, seed=8)
        evaluator = FilterEvaluator()

        # atr_pctile range is [0,1]; filter width 0.004 = 0.4% of range → narrow
        report = evaluator.evaluate(trades, "atr_pctile >= 0.423 and atr_pctile <= 0.427")

        codes = [w.code for w in report.warnings]
        assert "NARROW_THRESHOLD" in codes, (
            f"Expected NARROW_THRESHOLD warning, got: {codes}"
        )

    def test_tiny_sample_warning_emitted(self):
        """Filter leaving < 30 trades → TINY_SAMPLE warning."""
        # Create a dataset where only 5 trades have atr_pctile > 0.99
        rng = np.random.default_rng(9)
        n = 200
        idx = pd.date_range("2022-01-03", periods=n, freq="B")
        atr = np.full(n, 0.5)
        atr[:5] = 0.995  # only 5 trades pass the filter

        trades = pd.DataFrame(
            {
                "entry_time": idx,
                "pnl_r": rng.normal(0, 1, n),
                "atr_pctile": atr,
                "regime": ["ranging"] * n,
            }
        )
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "atr_pctile > 0.99")

        codes = [w.code for w in report.warnings]
        assert "TINY_SAMPLE" in codes, (
            f"Expected TINY_SAMPLE warning, got: {codes}"
        )

    def test_single_year_stability_warning_emitted(self):
        """Filter improves only 1 of 5 years → SINGLE_YEAR_STABILITY warning."""
        # Build multi-year trades where trending_up only works in 2022
        years_data = []
        for yr, expectancy_trend in [(2022, 1.0), (2023, -0.5), (2024, -0.3),
                                      (2025, -0.4), (2026, -0.6)]:
            rng = np.random.default_rng(yr)
            n = 50
            idx = pd.date_range(f"{yr}-01-03", periods=n, freq="B")
            regime = rng.choice(["trending_up", "ranging"], n)
            pnl = np.where(regime == "trending_up",
                            rng.normal(expectancy_trend, 1.0, n),
                            rng.normal(0.0, 1.0, n))
            years_data.append(pd.DataFrame({
                "entry_time": idx, "pnl_r": pnl, "atr_pctile": 0.5,
                "regime": regime,
            }))

        trades = pd.concat(years_data, ignore_index=True)
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "regime == 'trending_up'")

        codes = [w.code for w in report.warnings]
        assert "SINGLE_YEAR_STABILITY" in codes, (
            f"Expected SINGLE_YEAR_STABILITY warning, got: {codes}\n"
            f"Yearly filtered metrics: {[(ym.year, ym.metrics.expectancy) for ym in report.yearly_filtered]}"
        )
