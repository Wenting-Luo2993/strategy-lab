from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade
from vibe.research_pipeline.inference import (
    MeanEstimate,
    Observation,
    Outcome,
    classify_outcome,
    cost_breakeven,
    estimate_mean,
    minimum_detectable_effect,
    paired_session_differences,
    required_observations,
    session_r_totals,
    split_alpha,
    trade_net_r,
    trade_session_date,
)


def _trade(pnl_prices=(100.0, 101.0), qty=10, risk=10.0, commission=0.0,
           entry_time=datetime(2024, 3, 4, 10, 0)) -> Trade:
    return Trade(
        symbol="QQQ",
        side="buy",
        quantity=qty,
        entry_price=pnl_prices[0],
        exit_price=pnl_prices[1],
        entry_time=entry_time,
        exit_time=entry_time + timedelta(hours=1),
        commission=commission,
        initial_risk=risk,
    )


def test_minimum_detectable_effect_matches_north_star_numbers() -> None:
    se = 1.545 / math.sqrt(502)
    assert se == pytest.approx(0.0690, abs=1e-3)
    assert minimum_detectable_effect(se) == pytest.approx(0.193, abs=1e-3)


def test_cluster_se_equals_iid_se_with_singleton_clusters() -> None:
    rng = random.Random(1)
    obs = [Observation(rng.gauss(0.1, 1.0), f"d{i}") for i in range(200)]
    est = estimate_mean(obs)
    # CR1 with singleton clusters differs from iid only by the D/(D-1) factor
    # against the n-1 denominator of the sample SD.
    assert est.se_cluster_r == pytest.approx(est.se_iid_r, rel=0.02)
    assert est.design_effect == pytest.approx(1.0, abs=0.05)


def test_design_effect_grows_with_within_day_correlation() -> None:
    rng = random.Random(2)
    obs = []
    for day in range(1000):
        shared = rng.gauss(0.0, 1.0)
        obs.extend(Observation(shared, f"d{day}") for _ in range(5))
    est = estimate_mean(obs)
    assert est.design_effect == pytest.approx(5.0, abs=0.2)


def test_hand_computed_cr1() -> None:
    obs = [
        Observation(1, "a"), Observation(2, "a"),
        Observation(0, "b"),
        Observation(-1, "c"), Observation(3, "c"),
    ]
    # N=5, mean=1. Cluster residuals S_d - n_d*mean: a: 3-2=1, b: 0-1=-1,
    # c: 2-2=0. Sum of squares=2. D=3 -> sqrt(3/2*2)/5 = sqrt(3)/5.
    est = estimate_mean(obs)
    assert est.mean_r == pytest.approx(1.0)
    assert est.se_cluster_r == pytest.approx(math.sqrt(3) / 5)
    assert est.n_clusters == 3


def test_interval_coverage_with_shared_day_effect() -> None:
    rng = random.Random(3)
    covered = 0
    reps = 400
    for _ in range(reps):
        obs = []
        for day in range(100):
            shared = rng.gauss(0.0, 0.7)
            for _ in range(3):
                obs.append(Observation(0.1 + shared + rng.gauss(0.0, 0.7), f"d{day}"))
        est = estimate_mean(obs)
        covered += est.ci_low_r <= 0.1 <= est.ci_high_r
    assert 0.93 <= covered / reps <= 0.97


def test_bootstrap_seed_and_order_behaviour() -> None:
    rng = random.Random(4)
    obs = [Observation(rng.gauss(0, 1), f"d{i % 40}") for i in range(200)]
    a = estimate_mean(obs, bootstrap_replicates=300, bootstrap_seed=7)
    b = estimate_mean(obs, bootstrap_replicates=300, bootstrap_seed=7)
    c = estimate_mean(obs, bootstrap_replicates=300, bootstrap_seed=8)
    shuffled = list(obs)
    random.Random(9).shuffle(shuffled)
    d = estimate_mean(shuffled, bootstrap_replicates=300, bootstrap_seed=7)
    assert (a.bootstrap_ci_low_r, a.bootstrap_ci_high_r) == (b.bootstrap_ci_low_r, b.bootstrap_ci_high_r)
    assert (a.bootstrap_ci_low_r, a.bootstrap_ci_high_r) != (c.bootstrap_ci_low_r, c.bootstrap_ci_high_r)
    assert a.bootstrap_ci_low_r == pytest.approx(d.bootstrap_ci_low_r)
    assert a.bootstrap_ci_high_r == pytest.approx(d.bootstrap_ci_high_r)
    with pytest.raises(ValueError):
        estimate_mean(obs, bootstrap_replicates=10)


def test_edge_cases() -> None:
    empty = estimate_mean([])
    assert empty.n == 0 and empty.mean_r is None and empty.ci_low_r is None

    one = estimate_mean([Observation(0.5, "a")])
    assert one.mean_r == 0.5 and one.se_cluster_r is None

    one_cluster = estimate_mean([Observation(1, "a"), Observation(3, "a")])
    assert one_cluster.mean_r == 2.0 and one_cluster.ci_low_r is None
    assert one_cluster.n_clusters == 1

    dirty = estimate_mean(
        [Observation(float("nan"), "a"), Observation(float("inf"), "b"),
         Observation(1.0, "c"), Observation(3.0, "d")]
    )
    assert dirty.n == 2 and dirty.mean_r == 2.0

    flat = estimate_mean([Observation(0.4, "a"), Observation(0.4, "b"), Observation(0.4, "c")])
    assert flat.se_cluster_r == 0.0
    assert flat.ci_low_r == flat.ci_high_r == pytest.approx(0.4)


