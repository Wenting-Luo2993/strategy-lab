"""Frozen-ledger diagnostics, not a simulator or an executable entry filter."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from vibe.common.indicators.daily_atr import atr_known_at_open
from vibe.common.indicators.efficiency import session_efficiency_ratio
from vibe.common.indicators.session import session_keys
from vibe.research_pipeline.inference import Observation, estimate_mean

FEATURES = ("opportunity", "persistence")
CONTRASTS = {
    "opportunity": {"H": 1.0, "L": -1.0},
    "persistence": {"H": 1.0, "L": -1.0},
    "interaction": {"HH": 1.0, "HL": -1.0, "LH": -1.0, "LL": 1.0},
}


def decision_features(
    bars: pd.DataFrame, protocol: Mapping[str, Any],
    *, complete_bars: pd.Series | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return close-available features and regular-session OHLC.

    Input bars are start-stamped, one symbol, sorted, unique and aware.
    Daily ATR is shifted by one session before any intraday mapping.
    """
    p = protocol["features"]["persistence"]
    o = protocol["features"]["opportunity"]
    local, regular = session_keys(
        bars, p["exchange_tz"], p["session_open"], p["session_close"]
    )
    rth = bars.loc[regular].copy()
    rth.index = local[regular]
    daily = rth.groupby(rth.index.strftime("%Y-%m-%d")).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    )
    atr = atr_known_at_open(daily, o["atr_period"], smoothing="wilder")
    efficiency_bars = bars
    if complete_bars is not None:
        if not complete_bars.index.equals(bars.index) or complete_bars.isna().any():
            raise ValueError("Bar completeness must align exactly to input bars")
        efficiency_bars = bars.copy()
        efficiency_bars.loc[~complete_bars, "close"] = np.nan
    efficiency = session_efficiency_ratio(
        efficiency_bars, p["changes"], bar_minutes=p["bar_minutes"],
        exchange_tz=p["exchange_tz"],
        session_open=p["session_open"], session_close=p["session_close"],
    )
    features = pd.DataFrame({
        "opportunity": pd.Series(local.strftime("%Y-%m-%d")).map(atr).to_numpy()
        / o["friction_dollars"],
        "persistence": efficiency.to_numpy(),
    }, index=bars.index + pd.Timedelta(minutes=p["bar_minutes"]))
    return features, daily


