"""
Stage 1 tests — Feature Engine.

Exit gate: all P0 + P1 tests must pass before trusting any research output.
Run: pytest vibe/tests/backtester/regime_research/test_features.py -v
"""

import numpy as np
import pandas as pd
import pytest

from vibe.common.indicators.batch import (
    atr_series,
    linear_slope,
    rolling_percentile_rank,
    sma_series,
)
from vibe.common.indicators.engine import IncrementalIndicatorEngine
from vibe.backtester.analysis.regime_research.features import FeatureEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV with a random walk close."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0, 2, n)
    low = close - rng.uniform(0, 2, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    idx = pd.date_range("2022-01-03", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _run_incremental_atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Run IncrementalIndicatorEngine ATR on df, return aligned Series.

    engine.py uses df.loc[i, col] where i is an integer from range(len(df)).
    This only works correctly on a RangeIndex; DatetimeIndex causes new rows to
    be inserted. Reset to RangeIndex, run, then re-attach the original index.
    """
    engine = IncrementalIndicatorEngine()
    df_reset = df.reset_index(drop=True)
    result = engine.update(
        df_reset.copy(),
        start_idx=0,
        indicators=[{"name": "atr", "params": {"length": length}}],
        symbol="TEST",
        timeframe="1D",
    )
    col = f"ATR_{length}"
    return pd.Series(result[col].values, index=df.index)


# ---------------------------------------------------------------------------
# P0 Tests
# ---------------------------------------------------------------------------

class TestP0:
    def test_atr_series_matches_incremental_engine(self):
        """batch.atr_series must agree with IncrementalIndicatorEngine on same data."""
        df = _make_ohlcv(60, seed=1)
        length = 14

        batch_atr = atr_series(df, length)
        engine_atr = _run_incremental_atr(df, length)

        # Both should produce NaN for the first length-1 rows
        assert batch_atr.iloc[: length - 1].isna().all()

        # Compare valid rows — allow tiny floating-point drift
        valid = ~batch_atr.isna() & ~engine_atr.isna()
        assert valid.sum() >= 40, "Expected at least 40 valid ATR values"
        np.testing.assert_allclose(
            batch_atr[valid].values,
            engine_atr[valid].values,
            rtol=1e-6,
            err_msg="batch.atr_series diverges from IncrementalIndicatorEngine",
        )

    def test_no_future_leakage_rolling(self):
        """Spike at row 50: rows <50 must be unchanged; feature changes only at/after 50."""
        df = _make_ohlcv(100, seed=2)

        engine = FeatureEngine()
        baseline = engine.compute(df, ["atr_14"])

        # Inject a price spike at row 50
        df_spiked = df.copy()
        df_spiked.iloc[50, df_spiked.columns.get_loc("close")] += 1_000
        df_spiked.iloc[50, df_spiked.columns.get_loc("high")] += 1_000

        spiked = engine.compute(df_spiked, ["atr_14"])

        # Rows strictly before 50 must be identical
        pre_spike = baseline["atr_14"].iloc[:50]
        pre_spike_s = spiked["atr_14"].iloc[:50]
        pd.testing.assert_series_equal(pre_spike, pre_spike_s, check_names=False)

        # At least one row at/after 50 must differ
        post_diff = (baseline["atr_14"].iloc[50:] - spiked["atr_14"].iloc[50:]).abs()
        assert post_diff.max() > 0, "Spike at row 50 should affect ATR at/after row 50"


# ---------------------------------------------------------------------------
# P1 Tests
# ---------------------------------------------------------------------------

class TestP1:
    def test_feature_row_count_preserved(self):
        """Output DataFrame must have the same index length as input."""
        df = _make_ohlcv(100)
        engine = FeatureEngine()
        out = engine.compute(df, ["atr_14", "gap_pct", "slope_20d"])
        assert len(out) == len(df)
        pd.testing.assert_index_equal(out.index, df.index)

    def test_gap_pct_correctness(self):
        """prev_close=100, open=102 → gap_pct ≈ 2.0 (percent)."""
        idx = pd.date_range("2022-01-03", periods=3, freq="D")
        df = pd.DataFrame(
            {
                "open":   [100.0, 102.0, 103.0],
                "high":   [101.0, 103.0, 104.0],
                "low":    [99.0,  101.0, 102.0],
                "close":  [100.0, 102.0, 103.0],
                "volume": [1e6,   1e6,   1e6],
            },
            index=idx,
        )
        engine = FeatureEngine()
        out = engine.compute(df, ["gap_pct"])
        # Row 1: prev_close=100, open=102 → 2%
        assert pytest.approx(out["gap_pct"].iloc[1], rel=1e-6) == 2.0

    def test_trend_slope_positive_on_uptrend(self):
        """Monotonically increasing series → positive slope."""
        n = 50
        idx = pd.date_range("2022-01-03", periods=n, freq="D")
        prices = np.linspace(100, 200, n)
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices + 1,
                "low":  prices - 1,
                "close": prices,
                "volume": np.ones(n) * 1e6,
            },
            index=idx,
        )
        slope = linear_slope(pd.Series(prices, index=idx), window=20)
        valid = slope.dropna()
        assert len(valid) > 0
        assert (valid > 0).all(), f"Expected all positive slopes, got min={valid.min()}"

    def test_trend_slope_negative_on_downtrend(self):
        """Monotonically decreasing series → negative slope."""
        n = 50
        idx = pd.date_range("2022-01-03", periods=n, freq="D")
        prices = np.linspace(200, 100, n)
        slope = linear_slope(pd.Series(prices, index=idx), window=20)
        valid = slope.dropna()
        assert len(valid) > 0
        assert (valid < 0).all(), f"Expected all negative slopes, got max={valid.max()}"

    def test_percentile_features_bounded(self):
        """atr_pctile and vol_pctile must always be in [0.0, 1.0]."""
        df = _make_ohlcv(300, seed=5)
        engine = FeatureEngine()
        out = engine.compute(df, ["atr_pctile", "vol_pctile"])

        for col in ["atr_pctile", "vol_pctile"]:
            valid = out[col].dropna()
            assert (valid >= 0.0).all() and (valid <= 1.0).all(), (
                f"{col} out of [0,1]: min={valid.min()}, max={valid.max()}"
            )

    def test_warmup_rows_are_nan(self):
        """First length rows of ATR (14) and slope (20) must be NaN."""
        df = _make_ohlcv(100, seed=6)
        atr = atr_series(df, 14)
        slope = linear_slope(df["close"], 20)

        # ATR: rows 0..12 must be NaN (length-1 warmup)
        assert atr.iloc[:13].isna().all(), "ATR rows 0-12 should be NaN"
        assert not np.isnan(atr.iloc[13]), "ATR row 13 should be valid"

        # Slope: rows 0..18 must be NaN
        assert slope.iloc[:19].isna().all(), "slope rows 0-18 should be NaN"
        assert not np.isnan(slope.iloc[19]), "slope row 19 should be valid"

    def test_unknown_feature_raises(self):
        """FeatureEngine.compute with an unregistered name must raise ValueError."""
        df = _make_ohlcv(50)
        engine = FeatureEngine()
        with pytest.raises(ValueError, match="Unknown features"):
            engine.compute(df, ["not_a_feature"])

    def test_sma_series_correctness(self):
        """SMA(3) on [1,2,3,4,5] → [NaN,NaN,2.0,3.0,4.0]."""
        idx = pd.date_range("2022-01-03", periods=5, freq="D")
        df = pd.DataFrame(
            {
                "open":   [1, 2, 3, 4, 5],
                "high":   [2, 3, 4, 5, 6],
                "low":    [0, 1, 2, 3, 4],
                "close":  [1.0, 2.0, 3.0, 4.0, 5.0],
                "volume": [1e6] * 5,
            },
            index=idx,
        )
        sma = sma_series(df, 3)
        assert np.isnan(sma.iloc[0]) and np.isnan(sma.iloc[1])
        assert pytest.approx(sma.iloc[2]) == 2.0
        assert pytest.approx(sma.iloc[3]) == 3.0
        assert pytest.approx(sma.iloc[4]) == 4.0

    def test_rolling_percentile_rank_bounded(self):
        """rolling_percentile_rank output must be in [0.0, 1.0]."""
        idx = pd.date_range("2022-01-03", periods=300, freq="D")
        s = pd.Series(np.random.default_rng(99).normal(0, 1, 300), index=idx)
        result = rolling_percentile_rank(s, window=50)
        valid = result.dropna()
        assert (valid >= 0.0).all() and (valid <= 1.0).all()


# ---------------------------------------------------------------------------
# Stage 6 — P2 Hardening
# ---------------------------------------------------------------------------

class TestP2:
    def test_large_dataset_no_memory_explosion(self):
        """5-year 1-min dataset completes ATR + percentile without OOM; runtime < 90s."""
        import time

        # ~5 years × 252 days × 390 min/day ≈ 491_400 rows
        n = 490_000
        rng = np.random.default_rng(0)
        close = 100.0 + np.cumsum(rng.normal(0, 0.01, n))
        high = close + rng.uniform(0, 0.5, n)
        low = close - rng.uniform(0, 0.5, n)
        idx = pd.date_range("2018-01-02 09:30", periods=n, freq="1min")
        df = pd.DataFrame(
            {"open": close, "high": high, "low": low, "close": close,
             "volume": np.ones(n) * 1e5},
            index=idx,
        )

        start = time.perf_counter()
        engine = FeatureEngine()
        # atr_14 + atr_pctile are the heaviest; realized_vol + vol_pctile add coverage
        out = engine.compute(df, ["atr_14", "atr_pctile"])
        elapsed = time.perf_counter() - start

        assert len(out) == n, "Row count must be preserved on large dataset"
        assert elapsed < 90, f"Feature computation took {elapsed:.1f}s — exceeds 90s limit"

    def test_feature_cache_consistent(self):
        """Computing features twice on the same data must return identical results."""
        df = _make_ohlcv(200, seed=77)
        engine = FeatureEngine()

        first = engine.compute(df.copy(), ["atr_14", "atr_pctile", "gap_pct", "slope_20d"])
        second = engine.compute(df.copy(), ["atr_14", "atr_pctile", "gap_pct", "slope_20d"])

        pd.testing.assert_frame_equal(
            first, second,
            check_exact=False,
            rtol=1e-10,
            obj="Feature engine output must be deterministic",
        )
