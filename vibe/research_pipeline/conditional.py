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

# Trade-relative variables: trade side (+1 long, -1 short) times a session
# feature, so "with the gap/trend" and "against it" separate for both sides.
TRADE_RELATIVE_VARIABLES = {
    "gap_aligned_pct": "gap_pct",
    "gap_aligned_atr": "gap_atr",
    "trend_aligned_pct": "prior_trend_20d_pct",
}


def trade_side_sign(trade: Any) -> int | None:
    """+1 for a long (buy) entry, -1 for a short (sell) entry, else None."""
    side = str(getattr(trade, "side", "") or "").lower()
    if side in ("buy", "long"):
        return 1
    if side in ("sell", "short"):
        return -1
    return None


def _side_label(sign: int | None) -> str:
    return {1: "long", -1: "short"}.get(sign, "unknown")


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


def _group_means(rows: Sequence[Sequence[Any]], key: int) -> dict[str, dict[str, Any]]:
    """Mean R and count of each tercile within each group.

    ``key`` 0 groups by year, otherwise by ``row[key]`` (1 symbol, 5 side).
    """
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        label = row[0][:4] if key == 0 else row[key]
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


def _high_minus_low(
    rows: Sequence[Sequence[Any]],
    low_cut: float,
    high_cut: float,
    confidence_level: float,
) -> dict[str, Any]:
    """High-minus-low tercile mean R for ``rows`` bucketed at fixed cutoffs."""
    by: dict[str, list[Observation]] = {t: [] for t in TERCILES}
    for row in rows:
        by[_bucket(row[2], low_cut, high_cut)].append(Observation(row[3], cluster=row[0]))
    high = estimate_mean(by["high"], confidence_level=confidence_level)
    low = estimate_mean(by["low"], confidence_level=confidence_level)
    out: dict[str, Any] = {
        "n_trades": len(rows),
        "n_dates": len({row[0] for row in rows}),
        "n_by_tercile": {t: len(by[t]) for t in TERCILES},
        "mean_r_by_tercile": {
            t: estimate_mean(by[t], confidence_level=confidence_level).mean_r
            for t in TERCILES
        },
        "high_minus_low_r": None,
        "se_r": None,
        "ci_low_r": None,
        "ci_high_r": None,
    }
    if high.mean_r is None or low.mean_r is None:
        return out
    diff = high.mean_r - low.mean_r
    se = None
    if high.se_cluster_r is not None and low.se_cluster_r is not None:
        se = math.hypot(high.se_cluster_r, low.se_cluster_r)
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2)
    out.update(
        high_minus_low_r=diff,
        se_r=se,
        ci_low_r=diff - z * se if se is not None else None,
        ci_high_r=diff + z * se if se is not None else None,
    )
    return out


def _excludes_zero(block: Mapping[str, Any]) -> bool:
    lo, hi = block.get("ci_low_r"), block.get("ci_high_r")
    return lo is not None and hi is not None and (lo > 0 or hi < 0)


