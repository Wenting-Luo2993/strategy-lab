from typing import Dict, List

import numpy as np
import pandas as pd

from vibe.backtester.analysis.metrics import ConvexityMetrics
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade


class MarketRegimeClassifier:
    """
    ADX-based market regime classification.
    ADX > 25 → TRENDING, ADX 20-25 → TRANSITIONING, ADX < 20 → RANGING.
    """

    def __init__(self, period: int = 14) -> None:
        self.period = period

    def classify(self, df: pd.DataFrame) -> pd.Series:
        adx = self._calc_adx(df, self.period)
        regime = pd.Series(index=df.index, dtype=str)
        regime[adx > 25] = "TRENDING"
        regime[(adx >= 20) & (adx <= 25)] = "TRANSITIONING"
        regime[adx < 20] = "RANGING"
        return regime

    def _calc_adx(self, df: pd.DataFrame, period: int) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        dm_plus  = np.where((high - high.shift(1)) > (low.shift(1) - low),
                            np.maximum(high - high.shift(1), 0), 0)
        dm_minus = np.where((low.shift(1) - low) > (high - high.shift(1)),
                            np.maximum(low.shift(1) - low, 0), 0)

        atr = tr.ewm(span=period, adjust=False).mean()
        dip = pd.Series(dm_plus,  index=df.index).ewm(span=period, adjust=False).mean() / atr * 100
        dim = pd.Series(dm_minus, index=df.index).ewm(span=period, adjust=False).mean() / atr * 100

        dx = ((dip - dim).abs() / (dip + dim).replace(0, np.nan) * 100).fillna(0)
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx


def performance_by_regime(
    trades: List[Trade],
    regime: pd.Series,
) -> Dict[str, ConvexityMetrics]:
    """
    Split trades by the regime at their entry time and compute
    ConvexityMetrics for each regime bucket.
    """
    result: Dict[str, List[Trade]] = {}
    for trade in trades:
        if trade.entry_time not in regime.index:
            nearest = regime.index.asof(trade.entry_time)
            regime_label = regime.get(nearest, "UNKNOWN")
        else:
            regime_label = regime.get(trade.entry_time, "UNKNOWN")
        result.setdefault(regime_label, []).append(trade)

    return {
        label: PerformanceAnalyzer._calc_convexity(ts)
        for label, ts in result.items()
    }
