"""Take-profit methods for the ORB strategy and the daily ATR they rely on."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from vibe.common.indicators.daily_atr import add_daily_atr, daily_atr_column
from vibe.common.ruleset.models import (
    ATRMultipleTakeProfit,
    FixedPctTakeProfit,
    OrbRangeMultipleTakeProfit,
)
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig, take_profit_settings

TZ = "America/New_York"
SESSION_BARS = 78  # 09:30-15:55 on 5-minute bars


def _sessions(n_days: int, daily_range: float = 2.0, base: float = 100.0) -> pd.DataFrame:
    """Regular-hours 5-minute bars; each session spans ``daily_range`` and closes at ``base``."""
    days = pd.bdate_range("2024-01-02", periods=n_days)
    frames = []
    for day in days:
        stamps = pd.date_range(
            pd.Timestamp(day.date()).tz_localize(TZ) + pd.Timedelta(hours=9, minutes=30),
            periods=SESSION_BARS,
            freq="5min",
        )
        frame = pd.DataFrame(
            {"open": base, "high": base, "low": base, "close": base, "volume": 1_000_000},
            index=stamps,
        )
        frame.iloc[0, frame.columns.get_loc("high")] = base + daily_range / 2
        frame.iloc[0, frame.columns.get_loc("low")] = base - daily_range / 2
        frames.append(frame)
    return pd.concat(frames)


class TestDailyATR:
    def test_needs_period_completed_sessions_with_a_prior_close(self):
        df = add_daily_atr(_sessions(17), period=14)
        per_session = df.groupby(df.index.date)[daily_atr_column(14)].first()

        assert per_session.iloc[:15].isna().all()
        assert per_session.iloc[15:].tolist() == pytest.approx([2.0, 2.0])

    def test_every_bar_of_a_session_gets_the_same_value(self):
        df = add_daily_atr(_sessions(16), period=14)
        last_day = df[df.index.date == df.index[-1].date()]

        assert last_day[daily_atr_column(14)].nunique() == 1

    def test_no_lookahead_into_the_current_session(self):
        base = add_daily_atr(_sessions(16), period=14)
        shocked = _sessions(16)
        last_day = shocked.index.date == shocked.index[-1].date()
        shocked.loc[last_day, "high"] += 50.0
        shocked = add_daily_atr(shocked, period=14)

        column = daily_atr_column(14)
        assert shocked[column].iloc[-1] == base[column].iloc[-1]

    def test_ignores_extended_hours_bars(self):
        df = _sessions(16)
        premarket = df.iloc[[0]].copy()
        premarket.index = premarket.index - pd.Timedelta(hours=2)
        premarket[["high", "low"]] = [150.0, 50.0]
        result = add_daily_atr(pd.concat([premarket, df]), period=14)

        assert result[daily_atr_column(14)].iloc[-1] == pytest.approx(2.0)

    def test_timestamp_column_matches_index(self):
        indexed = add_daily_atr(_sessions(16), period=14)
        column_frame = _sessions(16).reset_index(names="timestamp")
        from_column = add_daily_atr(column_frame, period=14)

        np.testing.assert_array_equal(
            from_column[daily_atr_column(14)].to_numpy(),
            indexed[daily_atr_column(14)].to_numpy(),
        )

    def test_naive_timestamps_are_exchange_local(self):
        aware = _sessions(16).reset_index(names="timestamp")
        naive = aware.copy()
        naive["timestamp"] = naive["timestamp"].dt.tz_localize(None)

        column = daily_atr_column(14)
        np.testing.assert_array_equal(
            add_daily_atr(naive, 14)[column].to_numpy(),
            add_daily_atr(aware, 14)[column].to_numpy(),
        )


class TestTakeProfitSettings:
    def test_none_disables_target(self):
        assert take_profit_settings(None) == {"take_profit_multiplier": 0.0}

    def test_orb_range_multiple(self):
        settings = take_profit_settings(OrbRangeMultipleTakeProfit(multiplier=2.0))
        assert settings == {"take_profit_method": "orb_range_multiple", "take_profit_multiplier": 2.0}

    def test_atr_multiple_carries_period(self):
        settings = take_profit_settings(ATRMultipleTakeProfit(multiplier=0.5, period=10))
        assert settings == {
            "take_profit_method": "atr_multiple",
            "take_profit_multiplier": 0.5,
            "take_profit_atr_period": 10,
        }

    def test_fixed_pct_is_rejected(self):
        with pytest.raises(NotImplementedError):
            take_profit_settings(FixedPctTakeProfit(value=0.02))

    def test_unknown_method_is_rejected(self):
        with pytest.raises(NotImplementedError):
            take_profit_settings(SimpleNamespace(method="mystery", multiplier=1.0))


class TestORBTakeProfitLevels:
    """Opening range on the last session is 99-101 (range 2); prior daily ATR is 2."""

    def _context(self, with_daily_atr: bool) -> pd.DataFrame:
        df = _sessions(16)
        last_day = df.index.date == df.index[-1].date()
        df = df[~last_day | (df.index.time < pd.Timestamp("09:45").time())].copy()
        df["ATR_14"] = 1.0
        if with_daily_atr:
            df = add_daily_atr(df, 14)
        return df.reset_index(names="timestamp")

    def _signal(self, side: str, with_daily_atr: bool = True, **config):
        strategy = ORBStrategy(ORBStrategyConfig(name="ORB", use_volume_filter=False, **config))
        context = self._context(with_daily_atr)
        stamp = context["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)
        if side == "long":
            bar = {"timestamp": stamp.to_pydatetime(), "open": 100.5, "high": 101.5, "low": 100.5,
                   "close": 101.4, "volume": 2_000_000}
        else:
            bar = {"timestamp": stamp.to_pydatetime(), "open": 99.5, "high": 99.5, "low": 98.5,
                   "close": 98.6, "volume": 2_000_000}
        return strategy.generate_signal_incremental(symbol="TEST", current_bar=bar, df_context=context)

    def test_orb_range_multiple_long_and_short(self):
        signal, meta = self._signal("long", take_profit_multiplier=2.0)
        assert signal == 1 and meta["take_profit"] == pytest.approx(101.0 + 2 * 2.0)

        signal, meta = self._signal("short", take_profit_multiplier=2.0)
        assert signal == -1 and meta["take_profit"] == pytest.approx(99.0 - 2 * 2.0)

    def test_atr_multiple_uses_daily_atr_not_bar_atr(self):
        config = {"take_profit_method": "atr_multiple", "take_profit_multiplier": 0.5}
        signal, meta = self._signal("long", **config)
        assert signal == 1 and meta["take_profit"] == pytest.approx(101.0 + 0.5 * 2.0)

        signal, meta = self._signal("short", **config)
        assert signal == -1 and meta["take_profit"] == pytest.approx(99.0 - 0.5 * 2.0)

    def test_atr_multiple_computes_daily_atr_when_column_missing(self):
        signal, meta = self._signal(
            "long", with_daily_atr=False, take_profit_method="atr_multiple", take_profit_multiplier=1.0,
        )
        assert signal == 1 and meta["take_profit"] == pytest.approx(101.0 + 2.0)

    def test_atr_multiple_without_history_refuses_live_entry(self):
        strategy_config = {"take_profit_method": "atr_multiple", "take_profit_multiplier": 1.0,
                           "take_profit_atr_period": 30}
        signal, meta = self._signal("long", with_daily_atr=False, **strategy_config)
        assert signal == 0 and meta["reason"] == "daily_atr_unavailable"

    def test_backtest_nan_daily_atr_trades_without_target(self):
        # Precomputed column present but NaN (first sessions of the data): unchanged behaviour.
        strategy = ORBStrategy(ORBStrategyConfig(
            name="ORB", use_volume_filter=False, take_profit_method="atr_multiple",
            take_profit_multiplier=1.0, take_profit_atr_period=30,
        ))
        context = self._context(with_daily_atr=False)
        context["DAILY_ATR_30"] = float("nan")
        stamp = context["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)
        bar = {"timestamp": stamp.to_pydatetime(), "open": 100.5, "high": 101.5, "low": 100.5,
               "close": 101.4, "volume": 2_000_000}
        signal, meta = strategy.generate_signal_incremental(symbol="TEST", current_bar=bar, df_context=context)
        assert signal == 1 and meta["take_profit"] is None

    def test_injected_daily_atr_is_used(self):
        strategy = ORBStrategy(ORBStrategyConfig(
            name="ORB", use_volume_filter=False, take_profit_method="atr_multiple",
            take_profit_multiplier=1.0, take_profit_atr_period=30,
        ))
        assert strategy.needs_daily_atr
        context = self._context(with_daily_atr=False)
        stamp = context["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)
        strategy.set_daily_atr("TEST", stamp.date(), 3.0)
        assert strategy.has_daily_atr("TEST", stamp.date())
        bar = {"timestamp": stamp.to_pydatetime(), "open": 100.5, "high": 101.5, "low": 100.5,
               "close": 101.4, "volume": 2_000_000}
        signal, meta = strategy.generate_signal_incremental(symbol="TEST", current_bar=bar, df_context=context)
        assert signal == 1 and meta["take_profit"] == pytest.approx(101.0 + 3.0)

    def test_zero_multiplier_disables_target(self):
        signal, meta = self._signal("long", take_profit_multiplier=0.0)
        assert signal == 1 and meta["take_profit"] is None
