"""
Stage 3 tests — Day Regime Labeler.

Exit gate: all P0 + P1 tests must pass.
Run: pytest vibe/tests/backtester/regime_research/test_labeler.py -v
"""

import numpy as np
import pandas as pd
import pytest

from vibe.backtester.analysis.regime_research.labeler import (
    DayRegimeLabeler,
    LabelerConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_features(
    n: int,
    adx: float = 30.0,
    slope: float = 0.10,
    atr_pctile: float = 0.50,
) -> pd.DataFrame:
    """Uniform synthetic feature table (all rows identical)."""
    idx = pd.date_range("2022-01-03", periods=n, freq="D")
    return pd.DataFrame(
        {
            "adx_14":    [adx] * n,
            "slope_20d": [slope] * n,
            "atr_pctile": [atr_pctile] * n,
        },
        index=idx,
    )


def _make_varied_features(n: int, seed: int = 42) -> pd.DataFrame:
    """Feature table with randomised but valid values."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="D")
    return pd.DataFrame(
        {
            "adx_14":    rng.uniform(10, 50, n),
            "slope_20d": rng.uniform(-0.3, 0.3, n),
            "atr_pctile": rng.uniform(0, 1, n),
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# P0 Tests
# ---------------------------------------------------------------------------

class TestP0:
    def test_every_day_gets_a_label_or_nan(self):
        """No day is silently skipped; each row is either a valid label or NaN."""
        features = _make_features(30)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        assert len(labels) == len(features), "Output length must match input length"

        valid_labels = {"trending_up", "trending_down", "ranging",
                        "trending_up_high_vol", "trending_up_low_vol",
                        "trending_down_high_vol", "trending_down_low_vol",
                        "ranging_high_vol", "ranging_low_vol"}
        for val in labels.dropna():
            assert val in valid_labels, f"Unexpected label value: {val!r}"

    def test_labels_mutually_exclusive(self):
        """Each non-NaN day has exactly one label (no multi-label or empty string rows)."""
        features = _make_varied_features(100)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        non_null = labels.dropna()
        assert (non_null != "").all(), "Empty string labels found — should be NaN"
        # All non-null values are strings (single label per day)
        assert non_null.map(lambda x: isinstance(x, str)).all()

    def test_no_lookahead_shift_applied(self):
        """Modifying today's features must not change today's label.

        The labeler uses yesterday's features for today's label.  So if we
        change features on day T, only the label on day T+1 should change.
        """
        n = 10
        features = _make_varied_features(n, seed=7)
        labeler = DayRegimeLabeler()
        baseline = labeler.label(features)

        # Modify features on day index 5 (0-based)
        features_mod = features.copy()
        features_mod.iloc[5, features_mod.columns.get_loc("adx_14")] = 0.0
        features_mod.iloc[5, features_mod.columns.get_loc("slope_20d")] = 0.0

        modified = labeler.label(features_mod)

        # Day 5's label must be UNCHANGED (it uses day 4's features)
        assert baseline.iloc[5] == modified.iloc[5], (
            "Day 5's label changed after modifying day 5's features — "
            "lookahead detected! Label should only change at day 6."
        )

        # Day 6's label may differ (it now uses the modified day 5 features)
        # (no assertion required here — just documenting expected behaviour)


# ---------------------------------------------------------------------------
# P1 Tests
# ---------------------------------------------------------------------------

class TestP1:
    def test_trending_up_labeled_correctly(self):
        """ADX=30, slope=+0.2 → 'trending_up' (after warmup row 0)."""
        features = _make_features(5, adx=30.0, slope=0.20, atr_pctile=0.50)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        # Row 0 is NaN (shift(1) means row 0 has no prior data)
        assert pd.isna(labels.iloc[0])
        # Rows 1+ should all be trending_up
        assert (labels.iloc[1:] == "trending_up").all(), (
            f"Expected 'trending_up', got: {labels.iloc[1:].tolist()}"
        )

    def test_trending_down_labeled_correctly(self):
        """ADX=30, slope=-0.2 → 'trending_down'."""
        features = _make_features(5, adx=30.0, slope=-0.20, atr_pctile=0.50)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        assert pd.isna(labels.iloc[0])
        assert (labels.iloc[1:] == "trending_down").all(), (
            f"Expected 'trending_down', got: {labels.iloc[1:].tolist()}"
        )

    def test_ranging_labeled_correctly(self):
        """ADX=15, slope=+0.01 → 'ranging' (ADX below threshold)."""
        features = _make_features(5, adx=15.0, slope=0.01, atr_pctile=0.50)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        assert pd.isna(labels.iloc[0])
        assert (labels.iloc[1:] == "ranging").all(), (
            f"Expected 'ranging', got: {labels.iloc[1:].tolist()}"
        )

    def test_high_vol_suffix_applied(self):
        """ADX=15, atr_pctile=0.9 → 'ranging_high_vol'."""
        features = _make_features(5, adx=15.0, slope=0.01, atr_pctile=0.90)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        assert pd.isna(labels.iloc[0])
        assert (labels.iloc[1:] == "ranging_high_vol").all(), (
            f"Expected 'ranging_high_vol', got: {labels.iloc[1:].tolist()}"
        )

    def test_warmup_days_return_nan(self):
        """Row 0 must be NaN because shift(1) has no prior data there."""
        features = _make_features(20)
        # Introduce NaN in first 5 rows to simulate indicator warmup
        features.iloc[:5, :] = np.nan
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        # Rows 0..5: row 0 always NaN; rows 1..5 are NaN because shift sees NaN
        assert labels.iloc[:6].isna().all(), (
            f"Expected NaN for warmup rows 0-5, got: {labels.iloc[:6].tolist()}"
        )

    def test_custom_thresholds_change_labels(self):
        """Lower adx_trend_threshold means more days labeled as trending."""
        features = _make_varied_features(200, seed=10)
        labeler = DayRegimeLabeler()

        default_cfg = LabelerConfig(adx_trend_threshold=25.0)
        low_cfg = LabelerConfig(adx_trend_threshold=10.0)

        default_labels = labeler.label(features, default_cfg)
        low_labels = labeler.label(features, low_cfg)

        default_trending = default_labels.isin(
            ["trending_up", "trending_down", "trending_up_high_vol",
             "trending_up_low_vol", "trending_down_high_vol", "trending_down_low_vol"]
        ).sum()
        low_trending = low_labels.isin(
            ["trending_up", "trending_down", "trending_up_high_vol",
             "trending_up_low_vol", "trending_down_high_vol", "trending_down_low_vol"]
        ).sum()

        assert low_trending >= default_trending, (
            "Lower ADX threshold should yield >= trending days"
        )


# ---------------------------------------------------------------------------
# P2 Tests
# ---------------------------------------------------------------------------

class TestP2:
    def test_custom_config_affects_vol_overlay(self):
        """Setting high_vol_pctile=0.3 → more days get _high_vol suffix."""
        features = _make_varied_features(200, seed=11)
        labeler = DayRegimeLabeler()

        default_cfg = LabelerConfig(high_vol_pctile=0.80)
        loose_cfg = LabelerConfig(high_vol_pctile=0.30)

        default_hv = labeler.label(features, default_cfg).str.endswith("_high_vol").sum()
        loose_hv = labeler.label(features, loose_cfg).str.endswith("_high_vol").sum()

        assert loose_hv >= default_hv

    def test_label_distribution_not_degenerate(self):
        """On varied data, no single label should exceed 90% of non-NaN days."""
        features = _make_varied_features(300, seed=12)
        labeler = DayRegimeLabeler()
        labels = labeler.label(features)

        non_null = labels.dropna()
        # Map labels to base categories for the concentration check
        base = non_null.str.replace("_high_vol|_low_vol", "", regex=True)
        counts = base.value_counts()
        dominant_pct = counts.iloc[0] / len(non_null)
        assert dominant_pct < 0.90, (
            f"Label distribution degenerate: dominant label is {dominant_pct:.1%} of days"
        )
