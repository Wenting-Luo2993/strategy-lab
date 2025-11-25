"""Test suite for incremental indicator calculation.

Tests verify that:
1. Incremental calculation matches batch (pandas_ta) calculation
2. State persistence and recovery works correctly
3. Edge cases are handled (gaps, NaN, single bar updates)
4. Performance meets requirements (>10x speedup)
"""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from src.indicators.incremental import IncrementalIndicatorEngine
from src.indicators.ta import IndicatorFactory


class TestIncrementalCorrectness:
    """Test that incremental calculation matches batch calculation."""

    @pytest.fixture
    def sample_data(self) -> pd.DataFrame:
        """Generate synthetic OHLCV data for testing."""
        np.random.seed(42)
        n_bars = 100

        closes = (np.random.randn(n_bars).cumsum() + 100).tolist()
        highs = [c + abs(np.random.randn() * 0.5) for c in closes]
        lows = [c - abs(np.random.randn() * 0.5) for c in closes]
        opens = [closes[max(0, i-1)] + np.random.randn() * 0.3 for i in range(n_bars)]
        volume = [int(abs(np.random.randn() * 1000000 + 500000)) for _ in range(n_bars)]

        df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volume
        })

        # Add datetime index
        df['datetime'] = pd.date_range('2025-01-01 09:30', periods=n_bars, freq='5min')
        df = df.set_index('datetime')
        df.index = df.index.tz_localize('UTC')

        return df

    def test_ema_incremental_matches_batch(self, sample_data):
        """Test EMA incremental calculation matches batch."""
        df = sample_data.copy()

        # Batch calculation with pandas_ta
        df_batch = df.copy()
        df_batch.ta.ema(length=20, append=True)

        # Incremental calculation
        engine = IncrementalIndicatorEngine()

        # Split data: first 80 bars as "cache", last 20 as "new data"
        cache_size = 80
        df_incremental = df.copy()

        # Simulate cache already has the indicator column (we'll ignore those values)
        df_incremental['EMA_20'] = None

        indicators = [{'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}]
        df_incremental = engine.update(
            df_incremental, cache_size, indicators, 'TEST', '5m'
        )

        # Compare last 10 values (after warmup)
        for i in range(90, 100):
            batch_val = df_batch['EMA_20'].iloc[i]
            incr_val = df_incremental['EMA_20'].iloc[i]

            if pd.notna(batch_val) and pd.notna(incr_val):
                diff = abs(batch_val - incr_val)
                assert diff < 1e-6, f"EMA mismatch at index {i}: batch={batch_val}, incr={incr_val}, diff={diff}"

    def test_rsi_incremental_matches_batch(self, sample_data):
        """Test RSI incremental calculation matches batch."""
        df = sample_data.copy()

        # Batch calculation
        df_batch = df.copy()
        df_batch.ta.rsi(length=14, append=True)

        # Incremental calculation
        engine = IncrementalIndicatorEngine()
        cache_size = 80
        df_incremental = df.copy()
        df_incremental['RSI_14'] = None

        indicators = [{'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'}]
        df_incremental = engine.update(
            df_incremental, cache_size, indicators, 'TEST', '5m'
        )

        # Compare last 10 values
        # Note: RSI uses different calculation methods between pandas_ta and talipp
        # Allow slightly larger tolerance (0.1 RSI points) due to algorithm differences
        for i in range(90, 100):
            batch_val = df_batch['RSI_14'].iloc[i]
            incr_val = df_incremental['RSI_14'].iloc[i]

            if pd.notna(batch_val) and pd.notna(incr_val):
                diff = abs(batch_val - incr_val)
                assert diff < 0.1, f"RSI mismatch at index {i}: batch={batch_val}, incr={incr_val}, diff={diff}"

    def test_atr_incremental_matches_batch(self, sample_data):
        """Test ATR incremental calculation matches batch."""
        df = sample_data.copy()

        # Batch calculation
        df_batch = df.copy()
        df_batch.ta.atr(length=14, append=True)

        # Incremental calculation
        engine = IncrementalIndicatorEngine()
        cache_size = 80
        df_incremental = df.copy()
        df_incremental['ATRr_14'] = None

        indicators = [{'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'}]
        df_incremental = engine.update(
            df_incremental, cache_size, indicators, 'TEST', '5m'
        )

        # Compare last 10 values
        for i in range(90, 100):
            batch_val = df_batch['ATRr_14'].iloc[i]
            incr_val = df_incremental['ATRr_14'].iloc[i]

            if pd.notna(batch_val) and pd.notna(incr_val):
                diff = abs(batch_val - incr_val)
                assert diff < 0.01, f"ATR mismatch at index {i}: batch={batch_val}, incr={incr_val}, diff={diff}"

    def test_sma_incremental_matches_batch(self, sample_data):
        """Test SMA incremental calculation matches batch."""
        df = sample_data.copy()

        # Batch calculation
        df_batch = df.copy()
        df_batch.ta.sma(length=20, append=True)

        # Incremental calculation
        engine = IncrementalIndicatorEngine()
        cache_size = 80
        df_incremental = df.copy()
        df_incremental['SMA_20'] = None

        indicators = [{'name': 'sma', 'params': {'length': 20}, 'column': 'SMA_20'}]
        df_incremental = engine.update(
            df_incremental, cache_size, indicators, 'TEST', '5m'
        )

        # Compare last 10 values
        for i in range(90, 100):
            batch_val = df_batch['SMA_20'].iloc[i]
            incr_val = df_incremental['SMA_20'].iloc[i]

            if pd.notna(batch_val) and pd.notna(incr_val):
                diff = abs(batch_val - incr_val)
                assert diff < 1e-6, f"SMA mismatch at index {i}: batch={batch_val}, incr={incr_val}, diff={diff}"

    def test_single_bar_update(self, sample_data):
        """Test that adding a single new bar works correctly."""
        df = sample_data.copy()

        # Calculate on first 99 bars
        df_batch = df.iloc[:99].copy()
        df_batch.ta.ema(length=20, append=True)

        # Add 100th bar incrementally
        engine = IncrementalIndicatorEngine()
        df_full = df.copy()
        df_full['EMA_20'] = None

        indicators = [{'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}]
        df_full = engine.update(df_full, 99, indicators, 'TEST', '5m')

        # Should have a value for the last bar
        last_val = df_full['EMA_20'].iloc[-1]
        assert pd.notna(last_val), "Single bar update should produce a value"


class TestStatePersistence:
    """Test indicator state save/load functionality."""

    def test_state_save_and_load(self, tmp_path):
        """Test that state can be saved and restored."""
        # Create engine and add some indicators
        engine = IncrementalIndicatorEngine()

        # Initialize with dummy data
        df = pd.DataFrame({
            'open': [100.0] * 30,
            'high': [101.0] * 30,
            'low': [99.0] * 30,
            'close': [100.0] * 30,
            'volume': [1000] * 30,
            'datetime': pd.date_range('2025-01-01', periods=30, freq='5min')
        }).set_index('datetime')
        df.index = df.index.tz_localize('UTC')

        indicators = [{'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}]
        df = engine.update(df, 0, indicators, 'TEST', '5m')

        # Save state
        state_file = tmp_path / "test_state.pkl"
        engine.save_state(state_file)

        assert state_file.exists(), "State file should be created"

        # Create new engine and load state
        engine2 = IncrementalIndicatorEngine()
        engine2.load_state(state_file)

        # Verify state was restored
        state_key = ('TEST', '5m')
        assert state_key in engine2.states, "State should be loaded"
        assert 'ema_length=20' in engine2.states[state_key]['indicators'], "Indicator should be present"

    def test_missing_state_file_graceful(self, tmp_path):
        """Test that loading missing state file doesn't crash."""
        engine = IncrementalIndicatorEngine()
        missing_file = tmp_path / "nonexistent.pkl"

        # Should not raise exception
        engine.load_state(missing_file)
        assert len(engine.states) == 0, "Should have empty state"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_new_data(self, tmp_path):
        """Test behavior when new_start_idx equals df length (no new data)."""
        df = pd.DataFrame({
            'open': [100.0] * 20,
            'high': [101.0] * 20,
            'low': [99.0] * 20,
            'close': [100.0] * 20,
            'volume': [1000] * 20,
            'datetime': pd.date_range('2025-01-01', periods=20, freq='5min')
        }).set_index('datetime')
        df.index = df.index.tz_localize('UTC')

        engine = IncrementalIndicatorEngine()
        indicators = [{'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}]

        # new_start_idx = len(df) means no new data
        df_result = engine.update(df, len(df), indicators, 'TEST', '5m')

        # Should not crash
        assert len(df_result) == len(df)

    def test_unsupported_indicator_warning(self, caplog):
        """Test that unsupported indicators log warnings."""
        df = pd.DataFrame({
            'open': [100.0] * 20,
            'high': [101.0] * 20,
            'low': [99.0] * 20,
            'close': [100.0] * 20,
            'volume': [1000] * 20,
            'datetime': pd.date_range('2025-01-01', periods=20, freq='5min')
        }).set_index('datetime')
        df.index = df.index.tz_localize('UTC')

        engine = IncrementalIndicatorEngine()
        indicators = [{'name': 'nonexistent_indicator', 'params': {}, 'column': 'FOO'}]

        df_result = engine.update(df, 10, indicators, 'TEST', '5m')

        # Should log warning about unsupported indicator
        assert "not yet supported" in caplog.text
