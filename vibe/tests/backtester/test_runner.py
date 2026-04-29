import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")
RULESETS_DIR = Path("vibe/rulesets")


@pytest.fixture
def ruleset():
    return RuleSetLoader.from_name("orb_production")


@pytest.fixture
def runner(ruleset):
    from vibe.backtester.runner import RuleSetRunner
    return RuleSetRunner(ruleset)


def _make_df(n=50, close_price=480.0):
    """Minimal DataFrame with ATR_14 column for signal generation."""
    idx = pd.date_range("2024-01-15 09:30", periods=n, freq="5min", tz=ET)
    df = pd.DataFrame({
        "open": close_price - 1,
        "high": close_price + 2,
        "low": close_price - 2,
        "close": close_price,
        "volume": 500_000,
        "ATR_14": 1.5,
    }, index=idx)
    return df


def test_runner_creates_strategy(runner):
    from vibe.common.strategies.orb import ORBStrategy
    assert isinstance(runner.strategy, ORBStrategy)


def test_generate_signal_returns_tuple(runner):
    df = _make_df()
    bar = df.iloc[-1].to_dict()
    bar["timestamp"] = df.index[-1].to_pydatetime()
    result = runner.generate_signal("QQQ", bar, df)
    assert isinstance(result, tuple)
    assert len(result) == 2
    signal_value, metadata = result
    assert signal_value in (-1, 0, 1)


def test_no_signal_without_orb_levels(runner):
    """ORB requires the first bar of the day (9:30) to establish levels."""
    df = _make_df()
    bar = df.iloc[-1].to_dict()
    bar["timestamp"] = df.index[-1].to_pydatetime()
    signal_value, _ = runner.generate_signal("QQQ", bar, df)
    # Mid-session without proper ORB bar — expect no signal
    assert signal_value == 0


def test_track_and_close_position(runner):
    runner.track_position(
        symbol="QQQ", side="buy",
        entry_price=480.0, take_profit=490.0, stop_loss=470.0,
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
    )
    assert runner.strategy.has_position("QQQ")
    runner.close_position("QQQ")
    assert not runner.strategy.has_position("QQQ")
