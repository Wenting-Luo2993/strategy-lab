"""Predeclared, decision-time-causal regime evidence for ORB studies."""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

import pandas as pd

from vibe.research_pipeline.contracts import SegmentRole


@dataclass(frozen=True)
class RegimeBreakdown:
    return_contributions_r: Mapping[str, float]
    definitions: tuple[Mapping[str, Any], ...]
    observations: tuple[Mapping[str, Any], ...]
    unclassified_trades: int


def _daily_features(
    bars: pd.DataFrame,
    *,
    trend_sessions: int,
    volatility_sessions: int,
) -> pd.DataFrame:
    required = {"high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Regime evidence requires columns: {sorted(missing)}")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError("Regime evidence requires a DatetimeIndex")
    daily = bars.groupby(bars.index.date).agg(
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    previous_close = daily["close"].shift(1)
    true_range = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - previous_close).abs(),
            (daily["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["prior_trend_sma"] = (
        daily["close"].rolling(trend_sessions, min_periods=trend_sessions).mean()
    )
    daily["prior_atr_pct"] = (
        true_range.rolling(
            volatility_sessions,
            min_periods=volatility_sessions,
        ).mean()
        / daily["close"]
    )
    return daily.shift(1)


def build_orb_regime_breakdown(
    study: Any,
    plan: Any,
    bars: pd.DataFrame,
    *,
    trend_sessions: int = 50,
    volatility_sessions: int = 20,
) -> RegimeBreakdown:
    """Classify TEST trades without using information after their entry session.

    The strategy is not filtered or selected by these labels. Each fold's
    high/low-volatility threshold is the median prior ATR percentage observed
    in that fold's TRAIN segment, then frozen for its TEST segment.
    """
    if trend_sessions < 2 or volatility_sessions < 2:
        raise ValueError("Regime lookbacks must be at least two sessions")
    features = _daily_features(
        bars,
        trend_sessions=trend_sessions,
        volatility_sessions=volatility_sessions,
    )
    feature_dates = list(features.index)
    contributions = {
        "aligned_low_volatility": 0.0,
        "aligned_high_volatility": 0.0,
        "countertrend_low_volatility": 0.0,
        "countertrend_high_volatility": 0.0,
    }
    counts = {key: 0 for key in contributions}
    unclassified = 0

    for fold, manifest in zip(getattr(study, "folds", ()), plan.folds):
        train = next(
            segment for segment in manifest.segments
            if segment.role is SegmentRole.TRAIN
        )
        training_values = features.loc[
            (features.index >= train.start_session)
            & (features.index <= train.end_session),
            "prior_atr_pct",
        ].dropna()
        if training_values.empty:
            unclassified += len(
                getattr(
                    getattr(getattr(fold, "test_result", None), "result", None),
                    "trades",
                    (),
                )
            )
            continue
        volatility_threshold = float(statistics.median(training_values))
        trades = getattr(
            getattr(getattr(fold, "test_result", None), "result", None),
            "trades",
            (),
        )
        for trade in trades:
            session = trade.entry_time.date()
            position = bisect.bisect_left(feature_dates, session)
            if position >= len(feature_dates) or feature_dates[position] != session:
                unclassified += 1
                continue
            row = features.iloc[position]
            if pd.isna(row["prior_trend_sma"]) or pd.isna(row["prior_atr_pct"]):
                unclassified += 1
                continue
            trend_up = float(row["close"]) >= float(row["prior_trend_sma"])
            aligned = (
                (trade.side == "buy" and trend_up)
                or (trade.side == "sell" and not trend_up)
            )
            high_volatility = float(row["prior_atr_pct"]) >= volatility_threshold
            label = (
                "aligned" if aligned else "countertrend"
            ) + (
                "_high_volatility" if high_volatility else "_low_volatility"
            )
            if not trade.initial_risk or trade.initial_risk <= 0:
                unclassified += 1
                continue
            net_r = (
                float(trade.pnl) - float(trade.commission or 0.0)
            ) / float(trade.initial_risk)
            contributions[label] += net_r
            counts[label] += 1

    observations = tuple(
        {
            "regime": label,
            "n_trades": counts[label],
            "return_contribution_r": contributions[label],
            "expectancy_r": (
                contributions[label] / counts[label] if counts[label] else None
            ),
        }
        for label in contributions
        if counts[label]
    )
    return RegimeBreakdown(
        return_contributions_r={
            item["regime"]: item["return_contribution_r"]
            for item in observations
        },
        definitions=(
            {
                "dimension": "trend_alignment",
                "definition": (
                    f"Trade direction versus prior close relative to its "
                    f"{trend_sessions}-session moving average"
                ),
                "availability": "known before the entry session opens",
            },
            {
                "dimension": "prior_volatility",
                "definition": (
                    f"Prior {volatility_sessions}-session ATR percentage above "
                    "or below the TRAIN-segment median"
                ),
                "availability": "threshold frozen on TRAIN; value known before entry",
            },
        ),
        observations=observations,
        unclassified_trades=unclassified,
    )
