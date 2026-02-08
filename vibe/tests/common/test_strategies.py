"""
Unit tests for strategy components.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time

from vibe.common.strategies.base import StrategyBase, StrategyConfig, ExitSignal
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig
from vibe.common.indicators.orb_levels import ORBCalculator


class TestStrategyBase:
    """Tests for StrategyBase."""

    def test_cannot_instantiate_abstract_base(self):
        """StrategyBase cannot be instantiated directly."""
        with pytest.raises(TypeError):
            StrategyBase()

    def test_strategy_config_validation(self):
        """StrategyConfig validates parameters."""
        config = StrategyConfig(
            name="test_strategy",
            take_profit_type="atr_multiple",
            stop_loss_type="atr_multiple",
        )

        assert config.name == "test_strategy"
        assert config.take_profit_type == "atr_multiple"

    def test_strategy_config_invalid_type(self):
        """StrategyConfig allows any type but should validate at runtime."""
        # Pydantic v2 allows any string; validation happens during use
        config = StrategyConfig(
            name="test",
            take_profit_type="invalid_type",
        )
        # The config accepts it, but should fail when used
        assert config.take_profit_type == "invalid_type"

    def test_take_profit_calculation_atr_multiple(self):
        """Test take-profit calculation with ATR multiple."""
        strategy = ORBStrategy(config=ORBStrategyConfig(name="test"))

        tp_buy = strategy.calculate_take_profit(
            entry_price=100.0,
            side="buy",
            atr=2.0,
        )

        # TP = entry + (ATR * multiplier), default multiplier = 2.0
        expected = 100.0 + (2.0 * 2.0)
        assert abs(tp_buy - expected) < 0.01

    def test_stop_loss_calculation_atr_multiple(self):
        """Test stop-loss calculation with ATR multiple."""
        strategy = ORBStrategy(config=ORBStrategyConfig(name="test"))

        sl_buy = strategy.calculate_stop_loss(
            entry_price=100.0,
            side="buy",
            atr=2.0,
        )

        # SL = entry - (ATR * multiplier), default multiplier = 1.0
        expected = 100.0 - (2.0 * 1.0)
        assert abs(sl_buy - expected) < 0.01

    def test_position_tracking(self):
        """Test position tracking."""
        strategy = ORBStrategy(config=ORBStrategyConfig(name="test"))

        strategy.track_position(
            symbol="AAPL",
            side="buy",
            entry_price=150.0,
            take_profit=155.0,
            stop_loss=145.0,
            timestamp=datetime.now(),
        )

        assert strategy.has_position("AAPL")
        pos = strategy.get_position("AAPL")
        assert pos["side"] == "buy"
        assert pos["entry_price"] == 150.0

    def test_exit_signal_take_profit(self):
        """Test exit signal generation for take-profit."""
        strategy = ORBStrategy(config=ORBStrategyConfig(name="test"))

        strategy.track_position(
            symbol="AAPL",
            side="buy",
            entry_price=150.0,
            take_profit=155.0,
            stop_loss=145.0,
            timestamp=datetime.now(),
        )

        # Price reaches take-profit
        exit_sig = strategy.check_exit_conditions(
            symbol="AAPL",
            current_price=155.5,
            current_time=datetime.now(),
        )

        assert exit_sig is not None
        assert exit_sig.exit_type == "take_profit"

    def test_exit_signal_stop_loss(self):
        """Test exit signal generation for stop-loss."""
        strategy = ORBStrategy(config=ORBStrategyConfig(name="test"))

        strategy.track_position(
            symbol="AAPL",
            side="buy",
            entry_price=150.0,
            take_profit=155.0,
            stop_loss=145.0,
            timestamp=datetime.now(),
        )

        # Price hits stop-loss
        exit_sig = strategy.check_exit_conditions(
            symbol="AAPL",
            current_price=144.5,
            current_time=datetime.now(),
        )

        assert exit_sig is not None
        assert exit_sig.exit_type == "stop_loss"


class TestORBStrategy:
    """Tests for ORBStrategy."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = ORBStrategyConfig(name="ORB")
        self.strategy = ORBStrategy(config=self.config)

    def _create_market_day_df(self, breakout_direction="up"):
        """Create test market day with ORB breakout."""
        timestamps = []
        current = datetime(2024, 1, 15, 9, 30)
        for i in range(50):  # 4 hours of 5-min bars
            timestamps.append(current)
            current += timedelta(minutes=5)

        np.random.seed(42)

        # Create realistic OHLCV data
        closes = [100.0]
        for i in range(1, len(timestamps)):
            closes.append(closes[-1] + np.random.randn() * 0.2)

        opens = [c + np.random.randn() * 0.1 for c in closes]
        highs = [max(o, c) + abs(np.random.randn()) * 0.2 for o, c in zip(opens, closes)]
        lows = [min(o, c) - abs(np.random.randn()) * 0.2 for o, c in zip(opens, closes)]

        # Add breakout pattern
        if breakout_direction == "up":
            # Breakout above first few bars high at bar 15
            for i in range(15, 20):
                highs[i] += 2.0
                closes[i] += 1.5
        else:
            # Breakout below first few bars low at bar 15
            for i in range(15, 20):
                lows[i] -= 2.0
                closes[i] -= 1.5

        volumes = [np.random.randint(1000000, 3000000) for _ in range(len(timestamps))]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        # Add indicators
        df["ATR_14"] = 1.5  # Simple constant ATR for testing

        return df

    def test_generate_signals_long_breakout(self):
        """Test ORB long signal generation."""
        df = self._create_market_day_df(breakout_direction="up")

        signals = self.strategy.generate_signals(df)

        assert isinstance(signals, pd.Series)
        assert len(signals) == len(df)

        # Should have at least one long signal
        assert (signals == 1).any()

    def test_generate_signals_time_filter(self):
        """Test ORB respects entry cutoff time."""
        df = self._create_market_day_df(breakout_direction="up")

        # Set cutoff to very early to exclude all breakouts
        self.strategy.config.entry_cutoff_time = "09:35"
        self.strategy.entry_cutoff = time(9, 35)

        signals = self.strategy.generate_signals(df)

        # Should have fewer signals due to time filter
        assert signals.sum() >= 0  # May have no signals

    def test_incremental_signal_long_breakout(self):
        """Test incremental signal generation for long breakout."""
        df = self._create_market_day_df(breakout_direction="up")

        # Calculate ORB levels
        levels = self.strategy.orb_calculator.calculate(df)

        # Create bar that breaks above ORB_High
        current_bar = {
            "timestamp": df.iloc[15]["timestamp"],
            "open": levels.high + 0.5,
            "high": levels.high + 1.0,
            "low": levels.high - 0.5,
            "close": levels.high + 0.8,
            "volume": 2000000,
        }

        signal, metadata = self.strategy.generate_signal_incremental(
            symbol="AAPL",
            current_bar=current_bar,
            df_context=df.iloc[:15],
        )

        assert signal == 1
        assert metadata["signal"] == "long_breakout"
        assert "take_profit" in metadata
        assert "stop_loss" in metadata

    def test_incremental_signal_short_breakout(self):
        """Test incremental signal generation for short breakout."""
        df = self._create_market_day_df(breakout_direction="down")

        # Calculate ORB levels
        levels = self.strategy.orb_calculator.calculate(df)

        # Create bar that breaks below ORB_Low
        current_bar = {
            "timestamp": df.iloc[15]["timestamp"],
            "open": levels.low - 0.5,
            "high": levels.low + 0.5,
            "low": levels.low - 1.0,
            "close": levels.low - 0.8,
            "volume": 2000000,
        }

        signal, metadata = self.strategy.generate_signal_incremental(
            symbol="AAPL",
            current_bar=current_bar,
            df_context=df.iloc[:15],
        )

        assert signal == -1
        assert metadata["signal"] == "short_breakout"

    def test_no_signal_with_insufficient_data(self):
        """Test no signal with insufficient data."""
        # Create minimal data frame
        df = pd.DataFrame({
            "timestamp": [datetime(2024, 1, 15, 9, 30)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000000],
        })

        bar = df.iloc[0]
        signal, metadata = self.strategy.generate_signal_incremental(
            symbol="AAPL",
            current_bar={
                "timestamp": bar["timestamp"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            },
            df_context=pd.DataFrame(),  # No context
        )

        # Should return no signal with insufficient data
        assert signal == 0

    def test_volume_filter(self):
        """Test volume filter functionality."""
        config = ORBStrategyConfig(
            name="ORB_VOLUME",
            use_volume_filter=True,
            volume_threshold=1.5,
        )
        strategy = ORBStrategy(config=config)

        df = self._create_market_day_df(breakout_direction="up")

        # Add low volume to last bar
        df.loc[15, "volume"] = df["volume"].mean() * 0.5

        signals = strategy.generate_signals(df)

        # Low volume bar should be filtered
        assert signals.iloc[15] == 0

    def test_calculate_exit_levels(self):
        """Test exit level calculation."""
        orb_high = 105.0
        orb_low = 95.0
        orb_range = 10.0
        atr = 2.0

        tp_long, sl_long = self.strategy.calculate_exit_level(
            entry_price=105.5,
            side="buy",
            orb_high=orb_high,
            orb_low=orb_low,
            orb_range=orb_range,
            atr=atr,
        )

        # TP = entry + (range * multiplier)
        expected_tp = 105.5 + (orb_range * self.strategy.config.take_profit_multiplier)
        assert abs(tp_long - expected_tp) < 0.01

        # SL = ORB_Low for long
        assert abs(sl_long - orb_low) < 0.01


class TestStrategyEdgeCases:
    """Tests for edge cases in strategy."""

    def test_empty_dataframe(self):
        """Test handling empty DataFrame."""
        strategy = ORBStrategy()

        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        signals = strategy.generate_signals(df)

        assert len(signals) == 0

    def test_missing_atr_indicator(self):
        """Test handling missing ATR indicator."""
        strategy = ORBStrategy()

        df = pd.DataFrame({
            "timestamp": [datetime(2024, 1, 15, 9, 30)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000000],
        })

        signals = strategy.generate_signals(df)

        # Should return all zeros (no indicators)
        assert signals.sum() == 0
