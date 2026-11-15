import copy

import numpy as np
import pandas as pd
import pytest
import pyarrow.parquet as pq

from scripts.run_movement_noise_study import load_exploration_bars
from vibe.research_pipeline.movement_noise import (
    clustered_contrast, decision_features, freeze_labels, join_features, summarize_diagnostics,
)
from vibe.research_pipeline.publishing import forbidden_payload_path


def _protocol():
    return {
        "features": {
            "opportunity": {"atr_period": 2, "friction_dollars": 0.1},
            "persistence": {
                "changes": 2, "bar_minutes": 5,
                "exchange_tz": "America/New_York",
                "session_open": "09:30", "session_close": "16:00",
            },
        },
        "symbols": ["AAA"],
        "calibration_end": "2021-12-31",
        "thresholds": {"minimum_calibration_rows_per_symbol": 2},
    }


def _bars():
    index = pd.DatetimeIndex([
        f"2024-03-{day} {clock}" for day in ("07", "08", "11")
        for clock in ("09:30", "09:35", "09:40", "09:45")
    ], tz="America/New_York")
    closes = np.array([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21.])
    return pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": 100.0,
    }, index=index)


def test_opportunity_is_prior_session_and_features_available_at_close():
    bars = _bars()
    full, _ = decision_features(bars, _protocol())
    assert full["opportunity"].iloc[:8].isna().all()
    # Day 1 TR=5; day 2 TR=5; prior Wilder ATR2=5.
    np.testing.assert_allclose(full["opportunity"].iloc[8:], 50)
    assert full.index[0] == bars.index[0] + pd.Timedelta(minutes=5)
    for length in (1, 4, 7, 9, 11):
        prefix, _ = decision_features(bars.iloc[:length], _protocol())
        pd.testing.assert_frame_equal(full.iloc[:length], prefix)
    partial = pd.Series(True, index=bars.index)
    partial.iloc[9] = False
    masked, _ = decision_features(bars, _protocol(), complete_bars=partial)
    assert masked["persistence"].iloc[9:].isna().all()
    pd.testing.assert_series_equal(masked["opportunity"], full["opportunity"])


def test_opportunity_reuses_shared_mean_true_range_seed_not_first_observation_ewm():
    bars = pd.DataFrame({
        "open": [10., 11., 12., 16.], "close": [10., 11., 12., 16.],
        "high": [11., 13., 16., 20.], "low": [9., 9., 8., 12.],
        "volume": [100.] * 4,
    }, index=pd.date_range(
        "2024-03-04 09:30", periods=4, freq="D", tz="America/New_York"
    ))
    protocol = _protocol()
    protocol["features"]["opportunity"]["atr_period"] = 3
    result, _ = decision_features(bars, protocol)
    assert result["opportunity"].iloc[:3].isna().all()
    assert result["opportunity"].iloc[3] == pytest.approx((2 + 4 + 8) / 3 / .1)


def test_feature_join_never_reads_entry_bar_close_or_later_stamp():
    bars = _bars()
    features, _ = decision_features(bars, _protocol())
    entry = bars.index[3]  # 09:45 fill: signal bar 09:40 closed here.
    trades = pd.DataFrame({
        "symbol": ["AAA"], "entry_time": [entry], "exit_time": [entry],
        "pnl": [1.0], "commission": [0.1], "initial_risk": [2.0],
        "quantity": [1], "side": ["buy"],
    })
    joined = join_features(trades, {"AAA": features})
    assert joined["persistence"].iloc[0] == 1
    assert joined["net_r"].iloc[0] == 0.45
    future = bars.copy()
    future.iloc[3:, future.columns.get_loc("close")] = 1000
    altered, _ = decision_features(future, _protocol())
    pd.testing.assert_series_equal(
        joined["persistence"], join_features(trades, {"AAA": altered})["persistence"]
    )
    trades["entry_time"] = entry + pd.Timedelta(minutes=1)
    trades["exit_time"] = trades["entry_time"]
    assert not join_features(trades, {"AAA": features})["measurable"].any()


