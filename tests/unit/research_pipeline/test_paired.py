"""S4: paired comparison runner (library + CLI)."""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from test_fixed_rule import (
    _performance_result,
    _read_experiment,
)
from vibe.common.models.trade import Trade
from vibe.research_pipeline.contracts import RetentionProfile, SegmentRole
from vibe.research_pipeline.fixed_rule import ConfirmationNotSupported
from vibe.research_pipeline.holdout import HoldoutViolation
from vibe.research_pipeline.inference import Observation, estimate_mean
from vibe.research_pipeline.paired import (
    PairedSpec,
    paired_definition,
    record_paired_comparison,
    run_paired_comparison,
)
from vibe.research_pipeline.publishing import forbidden_payload_path
from vibe.research_pipeline.recording import BacktestRunSink
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.trials import TrialTag

NY = ZoneInfo("America/New_York")
START = date(2020, 1, 1)
END = date(2020, 12, 31)


def _days(n: int) -> list[date]:
    out, day = [], START
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _trade(day: date, r: float, symbol: str) -> Trade:
    stamp = datetime(day.year, day.month, day.day, 10, 0, tzinfo=NY)
    return Trade(
        symbol=symbol, side="buy", quantity=1, entry_price=100.0,
        exit_price=100.0 + r * 10.0, entry_time=stamp, exit_time=stamp,
        initial_risk=10.0, commission=0.0, exit_reason="EOD",
    )


def _spec(**kw) -> PairedSpec:
    base = dict(
        title="t", baseline_ruleset="b.yaml", variant_ruleset="v.yaml",
        lever="lever", symbols=("BBB", "AAA"), start=START, end=END,
        evidence_role="exploration", bootstrap_replicates=100,
    )
    base.update(kw)
    return PairedSpec(**base)


SESSIONS = {s: [d.isoformat() for d in _days(40)] for s in ("AAA", "BBB")}


def _baseline() -> dict[str, list[Trade]]:
    rng = random.Random(1)
    out = {}
    for s in ("AAA", "BBB"):
        out[s] = [
            _trade(d, round(rng.uniform(-1.5, 2.0), 3), s)
            for i, d in enumerate(_days(40))
            if i % 5 != 0
        ]
    return out


def _run(spec, base, var, sessions=SESSIONS):
    return run_paired_comparison(
        spec,
        lambda s: SimpleNamespace(trades=base[s]),
        lambda s: SimpleNamespace(trades=var[s]),
        lambda s: sessions[s],
    )


def test_identical_rules_have_zero_difference():
    base = _baseline()
    s = _run(_spec(), base, base).summary
    d = s["difference"]
    assert d["mean_r"] == 0.0
    assert (d["ci_low_r"], d["ci_high_r"]) == (0.0, 0.0)
    assert s["outcome"] == "contradicted"
    assert s["sessions_changed"] == 0
    assert s["n_symbol_sessions"] == 80 and s["n_dates"] == 40


def test_constant_uplift_equals_uplift_times_trades_per_session():
    base = _baseline()
    var = {
        s: [_trade(t.entry_time.date(), t.pnl / 10.0 + 0.2, s) for t in ts]
        for s, ts in base.items()
    }
    result = _run(_spec(), base, var).summary
    n_trades = sum(len(v) for v in base.values())
    assert result["difference"]["mean_r"] == pytest.approx(0.2 * n_trades / 80)
    assert result["min_edge_per_session_r"] == pytest.approx(0.05 * n_trades / 80)
    assert result["baseline"]["n_trades"] == n_trades


def test_filter_difference_is_minus_skipped_r_per_session():
    base = _baseline()
    var = {s: ts[::2] for s, ts in base.items()}
    skipped = [t for s, ts in base.items() for t in ts[1::2]]
    result = _run(_spec(), base, var).summary
    skipped_r = sum(t.pnl / 10.0 for t in skipped)
    assert result["difference"]["mean_r"] == pytest.approx(-skipped_r / 80)
    nonzero = sum(1 for t in skipped if abs(t.pnl) > 1e-9)
    assert result["sessions_changed"] == nonzero
    assert result["variant"]["n_trades"] == sum(len(v) for v in var.values())


def test_trade_on_ungraded_date_raises():
    base = _baseline()
    short = {s: v[:-1] for s, v in SESSIONS.items()}
    with pytest.raises(ValueError, match="same sessions"):
        _run(_spec(), base, base, short)


def test_pairing_is_much_tighter_than_independent_intervals():
    rng = random.Random(7)
    days = _days(250)
    base, var = {}, {}
    for s in ("AAA", "BBB"):
        shared = [rng.gauss(0.0, 2.0) for _ in days]
        base[s] = [_trade(d, r, s) for d, r in zip(days, shared)]
        var[s] = [
            _trade(d, r + 0.05 + rng.gauss(0.0, 0.1), s)
            for d, r in zip(days, shared)
        ]
    sessions = {s: [d.isoformat() for d in days] for s in base}
    spec = _spec(start=days[0], end=days[-1], bootstrap_replicates=0)
    diff = _run(spec, base, var, sessions).summary["difference"]
    paired_width = diff["ci_high_r"] - diff["ci_low_r"]

    def est(trades_by_symbol):
        obs = [
            Observation(t.pnl / 10.0, cluster=t.entry_time.date().isoformat())
            for ts in trades_by_symbol.values()
            for t in ts
        ]
        return estimate_mean(obs, confidence_level=0.95)

    se = math.hypot(est(base).se_cluster_r, est(var).se_cluster_r)
    independent_width = 2 * 1.96 * se
    assert paired_width < 0.5 * independent_width


