import pytest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from vibe.backtester.reporting.report import ReportGenerator
from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)

ET = ZoneInfo("America/New_York")


def _minimal_result():
    cm = ConvexityMetrics(
        n_trades=4, win_rate=0.5, avg_win_r=2.5, avg_loss_r=-1.0,
        expectancy_r=0.75, max_win_r=3.0, max_loss_r=-1.0,
        top10_pct=40.0, skewness=0.8, max_losing_streak=2,
        total_pnl=600.0, stop_wins=1, stop_losses=1, eod_wins=1, eod_losses=1,
        r_multiples=[3.0, -1.0, 2.0, -1.0],
        first_date="2024-01-02", last_date="2024-01-31",
    )
    eq = EquityMetrics(
        total_return=0.06, annualized_return=0.06, sharpe_ratio=1.5,
        max_drawdown=-0.02, max_drawdown_duration_days=3,
        equity_curve=pd.Series([10000, 10200, 10400, 10600],
                                index=pd.date_range("2024-01-01", periods=4, tz=ET)),
        drawdown_curve=pd.Series([0, 0, 0, 0],
                                  index=pd.date_range("2024-01-01", periods=4, tz=ET)),
    )
    return BacktestResult(
        overall=cm, by_year={2024: cm}, equity=eq, trades=[],
        regime_breakdown={"TRENDING": cm, "RANGING": cm},
        symbol="QQQ", start_date="2024-01-01", end_date="2024-01-31",
        ruleset_name="orb_production", ruleset_version="1.0.0",
    )


def test_generate_html_creates_file(tmp_path):
    gen = ReportGenerator()
    result = _minimal_result()
    out = tmp_path / "report.html"
    gen.generate_html(result, out)
    assert out.exists()


def test_report_contains_expected_sections(tmp_path):
    gen = ReportGenerator()
    out = tmp_path / "report.html"
    gen.generate_html(_minimal_result(), out)
    html = out.read_text(encoding="utf-8")
    assert "Convexity" in html
    assert "Win Rate" in html
    assert "Expectancy" in html
    assert "2024" in html
    assert "TRENDING" in html
    assert "plotly" in html.lower()


def test_report_is_self_contained_html(tmp_path):
    gen = ReportGenerator()
    out = tmp_path / "report.html"
    gen.generate_html(_minimal_result(), out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html")
    assert "</html>" in html
