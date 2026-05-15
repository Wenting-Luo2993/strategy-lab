"""
Stage 5 tests — Reporting & CLI.

All tests are P2 (must pass before promoting any filter to production use).
Run: pytest vibe/tests/backtester/regime_research/test_reporting.py -v
"""

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vibe.backtester.analysis.regime_research.filter_evaluator import FilterEvaluator
from vibe.backtester.analysis.regime_research.reporting import ReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_enriched_trades(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    regimes = rng.choice(["trending_up", "trending_down", "ranging"], n)
    return pd.DataFrame(
        {
            "entry_time": idx,
            "pnl_r": rng.normal(0.1, 1.0, n),
            "atr_pctile": rng.uniform(0, 1, n),
            "regime": regimes,
        }
    )


def _make_filter_reports(trades: pd.DataFrame) -> list:
    evaluator = FilterEvaluator()
    return [evaluator.evaluate(trades, "regime == 'trending_up'")]


# ---------------------------------------------------------------------------
# P2 Tests
# ---------------------------------------------------------------------------

class TestP2:
    def test_report_reproducibility(self):
        """Same config + same data → byte-identical JSON output."""
        trades = _make_enriched_trades(seed=42)
        filter_reports = _make_filter_reports(trades)
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            generator.generate(trades, filter_reports, Path(d1))
            generator.generate(trades, filter_reports, Path(d2))

            json1 = (Path(d1) / "summary.json").read_text(encoding="utf-8")
            json2 = (Path(d2) / "summary.json").read_text(encoding="utf-8")
            assert json1 == json2, "summary.json output is not reproducible"

    def test_json_no_nan_values(self):
        """summary.json must not contain NaN (JSON-invalid); use null instead."""
        trades = _make_enriched_trades(seed=1)
        filter_reports = _make_filter_reports(trades)
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d:
            generator.generate(trades, filter_reports, Path(d))
            raw = (Path(d) / "summary.json").read_text(encoding="utf-8")

            # Verify it parses cleanly
            data = json.loads(raw)

            # Check all numeric values in filters are not NaN
            for f in data.get("filters", []):
                for section in ("baseline", "filtered"):
                    for k, v in f.get(section, {}).items():
                        assert v is None or not (isinstance(v, float) and math.isnan(v)), (
                            f"NaN found in JSON at filters[].{section}.{k}"
                        )

    def test_markdown_generated_with_no_trades(self):
        """Zero-trade filtered set → report generates without crash."""
        trades = _make_enriched_trades(seed=2)
        evaluator = FilterEvaluator()
        # Impossible filter → 0 trades
        report = evaluator.evaluate(trades, "atr_pctile > 10")
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d:
            # Should not raise
            generator.generate(trades, [report], Path(d))
            assert (Path(d) / "filter_comparison.md").exists()
            assert (Path(d) / "summary.json").exists()

    def test_markdown_generated_with_no_profitable_filters(self):
        """No filter beats baseline expectancy → report generated, no crash."""
        rng = np.random.default_rng(99)
        n = 60
        idx = pd.date_range("2022-01-03", periods=n, freq="B")
        # All regimes have negative expectancy → filter won't improve things
        trades = pd.DataFrame(
            {
                "entry_time": idx,
                "pnl_r": rng.normal(-0.5, 0.5, n),
                "atr_pctile": rng.uniform(0, 1, n),
                "regime": rng.choice(["trending_up", "ranging"], n),
            }
        )
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(trades, "regime == 'trending_up'")
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d:
            generator.generate(trades, [report], Path(d))
            md = (Path(d) / "filter_comparison.md").read_text(encoding="utf-8")
            assert len(md) > 0, "filter_comparison.md should not be empty"

    def test_output_files_created(self):
        """All three output files are created."""
        trades = _make_enriched_trades(seed=3)
        filter_reports = _make_filter_reports(trades)
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d:
            generator.generate(trades, filter_reports, Path(d))
            assert (Path(d) / "regime_analysis.md").exists()
            assert (Path(d) / "filter_comparison.md").exists()
            assert (Path(d) / "summary.json").exists()

    def test_empty_trades_no_crash(self):
        """Passing an empty trade DataFrame should not raise."""
        empty = pd.DataFrame(columns=["entry_time", "pnl_r", "regime", "atr_pctile"])
        generator = ReportGenerator()

        with tempfile.TemporaryDirectory() as d:
            generator.generate(empty, [], Path(d))
            md = (Path(d) / "regime_analysis.md").read_text(encoding="utf-8")
            assert "No trades" in md
