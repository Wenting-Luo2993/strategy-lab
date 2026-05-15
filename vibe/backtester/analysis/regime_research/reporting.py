"""
Report Generator for regime research output.

Produces:
- regime_analysis.md   — feature bucket tables, label distribution, per-year breakdown
- filter_comparison.md — side-by-side filtered vs unfiltered for each candidate filter
- summary.json         — machine-readable version (NaN → null)

Handles edge cases cleanly: no profitable filters found, zero trades after
filtering, all-NaN feature columns.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from vibe.backtester.analysis.regime_research.filter_evaluator import (
    FilterReport,
    MetricsSnapshot,
)


class ReportGenerator:
    """Generate human-readable and machine-readable research artifacts."""

    def generate(
        self,
        enriched_trades: pd.DataFrame,
        filter_reports: list[FilterReport],
        output_dir: Path,
    ) -> None:
        """
        Args:
            enriched_trades: Output of TradeAttributor.enrich() (may include a
                             ``regime`` column from DayRegimeLabeler).
            filter_reports:  List of FilterReport from FilterEvaluator.evaluate().
                             May be empty if no filters were tested.
            output_dir:      Directory to write output files.  Created if absent.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        regime_md = self._build_regime_analysis(enriched_trades)
        filter_md = self._build_filter_comparison(filter_reports)
        summary_dict = self._build_summary(enriched_trades, filter_reports)

        (output_dir / "regime_analysis.md").write_text(regime_md, encoding="utf-8")
        (output_dir / "filter_comparison.md").write_text(filter_md, encoding="utf-8")
        (output_dir / "summary.json").write_text(
            json.dumps(summary_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_regime_analysis(self, trades: pd.DataFrame) -> str:
        lines = ["# Regime Analysis\n"]

        if len(trades) == 0:
            lines.append("_No trades available for analysis._\n")
            return "\n".join(lines)

        # Label distribution (if regime column exists)
        if "regime" in trades.columns:
            lines.append("## Regime Label Distribution\n")
            counts = trades["regime"].value_counts(dropna=False)
            lines.append("| Regime | Count | % of Trades |")
            lines.append("|--------|-------|-------------|")
            total = len(trades)
            for label, cnt in counts.items():
                pct = cnt / total * 100
                lines.append(f"| {label} | {cnt} | {pct:.1f}% |")
            lines.append("")

            # Per-regime performance
            lines.append("## Performance by Regime\n")
            lines.append("| Regime | Trades | Expectancy | Win Rate | Sharpe |")
            lines.append("|--------|--------|-----------|----------|--------|")
            for label in counts.index:
                subset = trades[trades["regime"] == label]
                if len(subset) == 0 or subset["pnl_r"].isna().all():
                    continue
                r = subset["pnl_r"].dropna()
                exp = r.mean()
                wr = (r > 0).mean()
                std = r.std()
                sharpe = exp / std * (252 ** 0.5) if std > 0 else float("nan")
                exp_str = f"{exp:.4f}" if not math.isnan(exp) else "N/A"
                wr_str = f"{wr:.1%}" if not math.isnan(wr) else "N/A"
                sh_str = f"{sharpe:.4f}" if not math.isnan(sharpe) else "N/A"
                lines.append(f"| {label} | {len(r)} | {exp_str} | {wr_str} | {sh_str} |")
            lines.append("")

        # Year-by-year summary
        if "entry_time" in trades.columns:
            lines.append("## Year-by-Year Performance\n")
            trades_copy = trades.copy()
            trades_copy["_year"] = pd.to_datetime(trades_copy["entry_time"]).dt.year
            lines.append("| Year | Trades | Expectancy | Win Rate | Sharpe |")
            lines.append("|------|--------|-----------|----------|--------|")
            for yr, grp in trades_copy.groupby("_year"):
                r = grp["pnl_r"].dropna()
                if len(r) == 0:
                    continue
                exp = r.mean()
                wr = (r > 0).mean()
                std = r.std()
                sharpe = exp / std * (252 ** 0.5) if std > 0 else float("nan")
                lines.append(
                    f"| {yr} | {len(r)} | {exp:.4f} | {wr:.1%} | "
                    f"{'N/A' if math.isnan(sharpe) else f'{sharpe:.4f}'} |"
                )
            lines.append("")

        # Five-question summary
        lines += self._five_questions(trades, [])

        return "\n".join(lines)

    def _build_filter_comparison(self, filter_reports: list[FilterReport]) -> str:
        lines = ["# Filter Comparison\n"]

        if not filter_reports:
            lines.append("_No filters evaluated._\n")
            return "\n".join(lines)

        profitable = [r for r in filter_reports
                      if not math.isnan(r.filtered.expectancy) and r.filtered.expectancy > 0]
        if not profitable:
            lines.append(
                "_No filter produced a positive filtered expectancy.  "
                "This may indicate the strategy has no stable regime dependency, "
                "or the evaluated filters were not well-targeted._\n"
            )

        for report in filter_reports:
            lines.append(f"## Filter: `{report.filter_expr or '(none — baseline)'}`\n")
            lines.append("```")
            lines.append(report.summary())
            lines.append("```\n")

            if report.yearly_filtered:
                lines.append("### Year-by-Year (filtered)\n")
                lines.append("| Year | Trades | Expectancy | Sharpe |")
                lines.append("|------|--------|-----------|--------|")
                for ym in report.yearly_filtered:
                    m = ym.metrics
                    exp_str = f"{m.expectancy:.4f}" if not math.isnan(m.expectancy) else "N/A"
                    sh_str = f"{m.sharpe:.4f}" if not math.isnan(m.sharpe) else "N/A"
                    lines.append(f"| {ym.year} | {m.trade_count} | {exp_str} | {sh_str} |")
                lines.append("")

        return "\n".join(lines)

    def _build_summary(
        self,
        trades: pd.DataFrame,
        filter_reports: list[FilterReport],
    ) -> dict[str, Any]:
        def _clean(v: Any) -> Any:
            import numpy as np
            if isinstance(v, float) and math.isnan(v):
                return None
            # Convert numpy scalars (int32, int64, float32, etc.) to native Python types
            if isinstance(v, np.integer):
                return int(v)
            if isinstance(v, np.floating):
                return None if np.isnan(v) else float(v)
            return v

        def _metrics_dict(m: MetricsSnapshot) -> dict[str, Any]:
            return {
                "trade_count": m.trade_count,
                "expectancy": _clean(m.expectancy),
                "win_rate": _clean(m.win_rate),
                "sharpe": _clean(m.sharpe),
                "max_drawdown": _clean(m.max_drawdown),
                "profit_factor": _clean(m.profit_factor),
                "convexity": _clean(m.convexity),
            }

        summary: dict[str, Any] = {
            "total_trades": len(trades),
            "filters": [],
        }

        for report in filter_reports:
            entry: dict[str, Any] = {
                "filter_expr": report.filter_expr,
                "baseline": _metrics_dict(report.baseline),
                "filtered": _metrics_dict(report.filtered),
                "warnings": [{"code": w.code, "message": w.message} for w in report.warnings],
            }
            if report.yearly_filtered:
                entry["yearly_filtered"] = [
                    {"year": _clean(ym.year), "metrics": _metrics_dict(ym.metrics)}
                    for ym in report.yearly_filtered
                ]
            summary["filters"].append(entry)

        return summary

    # ------------------------------------------------------------------
    # Five-question PRD summary
    # ------------------------------------------------------------------

    def _five_questions(
        self,
        trades: pd.DataFrame,
        filter_reports: list[FilterReport],
    ) -> list[str]:
        lines = ["## Summary\n"]
        lines.append(
            "_The following questions are drawn directly from the PRD. "
            "Answers below are based on the data available in this run._\n"
        )
        questions = [
            "What market conditions help this strategy?",
            "What market conditions hurt it?",
            "Is the relationship stable?",
            "Are candidate filters robust?",
            "What is the tradeoff between selectivity and opportunity?",
        ]
        for q in questions:
            lines.append(f"**{q}**")
            lines.append("_Requires manual interpretation of the tables above._\n")
        return lines