def test_cutoffs_ignore_later_features_and_all_outcomes_and_missing_stays_missing():
    rows = pd.DataFrame({
        "symbol": ["AAA"] * 5,
        "session": ["2019-01-02", "2020-01-02", "2022-01-03", "2024-01-02", "2024-01-03"],
        "opportunity": [10., 20., 1000., 2000., np.nan],
        "persistence": [.2, .4, .9, .8, np.nan],
        "measurable": [True, True, True, True, False],
        "net_r": [-1, 1, 100, -100, 1000],
    })
    labelled, cuts = freeze_labels(rows, _protocol())
    assert cuts["AAA"] == {"n": 2, "opportunity": 15., "persistence": pytest.approx(.3)}
    assert labelled["cell"].iloc[:4].tolist() == ["LL", "HH", "HH", "HH"]
    assert pd.isna(labelled["cell"].iloc[-1])
    changed = rows.copy()
    changed["net_r"] *= -100
    changed.loc[2:3, "opportunity"] = [-1e9, -1e8]
    assert freeze_labels(changed, _protocol())[1] == cuts
    protocol = copy.deepcopy(_protocol())
    protocol["thresholds"]["minimum_calibration_rows_per_symbol"] = 3
    with pytest.raises(ValueError, match="insufficient calibration"):
        freeze_labels(rows, protocol)


def test_shared_date_covariance_cancels_common_market_shock_in_difference():
    rows = pd.DataFrame({
        "session": ["2020-01-02"] * 2 + ["2020-01-03"] * 2,
        "group": ["H", "L"] * 2,
        "net_r": [10.5, 10., -9.5, -10.],
    })
    contrast = clustered_contrast(rows, "group", {"H": 1, "L": -1})
    assert contrast["estimate_r"] == .5
    assert contrast["ci_low_r"] == pytest.approx(.5)
    assert contrast["ci_high_r"] == pytest.approx(.5)
    # With unequal group counts, influence contributions still use group means.
    unequal = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    result = clustered_contrast(unequal, "group", {"H": 1, "L": -1})
    assert result["estimate_r"] == pytest.approx((10.5 * 2 - 9.5) / 3)
    assert result["ci_low_r"] < result["estimate_r"] < result["ci_high_r"]
    missing = clustered_contrast(rows, "group", {"H": 1, "X": -1})
    assert missing["estimate_r"] is None


def test_parquet_predicate_load_never_materializes_reserved_rows(tmp_path, monkeypatch):
    path = tmp_path / "AAA.parquet"
    bars = _bars()
    reserved = bars.iloc[:1].copy()
    reserved.index = pd.DatetimeIndex(["2025-01-02 09:30"], tz="America/New_York")
    pd.concat([bars, reserved]).to_parquet(path)
    original = pd.read_parquet
    observed = {}

    def bounded_read(*args, **kwargs):
        observed.update(kwargs)
        frame = original(*args, **kwargs)
        assert frame.index.max().year == 2024
        return frame

    monkeypatch.setattr(pd, "read_parquet", bounded_read)
    result = load_exploration_bars(path, "2024-03-07", "2024-12-31")
    assert len(result) == len(bars)
    assert len(observed["filters"]) == 2
    schema = pq.ParquetFile(path).schema_arrow
    for column, _, scalar in observed["filters"]:
        assert scalar.type == schema.field(column).type


def test_diagnostic_summary_supports_missing_strata_and_is_aggregate_only():
    protocol = _protocol()
    protocol["analysis"] = {
        "familywise_confidence_level": 1 - .05 / 3,
        "minimum_group_trades": 100, "minimum_group_dates": 50,
        "meaningful_contrast_r": .05,
    }
    rows = pd.DataFrame({
        "symbol": ["AAA"] * 4,
        "session": ["2019-01-02", "2020-01-02", "2022-01-03", "2024-01-02"],
        "opportunity": [10., 20., 1000., 2000.],
        "persistence": [.2, .4, .9, .8],
        "measurable": [True] * 4,
        "net_r": [-1., 1., 1., -1.], "net_dollars": [-10., 10., 10., -10.],
        "initial_risk": [10.] * 4, "quantity": [1.] * 4,
        "year": ["2019", "2020", "2022", "2024"],
        "side": ["buy"] * 4, "entry_band": ["early"] * 4,
    })
    labelled, _ = freeze_labels(rows, protocol)
    summary = summarize_diagnostics(labelled, protocol)
    assert forbidden_payload_path(summary) is None
    assert not summary["triage"]["opportunity"]["advance_to_separate_gate_design"]
    assert summary["slices"]["all"]["contrasts"]["interaction"]["estimate_r"] is None
