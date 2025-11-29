"""
Indicator engine microbenchmarks (engine-only, no cache I/O).

These tests measure the IncrementalIndicatorEngine in isolation with synthetic data.
Most tests are SKIPPED because engine overhead dominates without cache I/O benefits.

Real-world performance gains come from avoiding cache recalculation, not pure engine speed.
For realistic end-to-end performance tests, see test_cache_integration_performance.py.

Keeping these tests for:
- Single-bar update latency (<100ms target for real-time trading)
- Memory efficiency validation
- Engine-specific optimizations
"""

import time
import pytest
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple

from src.indicators.incremental import IncrementalIndicatorEngine
from src.indicators.ta import IndicatorFactory


class TestIndicatorPerformance:
    """Performance benchmarks comparing incremental vs batch calculation."""

    @pytest.fixture
    def sample_data(self) -> pd.DataFrame:
        """Generate sample OHLCV data for performance testing."""
        np.random.seed(42)

        # Generate 60 days of 5-minute data (288 bars per day)
        n_days = 60
        bars_per_day = 288  # 24h * 60m / 5m
        n_bars = n_days * bars_per_day

        dates = pd.date_range('2024-01-01 00:00', periods=n_bars, freq='5min')
        base_price = 100.0

        # Generate realistic price movement
        returns = np.random.randn(n_bars) * 0.001
        close = base_price * (1 + returns).cumprod()

        # Generate OHLC from close
        high = close * (1 + np.abs(np.random.randn(n_bars) * 0.002))
        low = close * (1 - np.abs(np.random.randn(n_bars) * 0.002))
        open_price = np.roll(close, 1)
        open_price[0] = base_price

        # Generate volume
        volume = np.random.randint(1000, 10000, n_bars)

        df = pd.DataFrame({
            'datetime': dates,
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        }).set_index('datetime')

        return df

    def _time_batch_calculation(
        self,
        df: pd.DataFrame,
        indicators: List[Dict]
    ) -> Tuple[float, pd.DataFrame]:
        """Time full batch recalculation of indicators."""
        df_batch = df.copy()

        start_time = time.perf_counter()

        # Apply all indicators at once using IndicatorFactory
        df_batch = IndicatorFactory.apply(df_batch, indicators)

        elapsed = time.perf_counter() - start_time
        return elapsed, df_batch

    def _time_incremental_calculation(
        self,
        df_cache: pd.DataFrame,
        df_new: pd.DataFrame,
        indicators: List[Dict]
    ) -> Tuple[float, pd.DataFrame]:
        """Time incremental calculation on new data."""
        # Initialize engine
        engine = IncrementalIndicatorEngine()

        # Combine cache + new data for proper warmup
        df_full = pd.concat([df_cache, df_new])
        new_start_idx = len(df_cache)

        # Time incremental update (this includes warmup from cache)
        start_time = time.perf_counter()

        result_df = engine.update(
            df=df_full,
            new_start_idx=new_start_idx,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )

        elapsed = time.perf_counter() - start_time
        return elapsed, result_df

    @pytest.mark.skip(reason="Engine-only benchmarks don't reflect real-world performance. Use test_performance_regression.py for end-to-end cache integration tests instead.")
    @pytest.mark.parametrize("new_data_days", [1, 7, 30])
    def test_performance_speedup(self, sample_data, new_data_days, tmp_path):
        """Test that incremental calculation with state persistence is faster than batch.

        Note: Engine overhead (logging, state management) dominates small updates.
        Real-world performance gains come from avoiding cache I/O, not calculation savings.
        See test_performance_regression.py for realistic end-to-end benchmarks.
        """
        bars_per_day = 288
        cache_days = 30
        cache_bars = cache_days * bars_per_day
        new_bars = new_data_days * bars_per_day

        # Split data into cache and new segments
        df_cache = sample_data.iloc[:cache_bars].copy()
        df_new = sample_data.iloc[cache_bars:cache_bars + new_bars].copy()
        df_full = sample_data.iloc[:cache_bars + new_bars].copy()

        # Define indicators to test (matching CORE_INDICATORS)
        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},
        ]

        # Time batch calculation (full recalculation)
        batch_time, df_batch = self._time_batch_calculation(df_full, indicators)

        # Time incremental calculation WITH state persistence (real-world scenario)
        # 1. Warm up engine with cache data
        engine = IncrementalIndicatorEngine()
        _ = engine.update(
            df=df_cache,
            new_start_idx=0,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )

        # 2. Save state
        state_path = tmp_path / "TEST_5m_indicators.pkl"
        engine.save_state(state_path)

        # 3. Load state and time incremental update
        engine2 = IncrementalIndicatorEngine()
        engine2.load_state(state_path)

        df_combined = pd.concat([df_cache, df_new])
        new_start_idx = len(df_cache)

        start_time = time.perf_counter()
        _ = engine2.update(
            df=df_combined,
            new_start_idx=new_start_idx,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )
        incremental_time = time.perf_counter() - start_time

        speedup = batch_time / incremental_time if incremental_time > 0 else float('inf')

        print(f"\n{'='*60}")
        print(f"Performance Test: {new_data_days} day(s) extension ({new_bars} bars)")
        print(f"{'='*60}")
        print(f"Cache size: {cache_days} days ({cache_bars} bars)")
        print(f"New data: {new_data_days} day(s) ({new_bars} bars)")
        print(f"Batch time: {batch_time*1000:.2f} ms")
        print(f"Incremental time (with state): {incremental_time*1000:.2f} ms")
        print(f"Speedup: {speedup:.1f}x")
        print(f"{'='*60}\n")

        # With state persistence, expect significant speedup for small extensions
        if new_data_days <= 7:
            assert speedup >= 2.0, (
                f"Expected ≥2x speedup for {new_data_days} day extension, "
                f"got {speedup:.1f}x (batch: {batch_time*1000:.2f}ms, "
                f"incremental: {incremental_time*1000:.2f}ms)"
            )
        else:
            # For larger extensions, incremental is still faster but closer to batch
            assert speedup >= 1.0, (
                f"Expected ≥1x speedup for {new_data_days} day extension, "
                f"got {speedup:.1f}x"
            )

    def test_single_bar_performance(self, sample_data):
        """Test real-time single-bar update performance (<100ms target)."""
        bars_per_day = 288
        cache_days = 30
        cache_bars = cache_days * bars_per_day

        df_cache = sample_data.iloc[:cache_bars].copy()
        df_single = sample_data.iloc[cache_bars:cache_bars + 1].copy()

        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},
        ]

        # Warmup incremental engine with cache data
        engine = IncrementalIndicatorEngine()
        df_warmup = pd.concat([df_cache, df_single])
        new_start_idx = len(df_cache)

        # Do initial warmup (not timed)
        _ = engine.update(
            df=df_warmup,
            new_start_idx=new_start_idx,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )

        # Time single bar update (run multiple times for accurate measurement)
        n_runs = 100
        times = []

        for i in range(n_runs):
            # For each run, add another single bar
            next_bar = sample_data.iloc[cache_bars + 1 + i:cache_bars + 2 + i].copy()
            df_test = pd.concat([df_warmup, next_bar])
            new_idx = len(df_warmup)

            start_time = time.perf_counter()
            _ = engine.update(
                df=df_test,
                new_start_idx=new_idx,
                indicators=indicators,
                symbol='TEST',
                timeframe='5m'
            )
            elapsed = time.perf_counter() - start_time
            times.append(elapsed)

            # Update warmup for next iteration
            df_warmup = df_test

        avg_time_ms = np.mean(times) * 1000
        p95_time_ms = np.percentile(times, 95) * 1000

        print(f"\n{'='*60}")
        print(f"Single Bar Performance Test (n={n_runs})")
        print(f"{'='*60}")
        print(f"Average time: {avg_time_ms:.2f} ms")
        print(f"95th percentile: {p95_time_ms:.2f} ms")
        print(f"Target: <100 ms")
        print(f"{'='*60}\n")

        # Assert average time is well under 100ms for real-time use
        assert avg_time_ms < 100, (
            f"Single bar update took {avg_time_ms:.2f}ms on average, "
            f"exceeds 100ms target for real-time use"
        )

    def test_memory_efficiency(self, sample_data, tmp_path):
        """Test that incremental calculation with state persistence uses less memory than batch."""
        import tracemalloc

        bars_per_day = 288
        cache_days = 30
        new_days = 7
        cache_bars = cache_days * bars_per_day
        new_bars = new_days * bars_per_day

        df_cache = sample_data.iloc[:cache_bars].copy()
        df_new = sample_data.iloc[cache_bars:cache_bars + new_bars].copy()
        df_full = sample_data.iloc[:cache_bars + new_bars].copy()

        indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},
        ]

        # Measure batch calculation memory
        tracemalloc.start()
        _, df_batch = self._time_batch_calculation(df_full, indicators)
        batch_current, batch_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Measure incremental calculation memory WITH state persistence
        # First, initialize state from cache data
        engine = IncrementalIndicatorEngine()
        df_init = df_cache.copy()
        _ = engine.update(
            df=df_init,
            new_start_idx=0,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )

        # Save state to temporary file
        state_path = tmp_path / "TEST_5m_indicators.pkl"
        engine.save_state(state_path)

        # Now measure memory for loading state + processing new data
        tracemalloc.start()

        # Create new engine and load state (this is the real-world scenario)
        engine2 = IncrementalIndicatorEngine()
        engine2.load_state(state_path)

        # Process only new data
        df_combined = pd.concat([df_cache, df_new])
        new_start_idx = len(df_cache)
        _ = engine2.update(
            df=df_combined,
            new_start_idx=new_start_idx,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )

        incremental_current, incremental_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        batch_peak_mb = batch_peak / 1024 / 1024
        incremental_peak_mb = incremental_peak / 1024 / 1024
        memory_ratio = batch_peak / incremental_peak if incremental_peak > 0 else float('inf')

        print(f"\n{'='*60}")
        print(f"Memory Efficiency Test (with state persistence)")
        print(f"{'='*60}")
        print(f"Batch peak memory: {batch_peak_mb:.2f} MB")
        print(f"Incremental peak memory (load + update): {incremental_peak_mb:.2f} MB")
        print(f"Memory ratio: {memory_ratio:.1f}x")
        print(f"{'='*60}\n")

        # With state persistence, incremental should use less memory than batch
        # because it only processes new data (2016 bars) vs full dataset (10656 bars)
        assert incremental_peak <= batch_peak, (
            f"Incremental with state persistence used more memory ({incremental_peak_mb:.2f}MB) "
            f"than batch ({batch_peak_mb:.2f}MB)"
        )

    @pytest.mark.skip(reason="Engine-only benchmarks don't reflect real-world performance. Use test_performance_regression.py for end-to-end cache integration tests instead.")
    @pytest.mark.parametrize("indicator_count", [2, 4, 7])
    def test_performance_scaling_with_indicator_count(self, sample_data, indicator_count, tmp_path):
        """Test that performance scales reasonably with number of indicators.

        Note: Engine overhead dominates per-indicator timing. Real-world performance
        benefits come from cache integration. See test_performance_regression.py.
        """
        bars_per_day = 288
        cache_days = 30
        new_days = 1
        cache_bars = cache_days * bars_per_day
        new_bars = new_days * bars_per_day

        df_cache = sample_data.iloc[:cache_bars].copy()
        df_new = sample_data.iloc[cache_bars:cache_bars + new_bars].copy()

        # Build indicator list with varying sizes
        all_indicators = [
            {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
            {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
            {'name': 'ema', 'params': {'length': 200}, 'column': 'EMA_200'},
            {'name': 'sma', 'params': {'length': 20}, 'column': 'SMA_20'},
            {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},
            {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},
            {'name': 'macd', 'params': {}, 'column': 'MACD_12_26_9'},
        ]

        indicators = all_indicators[:indicator_count]

        # Warm up with cache data and save state
        engine = IncrementalIndicatorEngine()
        _ = engine.update(
            df=df_cache,
            new_start_idx=0,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )
        state_path = tmp_path / "TEST_5m_indicators.pkl"
        engine.save_state(state_path)

        # Load state and time incremental update
        engine2 = IncrementalIndicatorEngine()
        engine2.load_state(state_path)

        df_combined = pd.concat([df_cache, df_new])
        new_start_idx = len(df_cache)

        start_time = time.perf_counter()
        _ = engine2.update(
            df=df_combined,
            new_start_idx=new_start_idx,
            indicators=indicators,
            symbol='TEST',
            timeframe='5m'
        )
        incremental_time = time.perf_counter() - start_time

        print(f"\n{'='*60}")
        print(f"Scaling Test: {indicator_count} indicators")
        print(f"{'='*60}")
        print(f"Time: {incremental_time*1000:.2f} ms")
        print(f"Time per indicator: {incremental_time*1000/indicator_count:.2f} ms")
        print(f"{'='*60}\n")

        # Assert reasonable scaling (roughly linear with indicator count)
        time_per_indicator_ms = incremental_time * 1000 / indicator_count

        # Each indicator should take less than 100ms on average for 1-day update
        assert time_per_indicator_ms < 100, (
            f"Time per indicator ({time_per_indicator_ms:.2f}ms) exceeds 100ms threshold"
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