def oos_tercile_test(
    rows: Sequence[Sequence[Any]],
    *,
    confidence_level: float = 0.95,
    split_date: str | None = None,
) -> dict[str, Any] | None:
    """Time-split out-of-sample tercile test.

    Tercile cutoffs come only from trades dated before ``split_date`` (default:
    the median distinct trade date) and are applied unchanged to the later
    half. ``rows`` are ``[date, symbol, value, net_r, ...]``. A condition that
    is real should show a test high-low with the train sign.
    """
    dates = sorted({row[0] for row in rows})
    if len(dates) < 2:
        return None
    if split_date is None:
        split_date = dates[len(dates) // 2]
    train = [row for row in rows if row[0] < split_date]
    test = [row for row in rows if row[0] >= split_date]
    if not train or not test:
        return None
    values = np.array([row[2] for row in train])
    low_cut, high_cut = (float(q) for q in np.quantile(values, [1 / 3, 2 / 3]))
    train_block = _high_minus_low(train, low_cut, high_cut, confidence_level)
    test_block = _high_minus_low(test, low_cut, high_cut, confidence_level)
    train_sign = _sign(train_block["high_minus_low_r"])
    test_sign = _sign(test_block["high_minus_low_r"])
    if test_block["high_minus_low_r"] is None or train_sign is None:
        verdict = "insufficient"
    elif test_sign == train_sign and _excludes_zero(test_block):
        verdict = "replicated"
    elif test_sign == train_sign:
        verdict = "same_sign_not_significant"
    else:
        verdict = "reversed"
    return {
        "split_date": split_date,
        "train_period": [min(r[0] for r in train), max(r[0] for r in train)],
        "test_period": [min(r[0] for r in test), max(r[0] for r in test)],
        "train_cutoffs": {"low": low_cut, "high": high_cut},
        "train": train_block,
        "test": test_block,
        "verdict": verdict,
        "confidence_level": confidence_level,
        "approximate": True,
    }


def condition_summary_text(block: Mapping[str, Any]) -> str:
    """One-line reading of a condition block that leads with the OOS test."""
    variable = block.get("variable", "?")
    oos = block.get("oos")

    def fmt(b: Mapping[str, Any]) -> str:
        d = b.get("high_minus_low_r")
        if d is None:
            return "n/a"
        lo, hi = b.get("ci_low_r"), b.get("ci_high_r")
        ci = f" [{lo:+.3f}, {hi:+.3f}]" if lo is not None and hi is not None else ""
        return f"{d:+.3f}R{ci}"

    if oos:
        return (
            f"{variable}: out-of-sample high-low {fmt(oos['test'])} "
            f"(train {fmt(oos['train'])}, split {oos['split_date']}) -> {oos['verdict']}"
        )
    ix = block.get("interaction")
    return f"{variable}: in-sample high-low {fmt(ix) if ix else 'n/a'}; no out-of-sample test"


def tercile_breakdown(
    trades_by_symbol: Mapping[str, Sequence[Any]],
    features_by_symbol: Mapping[str, pd.DataFrame],
    variable: str,
    *,
    confidence_level: float = 0.95,
    min_edge_r: float = 0.05,
    cutoffs: Mapping[str, float] | None = None,
    oos_split_date: str | None = None,
) -> dict[str, Any]:
    # rows: [session date, symbol, variable value, net R, tercile, side]
    joined: list[list[Any]] = []
    n_missing = 0
    base_variable = TRADE_RELATIVE_VARIABLES.get(variable, variable)
    trade_relative = variable in TRADE_RELATIVE_VARIABLES
    for symbol in sorted(trades_by_symbol):
        features = features_by_symbol.get(symbol)
        for trade in trades_by_symbol[symbol]:
            net_r = trade_net_r(trade)
            if net_r is None:
                continue
            day = trade_session_date(trade)
            sign = trade_side_sign(trade)
            value = float("nan")
            if features is not None and day in features.index and base_variable in features.columns:
                value = float(features.at[day, base_variable])
                if trade_relative:
                    value = value * sign if sign is not None else float("nan")
            if not math.isfinite(value):
                n_missing += 1
                continue
            joined.append([day, symbol, value, net_r, None, _side_label(sign)])

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
        "by_side": {},
        "oos": None,
        "summary_text": None,
    }
    if not joined:
        result["summary_text"] = condition_summary_text(result)
        return result

    if cutoffs is None:
        values = np.array([row[2] for row in joined])
        low_cut, high_cut = (float(q) for q in np.quantile(values, [1 / 3, 2 / 3]))
    else:
        low_cut, high_cut = float(cutoffs["low"]), float(cutoffs["high"])
        if low_cut > high_cut:
            raise ValueError("cutoffs: low must not exceed high")
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
    result["by_side"] = _group_means(joined, 5)

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
    result["oos"] = oos_tercile_test(
        joined, confidence_level=confidence_level, split_date=oos_split_date
    )
    result["summary_text"] = condition_summary_text(result)
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

    unknown = [
        v for v in variables
        if v not in FEATURE_COLUMNS and v not in TRADE_RELATIVE_VARIABLES
    ]
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
