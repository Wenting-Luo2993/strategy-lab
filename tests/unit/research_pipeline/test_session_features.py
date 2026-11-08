"""S5: causal session features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vibe.research_pipeline.session_features import (
    FEATURE_COLUMNS,
    build_session_features,
)

NY = "America/New_York"


def _bars(n_sessions: int, *, seed: int = 0, bars_per_day: int = 78) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2020-01-02", periods=n_sessions)
    frames = []
    price = 100.0
    for day in days:
        idx = pd.date_range(
            day.strftime("%Y-%m-%d") + " 09:30", periods=bars_per_day, freq="5min", tz=NY
        )
        steps = rng.normal(0, 0.3, bars_per_day)
        close = price + np.cumsum(steps)
        open_ = np.concatenate([[price], close[:-1]])
        frames.append(
            pd.DataFrame(
                {
                    "open": open_,
                    "high": np.maximum(open_, close) + rng.uniform(0, 0.2, bars_per_day),
                    "low": np.minimum(open_, close) - rng.uniform(0, 0.2, bars_per_day),
                    "close": close,
                    "volume": rng.uniform(500, 1500, bars_per_day),
                },
                index=idx,
            )
        )
        price = close[-1] + rng.normal(0, 0.5)
    return pd.concat(frames)


def test_features_are_causal_by_truncation():
    bars = _bars(300)
    full = build_session_features(bars)
    sessions = list(full.index)
    rng = np.random.default_rng(99)
    for pos in rng.choice(len(sessions) - 1, size=20, replace=False):
        session = sessions[pos]
        or_end = pd.Timestamp(session + " 09:35", tz=NY)
        corrupted = bars.copy()
        mask = corrupted.index >= or_end
        corrupted.loc[mask, ["open", "high", "low", "close"]] = rng.uniform(
            1, 500, size=(int(mask.sum()), 4)
        )
        corrupted.loc[mask, "volume"] = rng.uniform(1, 1e6, size=int(mask.sum()))
        again = build_session_features(corrupted)
        pd.testing.assert_series_equal(
            full.loc[session], again.loc[session], check_names=False
        )


def _small() -> pd.DataFrame:
    rows = []
    # (day, open, high, low, close, or_volume) with a single 5-minute OR bar then one later bar
    spec = [
        ("2020-01-02", 100, 102, 99, 101, 1000),
        ("2020-01-03", 103, 106, 102, 105, 2000),
        ("2020-01-06", 104, 108, 103, 107, 3000),
    ]
    for day, o, h, l, c, v in spec:
        rows.append((pd.Timestamp(day + " 09:30", tz=NY), o, h, l, o, v))
        rows.append((pd.Timestamp(day + " 09:35", tz=NY), o, h, l, c, 10))
    idx = [r[0] for r in rows]
    return pd.DataFrame(
        [(r[1], r[2], r[3], r[4], r[5]) for r in rows],
        index=idx,
        columns=["open", "high", "low", "close", "volume"],
    )


def test_hand_computed_values():
    feats = build_session_features(_small(), or_minutes=5)
    assert feats.loc["2020-01-03", "gap_pct"] == pytest.approx((103 - 101) / 101 * 100)
    assert feats.loc["2020-01-06", "gap_pct"] == pytest.approx((104 - 105) / 105 * 100)
    assert feats.loc["2020-01-06", "abs_gap_pct"] == pytest.approx(1 / 105 * 100)
    # ATR and relative volume need more history than this dataset has.
    assert np.isnan(feats["or_width_atr"]).all()
    assert np.isnan(feats["rel_open_volume"]).all()


def test_or_width_atr_and_relative_volume_hand_computed():
    days = pd.bdate_range("2020-01-02", periods=22)
    rows = []
    for i, day in enumerate(days):
        o = 100.0
        # prior sessions: high-low = 2, close = open, so TR = 2 once gaps are zero
        rows.append((pd.Timestamp(day.strftime("%Y-%m-%d") + " 09:30", tz=NY), o, 101.0, 99.0, o, 100.0 + i))
        rows.append((pd.Timestamp(day.strftime("%Y-%m-%d") + " 09:35", tz=NY), o, 101.0, 99.0, o, 5.0))
    df = pd.DataFrame(
        [r[1:] for r in rows], index=[r[0] for r in rows],
        columns=["open", "high", "low", "close", "volume"],
    )
    feats = build_session_features(df)
    last = feats.iloc[-1]
    assert last["or_width_atr"] == pytest.approx(2.0 / 2.0)
    prior_mean = np.mean([100.0 + i for i in range(1, 21)])
    assert last["rel_open_volume"] == pytest.approx((100.0 + 21) / prior_mean)
    assert last["prior_trend_20d_pct"] == pytest.approx(0.0)


def test_gap_atr_hand_computed():
    days = pd.bdate_range("2020-01-02", periods=17)
    opens = [100.0] * 15 + [103.0, 100.0]
    rows = []
    for day, o in zip(days, opens):
        d = day.strftime("%Y-%m-%d")
        # every session has high-low = 2 and closes at 100
        rows.append((pd.Timestamp(d + " 09:30", tz=NY), o, max(o, 101.0), min(o, 99.0), 100.0, 10.0))
        rows.append((pd.Timestamp(d + " 09:35", tz=NY), 100.0, 101.0, 99.0, 100.0, 10.0))
    df = pd.DataFrame(
        [r[1:] for r in rows], index=[r[0] for r in rows],
        columns=["open", "high", "low", "close", "volume"],
    )
    feats = build_session_features(df)
    assert np.isnan(feats["gap_atr"].iloc[:15]).all()  # ATR needs 14 prior true ranges
    # Day 15: ATR of prior 14 sessions is 2; gap is +3.
    assert feats["gap_atr"].iloc[15] == pytest.approx(1.5)
    assert feats["abs_gap_atr"].iloc[15] == pytest.approx(1.5)
    # Day 16: prior TR window now includes day 15's TR of 4 (high 103 - low 99).
    atr = (13 * 2.0 + 4.0) / 14
    assert feats["gap_atr"].iloc[16] == pytest.approx(0.0)
    assert feats["abs_gap_atr"].iloc[16] == pytest.approx(0.0)
    assert feats["or_width_atr"].iloc[16] == pytest.approx(2.0 / atr)


def test_warmup_rows_are_nan_and_percentile_needs_history():
    feats = build_session_features(_bars(40))
    assert list(feats.columns) == list(FEATURE_COLUMNS)
    assert np.isnan(feats["atr_pctile"]).all()
    assert np.isnan(feats["rel_open_volume"].iloc[:20]).all()
    assert not np.isnan(feats["rel_open_volume"].iloc[20])
    assert np.isnan(feats["gap_pct"].iloc[0])
    assert np.isnan(feats["prior_trend_20d_pct"].iloc[:21]).all()


def test_or_minutes_15_uses_three_five_minute_bars():
    bars = _bars(3)
    five = build_session_features(bars, or_minutes=5)
    fifteen = build_session_features(bars, or_minutes=15)
    assert np.isnan(five["or_width_atr"]).all()
    day = "2020-01-06"
    day_bars = bars[bars.index.strftime("%Y-%m-%d") == day]
    or_bars = day_bars.iloc[:3]
    assert len(or_bars) == 3
    assert fifteen.loc[day, "gap_pct"] == pytest.approx(five.loc[day, "gap_pct"])
    with pytest.raises(ValueError):
        build_session_features(bars, or_minutes=0)
