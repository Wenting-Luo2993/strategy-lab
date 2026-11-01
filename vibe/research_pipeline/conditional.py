"""Tercile breakdowns of expectancy by a session-level variable (diagnostic only)."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from vibe.research_pipeline.inference import (
    Observation,
    classify_outcome,
    estimate_mean,
    trade_net_r,
    trade_session_date,
)

TERCILES = ("low", "mid", "high")


def _sign(value: float | None) -> int | None:
    if value is None or not math.isfinite(value) or value == 0:
        return None
    return 1 if value > 0 else -1


def _bucket(value: float, low_cut: float, high_cut: float) -> str:
    if value <= low_cut:
        return "low"
    if value > high_cut:
        return "high"
    return "mid"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group_means(rows: Sequence[tuple[str, str, float, float]], key: int) -> dict[str, dict[str, Any]]:
    """Mean R and count of each tercile within each year (key=0) or symbol (key=1)."""
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        label = row[0][:4] if key == 0 else row[1]
        grouped[label][row[4]].append(row[3])
    return {
        label: {
            tercile: {"n": len(values), "mean_r": _mean(values)}
            for tercile, values in sorted(by_tercile.items())
        }
        for label, by_tercile in sorted(grouped.items())
    }


def _consistency(groups: Mapping[str, Mapping[str, Mapping[str, Any]]], sign: int | None) -> float | None:
    if sign is None:
        return None
    agree = total = 0
    for by_tercile in groups.values():
        high = by_tercile.get("high", {}).get("mean_r")
        low = by_tercile.get("low", {}).get("mean_r")
        if high is None or low is None:
            continue
        total += 1
        if _sign(high - low) == sign:
            agree += 1
    return agree / total if total else None


def tercile_breakdown(
    trades_by_symbol: Mapping[str, Sequence[Any]],
    features_by_symbol: Mapping[str, pd.DataFrame],
    variable: str,
    *,
    confidence_level: float = 0.95,
    min_edge_r: float = 0.05,
) -> dict[str, Any]:
    # (session date, symbol, variable value, net R, assigned later)
    joined: list[list[Any]] = []
    n_missing = 0
    for symbol in sorted(trades_by_symbol):
        features = features_by_symbol.get(symbol)
        for trade in trades_by_symbol[symbol]:
            net_r = trade_net_r(trade)
            if net_r is None:
                continue
            day = trade_session_date(trade)
            value = float("nan")
            if features is not None and day in features.index and variable in features.columns:
                value = float(features.at[day, variable])
            if not math.isfinite(value):
                n_missing += 1
                continue
            joined.append([day, symbol, value, net_r, None])

    result: dict[str, Any] = {
        "label": "diagnostic",
        "variable": variable,
        "n_trades": len(joined),
        "n_missing": n_missing,
        "cutoffs": None,
        "terciles": {},
        "interaction": None,
        "monotonic": "none",
        "consistency": {"share_years": None, "share_symbols": None},
        "by_year": {},
        "by_symbol": {},
    }
    if not joined:
        return result

    values = np.array([row[2] for row in joined])
    low_cut, high_cut = (float(q) for q in np.quantile(values, [1 / 3, 2 / 3]))
    result["cutoffs"] = {"low": low_cut, "high": high_cut}
    for row in joined:
        row[4] = _bucket(row[2], low_cut, high_cut)

    estimates = {}
    for tercile in TERCILES:
        obs = [Observation(row[3], cluster=row[0]) for row in joined if row[4] == tercile]
        est = estimate_mean(obs, confidence_level=confidence_level)
        outcome = classify_outcome(est.ci_low_r, est.ci_high_r, min_edge_r=min_edge_r)
        estimates[tercile] = est
        result["terciles"][tercile] = {**est.to_dict(), "outcome": outcome.value}

    by_year = _group_means(joined, 0)
    by_symbol = _group_means(joined, 1)
    result["by_year"] = by_year
    result["by_symbol"] = by_symbol

    means = [estimates[t].mean_r for t in TERCILES]
    if all(m is not None for m in means):
        low, mid, high = means
        if low < mid < high:
            result["monotonic"] = "increasing"
        elif low > mid > high:
            result["monotonic"] = "decreasing"

    high_est, low_est = estimates["high"], estimates["low"]
    if high_est.mean_r is not None and low_est.mean_r is not None:
        diff = high_est.mean_r - low_est.mean_r
        se = None
        if high_est.se_cluster_r is not None and low_est.se_cluster_r is not None:
            se = math.hypot(high_est.se_cluster_r, low_est.se_cluster_r)
        z = NormalDist().inv_cdf(0.5 + confidence_level / 2)
        result["interaction"] = {
            "high_minus_low_r": diff,
            "se_r": se,
            "ci_low_r": diff - z * se if se is not None else None,
            "ci_high_r": diff + z * se if se is not None else None,
            "confidence_level": confidence_level,
            "approximate": True,
            "note": "terciles can share dates, so the interval is approximate",
        }
        sign = _sign(diff)
        result["consistency"] = {
            "share_years": _consistency(by_year, sign),
            "share_symbols": _consistency(by_symbol, sign),
        }
    return result

def condition_blocks(
    data_dir,
    trades_by_symbol: Mapping[str, Sequence[Any]],
    variables: Sequence[str],
    *,
    start,
    end,
    or_minutes: int,
    timeframe: str = "5m",
    confidence_level: float = 0.95,
    min_edge_r: float = 0.05,
    history_days: int = 420,
) -> dict[str, dict[str, Any]]:
    """Build ``conditions.{variable}`` blocks, loading bars as the engine does.

    Bars begin ``history_days`` before ``start`` so trailing windows are filled.
    Only data before each session is used for any feature.
    """
    import asyncio
    from datetime import datetime, time, timedelta
    from pathlib import Path

    from vibe.backtester.core.engine import _resample
    from vibe.backtester.data.parquet_loader import ParquetLoader
    from vibe.research_pipeline.session_features import (
        EXCHANGE_TZ,
        FEATURE_COLUMNS,
        build_session_features,
    )
    from zoneinfo import ZoneInfo

    unknown = [v for v in variables if v not in FEATURE_COLUMNS]
    if unknown:
        raise ValueError(f"unknown condition variable(s): {unknown}")
    tz = ZoneInfo(EXCHANGE_TZ)
    lo = datetime.combine(start - timedelta(days=history_days), time(0, 0), tzinfo=tz)
    hi = datetime.combine(end, time(23, 59, 59), tzinfo=tz)
    features: dict[str, pd.DataFrame] = {}
    for symbol in trades_by_symbol:
        loader = ParquetLoader(Path(data_dir), [symbol])
        raw = asyncio.run(loader.get_bars(symbol, start_time=lo, end_time=hi))
        bars = _resample(raw, timeframe.replace("m", "min"))
        features[symbol] = build_session_features(bars, or_minutes=or_minutes)
    return {
        variable: tercile_breakdown(
            trades_by_symbol,
            features,
            variable,
            confidence_level=confidence_level,
            min_edge_r=min_edge_r,
        )
        for variable in variables
    }