def join_features(
    trades: pd.DataFrame, features: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Exact as-of-close join; never match a future or entry-bar close."""
    out = trades.copy()
    out["entry_time"] = pd.to_datetime(out["entry_time"], utc=True)
    out["exit_time"] = pd.to_datetime(out["exit_time"], utc=True)
    if (out["exit_time"] < out["entry_time"]).any():
        raise ValueError("Exit before entry in source ledger")
    numeric = out[["pnl", "commission", "quantity", "initial_risk"]].to_numpy(float)
    if not np.isfinite(numeric).all() or (out["initial_risk"] <= 0).any():
        raise ValueError("Source trades need finite P&L/costs and positive risk")
    if (out["quantity"] <= 0).any():
        raise ValueError("Source trade quantities must be positive")
    if not out["side"].isin(["buy", "sell"]).all():
        raise ValueError("Unknown source trade direction")
    out["net_dollars"] = out["pnl"] - out["commission"]
    out["net_r"] = out["net_dollars"] / out["initial_risk"]
    local = out["entry_time"].dt.tz_convert("America/New_York")
    out["session"] = local.dt.strftime("%Y-%m-%d")
    out["year"] = local.dt.strftime("%Y")
    out["entry_band"] = np.where(
        local.dt.hour < 11, "early", np.where(local.dt.hour < 13, "mid", "late")
    )
    if out.duplicated(["symbol", "session"]).any():
        raise ValueError("Expected at most one source trade per symbol/session")
    for feature in FEATURES:
        out[feature] = np.nan
    for symbol, indices in out.groupby("symbol").groups.items():
        block = features[symbol].reindex(pd.DatetimeIndex(out.loc[indices, "entry_time"]))
        out.loc[indices, list(FEATURES)] = block[list(FEATURES)].to_numpy()
    out["measurable"] = np.isfinite(out[list(FEATURES)]).all(axis=1)
    return out


def freeze_labels(
    rows: pd.DataFrame, protocol: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Calibrate labels from earlier feature values only; no outcomes read."""
    out = rows.copy()
    thresholds = {}
    for name in FEATURES:
        out[f"{name}_group"] = pd.Series(None, index=out.index, dtype=object)
    for symbol in protocol["symbols"]:
        calibration = out[
            (out["symbol"] == symbol) & out["measurable"]
            & (out["session"] <= protocol["calibration_end"])
        ]
        if len(calibration) < protocol["thresholds"]["minimum_calibration_rows_per_symbol"]:
            raise ValueError(f"{symbol}: insufficient calibration feature history")
        cuts = {name: float(calibration[name].median()) for name in FEATURES}
        thresholds[symbol] = {"n": len(calibration), **cuts}
        mask = (out["symbol"] == symbol) & out["measurable"]
        for name in FEATURES:
            out.loc[mask, f"{name}_group"] = np.where(
                out.loc[mask, name] > cuts[name], "H", "L"
            )
    out["cell"] = out["opportunity_group"] + out["persistence_group"]
    return out, thresholds


def mean_estimate(rows: pd.DataFrame) -> dict[str, Any]:
    return estimate_mean([
        Observation(float(r), str(day))
        for r, day in zip(rows["net_r"], rows["session"])
    ]).to_dict()


def clustered_contrast(
    rows: pd.DataFrame, column: str, weights: Mapping[str, float],
    *, confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Difference of group means with shared-date CR1 covariance.

    A zero-mean influence observation n*w_g*(y-mean_g)/n_g for every
    trade permits reuse of estimate_mean's date-clustered t interval.
    Unlike independent-group SEs this keeps cross-group date covariance.
    """
    data = rows[rows[column].isin(weights)]
    sizes = {g: int((data[column] == g).sum()) for g in weights}
    dates = {g: int(data.loc[data[column] == g, "session"].nunique()) for g in weights}
    result = {
        "n": len(data), "n_clusters": int(data["session"].nunique()),
        "group_sizes": sizes, "group_dates": dates,
        "estimate_r": None, "ci_low_r": None, "ci_high_r": None,
        "confidence_level": confidence_level,
    }
    if not sizes or min(sizes.values()) == 0:
        return result
    means = data.groupby(column)["net_r"].mean().to_dict()
    value = sum(weights[g] * means[g] for g in weights)
    influences = [
        Observation(
            len(data) * weights[g] * (float(y) - means[g]) / sizes[g], str(day)
        )
        for g, y, day in zip(data[column], data["net_r"], data["session"])
    ]
    estimate = estimate_mean(influences, confidence_level=confidence_level)
    result["estimate_r"] = value
    if estimate.ci_low_r is not None:
        result["ci_low_r"] = value + estimate.ci_low_r - estimate.mean_r
        result["ci_high_r"] = value + estimate.ci_high_r - estimate.mean_r
    return result


def diagnostic_block(rows: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    """All three frozen contrasts on one declared slice, without recalibration."""
    data = rows[rows["measurable"]]
    groups = {}
    for cell in ("HH", "HL", "LH", "LL"):
        subset = data[data["cell"] == cell]
        groups[cell] = {
            **mean_estimate(subset),
            "net_dollars": float(subset["net_dollars"].sum()),
            "total_net_r": float(subset["net_r"].sum()),
        }
    contrasts = {}
    for name, weights in CONTRASTS.items():
        column = "cell" if name == "interaction" else f"{name}_group"
        block = clustered_contrast(data, column, weights)
        block["familywise_interval"] = clustered_contrast(
            data, column, weights,
            confidence_level=protocol["analysis"]["familywise_confidence_level"],
        )
        block["adequate_group_sizes"] = bool(
            block["group_sizes"]
            and min(block["group_sizes"].values()) >= protocol["analysis"]["minimum_group_trades"]
            and min(block["group_dates"].values()) >= protocol["analysis"]["minimum_group_dates"]
        )
        contrasts[name] = block
    return {
        "baseline_n": len(rows), "measurable_n": len(data),
        "missing_n": len(rows) - len(data),
        "measurable": mean_estimate(data),
        "unmeasurable": mean_estimate(rows[~rows["measurable"]]),
        "cells": groups, "contrasts": contrasts,
    }


def summarize_diagnostics(rows: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    slices = {
        "all": rows,
        "calibration_2018_2021": rows[rows["session"] <= protocol["calibration_end"]],
        "later_2022_2024": rows[rows["session"] > protocol["calibration_end"]],
        "outside_2020_2022": rows[~rows["year"].isin(["2020", "2022"])],
    }
    for column in ("year", "symbol", "side", "entry_band"):
        for label, subset in rows.groupby(column):
            slices[f"{column}:{label}"] = subset
    best_dates = rows.groupby("session")["net_r"].sum().nlargest(20).index
    slices["without_best_20_baseline_dates"] = rows[~rows["session"].isin(best_dates)]
    for extra in (0.02, 0.04):
        stressed = rows.copy()
        stressed["net_dollars"] -= extra * stressed["quantity"]
        stressed["net_r"] = stressed["net_dollars"] / stressed["initial_risk"]
        slices[f"extra_roundtrip_cost_{extra:.2f}"] = stressed
    blocks = {name: diagnostic_block(data, protocol) for name, data in slices.items()}
    triage = {}
    for feature in FEATURES:
        later = blocks["later_2022_2024"]["contrasts"][feature]
        outside = blocks["outside_2020_2022"]["contrasts"][feature]
        tails = blocks["without_best_20_baseline_dates"]["contrasts"][feature]
        positive = sum(
            (blocks[f"symbol:{s}"]["contrasts"][feature]["estimate_r"] or 0) > 0
            for s in protocol["symbols"]
        )
        checks = {
            "later_sample_adequate": later["adequate_group_sizes"],
            "later_at_least_005r": (later["estimate_r"] or 0)
            >= protocol["analysis"]["meaningful_contrast_r"],
            "later_nominal_ci_above_zero": (later["ci_low_r"] or 0) > 0,
            "outside_winning_years_positive": (outside["estimate_r"] or 0) > 0,
            "at_least_four_symbols_positive": positive >= 4,
            "without_best_20_positive": (tails["estimate_r"] or 0) > 0,
        }
        triage[feature] = {
            "checks": checks, "positive_symbols": positive,
            "advance_to_separate_gate_design": all(checks.values()),
        }
    return {"slices": blocks, "triage": triage, "removed_best_dates": list(best_dates)}
