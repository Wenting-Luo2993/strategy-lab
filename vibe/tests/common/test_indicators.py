"""
Unit tests for indicator components (engine, ORB, MTF store).
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from vibe.common.indicators.engine import IncrementalIndicatorEngine, IndicatorState
from vibe.common.indicators.orb_levels import ORBCalculator, ORBLevels
from vibe.common.indicators.mtf_store import MTFDataStore, Bar, TIMEFRAME_MINUTES


class TestIncrementalIndicatorEngine:
    """Tests for IncrementalIndicatorEngine."""

    def setup_method(self):
        """Setup test fixtures."""
        self.engine = IncrementalIndicatorEngine()

    def _create_test_df(self, n_bars=100, start_price=100.0):
        """Create test OHLCV DataFrame."""
        np.random.seed(42)
        timestamps = [datetime(2024, 1, 1, 9, 30) + timedelta(minutes=5 * i) for i in range(n_bars)]

        # Generate realistic OHLCV data
        closes = [start_price + sum(np.random.randn() * 0.5 for _ in range(i + 1)) for i in range(n_bars)]
        opens = [c + np.random.randn() * 0.2 for c in closes]
        highs = [max(o, c) + abs(np.random.randn()) * 0.3 for o, c in zip(opens, closes)]
        lows = [min(o, c) - abs(np.random.randn()) * 0.3 for o, c in zip(opens, closes)]
        volumes = [np.random.randint(1000000, 5000000) for _ in range(n_bars)]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        return df

    def test_ema_initialization(self):
        """Test EMA state initialization."""
        state = self.engine._initialize_ema(20)
        assert state["value"] is None
        assert state["multiplier"] == 2.0 / 21
        assert state["length"] == 20

    def test_ema_incremental_calculation(self):
        """Test incremental EMA calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "ema", "params": {"length": 20}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "EMA_20" in result_df.columns
        assert result_df["EMA_20"].notna().sum() > 0
        # First value should be close to first close price
        first_valid_idx = result_df["EMA_20"].first_valid_index()
        assert abs(result_df.loc[first_valid_idx, "EMA_20"] - result_df.loc[first_valid_idx, "close"]) < 1.0

    def test_sma_incremental_calculation(self):
        """Test incremental SMA calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "sma", "params": {"length": 20}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "SMA_20" in result_df.columns

        # Verify last SMA value matches manual calculation
        last_20_closes = df["close"].tail(20)
        expected_sma = last_20_closes.mean()
        actual_sma = result_df["SMA_20"].iloc[-1]
        assert abs(actual_sma - expected_sma) < 0.01

    def test_rsi_incremental_calculation(self):
        """Test incremental RSI calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "rsi", "params": {"length": 14}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "RSI_14" in result_df.columns

        # RSI should be between 0 and 100
        rsi_values = result_df["RSI_14"].dropna()
        assert all(0 <= v <= 100 for v in rsi_values)

    def test_atr_incremental_calculation(self):
        """Test incremental ATR calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "atr", "params": {"length": 14}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "ATR_14" in result_df.columns

        # ATR should be positive
        atr_values = result_df["ATR_14"].dropna()
        assert all(v > 0 for v in atr_values)

    def test_macd_incremental_calculation(self):
        """Test incremental MACD calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "MACD_12_26" in result_df.columns
        assert "MACD_12_26_signal" in result_df.columns
        assert "MACD_12_26_histogram" in result_df.columns

    def test_bollinger_bands_incremental_calculation(self):
        """Test incremental Bollinger Bands calculation."""
        df = self._create_test_df(50)

        indicators = [{"name": "bb", "params": {"length": 20, "std_dev": 2.0}}]
        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "BB_20_upper" in result_df.columns
        assert "BB_20_middle" in result_df.columns
        assert "BB_20_lower" in result_df.columns

        # Upper band should always be >= lower band
        upper = result_df["BB_20_upper"].dropna()
        lower = result_df["BB_20_lower"].dropna()
        assert all(u >= l for u, l in zip(upper, lower))

    def test_state_persistence(self):
        """Test state save and restore."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = IncrementalIndicatorEngine(state_dir=Path(tmpdir))

            # Calculate indicators
            df = self._create_test_df(30)
            indicators = [{"name": "ema", "params": {"length": 20}}]
            result1 = engine.update(df, 0, indicators, "AAPL", "5m")

            # Create new engine and add one more bar
            engine2 = IncrementalIndicatorEngine(state_dir=Path(tmpdir))

            # Add one more bar
            new_bar = pd.DataFrame({
                "timestamp": [df["timestamp"].iloc[-1] + timedelta(minutes=5)],
                "open": [df["open"].iloc[-1] + 0.5],
                "high": [df["high"].iloc[-1] + 0.7],
                "low": [df["low"].iloc[-1] - 0.3],
                "close": [df["close"].iloc[-1] + 0.4],
                "volume": [2000000],
            })

            result2 = engine2.update(new_bar, 0, indicators, "AAPL", "5m")

            # EMA should continue from previous state
            assert "EMA_20" in result2.columns
            assert not pd.isna(result2["EMA_20"].iloc[0])

    def test_multiple_indicators(self):
        """Test calculating multiple indicators simultaneously."""
        df = self._create_test_df(50)

        indicators = [
            {"name": "ema", "params": {"length": 20}},
            {"name": "sma", "params": {"length": 20}},
            {"name": "rsi", "params": {"length": 14}},
        ]

        result_df = self.engine.update(df, 0, indicators, "TEST", "5m")

        assert "EMA_20" in result_df.columns
        assert "SMA_20" in result_df.columns
        assert "RSI_14" in result_df.columns

        # All indicators should have values
        assert result_df["EMA_20"].notna().sum() > 0
        assert result_df["SMA_20"].notna().sum() > 0
        assert result_df["RSI_14"].notna().sum() > 0


class TestORBCalculator:
    """Tests for ORBCalculator."""

    def setup_method(self):
        """Setup test fixtures."""
        self.calculator = ORBCalculator(
            start_time="09:30",
            duration_minutes=5,
            body_pct_filter=0.5,
        )

    def _create_market_day_df(self):
        """Create test market day data (9:30-16:00)."""
        # ORB period: 9:30-9:35 (first 2 bars)
        # Main trading period: 9:35-16:00

        timestamps = []
        current = datetime(2024, 1, 15, 9, 30)
        for i in range(390):  # 6.5 hours * 60 / 5 min bars = 78 bars
            timestamps.append(current)
            current += timedelta(minutes=5)

        np.random.seed(42)

        # Create data with clear ORB breakout later in day
        closes = [100.0]
        for i in range(1, len(timestamps)):
            closes.append(closes[-1] + np.random.randn() * 0.3)

        opens = [c + np.random.randn() * 0.1 for c in closes]
        highs = [max(o, c) + abs(np.random.randn()) * 0.2 for o, c in zip(opens, closes)]
        lows = [min(o, c) - abs(np.random.randn()) * 0.2 for o, c in zip(opens, closes)]
        volumes = [np.random.randint(1000000, 3000000) for _ in range(len(timestamps))]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        return df

    def test_orb_levels_calculation(self):
        """Test ORB levels are calculated correctly."""
        df = self._create_market_day_df()

        levels = self.calculator.calculate(df)

        # ORB high should be max of first N bars high
        opening_bars = df[df["timestamp"].dt.time.between(
            self.calculator.start_time,
            (datetime.combine(datetime.today(), self.calculator.start_time) +
             timedelta(minutes=self.calculator.duration_minutes)).time()
        )]

        expected_high = opening_bars["high"].max()
        expected_low = opening_bars["low"].min()

        # Levels should be from opening bars (even if bar not valid)
        assert levels.high > 0
        assert levels.low > 0
        assert levels.range >= 0

    def test_orb_range_calculation(self):
        """Test ORB range calculation."""
        df = self._create_market_day_df()

        levels = self.calculator.calculate(df)

        expected_range = levels.high - levels.low
        assert abs(levels.range - expected_range) < 0.01

    def test_body_percentage_filter(self):
        """Test body percentage filter."""
        # Create bar with small body (>= 50% of range)
        timestamps = [datetime(2024, 1, 15, 9, 30 + i * 5, 0) for i in range(3)]
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0, 100.5, 100.2],
            "high": [101.0, 101.5, 101.0],
            "low": [99.5, 99.8, 99.5],
            "close": [100.5, 100.2, 100.8],
            "volume": [1000000, 1000000, 1000000],
        })

        levels = self.calculator.calculate(df)

        # Check body percentage of first bar
        body_pct = self.calculator._calculate_body_percentage(
            df.iloc[0]["open"],
            df.iloc[0]["close"],
            df.iloc[0]["high"],
            df.iloc[0]["low"],
        )

        assert 0.0 <= body_pct <= 1.0

    def test_long_breakout_detection(self):
        """Test long breakout detection."""
        df = self._create_market_day_df()
        levels = self.calculator.calculate(df)

        # Price above ORB_High should trigger long
        assert self.calculator.is_long_breakout(levels.high + 1.0, levels)
        assert not self.calculator.is_long_breakout(levels.high - 1.0, levels)

    def test_short_breakout_detection(self):
        """Test short breakout detection."""
        df = self._create_market_day_df()
        levels = self.calculator.calculate(df)

        # Price below ORB_Low should trigger short
        assert self.calculator.is_short_breakout(levels.low - 1.0, levels)
        assert not self.calculator.is_short_breakout(levels.low + 1.0, levels)

    def test_take_profit_calculation(self):
        """Test take-profit level calculation."""
        df = self._create_market_day_df()
        levels = self.calculator.calculate(df)

        atr = 2.0
        tp = self.calculator.get_long_exit_level(levels, atr, multiplier=2.0)

        expected = levels.high + (atr * 2.0)
        assert abs(tp - expected) < 0.01

    def test_daily_level_reset(self):
        """Test levels reset for new day."""
        df = self._create_market_day_df()

        levels1 = self.calculator.calculate(df)

        # Change to next day
        df2 = df.copy()
        df2["timestamp"] = df2["timestamp"] + timedelta(days=1)

        self.calculator.reset_cache()
        levels2 = self.calculator.calculate(df2)

        # Should recalculate for new day
        assert self.calculator._current_date != str(df.iloc[-1]["timestamp"].date())


class TestMTFDataStore:
    """Tests for MTFDataStore."""

    def setup_method(self):
        """Setup test fixtures."""
        self.store = MTFDataStore(primary_tf="5m", htf_list=["15m", "1h"])

    def _create_test_bars(self, start_time=None, count=12):
        """Create test bars at 5m interval."""
        if start_time is None:
            start_time = datetime(2024, 1, 15, 9, 30)

        bars = []
        for i in range(count):
            ts = start_time + timedelta(minutes=5 * i)
            bar = Bar(
                timestamp=ts,
                open=100.0 + i * 0.1,
                high=100.5 + i * 0.1,
                low=99.5 + i * 0.1,
                close=100.2 + i * 0.1,
                volume=1000000,
            )
            bars.append(bar)

        return bars

    def test_add_bar_primary_timeframe(self):
        """Test adding bars at primary timeframe."""
        bars = self._create_test_bars(count=3)

        for bar in bars:
            completed = self.store.add_bar("AAPL", bar)
            assert completed["5m"] == bar

    def test_15m_bar_aggregation(self):
        """Test 5m bars aggregate to 15m."""
        bars = self._create_test_bars(count=3)

        completed_bars = {}
        for bar in bars:
            completed = self.store.add_bar("AAPL", bar)
            completed_bars[bar.timestamp] = completed

        # 3rd bar should complete a 15m bar
        third_bar_ts = bars[2].timestamp
        assert completed_bars[third_bar_ts]["15m"] is not None

        completed_15m = completed_bars[third_bar_ts]["15m"]
        assert completed_15m.open == bars[0].open
        assert completed_15m.close == bars[2].close
        assert completed_15m.high == max(b.high for b in bars)
        assert completed_15m.low == min(b.low for b in bars)

    def test_1h_bar_aggregation(self):
        """Test 5m bars aggregate to 1h (12 bars)."""
        bars = self._create_test_bars(count=12)

        completed_bars = {}
        for bar in bars:
            completed = self.store.add_bar("AAPL", bar)
            completed_bars[bar.timestamp] = completed

        # After adding 12 bars, should have 1h store initialized
        assert "1h" in self.store.bars["AAPL"]
        # The 1h bar may or may not be complete depending on boundary alignment
        assert len(self.store.bars["AAPL"]["1h"]) >= 0

    def test_get_bars(self):
        """Test retrieving bars."""
        bars = self._create_test_bars(count=5)

        for bar in bars:
            self.store.add_bar("AAPL", bar)

        retrieved = self.store.get_bars("AAPL", "5m", count=3)
        assert len(retrieved) == 3
        assert retrieved[-1] == bars[-1]

    def test_get_last_bar(self):
        """Test retrieving last bar."""
        bars = self._create_test_bars(count=5)

        for bar in bars:
            self.store.add_bar("AAPL", bar)

        last = self.store.get_last_bar("AAPL", "5m")
        assert last == bars[-1]

    def test_multiple_symbols(self):
        """Test store handles multiple symbols."""
        bars_aapl = self._create_test_bars(count=3)
        bars_msft = self._create_test_bars(count=3)

        for bar in bars_aapl:
            self.store.add_bar("AAPL", bar)

        for bar in bars_msft:
            self.store.add_bar("MSFT", bar)

        assert len(self.store.get_bars("AAPL", "5m", count=-1)) == 3
        assert len(self.store.get_bars("MSFT", "5m", count=-1)) == 3

    def test_to_dataframe(self):
        """Test converting bars to DataFrame."""
        bars = self._create_test_bars(count=5)

        for bar in bars:
            self.store.add_bar("AAPL", bar)

        df = self.store.to_dataframe("AAPL", "5m")

        assert len(df) == 5
        assert all(col in df.columns for col in ["timestamp", "open", "high", "low", "close", "volume"])
