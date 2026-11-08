"""S5: tercile breakdowns."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from vibe.common.models.trade import Trade
from vibe.research_pipeline.conditional import tercile_breakdown
from vibe.research_pipeline.publishing import forbidden_payload_path

NY = ZoneInfo("America/New_York")


def _trade(day: date, r: float, symbol: str) -> Trade:
    stamp = datetime(day.year, day.month, day.day, 10, 0, tzinfo=NY)
    return Trade(
        symbol=symbol, side="buy", quantity=1, entry_price=100.0,
        exit_price=100.0 + r * 10.0, entry_time=stamp, exit_time=stamp,
        initial_risk=10.0, commission=0.0, exit_reason="EOD",
    )


def _days(n: int, start=date(2019, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _features(days, values) -> pd.DataFrame:
    return pd.DataFrame({"x": values}, index=[d.isoformat() for d in days])


def test_planted_interaction_is_detected():
    rng = random.Random(3)
    days = _days(600)
    values = [rng.random() for _ in days]
    median = sorted(values)[len(values) // 2]
    trades = [
        _trade(d, (0.3 if v > median else -0.1) + rng.gauss(0, 0.05), "AAA")
        for d, v in zip(days, values)
    ]
    out = tercile_breakdown({"AAA": trades}, {"AAA": _features(days, values)}, "x")
    assert out["label"] == "diagnostic"
    assert out["monotonic"] == "increasing"
    assert out["interaction"]["ci_low_r"] > 0
    assert out["interaction"]["approximate"] is True
    assert out["consistency"]["share_years"] == 1.0


def test_no_interaction_interval_contains_zero_in_most_replications():
    covered = 0
    days = _days(300)
    for seed in range(100):
        rng = random.Random(seed)
        values = [rng.random() for _ in days]
        trades = [_trade(d, rng.gauss(0, 1), "AAA") for d in days]
        out = tercile_breakdown({"AAA": trades}, {"AAA": _features(days, values)}, "x")
        ix = out["interaction"]
        if ix["ci_low_r"] <= 0 <= ix["ci_high_r"]:
            covered += 1
    assert covered >= 90


def test_missing_values_counted_cutoffs_stored_and_json_safe():
    days = _days(60)
    values = [float("nan") if i % 6 == 0 else float(i) for i in range(60)]
    trades = [_trade(d, 0.1 * (i % 5 - 2), "AAA") for i, d in enumerate(days)]
    trades.append(_trade(date(2030, 1, 7), 1.0, "AAA"))  # no feature row
    out = tercile_breakdown({"AAA": trades}, {"AAA": _features(days, values)}, "x")
    assert out["n_missing"] == 10 + 1
    assert out["n_trades"] == 50
    assert out["cutoffs"]["low"] < out["cutoffs"]["high"]
    assert sum(t["n"] for t in out["terciles"].values()) == 50
    assert forbidden_payload_path(out) is None
    json.dumps(out, allow_nan=False)


def test_supplied_cutoffs_are_used_as_given():
    import pytest

    days = _days(60)
    with pytest.raises(ValueError):
        tercile_breakdown(
            {"AAA": [_trade(days[0], 0.1, "AAA")]},
            {"AAA": _features(days, [1.0] * 60)}, "x",
            cutoffs={"low": 5.0, "high": 1.0},
        )
    days = _days(60)
    values = list(range(60))
    trades = [_trade(d, 0.1, "AAA") for d in days]
    out = tercile_breakdown(
        {"AAA": trades}, {"AAA": _features(days, values)}, "x",
        cutoffs={"low": 10.0, "high": 50.0},
    )
    assert out["cutoffs"] == {"low": 10.0, "high": 50.0}
    assert [out["terciles"][t]["n"] for t in ("low", "mid", "high")] == [11, 40, 9]


def _sided_trade(day: date, r: float, symbol: str, side: str) -> Trade:
    stamp = datetime(day.year, day.month, day.day, 10, 0, tzinfo=NY)
    sign = 1 if side == "buy" else -1
    return Trade(
        symbol=symbol, side=side, quantity=1, entry_price=100.0,
        exit_price=100.0 + sign * r * 10.0, entry_time=stamp, exit_time=stamp,
        initial_risk=10.0, commission=0.0, exit_reason="EOD",
    )


def test_trade_relative_variable_uses_side():
    rng = random.Random(7)
    days = _days(900)
    gaps = [rng.uniform(-1, 1) for _ in days]
    trades = []
    for d, g in zip(days, gaps):
        side = rng.choice(["buy", "sell"])
        aligned = (1 if side == "buy" else -1) * g
        trades.append(_sided_trade(d, (0.4 if aligned > 0.3 else -0.1) + rng.gauss(0, 0.05), "AAA", side))
    features = {"AAA": pd.DataFrame({"gap_atr": gaps}, index=[d.isoformat() for d in days])}

    aligned = tercile_breakdown({"AAA": trades}, features, "gap_aligned_atr")
    assert aligned["monotonic"] == "increasing"
    assert aligned["interaction"]["ci_low_r"] > 0
    assert set(aligned["by_side"]) == {"long", "short"}
    for side in ("long", "short"):
        assert aligned["by_side"][side]["high"]["mean_r"] > aligned["by_side"][side]["low"]["mean_r"]

    # The signed session feature mixes sides and hides the effect.
    signed = tercile_breakdown({"AAA": trades}, features, "gap_atr")
    assert abs(signed["interaction"]["high_minus_low_r"]) < aligned["interaction"]["high_minus_low_r"] / 2


def test_trade_relative_value_is_side_times_feature():
    from vibe.research_pipeline.conditional import trade_side_sign

    days = _days(2)
    features = {"AAA": pd.DataFrame({"gap_atr": [0.5, 0.5]}, index=[d.isoformat() for d in days])}
    long_t = _sided_trade(days[0], 0.1, "AAA", "buy")
    short_t = _sided_trade(days[1], 0.1, "AAA", "sell")
    assert trade_side_sign(long_t) == 1 and trade_side_sign(short_t) == -1
    assert trade_side_sign(type("T", (), {"side": "neutral"})()) is None
    out = tercile_breakdown({"AAA": [long_t, short_t]}, features, "gap_aligned_atr")
    # Values are +0.5 (long, with the gap) and -0.5 (short, against it).
    assert out["cutoffs"]["low"] == pytest.approx(-0.5 + 1 / 3)
    assert out["by_side"]["long"]["high"]["n"] == 1
    assert out["by_side"]["short"]["low"]["n"] == 1
    assert out["n_trades"] == 2 and out["n_missing"] == 0


def test_empty_and_unknown_variable_are_safe():
    out = tercile_breakdown({}, {}, "x")
    assert out["n_trades"] == 0 and out["interaction"] is None
    days = _days(5)
    trades = [_trade(d, 0.1, "AAA") for d in days]
    out = tercile_breakdown({"AAA": trades}, {"AAA": _features(days, [1] * 5)}, "nope")
    assert out["n_missing"] == 5
    json.dumps(out, allow_nan=False)
    assert np.isfinite(0)
