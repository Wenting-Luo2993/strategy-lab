import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.analysis.regime import MarketRegimeClassifier

ET = ZoneInfo("America/New_York")


def _make_trending_df(n=100):
    """Strong trend: high ADX."""
    idx = pd.date_range("2024-01-01 09:30", periods=n, freq="5min", tz=ET)
    close = pd.Series(np.linspace(100, 130, n), index=idx)
    df = pd.DataFrame({
        "open": close - 0.5, "high": close + 0.5,
        "low": close - 0.5, "close": close,
        "volume": 1_000_000,
    }, index=idx)
    return df


def _make_ranging_df(n=100):
    """Choppy: oscillates between 99 and 101."""
    idx = pd.date_range("2024-01-01 09:30", periods=n, freq="5min", tz=ET)
    close = pd.Series([100 + np.sin(i * 0.3) for i in range(n)], index=idx)
    df = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.2,
        "low": close - 0.2, "close": close,
        "volume": 500_000,
    }, index=idx)
    return df


def test_classify_returns_series():
    clf = MarketRegimeClassifier()
    df = _make_trending_df()
    result = clf.classify(df)
    assert isinstance(result, pd.Series)
    assert len(result) == len(df)


def test_regime_values_are_valid():
    clf = MarketRegimeClassifier()
    result = clf.classify(_make_ranging_df())
    valid = {"TRENDING", "RANGING", "TRANSITIONING"}
    assert set(result.dropna().unique()).issubset(valid)


def test_performance_by_regime():
    from vibe.backtester.analysis.regime import performance_by_regime
    from vibe.backtester.analysis.metrics import ConvexityMetrics
    from vibe.common.models.trade import Trade

    trades = [
        Trade(symbol="QQQ", side="buy", quantity=10,
              entry_price=100.0, exit_price=103.0,
              entry_time=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
              exit_time=datetime(2024, 1, 15, 15, 55, tzinfo=ET),
              initial_risk=100.0, exit_reason="EOD"),
    ]
    idx = pd.date_range("2024-01-15 09:30", periods=80, freq="5min", tz=ET)
    regime = pd.Series("TRENDING", index=idx)

    result = performance_by_regime(trades, regime)
    assert "TRENDING" in result
    assert isinstance(result["TRENDING"], ConvexityMetrics)
