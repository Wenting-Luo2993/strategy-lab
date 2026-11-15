import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vibe.common.indicators.batch import ema_series
from vibe.common.indicators.daily_atr import add_daily_atr, atr_known_at_open
from vibe.common.indicators.engine import IncrementalIndicatorEngine
from vibe.common.indicators.session import session_vwap_series, same_time_relative_volume_series


def _bars():
    # DST weekend: the same clock-time bar has a different UTC offset.
    index = pd.DatetimeIndex([
        "2024-03-07 09:30", "2024-03-07 09:35",
        "2024-03-08 09:30", "2024-03-08 09:35",
        "2024-03-11 09:30", "2024-03-11 09:35",
    ], tz="America/New_York")
    return pd.DataFrame({
        "high": [10., 12., 20., 24., 30., 32.],
        "low": [10., 12., 20., 24., 30., 32.],
        "close": [10., 12., 20., 24., 30., 32.],
        "volume": [10., 30., 20., 50., 60., 120.],
    }, index=index)


def test_session_vwap_hand_values_reset_and_utc_equivalence():
    bars = _bars()
    expected = [10., 11.5, 20., 22.857142857, 30., 31.333333333]
    np.testing.assert_allclose(session_vwap_series(bars), expected)
    utc = bars.tz_convert("UTC")
    np.testing.assert_allclose(session_vwap_series(utc), expected)


def test_same_clock_volume_excludes_current_session_and_handles_dst():
    result = same_time_relative_volume_series(_bars(), 2)
    assert result.iloc[:4].isna().all()
    assert result.iloc[4] == 4.0  # 60 / mean(10, 20)
    assert result.iloc[5] == 3.0  # 120 / mean(30, 50)


def test_missing_slot_does_not_reach_further_back_or_shorten_history():
    bars = _bars().drop(_bars().index[2])
    result = same_time_relative_volume_series(bars, 2)
    assert np.isnan(result.iloc[-2])
    assert result.iloc[-1] == 3.0


def test_missing_volume_and_zero_denominators_are_unavailable():
    bars = _bars()
    bars.loc[bars.index[1], "volume"] = np.nan
    assert np.isnan(session_vwap_series(bars).iloc[1])
    assert session_vwap_series(bars).iloc[2] == 20.0
    bars["volume"] = 0.0
    assert session_vwap_series(bars).isna().all()
    assert same_time_relative_volume_series(bars, 2).isna().all()


def test_ema_seed_matches_incremental_and_period_changes_measurement():
    values = pd.Series([10., 12., 11., 15., 14., 13.])
    engine = IncrementalIndicatorEngine()
    state = engine._initialize_ema(3)
    expected = [engine._update_ema(state, v) for v in values]
    result = ema_series(values, 3)
    assert result.iloc[:2].isna().all()
    np.testing.assert_allclose(result.iloc[2:], expected[2:])
    assert ema_series(values, 2).iloc[-1] != result.iloc[-1]
    values.iloc[3] = np.nan
    assert ema_series(values, 3).iloc[3:].isna().all()


def test_wilder_daily_atr_is_shifted_and_does_not_change_simple_default():
    daily = pd.DataFrame({
        "high": [11., 14., 16., 12., 30.],
        "low": [9., 10., 10., 10., 10.],
        "close": [10., 12., 12., 11., 20.],
    })
    wilder = atr_known_at_open(daily, 2, smoothing="wilder")
    assert wilder.iloc[:2].isna().all()
    np.testing.assert_allclose(wilder.iloc[2:], [3., 4.5, 3.25])
    default = atr_known_at_open(daily, 2)
    assert default.iloc[:3].isna().all()
    np.testing.assert_allclose(default.iloc[3:], [5., 4.])


@pytest.mark.parametrize("kind", ["vwap", "rvol", "ema", "daily_atr"])
def test_batch_prefix_invariance(kind):
    bars = _bars()
    calculate = {
        "vwap": session_vwap_series,
        "rvol": lambda df: same_time_relative_volume_series(df, 1),
        "ema": lambda df: ema_series(df["close"], 2),
        "daily_atr": lambda df: add_daily_atr(df, 1, smoothing="wilder")["DAILY_ATR_1_WILDER"],
    }[kind]
    full = calculate(bars)
    for length in (1, 3, 5):
        pd.testing.assert_series_equal(full.iloc[:length], calculate(bars.iloc[:length]))
    future = bars.copy()
    future.iloc[-1] = [1000., 1., 800., 1_000_000.]
    pd.testing.assert_series_equal(full.iloc[:-1], calculate(future).iloc[:-1])


def test_custom_session_and_price_source_are_not_strategy_specific():
    bars = _bars()
    bars["high"] += 6
    assert session_vwap_series(bars, price_source="close").iloc[0] == 10
    assert session_vwap_series(bars).iloc[0] == 12
    restricted = session_vwap_series(bars, session_open="09:35", session_close="09:40")
    assert restricted.iloc[::2].isna().all()
    np.testing.assert_allclose(restricted.iloc[1::2], [14., 26., 34.])


def test_wilder_daily_atr_does_not_skip_missing_prices_in_session_aggregation():
    bars = _bars()
    bars.loc[bars.index[1], "high"] = np.nan
    result = add_daily_atr(bars, 1, smoothing="wilder")["DAILY_ATR_1_WILDER"]
    assert result.isna().all()


def test_daily_atr_custom_session_uses_wall_clock_on_dst_transition_day():
    index = pd.date_range("2024-03-10 09:30", periods=2, freq="5min", tz="America/New_York")
    index = index.append(pd.date_range("2024-03-11 09:30", periods=2, freq="5min",
                                      tz="America/New_York"))
    bars = pd.DataFrame({"high": [12., 14., 16., 18.], "low": [10., 10., 14., 14.],
                         "close": [11., 12., 15., 16.]}, index=index)
    result = add_daily_atr(bars, 1, smoothing="wilder", session_open="09:30",
                           session_close="09:40")["DAILY_ATR_1_WILDER"]
    np.testing.assert_allclose(result.iloc[2:], [4., 4.])


@pytest.mark.parametrize("bad", ["naive", "duplicate", "unordered", "negative_volume"])
def test_invalid_indicator_input_fails_explicitly(bad):
    bars = _bars()
    if bad == "naive":
        bars.index = bars.index.tz_localize(None)
    elif bad == "duplicate":
        bars = pd.concat([bars, bars.iloc[-1:]])
    elif bad == "unordered":
        bars = bars.iloc[::-1]
    else:
        bars.iloc[0, bars.columns.get_loc("volume")] = -1
    for calculate in (session_vwap_series, same_time_relative_volume_series):
        with pytest.raises(ValueError):
            calculate(bars)


def test_indicator_layer_has_no_strategy_or_execution_imports():
    root = Path(__file__).resolve().parents[2] / "common" / "indicators"
    forbidden = ("vibe.common.strategies", "vibe.backtester", "vibe.trading_bot",
                 "vibe.research_pipeline", "vibe.common.ruleset")
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.startswith(forbidden) for name in imports), path