def test_to_dict_is_json_safe() -> None:
    import json

    payload = estimate_mean(
        [Observation(float(i % 5), f"d{i % 3}") for i in range(30)],
        bootstrap_replicates=20,
        bootstrap_seed=1,
    ).to_dict()
    json.dumps(payload, allow_nan=False)
    assert payload["bootstrap_ci_low_r"] is not None
    json.dumps(estimate_mean([]).to_dict(), allow_nan=False)
    assert isinstance(estimate_mean([Observation(1, "a")]), MeanEstimate)


def test_classify_outcome_table_and_boundaries() -> None:
    m = 0.05
    assert classify_outcome(0.06, 0.2, min_edge_r=m) is Outcome.SUPPORTED
    assert classify_outcome(-0.2, 0.04, min_edge_r=m) is Outcome.CONTRADICTED
    assert classify_outcome(-0.1, 0.2, min_edge_r=m) is Outcome.UNDERPOWERED
    assert classify_outcome(0.05, 0.2, min_edge_r=m) is Outcome.UNDERPOWERED
    assert classify_outcome(-0.2, 0.05, min_edge_r=m) is Outcome.UNDERPOWERED
    assert classify_outcome(None, 0.2, min_edge_r=m) is Outcome.UNDERPOWERED
    assert classify_outcome(0.1, None, min_edge_r=m) is Outcome.UNDERPOWERED


def test_paired_session_differences() -> None:
    sessions = [("QQQ", "2024-01-02"), ("QQQ", "2024-01-03"), ("AMZN", "2024-01-02")]
    baseline = {("QQQ", "2024-01-02"): 1.0}
    variant = {("QQQ", "2024-01-02"): 1.5, ("AMZN", "2024-01-02"): 0.25}
    diffs = paired_session_differences(baseline, variant, sessions)
    assert [d.value_r for d in diffs] == [0.5, 0.0, 0.25]
    assert [d.cluster for d in diffs] == ["2024-01-02", "2024-01-03", "2024-01-02"]
    same = paired_session_differences(baseline, baseline, sessions)
    assert all(d.value_r == 0.0 for d in same)
    with pytest.raises(ValueError):
        paired_session_differences({("QQQ", "2030-01-01"): 1.0}, {}, sessions)
    with pytest.raises(ValueError):
        paired_session_differences({}, {("QQQ", "2030-01-01"): 1.0}, sessions)


def test_trade_net_r_agrees_with_performance_analyzer() -> None:
    trades = [
        _trade(commission=2.0),
        _trade(pnl_prices=(100.0, 98.0), risk=15.0, commission=1.0),
        _trade(risk=0.0),
        _trade(risk=None),
    ]
    for trade in trades:
        expected = (
            PerformanceAnalyzer._net_pnl(trade) / trade.initial_risk
            if trade.initial_risk and trade.initial_risk > 0
            else None
        )
        assert trade_net_r(trade) == expected


def test_trade_session_date_uses_exchange_time() -> None:
    late_utc = datetime(2024, 3, 5, 1, 30, tzinfo=timezone.utc)  # 20:30 on 3/4 ET
    assert trade_session_date(_trade(entry_time=late_utc)) == "2024-03-04"
    assert trade_session_date(_trade()) == "2024-03-04"


def test_session_r_totals_sums_per_symbol_session() -> None:
    a = _trade(commission=0.0)  # pnl 10 / risk 10 = +1R
    b = _trade(pnl_prices=(100.0, 99.0), risk=20.0)  # pnl -10 / risk 20 = -0.5R
    skipped = _trade(risk=0.0)
    totals = session_r_totals({"QQQ": [a, b, skipped]})
    assert totals == {("QQQ", "2024-03-04"): pytest.approx(0.5)}


def test_cost_breakeven_hand_built() -> None:
    t1 = _trade(pnl_prices=(100.0, 102.0), qty=10, risk=10.0, commission=1.0)  # net 19
    t2 = _trade(pnl_prices=(50.0, 49.0), qty=20, risk=20.0, commission=1.0)  # net -21
    result = cost_breakeven([t1, t2])
    assert result["mean_net_r"] == pytest.approx((1.9 + -1.05) / 2)
    assert result["mean_commission_r"] == pytest.approx((0.1 + 0.05) / 2)
    assert result["breakeven_extra_cost_bps"] == pytest.approx((19 - 21) / (1000 + 1000) * 1e4)
    assert cost_breakeven([])["mean_net_r"] is None


def test_required_observations_round_trip() -> None:
    sd, deff = 1.5, 1.3
    n = required_observations(sd, 0.05, design_effect=deff)
    se = sd * math.sqrt(deff / n)
    assert minimum_detectable_effect(se) <= 0.05 + 1e-9
    se_less = sd * math.sqrt(deff / (n - 1))
    assert minimum_detectable_effect(se_less) > 0.05
    with pytest.raises(ValueError):
        required_observations(1.0, 0.0)
    with pytest.raises(ValueError):
        required_observations(-1.0, 0.1)


def test_split_alpha() -> None:
    assert split_alpha(0.05, 5) == pytest.approx(0.01)
    with pytest.raises(ValueError):
        split_alpha(0.05, 0)


def test_one_sided_mde_is_smaller() -> None:
    assert minimum_detectable_effect(1.0, two_sided=False) < minimum_detectable_effect(1.0)
