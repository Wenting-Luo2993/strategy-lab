"""
Trade Attribution Engine.

Joins an executed trade log with a feature table, giving each trade the most
recent feature snapshot available BEFORE (or at) its entry timestamp.  Uses
pd.merge_asof to ensure no future data leaks through.

Usage::

    attributor = TradeAttributor()
    enriched = attributor.enrich(trades_df, features_df)
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class TradeAttributor:
    """Enrich a trade log with regime features via an as-of join."""

    def enrich(
        self,
        trades: pd.DataFrame,
        features: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Join trades with features such that each trade receives the most recent
        feature snapshot where feature_time <= entry_time.

        Args:
            trades: DataFrame with at minimum an ``entry_time`` column
                    (datetime, timezone-aware or naive — must match features).
                    Any other columns are preserved.
            features: Feature table from FeatureEngine.compute().  Index must
                      be a DatetimeIndex (the feature timestamp).

        Returns:
            A copy of trades with one column per feature appended, plus a
            ``feature_snapshot_time`` column recording which bar's features
            were used.  Rows where no feature snapshot is available receive
            NaN for all feature columns; a count is logged.

        Raises:
            ValueError: If ``entry_time`` column is missing from trades.
            AssertionError: If any enriched row has feature_snapshot_time > entry_time
                            (hard causality check — should never fire).
        """
        if "entry_time" not in trades.columns:
            raise ValueError("trades DataFrame must have an 'entry_time' column")

        # Guard: refuse features whose names signal future leakage.
        # Columns ending in '_t1' or containing 'future' are presumed forward-derived.
        _future_cols = [
            c for c in features.columns
            if c.lower().startswith("future") or c.lower().endswith("_t1")
        ]
        if _future_cols:
            raise ValueError(
                f"Feature column(s) appear to be future-derived: {_future_cols}. "
                "Remove them or rename to avoid lookahead bias."
            )

        if len(trades) == 0:
            empty = trades.copy()
            empty["feature_snapshot_time"] = pd.NaT
            for col in features.columns:
                empty[col] = float("nan")
            return empty

        # Prepare feature table for merge_asof: index → explicit column
        feat = features.copy().reset_index()
        # The index is named (DatetimeIndex) or unnamed — normalise to "feature_time"
        time_col = feat.columns[0]
        feat = feat.rename(columns={time_col: "feature_time"})
        feat = feat.sort_values("feature_time")

        # Prepare trades: sort by entry_time (required by merge_asof)
        trades_sorted = trades.copy().sort_values("entry_time").reset_index(drop=False)
        original_index_col = trades_sorted.columns[0]  # the original index, preserved for re-ordering

        merged = pd.merge_asof(
            trades_sorted,
            feat,
            left_on="entry_time",
            right_on="feature_time",
            direction="backward",
        )

        # Rename the feature timestamp column
        merged = merged.rename(columns={"feature_time": "feature_snapshot_time"})

        # Hard causality assert — this must NEVER fire
        valid_mask = merged["feature_snapshot_time"].notna()
        causality_violations = (
            merged.loc[valid_mask, "feature_snapshot_time"]
            > merged.loc[valid_mask, "entry_time"]
        )
        assert not causality_violations.any(), (
            f"Causality violation: {causality_violations.sum()} trades have "
            "feature_snapshot_time > entry_time"
        )

        # Count and log NaN-attributed trades
        nan_count = (~valid_mask).sum()
        if nan_count > 0:
            logger.warning(
                "%d trade(s) have no feature snapshot (before the feature warmup period). "
                "Feature columns will be NaN for these trades.",
                nan_count,
            )

        # Trade count invariant
        if len(merged) != len(trades):
            raise RuntimeError(
                f"Trade count changed after attribution: "
                f"before={len(trades)}, after={len(merged)}"
            )

        # Restore original ordering
        merged = merged.sort_values(original_index_col).drop(columns=[original_index_col])
        merged = merged.reset_index(drop=True)

        return merged