def test_guards():
    base = _baseline()
    calls = []
    with pytest.raises(HoldoutViolation):
        run_paired_comparison(
            _spec(symbols=("QQQ",), end=date(2025, 2, 1)),
            lambda s: calls.append(s), lambda s: calls.append(s), lambda s: [],
        )
    with pytest.raises(ConfirmationNotSupported):
        run_paired_comparison(
            _spec(evidence_role="confirmation"),
            lambda s: calls.append(s), lambda s: calls.append(s), lambda s: [],
        )
    assert calls == []
    with pytest.raises(ValueError):
        _spec(symbols=())
    with pytest.raises(ValueError):
        _spec(start=date(2022, 1, 1))
    del base


def test_failure_in_either_rule_drops_the_symbol_and_is_inconclusive():
    base = _baseline()

    def run_variant(symbol):
        if symbol == "BBB":
            raise RuntimeError("bad variant")
        return SimpleNamespace(trades=base[symbol])

    result = run_paired_comparison(
        _spec(), lambda s: SimpleNamespace(trades=base[s]), run_variant,
        lambda s: SESSIONS[s],
    )
    assert result.state == "inconclusive"
    assert result.errors == {"BBB": "RuntimeError: bad variant"}
    assert result.summary["n_symbol_sessions"] == 40


def test_json_safe_and_deterministic():
    base = _baseline()
    var = {s: ts[::2] for s, ts in base.items()}
    a = _run(_spec(), base, var).summary
    assert forbidden_payload_path(a) is None
    json.dumps(a, allow_nan=False)
    assert json.dumps(a, sort_keys=True) == json.dumps(
        _run(_spec(), base, var).summary, sort_keys=True
    )
    empty = {s: [] for s in base}
    e = _run(_spec(), empty, empty).summary
    assert forbidden_payload_path(e) is None
    json.dumps(e, allow_nan=False)


def test_recording_links_baseline_and_variant_per_symbol(tmp_path):
    base = _baseline()
    results_b = {s: _performance_result(s, ts) for s, ts in base.items()}
    results_v = {s: _performance_result(s, ts[::2]) for s, ts in base.items()}
    spec = _spec(trial=TrialTag(
        programme="p", phase="exploration", family="enhancement", label="x"
    ))
    result = run_paired_comparison(
        spec, lambda s: results_b[s], lambda s: results_v[s], lambda s: SESSIONS[s]
    )
    path = tmp_path / "r.sqlite3"
    store = SqliteResearchStore(path)
    try:
        sinks = {
            s: BacktestRunSink(
                store, strategy_id=f"p_{s}", symbol=s, data_snapshot_id="snap",
                execution_model_version=6, methodology_version="paired/v1",
                repo_root=".", starting_equity=100_000,
                retention=RetentionProfile.DIAGNOSTIC,
            )
            for s in spec.symbols
        }
        rulesets = {
            name: {s: SimpleNamespace(model_dump=lambda **_: {"n": name}) for s in spec.symbols}
            for name in ("baseline", "variant")
        }
        definition = paired_definition(
            spec, baseline_ruleset_hash="a" * 64, variant_ruleset_hash="b" * 64
        )
        ids = [
            record_paired_comparison(
                store, result, sinks=sinks,
                baseline_ruleset_for=rulesets["baseline"],
                variant_ruleset_for=rulesets["variant"], definition=definition,
            )
            for _ in range(2)
        ]
        first = _read_experiment(path, ids[0])
        second = _read_experiment(path, ids[1])
        with __import__("sqlite3").connect(path) as conn:
            candidates = conn.execute(
                "SELECT fold_index, candidate_id FROM research_experiment_runs "
                "WHERE experiment_id=? ORDER BY fold_index, candidate_id", (ids[0],)
            ).fetchall()
    finally:
        store.close()
    assert first["result"].get("recording_errors") is None
    assert candidates == [(0, "baseline"), (0, "variant"), (1, "baseline"), (1, "variant")]
    assert sorted(first["runs"]) == sorted(second["runs"])
    assert all(r[2] == SegmentRole.TRAIN.value for r in first["runs"])
    assert first["definition"]["kind"] == "paired_comparison"
    assert first["result"]["kind"] == "paired_comparison"


def test_cli_smoke_with_two_rulesets(tmp_path, capsys):
    from scripts.run_paired_comparison import main

    idx = pd.date_range("2024-01-02 09:30", "2024-01-31 15:55", freq="5min", tz=NY)
    idx = idx[
        (idx.time >= pd.Timestamp("09:30").time())
        & (idx.time <= pd.Timestamp("15:55").time())
        & (idx.dayofweek < 5)
    ]
    price = 100 + (pd.Series(range(len(idx)), index=idx) % 7) * 0.1
    pd.DataFrame(
        {"open": price, "high": price + 0.3, "low": price - 0.3,
         "close": price + 0.05, "volume": 1000.0},
        index=idx,
    ).to_parquet(tmp_path / "ZZZ.parquet")
    cfg = yaml.safe_load(open("vibe/rulesets/orb_production.yaml", encoding="utf-8"))
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    cfg["strategy"]["orb_duration_minutes"] = 30
    var_path = tmp_path / "var.yaml"
    var_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    code = main([
        "--title", "smoke", "--baseline-ruleset", str(base_path),
        "--variant-ruleset", str(var_path), "--lever", "orb_minutes",
        "--symbols", "ZZZ", "--start", "2024-01-16", "--end", "2024-01-31",
        "--evidence-role", "platform_validation", "--data-dir", str(tmp_path),
        "--warmup-sessions", "5", "--bootstrap-replicates", "50", "--no-record",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Outcome vs" in out
