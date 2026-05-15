"""
Stage 2 + Stage 6 tests — Trade Attribution Engine.

Exit gate: all P0 + P1 tests must pass before trusting any attribution output.
Stage 6 P2 test (test_intentional_future_feature_flagged) must pass before
publishing research findings.

Run: pytest vibe/tests/backtester/regime_research/test_attribution.py -v
"""

import numpy as np
import pandas as pd
import pytest

from vibe.backtester.analysis.regime_research.attribution import TradeAttributor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_features(
    start: str = "2022-01-03",
    periods: int = 10,
    freq: str = "D",
) -> pd.DataFrame:
    """Synthetic daily feature table."""
    idx = pd.date_range(start, periods=periods, freq=freq)
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "atr_pctile": rng.uniform(0, 1, periods),
            "gap_pct":    rng.normal(0, 1, periods),
        },
        index=idx,
    )


def _make_trades(entry_times: list) -> pd.DataFrame:
    """Minimal trade DataFrame with entry_time + pnl_r."""
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "entry_time": pd.to_datetime(entry_times),
            "pnl_r":      rng.normal(0, 1, len(entry_times)),
        }
    )


# ---------------------------------------------------------------------------
# P0 Tests
# ---------------------------------------------------------------------------

class TestP0:
    def test_no_future_leakage_attribution(self):
        """Modifying features at T+1 must not change a trade's attribution at T."""
        features = _make_features(periods=10)
        trades = _make_trades(["2022-01-07"])  # row index 4

        attributor = TradeAttributor()
        original = attributor.enrich(trades.copy(), features)

        # Poison the feature at T+1 (2022-01-08)
        features_poisoned = features.copy()
        features_poisoned.loc["2022-01-08", "atr_pctile"] = 999.0

        poisoned = attributor.enrich(trades.copy(), features_poisoned)

        # Trade attributed from 2022-01-07 (or earlier) must be unchanged
        assert original["atr_pctile"].iloc[0] == poisoned["atr_pctile"].iloc[0], (
            "Modifying T+1 feature changed the trade's attribution — future leakage!"
        )

    def test_correct_timestamp_alignment(self):
        """Trade at 09:45 must receive features from ≤09:45 bars only."""
        # Intraday features: 09:30, 09:35, 09:40, 09:45, 09:50
        base = pd.Timestamp("2022-01-03")
        times = pd.to_datetime([
            "2022-01-03 09:30", "2022-01-03 09:35",
            "2022-01-03 09:40", "2022-01-03 09:45",
            "2022-01-03 09:50",
        ])
        features = pd.DataFrame(
            {"atr_pctile": [0.1, 0.2, 0.3, 0.4, 0.5]},
            index=times,
        )
        trades = _make_trades(["2022-01-03 09:45"])

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        assert enriched["atr_pctile"].iloc[0] == pytest.approx(0.4), (
            f"Expected feature from 09:45 bar (0.4), got {enriched['atr_pctile'].iloc[0]}"
        )
        # Snapshot time must be ≤ entry_time
        assert enriched["feature_snapshot_time"].iloc[0] <= enriched["entry_time"].iloc[0]

    def test_timestamp_causality_hard_assert(self):
        """feature_snapshot_time <= entry_time must hold for every row."""
        features = _make_features(periods=20)
        entry_times = pd.date_range("2022-01-07", periods=10, freq="D")
        trades = _make_trades(entry_times.tolist())

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        valid = enriched["feature_snapshot_time"].notna()
        violations = (
            enriched.loc[valid, "feature_snapshot_time"]
            > enriched.loc[valid, "entry_time"]
        )
        assert not violations.any(), (
            f"{violations.sum()} rows violate causality: feature_snapshot_time > entry_time"
        )

    def test_trade_count_preserved(self):
        """len(enriched) must equal len(original_trades)."""
        features = _make_features(periods=30)
        entry_times = pd.date_range("2022-01-05", periods=15, freq="D")
        trades = _make_trades(entry_times.tolist())

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        assert len(enriched) == len(trades), (
            f"Trade count changed: before={len(trades)}, after={len(enriched)}"
        )


# ---------------------------------------------------------------------------
# P1 Tests
# ---------------------------------------------------------------------------

class TestP1:
    def test_missing_feature_is_nan(self):
        """Trade before feature warmup period → NaN, not filled with a stale value."""
        # Features start 2022-01-10; trade is at 2022-01-03 (before any feature)
        features = _make_features(start="2022-01-10", periods=10)
        trades = _make_trades(["2022-01-03"])

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        assert enriched["atr_pctile"].isna().iloc[0], (
            "Trade before feature warmup should get NaN, not a fabricated value"
        )
        assert enriched["gap_pct"].isna().iloc[0]

    def test_no_silent_fill_on_missing(self):
        """No ffill/bfill: if the as-of join finds nothing, NaN propagates."""
        # Only one feature row at 2022-01-10; trade at 2022-01-03 finds nothing
        idx = pd.DatetimeIndex(["2022-01-10"])
        features = pd.DataFrame({"atr_pctile": [0.75]}, index=idx)
        trades = _make_trades(["2022-01-03"])

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        # No forward fill should have been applied
        assert pd.isna(enriched["atr_pctile"].iloc[0]), (
            "Silent fill detected: trade before all feature rows should be NaN"
        )

    def test_multiple_trades_correct_snapshots(self):
        """Each trade independently picks up the correct feature snapshot."""
        times = pd.to_datetime(["2022-01-03", "2022-01-04", "2022-01-05",
                                 "2022-01-06", "2022-01-07"])
        features = pd.DataFrame(
            {"atr_pctile": [0.1, 0.2, 0.3, 0.4, 0.5]},
            index=times,
        )
        # Trades at 2022-01-05 and 2022-01-07
        trades = _make_trades(["2022-01-05", "2022-01-07"])

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features).sort_values("entry_time")

        assert enriched["atr_pctile"].iloc[0] == pytest.approx(0.3)
        assert enriched["atr_pctile"].iloc[1] == pytest.approx(0.5)

    def test_empty_trades_returns_empty_with_columns(self):
        """Empty trades DataFrame should return empty DataFrame with feature columns."""
        features = _make_features(periods=10)
        trades = _make_trades([]).iloc[:0]  # empty

        attributor = TradeAttributor()
        enriched = attributor.enrich(trades, features)

        assert len(enriched) == 0
        assert "atr_pctile" in enriched.columns
        assert "feature_snapshot_time" in enriched.columns


# ---------------------------------------------------------------------------
# Stage 6 — P2 Hardening
# ---------------------------------------------------------------------------

class TestP2:
    def test_intentional_future_feature_flagged(self):
        """Feature column named with '_t1' suffix must raise ValueError.

        Convention: any feature name ending in '_t1' (or containing 'future')
        is presumed to be a forward-looking (leaky) feature.  The attributor
        must refuse to attach it without an explicit override flag.

        This guard ensures accidentally-leaked features don't silently enter
        the enriched trade table.
        """
        idx = pd.DatetimeIndex(pd.date_range("2022-01-03", periods=10, freq="D"))
        # Feature with a name that signals future derivation
        features = pd.DataFrame(
            {"future_return_5d": list(range(10))},
            index=idx,
        )
        trades = _make_trades(["2022-01-07"])

        attributor = TradeAttributor()
        with pytest.raises(ValueError, match="future"):
            attributor.enrich(trades, features)
