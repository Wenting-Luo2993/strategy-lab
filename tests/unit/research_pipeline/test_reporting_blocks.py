"""Robustness, power, dollar and out-of-sample reporting blocks."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.common.models.trade import Trade
from vibe.research_pipeline.conditional import (
    condition_summary_text,
    oos_tercile_test,
    tercile_breakdown,
)
from vibe.research_pipeline.fixed_rule import FixedRuleSpec, summarise
from vibe.research_pipeline.inference import (
    Observation,
    capped_share,
    distribution_robustness,
    dollar_summary,
    mean_without_top_clusters,
    mean_without_top_values,
    power_table,
    sample_skewness,
    session_pnl_totals,
    trimmed_mean,
)
from vibe.research_pipeline.paired import PairedSpec, summarise_paired
from vibe.research_pipeline.publishing import forbidden_payload_path
from vibe.research_pipeline.report_blocks import fixed_rule_report, paired_report

NY = ZoneInfo("America/New_York")


def _trade(day: date, r: float, symbol: str = "AAA", qty: float = 10, risk_per_share: float = 1.0) -> Trade:
    stamp = datetime(day.year, day.month, day.day, 10, 0, tzinfo=NY)
    return Trade(
        symbol=symbol, side="buy", quantity=qty, entry_price=100.0,
        exit_price=100.0 + r * risk_per_share, entry_time=stamp, exit_time=stamp,
        initial_risk=risk_per_share * qty, commission=0.0, exit_reason="EOD",
    )


def _days(n: int, start=date(2019, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# --- power -----------------------------------------------------------------


def test_power_table_reproduces_statistician_numbers():
    table = power_table(0.0230, edges=(0.03, 0.05, 0.08), alpha=0.05, deflation=0.81, mean=-0.0010)
    nominal = [row["power_nonzero"] for row in table["rows"]]
    deflated = [row["power_nonzero_deflated"] for row in table["rows"]]
    for got, want in zip(nominal, (0.26, 0.58, 0.94)):
        assert got == pytest.approx(want, abs=0.015)
    for got, want in zip(deflated, (0.18, 0.42, 0.80)):
        assert got == pytest.approx(want, abs=0.015)
    # Upper CI bound: -0.001 + 1.96 * 0.023 = 0.044
    assert table["excluded_edge_r"] == pytest.approx(0.044, abs=0.0005)
    assert table["excluded_edge_deflated_r"] > table["excluded_edge_r"]


def test_power_supported_is_below_nonzero_power_and_half_at_threshold():
    table = power_table(0.02, edges=(0.05, 0.10), min_edge_r=0.05)
    at, above = table["rows"]
    assert at["power_supported"] < 0.05  # true edge on the threshold: lower bound rarely clears it
    assert above["power_supported"] < above["power_nonzero"]
    assert above["power_supported_deflated"] < above["power_supported"]


def test_power_table_degenerate_inputs():
    assert power_table(None) is None
    assert power_table(0.0) is None
    assert power_table(0.02)["excluded_edge_r"] is None
    with pytest.raises(ValueError):
        power_table(0.02, deflation=0.0)


# --- distribution robustness -----------------------------------------------


def test_trimmed_mean_and_skew():
    values = list(range(1, 101))
    assert trimmed_mean(values, 0.0) == pytest.approx(50.5)
    assert trimmed_mean(values + [10_000], 0.01) == pytest.approx(51.0)
    assert sample_skewness([1, 2, 3, 4, 5]) == pytest.approx(0.0)
    assert sample_skewness([0, 0, 0, 0, 10]) > 1.5
    assert sample_skewness([1, 1]) is None
    with pytest.raises(ValueError):
        trimmed_mean(values, 0.5)


def test_mean_without_top_values_and_clusters():
    assert mean_without_top_values([1, 2, 3, 100], 1) == pytest.approx(2.0)
    obs = [
        Observation(5.0, "d1"), Observation(5.0, "d1"),
        Observation(1.0, "d2"), Observation(-1.0, "d3"),
    ]
    # Drops both trades on d1, the largest-sum day.
    assert mean_without_top_clusters(obs, 1) == pytest.approx(0.0)
    assert mean_without_top_clusters(obs, 10) is None


def test_distribution_robustness_tail_driven_mean():
    rng = random.Random(1)
    obs = [Observation(-1.0 if rng.random() < 0.62 else 0.5, f"d{i}") for i in range(500)]
    obs += [Observation(15.0, f"t{i}") for i in range(20)]
    rob = distribution_robustness(obs)
    assert rob["median_r"] == -1.0
    assert rob["skew"] > 2
    assert rob["mean_without_top_days_r"]["20"] < rob["mean_r"]
    assert rob["mean_without_top_trades_r"]["50"] < rob["mean_without_top_days_r"]["10"]
    assert rob["trimmed_mean_5pct_r"] < rob["mean_r"]
    assert 0 < rob["win_rate"] < 1


# --- dollars ---------------------------------------------------------------


def test_dollar_summary_and_session_totals():
    days = _days(4)
    trades = {
        "AAA": [_trade(days[0], 1.0, qty=10), _trade(days[0], -1.0, qty=10), _trade(days[1], 2.0, qty=5)],
        "BBB": [_trade(days[2], -1.0, "BBB", qty=20)],
    }
    totals = session_pnl_totals(trades)
    assert totals[("AAA", days[0].isoformat())] == pytest.approx(0.0)
    out = dollar_summary(trades, capital=1_000.0, years=1.0)
    assert out["total_net_pnl_usd"] == pytest.approx(10 - 10 + 10 - 20)
    assert out["per_trade"]["mean_usd"] == pytest.approx(-10 / 4)
    assert out["per_symbol_session"]["n"] == 3
    assert out["per_symbol_session"]["mean_usd"] == pytest.approx(-10 / 3)
    assert out["return_on_equity_per_year"] == pytest.approx(-10 / 2_000)
    assert out["median_initial_risk_usd"] == pytest.approx(10.0)
    # bps of notional: +10/1000, -10/1000, +10/500, -20/2000
    assert out["mean_bps_of_notional"] == pytest.approx((100 - 100 + 200 - 100) / 4)


def test_capped_share_from_diagnostics():
    results = [
        SimpleNamespace(trades=[1, 2, 3, 4], execution_diagnostics={"orders_capped_by_buying_power": 3.0}),
        SimpleNamespace(trades=[1, 2, 3, 4], execution_diagnostics={"orders_capped_by_buying_power": 1.0}),
        None,
    ]
    out = capped_share(results)
    assert out["share_capped_by_buying_power"] == pytest.approx(0.5)
    legacy = capped_share([SimpleNamespace(trades=[1], execution_diagnostics={})])
    assert legacy["share_capped_by_buying_power"] is None


# --- summaries -------------------------------------------------------------


def _fixed_spec(**kw):
    return FixedRuleSpec(
        title="t", ruleset_path="x.yaml", symbols=("AAA", "BBB"),
        start=date(2019, 1, 1), end=date(2019, 12, 31), evidence_role="exploration",
        bootstrap_replicates=50, **kw,
    )


def test_fixed_rule_summary_has_new_blocks_and_is_publishable():
    rng = random.Random(2)
    days = _days(120)
    trades = {
        s: [_trade(d, rng.gauss(0, 1), s, qty=rng.choice([5, 10])) for d in days]
        for s in ("AAA", "BBB")
    }
    s = summarise(_fixed_spec(), trades, {}, capped={"share_capped_by_buying_power": 0.5})
    assert s["robustness"]["n"] == 240
    assert s["power"]["se_r"] == pytest.approx(s["pooled"]["se_cluster_r"])
    assert s["power"]["excluded_edge_r"] == pytest.approx(
        s["pooled"]["mean_r"] + 1.959964 * s["pooled"]["se_cluster_r"], rel=1e-4
    )
    assert s["dollars"]["per_symbol_session"]["n"] == 240
    assert s["dollars"]["sizing"]["share_capped_by_buying_power"] == 0.5
    assert forbidden_payload_path(s) is None
    text = fixed_rule_report(s)
    assert "Power" in text and "$/symbol-session" in text and "trim5%" in text
    # Older callers that do not pass ``capped`` still work.
    assert summarise(_fixed_spec(), trades, {})["dollars"]["sizing"] is None


def test_paired_summary_reports_dollar_difference():
    days = _days(60)
    keys = [d.isoformat() for d in days]
    base = {"AAA": [_trade(d, 1.0, qty=10) for d in days]}
    # Same R, half the size: R difference is zero but dollars fall.
    var = {"AAA": [_trade(d, 1.0, qty=5) for d in days]}
    spec = PairedSpec(
        title="t", baseline_ruleset="b.yaml", variant_ruleset="v.yaml", lever="size",
        symbols=("AAA",), start=date(2019, 1, 1), end=date(2019, 12, 31),
        evidence_role="exploration", bootstrap_replicates=20,
    )
    s = summarise_paired(
        spec, base, var, {"AAA": keys}, {},
        baseline_capped={"share_capped_by_buying_power": 0.0},
        variant_capped={"share_capped_by_buying_power": 1.0},
    )
    assert s["difference"]["mean_r"] == pytest.approx(0.0)
    assert s["difference_usd"]["mean_diff_usd"] == pytest.approx(-5.0)
    assert s["variant"]["dollars"]["sizing"]["share_capped_by_buying_power"] == 1.0
    assert forbidden_payload_path(s) is None
    assert "$/symbol-session" in paired_report(s)


# --- out-of-sample tercile test --------------------------------------------


def _rows(days, values, rs):
    return [[d.isoformat(), "AAA", v, r] for d, v, r in zip(days, values, rs)]


def test_oos_replicates_a_stable_condition():
    rng = random.Random(5)
    days = _days(800)
    values = [rng.random() for _ in days]
    rs = [(0.4 if v > 0.66 else -0.1) + rng.gauss(0, 0.2) for v in values]
    out = oos_tercile_test(_rows(days, values, rs))
    assert out["split_date"] == days[400].isoformat()
    assert out["train_period"][1] < out["split_date"] <= out["test_period"][0]
    assert out["train"]["high_minus_low_r"] > 0.3
    assert out["test"]["high_minus_low_r"] > 0.3
    assert out["verdict"] == "replicated"


def test_oos_flags_a_condition_that_reverses():
    rng = random.Random(6)
    days = _days(800)
    values = [rng.random() for _ in days]
    rs = [
        ((0.4 if v > 0.66 else -0.1) if i < 400 else (-0.4 if v > 0.66 else 0.1)) + rng.gauss(0, 0.2)
        for i, v in enumerate(values)
    ]
    out = oos_tercile_test(_rows(days, values, rs))
    assert out["train"]["high_minus_low_r"] > 0
    assert out["test"]["high_minus_low_r"] < 0
    assert out["verdict"] == "reversed"


def test_oos_uses_train_cutoffs_only():
    days = _days(10)
    # Train values 0..4, test values 100..104: every test row lands in "high".
    values = list(range(5)) + list(range(100, 105))
    out = oos_tercile_test(_rows(days, values, [0.0] * 10))
    assert out["train_cutoffs"]["high"] < 5
    assert out["test"]["n_by_tercile"] == {"low": 0, "mid": 0, "high": 5}
    assert out["test"]["high_minus_low_r"] is None
    assert out["verdict"] == "insufficient"


def test_tercile_breakdown_includes_oos_and_prefers_it_in_text():
    rng = random.Random(7)
    days = _days(300)
    values = [rng.random() for _ in days]
    trades = {"AAA": [_trade(d, rng.gauss(0, 1)) for d in days]}
    features = {"AAA": pd.DataFrame({"x": values}, index=[d.isoformat() for d in days])}
    out = tercile_breakdown(trades, features, "x")
    assert "share_years" in out["consistency"]  # kept for older readers
    assert out["oos"]["test"]["n_trades"] + out["oos"]["train"]["n_trades"] == 300
    assert out["summary_text"].startswith("x: out-of-sample high-low")
    assert oos_tercile_test([]) is None
    assert "no out-of-sample test" in condition_summary_text({"variable": "y", "interaction": None, "oos": None})
