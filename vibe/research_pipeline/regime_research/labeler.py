"""
Day Regime Labeler.

Assigns each trading day exactly one regime label derived from the *prior day's*
feature values (no lookahead).  The shift(1) is applied internally — callers
must not pre-shift the feature table.

MVP labels
----------
trending_up      ADX > threshold  AND  slope_20d > +flat_band
trending_down    ADX > threshold  AND  slope_20d < -flat_band
ranging          everything else

Volatility overlay (suffix appended to any base label):
    _high_vol    if atr_pctile > high_vol_pctile
    _low_vol     if atr_pctile < low_vol_pctile

Examples: "ranging_high_vol", "trending_up_low_vol", "trending_down"

Days where any required feature is NaN (warmup period) return NaN.

Future extension
----------------
BarRegimeLabeler can follow the same interface and share LabelerConfig, but
consume intraday feature rows rather than daily ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_FEATURES = {"adx_14", "slope_20d", "atr_pctile"}

BASE_LABELS = ("trending_up", "trending_down", "ranging")


@dataclass
class LabelerConfig:
    adx_trend_threshold: float = 22.0
    slope_flat_band: float = 0.001     # normalized slope (price-change/day ÷ mean-price)
    high_vol_pctile: float = 0.80
    low_vol_pctile: float = 0.20


class DayRegimeLabeler:
    """
    Label each trading day with a regime category.

    The labeler applies a 1-day shift internally so that today's label is
    based on yesterday's features — ensuring no intraday lookahead.
    """

    def label(
        self,
        features: pd.DataFrame,
        config: LabelerConfig | None = None,
    ) -> pd.Series:
        """
        Args:
            features: Date-indexed DataFrame from FeatureEngine.compute().
                      Must contain: adx_14, slope_20d, atr_pctile.
            config: Threshold configuration.  Defaults to LabelerConfig().

        Returns:
            pd.Series of str labels aligned to features.index.
            NaN on days where required features are unavailable (warmup).
        """
        if config is None:
            config = LabelerConfig()

        missing = REQUIRED_FEATURES - set(features.columns)
        if missing:
            raise ValueError(f"Feature table missing required columns: {sorted(missing)}")

        # Shift by 1: today's label uses yesterday's feature values (no lookahead)
        prior = features[list(REQUIRED_FEATURES)].shift(1)

        adx = prior["adx_14"]
        slope = prior["slope_20d"]
        atr_p = prior["atr_pctile"]

        # Rows where any required feature is NaN → warmup, return NaN
        warmup = adx.isna() | slope.isna() | atr_p.isna()

        # Determine base label
        trending_up = (adx > config.adx_trend_threshold) & (slope > config.slope_flat_band)
        trending_down = (adx > config.adx_trend_threshold) & (slope < -config.slope_flat_band)

        base = np.where(trending_up, "trending_up",
               np.where(trending_down, "trending_down", "ranging"))

        # Volatility suffix
        high_vol = atr_p > config.high_vol_pctile
        low_vol = atr_p < config.low_vol_pctile

        suffix = np.where(high_vol, "_high_vol",
                 np.where(low_vol, "_low_vol", ""))

        labels = pd.Series(
            np.where(warmup, None, np.char.add(base.astype(str), suffix.astype(str))),
            index=features.index,
            dtype=object,
        )

        # Warmup rows → proper NaN (not the string "None")
        labels[warmup] = np.nan

        return labels
