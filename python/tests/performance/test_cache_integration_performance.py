"""
Cache integration performance tests for incremental indicators.

These tests measure REAL-WORLD end-to-end performance including:
- Cache I/O (parquet read/write)
- State persistence (pickle load/save)
- Incremental indicator calculation
- Full CacheDataLoader integration

This is the PRIMARY performance test suite for detecting regressions.
For engine-only microbenchmarks, see test_indicator_engine_microbenchmarks.py.

Baseline metrics are established on first run and saved to:
    tests/__baselines__/performance_baselines.json

Subsequent runs compare against these baselines and fail if performance degrades
beyond acceptable thresholds (default: 50% slower).
"""

import time
import json
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, asdict

from src.back_test.data_fetcher import CacheDataLoader
from src.config.indicators import CORE_INDICATORS


@dataclass
class PerformanceMetric:
    """Performance metric for a single test scenario."""
    test_name: str
    cache_bars: int
    new_bars: int
    indicator_count: int
    avg_time_ms: float
    p50_time_ms: float
    p95_time_ms: float
    p99_time_ms: float
    min_time_ms: float
    max_time_ms: float
    iterations: int
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class TestPerformanceRegression:
    """Performance regression tests using CacheDataLoader with state persistence."""

    BASELINE_DIR = Path(__file__).parent.parent / "__baselines__"
    BASELINE_FILE = BASELINE_DIR / "performance_baselines.json"
    REGRESSION_THRESHOLD = 1.5  # Fail if 50% slower than baseline

    @pytest.fixture(scope="class")
    def baseline_data(self) -> dict:
        """Load or initialize baseline performance data."""
        if self.BASELINE_FILE.exists():
            with open(self.BASELINE_FILE, 'r') as f:
                return json.load(f)
        return {}

    @pytest.fixture
    def temp_cache_path(self, tmp_path):
        """Create temporary cache directory for testing."""
        cache_dir = tmp_path / "data_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _generate_ohlcv_data(self, n_bars: int, seed: int = 42) -> pd.DataFrame:
        """Generate realistic OHLCV data for testing."""
        np.random.seed(seed)

        dates = pd.date_range('2024-01-01 09:30', periods=n_bars, freq='5min')
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
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        }, index=dates)

        return df

    def _create_cache_file(
        self,
        cache_path: Path,
        symbol: str,
        timeframe: str,
        data: pd.DataFrame
    ):
        """Create a cache file with the given data."""
        cache_file = cache_path / f"{symbol}_{timeframe}.parquet"
        data.to_parquet(cache_file)

    def _measure_cache_fetch(
        self,
        cache_path: Path,
        symbol: str,
        timeframe: str,
        indicators: List[Dict] = None,
        iterations: int = 10
    ) -> Tuple[List[float], pd.DataFrame]:
        """Measure performance of cache fetch with indicators.

        Returns:
            Tuple of (list of times in seconds, resulting DataFrame)
        """
        if indicators is None:
            indicators = CORE_INDICATORS

        times = []
        result_df = None

        for i in range(iterations):
            # Create new loader instance each time to simulate real usage
            loader = CacheDataLoader(
                cache_dir=str(cache_path),
                auto_indicators=indicators,
                indicator_mode='incremental'
            )

            start_time = time.perf_counter()
            result_df = loader.fetch(
                symbol=symbol,
                timeframe=timeframe,
                start='2024-01-01',
                end='2024-12-31'
            )
            elapsed = time.perf_counter() - start_time
            times.append(elapsed)

        return times, result_df

    def _calculate_metrics(
        self,
        test_name: str,
        times: List[float],
        cache_bars: int,
        new_bars: int,
        indicator_count: int
    ) -> PerformanceMetric:
        """Calculate performance metrics from timing data."""
        times_ms = [t * 1000 for t in times]

        return PerformanceMetric(
            test_name=test_name,
            cache_bars=cache_bars,
            new_bars=new_bars,
            indicator_count=indicator_count,
            avg_time_ms=float(np.mean(times_ms)),
            p50_time_ms=float(np.percentile(times_ms, 50)),
            p95_time_ms=float(np.percentile(times_ms, 95)),
            p99_time_ms=float(np.percentile(times_ms, 99)),
            min_time_ms=float(np.min(times_ms)),
            max_time_ms=float(np.max(times_ms)),
            iterations=len(times),
            timestamp=pd.Timestamp.now().isoformat()
        )

    def _save_baseline(self, metric: PerformanceMetric, baseline_data: dict):
        """Save performance metric as baseline."""
        self.BASELINE_DIR.mkdir(parents=True, exist_ok=True)

        baseline_data[metric.test_name] = metric.to_dict()

        with open(self.BASELINE_FILE, 'w') as f:
            json.dump(baseline_data, f, indent=2)

    def _check_regression(
        self,
        metric: PerformanceMetric,
        baseline_data: dict,
        threshold: float = None
    ) -> Tuple[bool, str]:
        """Check if performance has regressed compared to baseline.

        Returns:
            Tuple of (passed, message)
        """
        if threshold is None:
            threshold = self.REGRESSION_THRESHOLD

        baseline = baseline_data.get(metric.test_name)

        if not baseline:
            # No baseline exists, save this as baseline
            self._save_baseline(metric, baseline_data)
            return True, f"✓ Baseline established: {metric.avg_time_ms:.2f}ms (p95: {metric.p95_time_ms:.2f}ms)"

        baseline_avg = baseline['avg_time_ms']
        current_avg = metric.avg_time_ms
        ratio = current_avg / baseline_avg if baseline_avg > 0 else float('inf')

        if ratio > threshold:
            return False, (
                f"❌ Performance regression detected!\n"
                f"  Baseline: {baseline_avg:.2f}ms (from {baseline['timestamp'][:10]})\n"
                f"  Current:  {current_avg:.2f}ms\n"
                f"  Ratio:    {ratio:.2f}x (threshold: {threshold:.2f}x)\n"
                f"  p95:      {baseline['p95_time_ms']:.2f}ms → {metric.p95_time_ms:.2f}ms"
            )

        improvement = (1 - ratio) * 100
        return True, (
            f"✓ Performance OK: {current_avg:.2f}ms "
            f"({improvement:+.1f}% vs baseline {baseline_avg:.2f}ms, "
            f"p95: {metric.p95_time_ms:.2f}ms)"
        )

    @pytest.mark.parametrize("cache_size", ["small", "medium", "large"])
    def test_cache_extension_performance(
        self,
        temp_cache_path,
        baseline_data,
        cache_size
    ):
        """Test performance of cache extension with different cache sizes.

        Small: 3 days (864 bars)
        Medium: 30 days (8,640 bars)
        Large: 180 days (51,840 bars)
        """
        bars_per_day = 288  # 5-minute bars

        cache_sizes = {
            'small': (3, 1),      # 3 days cache, 1 day extension
            'medium': (30, 7),    # 30 days cache, 7 days extension
            'large': (180, 30)    # 180 days cache, 30 days extension
        }

        cache_days, extend_days = cache_sizes[cache_size]
        cache_bars = cache_days * bars_per_day
        extend_bars = extend_days * bars_per_day

        # Create initial cache
        symbol = 'TEST'
        timeframe = '5m'

        initial_data = self._generate_ohlcv_data(cache_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, initial_data)

        # First fetch - populate cache with indicators
        loader = CacheDataLoader(
            cache_dir=str(temp_cache_path),
            auto_indicators=CORE_INDICATORS,
            indicator_mode='incremental'
        )
        _ = loader.fetch(symbol, timeframe, '2024-01-01', '2024-12-31')

        # Extend cache with new data
        extended_data = self._generate_ohlcv_data(cache_bars + extend_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, extended_data)

        # Measure extension performance
        test_name = f"cache_extension_{cache_size}"
        times, result_df = self._measure_cache_fetch(
            temp_cache_path,
            symbol,
            timeframe,
            CORE_INDICATORS,
            iterations=10
        )

        metric = self._calculate_metrics(
            test_name,
            times,
            cache_bars,
            extend_bars,
            len(CORE_INDICATORS)
        )

        passed, message = self._check_regression(metric, baseline_data)
        print(f"\n{test_name}:")
        print(f"  Cache: {cache_days} days ({cache_bars} bars)")
        print(f"  Extension: {extend_days} days ({extend_bars} bars)")
        print(f"  Indicators: {len(CORE_INDICATORS)}")
        print(f"  {message}")

        assert passed, message

    def test_initial_cache_population_performance(
        self,
        temp_cache_path,
        baseline_data
    ):
        """Test performance of initial cache population (cold start)."""
        bars_per_day = 288
        cache_days = 30
        cache_bars = cache_days * bars_per_day

        symbol = 'TEST'
        timeframe = '5m'
        test_name = "initial_population_30d"

        # Create cache file
        data = self._generate_ohlcv_data(cache_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, data)

        # Measure initial population performance
        times, result_df = self._measure_cache_fetch(
            temp_cache_path,
            symbol,
            timeframe,
            CORE_INDICATORS,
            iterations=10
        )

        metric = self._calculate_metrics(
            test_name,
            times,
            cache_bars,
            0,  # No extension, just initial population
            len(CORE_INDICATORS)
        )

        passed, message = self._check_regression(metric, baseline_data)
        print(f"\n{test_name}:")
        print(f"  Cache: {cache_days} days ({cache_bars} bars)")
        print(f"  Indicators: {len(CORE_INDICATORS)}")
        print(f"  {message}")

        assert passed, message

    @pytest.mark.parametrize("indicator_count", [2, 4, 7])
    def test_performance_scaling_with_indicators(
        self,
        temp_cache_path,
        baseline_data,
        indicator_count
    ):
        """Test that performance scales reasonably with indicator count."""
        bars_per_day = 288
        cache_days = 30
        extend_days = 7
        cache_bars = cache_days * bars_per_day
        extend_bars = extend_days * bars_per_day

        symbol = 'TEST'
        timeframe = '5m'

        # Select subset of indicators
        indicators = CORE_INDICATORS[:indicator_count]
        test_name = f"indicator_scaling_{indicator_count}"

        # Create initial cache
        initial_data = self._generate_ohlcv_data(cache_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, initial_data)

        # First fetch
        loader = CacheDataLoader(
            cache_dir=str(temp_cache_path),
            auto_indicators=indicators,
            indicator_mode='incremental'
        )
        _ = loader.fetch(symbol, timeframe, '2024-01-01', '2024-12-31')

        # Extend cache
        extended_data = self._generate_ohlcv_data(cache_bars + extend_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, extended_data)

        # Measure performance
        times, result_df = self._measure_cache_fetch(
            temp_cache_path,
            symbol,
            timeframe,
            indicators,
            iterations=10
        )

        metric = self._calculate_metrics(
            test_name,
            times,
            cache_bars,
            extend_bars,
            indicator_count
        )

        passed, message = self._check_regression(metric, baseline_data)

        time_per_indicator = metric.avg_time_ms / indicator_count if indicator_count > 0 else 0

        print(f"\n{test_name}:")
        print(f"  Indicators: {indicator_count}")
        print(f"  Time per indicator: {time_per_indicator:.2f}ms")
        print(f"  {message}")

        assert passed, message

        # Also assert reasonable scaling
        assert time_per_indicator < 100, (
            f"Time per indicator ({time_per_indicator:.2f}ms) exceeds 100ms threshold"
        )

    def test_state_load_performance(
        self,
        temp_cache_path,
        baseline_data
    ):
        """Test performance of loading indicator state from disk."""
        from src.indicators.incremental import IncrementalIndicatorEngine

        bars_per_day = 288
        cache_days = 30
        cache_bars = cache_days * bars_per_day

        symbol = 'TEST'
        timeframe = '5m'
        test_name = "state_load"

        # Create cache and populate with indicators
        data = self._generate_ohlcv_data(cache_bars)
        self._create_cache_file(temp_cache_path, symbol, timeframe, data)

        loader = CacheDataLoader(
            cache_dir=str(temp_cache_path),
            auto_indicators=CORE_INDICATORS,
            indicator_mode='incremental'
        )
        _ = loader.fetch(symbol, timeframe, '2024-01-01', '2024-12-31')

        # Now measure state load performance
        state_file = temp_cache_path / f"{symbol}_{timeframe}_indicators.pkl"
        assert state_file.exists(), "State file should exist after fetch"

        times = []
        for _ in range(50):  # More iterations for precise measurement
            engine = IncrementalIndicatorEngine()

            start_time = time.perf_counter()
            engine.load_state(state_file)
            elapsed = time.perf_counter() - start_time

            times.append(elapsed)

        metric = self._calculate_metrics(
            test_name,
            times,
            cache_bars,
            0,
            len(CORE_INDICATORS)
        )

        passed, message = self._check_regression(metric, baseline_data)

        print(f"\n{test_name}:")
        print(f"  State size: {state_file.stat().st_size / 1024:.2f} KB")
        print(f"  Indicators: {len(CORE_INDICATORS)}")
        print(f"  {message}")

        assert passed, message

        # State load should be very fast (<50ms)
        assert metric.p95_time_ms < 50, (
            f"State load p95 ({metric.p95_time_ms:.2f}ms) exceeds 50ms threshold"
        )

    def test_multiple_symbol_performance(
        self,
        temp_cache_path,
        baseline_data
    ):
        """Test performance with multiple symbols (realistic scenario)."""
        bars_per_day = 288
        cache_days = 30
        extend_days = 1
        cache_bars = cache_days * bars_per_day
        extend_bars = extend_days * bars_per_day

        symbols = ['AAPL', 'NVDA', 'AMZN']
        timeframe = '5m'
        test_name = "multiple_symbols_3"

        # Create initial cache for all symbols
        for symbol in symbols:
            initial_data = self._generate_ohlcv_data(cache_bars, seed=hash(symbol) % 10000)
            self._create_cache_file(temp_cache_path, symbol, timeframe, initial_data)

        # First fetch for all symbols
        for symbol in symbols:
            loader = CacheDataLoader(
                cache_dir=str(temp_cache_path),
                auto_indicators=CORE_INDICATORS,
                indicator_mode='incremental'
            )
            _ = loader.fetch(symbol, timeframe, '2024-01-01', '2024-12-31')

        # Extend all caches
        for symbol in symbols:
            extended_data = self._generate_ohlcv_data(cache_bars + extend_bars, seed=hash(symbol) % 10000)
            self._create_cache_file(temp_cache_path, symbol, timeframe, extended_data)

        # Measure performance of fetching all symbols
        times = []
        for _ in range(10):
            start_time = time.perf_counter()

            for symbol in symbols:
                loader = CacheDataLoader(
                    cache_dir=str(temp_cache_path),
                    auto_indicators=CORE_INDICATORS,
                    indicator_mode='incremental'
                )
                _ = loader.fetch(symbol, timeframe, '2024-01-01', '2024-12-31')

            elapsed = time.perf_counter() - start_time
            times.append(elapsed)

        metric = self._calculate_metrics(
            test_name,
            times,
            cache_bars * len(symbols),
            extend_bars * len(symbols),
            len(CORE_INDICATORS)
        )

        passed, message = self._check_regression(metric, baseline_data)

        avg_per_symbol = metric.avg_time_ms / len(symbols)

        print(f"\n{test_name}:")
        print(f"  Symbols: {len(symbols)}")
        print(f"  Average time per symbol: {avg_per_symbol:.2f}ms")
        print(f"  {message}")

        assert passed, message


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
