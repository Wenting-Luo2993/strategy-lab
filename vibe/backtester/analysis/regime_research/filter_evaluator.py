"""
Filter Evaluator.

Applies a candidate regime filter to the enriched trade set, recomputes all
strategy metrics on the filtered subset, and flags overfitting signals.

Usage::

    evaluator = FilterEvaluator()
    report = evaluator.evaluate(enriched_trades, filter_expr="regime == 'trending_up'")
    print(report.summary())
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MetricsSnapshot:
    trade_count: int
    expectancy: float          # mean R-multiple
    win_rate: float            # fraction of positive pnl_r
    sharpe: float              # annualised on R-multiples (assumes ~252 trades/yr approx)
    max_drawdown: float        # max peak-to-trough in cumulative R
    profit_factor: float       # gross profits / gross losses
    convexity: float           # skewness of R-multiples (positive = right-skewed)


@dataclass
class YearlyMetrics:
    year: int
    metrics: MetricsSnapshot


@dataclass
class FilterWarning:
    code: str
    message: str


@dataclass
class FilterReport:
    filter_expr: str
    baseline: MetricsSnapshot
    filtered: MetricsSnapshot
    yearly_baseline: list[YearlyMetrics] = field(default_factory=list)
    yearly_filtered: list[YearlyMetrics] = field(default_factory=list)
    warnings: list[FilterWarning] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Filter: {self.filter_expr or '(none)'}",
            "",
            f"{'Metric':<20} {'Baseline':>12} {'Filtered':>12}",
            "-" * 46,
            f"{'Trade count':<20} {self.baseline.trade_count:>12} {self.filtered.trade_count:>12}",
            f"{'Expectancy (R)':<20} {self.baseline.expectancy:>12.4f} {self.filtered.expectancy:>12.4f}",
            f"{'Win rate':<20} {self.baseline.win_rate:>12.2%} {self.filtered.win_rate:>12.2%}",
            f"{'Sharpe':<20} {self.baseline.sharpe:>12.4f} {self.filtered.sharpe:>12.4f}",
            f"{'Max drawdown (R)':<20} {self.baseline.max_drawdown:>12.4f} {self.filtered.max_drawdown:>12.4f}",
            f"{'Profit factor':<20} {self.baseline.profit_factor:>12.4f} {self.filtered.profit_factor:>12.4f}",
            f"{'Convexity (skew)':<20} {self.baseline.convexity:>12.4f} {self.filtered.convexity:>12.4f}",
        ]
        if self.warnings:
            lines += ["", "Warnings:"]
            for w in self.warnings:
                lines.append(f"  [{w.code}] {w.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------

class FilterEvaluator:
    """Evaluate a candidate filter against the full enriched trade set."""

    # Minimum number of trades below which we warn about tiny sample
    MIN_SAMPLE = 30

    def evaluate(
        self,
        enriched_trades: pd.DataFrame,
        filter_expr: str = "",
    ) -> FilterReport:
        """
        Args:
            enriched_trades: Output of TradeAttributor.enrich().  Must contain
                             a ``pnl_r`` column (R-multiples) and optionally an
                             ``entry_time`` column for yearly breakdown.
            filter_expr: Pandas query string.  Use any column in enriched_trades,
                         including ``regime``.  Empty string = no filter.

        Returns:
            FilterReport with baseline vs filtered metrics and warnings.

        Raises:
            ValueError: If ``pnl_r`` column is missing.
        """
        if "pnl_r" not in enriched_trades.columns:
            raise ValueError("enriched_trades must have a 'pnl_r' column")

        baseline_metrics = _compute_metrics(enriched_trades)

        # Apply filter
        if filter_expr:
            filtered_trades = enriched_trades.query(filter_expr)
        else:
            filtered_trades = enriched_trades

        # Filtered count must be <= original
        assert len(filtered_trades) <= len(enriched_trades), (
            f"Filter increased trade count: {len(filtered_trades)} > {len(enriched_trades)}"
        )

        filtered_metrics = _compute_metrics(filtered_trades)

        # Yearly breakdown
        yearly_baseline: list[YearlyMetrics] = []
        yearly_filtered: list[YearlyMetrics] = []
        if "entry_time" in enriched_trades.columns:
            yearly_baseline, yearly_filtered = _yearly_breakdown(
                enriched_trades, filtered_trades
            )

        # Collect warnings
        filter_warnings = _check_overfitting(
            filter_expr, enriched_trades, filtered_trades, yearly_filtered
        )

        return FilterReport(
            filter_expr=filter_expr,
            baseline=baseline_metrics,
            filtered=filtered_metrics,
            yearly_baseline=yearly_baseline,
            yearly_filtered=yearly_filtered,
            warnings=filter_warnings,
        )


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _compute_metrics(trades: pd.DataFrame) -> MetricsSnapshot:
    if len(trades) == 0:
        return MetricsSnapshot(
            trade_count=0,
            expectancy=float("nan"),
            win_rate=float("nan"),
            sharpe=float("nan"),
            max_drawdown=float("nan"),
            profit_factor=float("nan"),
            convexity=float("nan"),
        )

    r = trades["pnl_r"].dropna()
    if len(r) == 0:
        return MetricsSnapshot(0, float("nan"), float("nan"), float("nan"),
                               float("nan"), float("nan"), float("nan"))

    expectancy = float(r.mean())
    win_rate = float((r > 0).mean())

    # Sharpe: mean/std * sqrt(252)  (treat each trade as independent)
    std = float(r.std())
    sharpe = (expectancy / std * np.sqrt(252)) if std > 0 else float("nan")

    # Max drawdown on cumulative R curve
    cum_r = r.cumsum()
    running_max = cum_r.cummax()
    drawdowns = cum_r - running_max
    max_drawdown = float(drawdowns.min())

    # Profit factor
    gross_profit = float(r[r > 0].sum()) if (r > 0).any() else 0.0
    gross_loss = float((-r[r < 0]).sum()) if (r < 0).any() else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Convexity: skewness of R-multiples
    convexity = float(r.skew()) if len(r) >= 3 else float("nan")

    return MetricsSnapshot(
        trade_count=len(r),
        expectancy=expectancy,
        win_rate=win_rate,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        convexity=convexity,
    )


def _yearly_breakdown(
    baseline_trades: pd.DataFrame,
    filtered_trades: pd.DataFrame,
) -> tuple[list[YearlyMetrics], list[YearlyMetrics]]:
    baseline_trades = baseline_trades.copy()
    baseline_trades["_year"] = pd.to_datetime(baseline_trades["entry_time"]).dt.year

    filtered_trades = filtered_trades.copy()
    filtered_trades["_year"] = pd.to_datetime(filtered_trades["entry_time"]).dt.year

    years = sorted(baseline_trades["_year"].unique())

    yearly_base = []
    yearly_filt = []
    for yr in years:
        yearly_base.append(
            YearlyMetrics(yr, _compute_metrics(baseline_trades[baseline_trades["_year"] == yr]))
        )
        filt_yr = filtered_trades[filtered_trades["_year"] == yr]
        yearly_filt.append(YearlyMetrics(yr, _compute_metrics(filt_yr)))

    return yearly_base, yearly_filt


# ---------------------------------------------------------------------------
# Overfitting guardrails
# ---------------------------------------------------------------------------

def _check_overfitting(
    filter_expr: str,
    original: pd.DataFrame,
    filtered: pd.DataFrame,
    yearly_filtered: list[YearlyMetrics],
) -> list[FilterWarning]:
    warns: list[FilterWarning] = []

    # 1. Tiny sample
    if 0 < len(filtered) < FilterEvaluator.MIN_SAMPLE:
        warns.append(FilterWarning(
            code="TINY_SAMPLE",
            message=(
                f"Filter leaves only {len(filtered)} trades "
                f"(minimum recommended: {FilterEvaluator.MIN_SAMPLE}). "
                "Metrics are unreliable at this sample size."
            ),
        ))

    # 2. Narrow threshold — heuristic: detect numeric range filters
    if filter_expr:
        warns += _check_narrow_threshold(filter_expr, original)

    # 3. Single-year stability
    if yearly_filtered and len(yearly_filtered) >= 2:
        warns += _check_single_year_stability(yearly_filtered)

    return warns


def _check_narrow_threshold(
    filter_expr: str,
    original: pd.DataFrame,
) -> list[FilterWarning]:
    """Warn if a numeric range filter covers < 10% of the feature's total range."""
    import re

    warns = []
    # Match patterns like: col >= lower_val & col <= upper_val  (various operators)
    between_pattern = re.compile(
        r"(\w+)\s*[><=!]+\s*([\d.]+).*?(\w+)\s*[><=!]+\s*([\d.]+)"
    )
    m = between_pattern.search(filter_expr)
    if m:
        col1, val1, col2, val2 = m.group(1), float(m.group(2)), m.group(3), float(m.group(4))
        if col1 == col2 and col1 in original.columns:
            width = abs(val2 - val1)
            col_range = original[col1].max() - original[col1].min()
            if col_range > 0 and (width / col_range) < 0.10:
                warns.append(FilterWarning(
                    code="NARROW_THRESHOLD",
                    message=(
                        f"Filter on '{col1}' covers only {width / col_range:.1%} of its range "
                        f"({val1}–{val2} within [{original[col1].min():.3f}, {original[col1].max():.3f}]). "
                        "This threshold is likely overfit."
                    ),
                ))
    return warns


def _check_single_year_stability(yearly_filtered: list[YearlyMetrics]) -> list[FilterWarning]:
    """Warn if the filter only produces positive expectancy in ≤ 1 year."""
    years_with_edge = sum(
        1 for ym in yearly_filtered
        if not np.isnan(ym.metrics.expectancy) and ym.metrics.expectancy > 0
    )
    total_years = sum(
        1 for ym in yearly_filtered if ym.metrics.trade_count > 0
    )
    if total_years >= 2 and years_with_edge <= 1:
        warns = [FilterWarning(
            code="SINGLE_YEAR_STABILITY",
            message=(
                f"Filter produces positive expectancy in only {years_with_edge}/{total_years} years. "
                "This filter may not be stable across market regimes."
            ),
        )]
        return warns
    return []
