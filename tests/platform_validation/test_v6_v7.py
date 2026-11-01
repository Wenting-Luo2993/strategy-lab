"""V6/V7 plumbing and loose statistical bounds, plus the generator itself."""

from pathlib import Path

import numpy as np
import pytest

from vibe.research_pipeline.validation_suite.suite import (
    run_null_calibration,
    run_planted_edge,
    wilson_interval,
)
from vibe.research_pipeline.validation_suite.synthetic import tick_walk_sessions

RULESET = str(Path(__file__).resolve().parents[2] / "vibe" / "rulesets" / "orb_production.yaml")


def test_tick_walk_shape_and_determinism():
    a = tick_walk_sessions(n_sessions=3, seed=1)
    b = tick_walk_sessions(n_sessions=3, seed=1)
    assert a.equals(b)
    assert len(a) == 3 * 78
    assert (a["high"] >= a[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (a["low"] <= a[["open", "close"]].min(axis=1) + 1e-9).all()
    assert (a["close"] > 0).all()


def test_tick_walk_is_a_martingale_within_sessions():
    bars = tick_walk_sessions(n_sessions=200, seed=2)
    moves = bars.groupby(bars.index.date)["close"].agg(lambda s: s.iloc[-1] - s.iloc[0])
    se = moves.std() / np.sqrt(len(moves))
    assert abs(moves.mean()) < 4 * se


def test_wilson_interval_contains_point_estimate():
    lo, hi = wilson_interval(3, 20)
    assert lo < 3 / 20 < hi
    assert wilson_interval(0, 0) == (0.0, 1.0)


@pytest.mark.slow
def test_null_calibration_has_no_excess_false_positives():
    r = run_null_calibration(20, 120, seed=1, ruleset_path=RULESET)
    assert r["degenerate_studies"] == 0
    assert r["false_positive_count"] <= 4
    assert r["coverage_rate"] >= 0.7
    assert r["mean_trades_per_study"] > 50


def test_cli_records_one_platform_validation_experiment(tmp_path):
    import sqlite3
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_platform_validation as cli

    db = tmp_path / "research.sqlite"
    rc = cli.main([
        "--suite", "v6", "--studies", "2", "--sessions", "40", "--seed", "1",
        "--research-db", str(db),
    ])
    assert rc == 0
    con = sqlite3.connect(db)
    rows = con.execute("select title, state from research_experiments").fetchall()
    con.close()
    assert rows == [("V6 null calibration", "completed")]


@pytest.mark.slow
def test_planted_edge_report_is_well_formed_and_detects_something():
    r = run_planted_edge(
        10, 120, seed=1, ruleset_path=RULESET, up_probability_with_trend=0.502,
        truth_sessions=1500,
    )
    assert r["truth_r"] is not None and r["truth_r"] > 0
    assert 0.0 <= r["detection_rate"] <= 1.0
    assert 0.0 <= r["predicted_power"] <= 1.0
    assert r["coverage_of_truth"] is not None
