"""Phase 5: Snapshot tests for incremental indicator calculations.

These tests capture reference values for all indicators to ensure:
1. Incremental calculation results remain stable across code changes
2. Results match expected values within numerical tolerance
3. Regression detection for algorithm changes

Uses real market data fixtures (AAPL, NVDA, AMZN) for deterministic testing.

Run with:
    pytest tests/indicators/test_indicator_snapshots.py --auto-create-snapshots  # Create snapshots
    pytest tests/indicators/test_indicator_snapshots.py                          # Validate against snapshots
    pytest tests/indicators/test_indicator_snapshots.py --update-snapshots       # Update if intentional change
"""

import pytest
import pandas as pd
from pathlib import Path

from src.indicators.incremental import IncrementalIndicatorEngine
from src.config.indicators import CORE_INDICATORS
from paths import get_scenarios_root


@pytest.fixture(scope="function")  # Function scope to reload data each time
def fixture_data() -> dict[str, pd.DataFrame]:
    """Load real market data fixtures for deterministic testing.

    Function-scoped to ensure fresh data is loaded for each test,
    preventing contamination from indicator calculations that modify DataFrames.

    Returns dict with keys: 'AAPL', 'NVDA', 'AMZN'
    Each contains 3 days of 5-minute OHLCV data (234 bars per ticker).
    """
    fixture_path = get_scenarios_root() / "indicator_snapshots"

    data = {}
    for ticker in ['AAPL', 'NVDA', 'AMZN']:
        file_path = fixture_path / f"{ticker}.parquet"
        df = pd.read_parquet(file_path)
        data[ticker] = df

    return data


@pytest.fixture
def aapl_data(fixture_data) -> pd.DataFrame:
    """AAPL fixture data (234 bars, 3 days)."""
    return fixture_data['AAPL'].copy()


@pytest.fixture
def nvda_data(fixture_data) -> pd.DataFrame:
    """NVDA fixture data (234 bars, 3 days)."""
    return fixture_data['NVDA'].copy()


@pytest.fixture
def amzn_data(fixture_data) -> pd.DataFrame:
    """AMZN fixture data (234 bars, 3 days)."""
    return fixture_data['AMZN'].copy()


class TestIndicatorSnapshots:
    """Snapshot tests for all indicator calculations."""

    def test_ema_20_snapshot(self, aapl_data, assert_snapshot):
        """Snapshot test for EMA_20 indicator using AAPL data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}
        ]

        result = engine.update(
            df=aapl_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AAPL',
            timeframe='5m'
        )

        # Extract indicator values for snapshot (skip warmup period)
        snapshot_df = result[['close', 'EMA_20']].iloc[20:].copy()

        assert_snapshot(
            snapshot_df,
            name='ema_20_incremental',
            kind='indicators',
            extra_config={'indicator': 'EMA_20', 'params': {'length': 20}}
        )

    def test_ema_50_snapshot(self, nvda_data, assert_snapshot):
        """Snapshot test for EMA_50 indicator using NVDA data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'}
        ]

        result = engine.update(
            df=nvda_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='NVDA',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'EMA_50']].iloc[50:].copy()

        assert_snapshot(
            snapshot_df,
            name='ema_50_incremental',
            kind='indicators',
            extra_config={'indicator': 'EMA_50', 'params': {'length': 50}}
        )

    def test_ema_200_snapshot(self, amzn_data, assert_snapshot):
        """Snapshot test for EMA_200 indicator using AMZN data.

        Note: 234 bars is below optimal convergence for EMA_200, but sufficient
        for regression detection. Values may have higher warmup period.
        """
        engine = IncrementalIndicatorEngine()
        indicators = [
            {'name': 'ema', 'params': {'length': 200}, 'column': 'EMA_200'}
        ]

        result = engine.update(
            df=amzn_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AMZN',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'EMA_200']].iloc[200:].copy()

        assert_snapshot(
            snapshot_df,
            name='ema_200_incremental',
            kind='indicators',
            extra_config={'indicator': 'EMA_200', 'params': {'length': 200}}
        )

    def test_sma_20_snapshot(self, aapl_data, assert_snapshot):
        """Snapshot test for SMA_20 indicator using AAPL data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'sma', 'params': {'length': 20}, 'column': 'SMA_20'}
        ]

        result = engine.update(
            df=aapl_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AAPL',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'SMA_20']].iloc[20:].copy()

        assert_snapshot(
            snapshot_df,
            name='sma_20_incremental',
            kind='indicators',
            extra_config={'indicator': 'SMA_20', 'params': {'length': 20}}
        )

    def test_rsi_14_snapshot(self, nvda_data, assert_snapshot):
        """Snapshot test for RSI_14 indicator using NVDA data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'}
        ]

        result = engine.update(
            df=nvda_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='NVDA',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'RSI_14']].iloc[15:].copy()

        assert_snapshot(
            snapshot_df,
            name='rsi_14_incremental',
            kind='indicators',
            extra_config={'indicator': 'RSI_14', 'params': {'length': 14}}
        )

    def test_atr_14_snapshot(self, amzn_data, assert_snapshot):
        """Snapshot test for ATR_14 indicator using AMZN data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'}
        ]

        result = engine.update(
            df=amzn_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AMZN',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'ATRr_14']].iloc[15:].copy()

        assert_snapshot(
            snapshot_df,
            name='atr_14_incremental',
            kind='indicators',
            extra_config={'indicator': 'ATRr_14', 'params': {'length': 14}}
        )

    def test_macd_snapshot(self, aapl_data, assert_snapshot):
        """Snapshot test for MACD indicator using AAPL data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'macd', 'params': {'fast': 12, 'slow': 26, 'signal': 9},
             'columns': ['MACD_12_26_9', 'MACDs_12_26_9', 'MACDh_12_26_9']}
        ]

        result = engine.update(
            df=aapl_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AAPL',
            timeframe='5m'
        )        # Skip warmup period (MACD uses slow=26 + signal=9 = 35 bars)
        snapshot_df = result[['close', 'MACD_12_26_9', 'MACDs_12_26_9', 'MACDh_12_26_9']].iloc[35:].copy()

        assert_snapshot(
            snapshot_df,
            name='macd_12_26_9_incremental',
            kind='indicators',
            extra_config={'indicator': 'MACD', 'params': {'fast': 12, 'slow': 26, 'signal': 9}}
        )

    def test_bbands_snapshot(self, nvda_data, assert_snapshot):
        """Snapshot test for Bollinger Bands indicator using NVDA data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'bbands', 'params': {'length': 20, 'std': 2.0},
             'columns': ['BBL_20_2.0', 'BBM_20_2.0', 'BBU_20_2.0']}
        ]

        result = engine.update(
            df=nvda_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='NVDA',
            timeframe='5m'
        )

        # Skip warmup period
        snapshot_df = result[['close', 'BBL_20_2.0', 'BBM_20_2.0', 'BBU_20_2.0']].iloc[20:].copy()

        assert_snapshot(
            snapshot_df,
            name='bbands_20_2_incremental',
            kind='indicators',
            extra_config={'indicator': 'BBands', 'params': {'length': 20, 'std': 2.0}}
        )

    def test_orb_levels_snapshot(self, amzn_data, assert_snapshot):
        """Snapshot test for ORB levels indicator using AMZN data (3 days)."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'orb_levels', 'params': {'start_time': '09:30', 'duration_minutes': 5, 'body_pct': 0.5},
             'columns': ['ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_Breakout']}
        ]

        result = engine.update(
            df=amzn_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AMZN',
            timeframe='5m'
        )

        # Include OHLC for context (no warmup needed for ORB, it's day-scoped)
        snapshot_df = result[['open', 'high', 'low', 'close',
                              'ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_Breakout']].copy()

        assert_snapshot(
            snapshot_df,
            name='orb_levels_incremental',
            kind='indicators',
            extra_config={'indicator': 'ORB', 'params': {'start_time': '09:30', 'duration_minutes': 5, 'body_pct': 0.5}}
        )

    def test_core_indicators_combined_snapshot(self, aapl_data, assert_snapshot):
        """Snapshot test for all CORE_INDICATORS calculated together using AAPL data."""
        engine = IncrementalIndicatorEngine()

        # Calculate all CORE_INDICATORS except ORB (tested separately)
        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'ema', 'params': {'length': 30}, 'column': 'EMA_30'},
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},
        ]

        result = engine.update(
            df=aapl_data.copy(),
            new_start_idx=0,
            indicators=indicators,
            symbol='AAPL',
            timeframe='5m'
        )

        # Skip warmup period (longest is EMA_50)
        snapshot_df = result[['close', 'EMA_20', 'EMA_30', 'EMA_50', 'RSI_14', 'ATRr_14']].iloc[50:].copy()

        assert_snapshot(
            snapshot_df,
            name='core_indicators_combined',
            kind='indicators',
            extra_config={'indicators': 'CORE_INDICATORS_SUBSET'}
        )

    def test_incremental_extension_snapshot(self, nvda_data, assert_snapshot, tmp_path):
        """Snapshot test for incremental extension with state persistence using NVDA data."""
        engine = IncrementalIndicatorEngine()

        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
        ]

        # Calculate on first 2 days (156 bars)
        initial_data = nvda_data.iloc[:156].copy()
        result1 = engine.update(
            df=initial_data,
            new_start_idx=0,
            indicators=indicators,
            symbol='NVDA',
            timeframe='5m'
        )

        # Save state
        state_path = tmp_path / "NVDA_5m_indicators.pkl"
        engine.save_state(state_path)

        # Create new engine and load state
        engine2 = IncrementalIndicatorEngine()
        engine2.load_state(state_path)

        # Calculate incrementally on day 3 (78 bars)
        full_data = nvda_data.copy()
        result2 = engine2.update(
            df=full_data,
            new_start_idx=156,
            indicators=indicators,
            symbol='NVDA',
            timeframe='5m'
        )

        # Snapshot the final result (skip warmup)
        snapshot_df = result2[['close', 'EMA_20', 'RSI_14']].iloc[20:].copy()

        assert_snapshot(
            snapshot_df,
            name='incremental_extension_state_load',
            kind='indicators',
            extra_config={'scenario': 'state_persistence', 'initial_bars': 80, 'extension_bars': 20}
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--auto-create-snapshots'])
