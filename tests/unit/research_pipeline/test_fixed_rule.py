"""S3: fixed-rule study runner (library + CLI)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.integrity import (
    EngineIntegrityCategory,
    EngineIntegrityReport,
    EngineIntegrityStatus,
)
from vibe.common.models.trade import Trade
from vibe.research_pipeline.contracts import RetentionProfile, SegmentRole
from vibe.research_pipeline.fixed_rule import (
    ConfirmationNotSupported,
    FixedRuleSpec,
    histogram,
    record_fixed_rule_study,
    run_fixed_rule_study,
    study_definition,
)
from vibe.research_pipeline.holdout import HoldoutViolation
from vibe.research_pipeline.inference import Observation, estimate_mean
from vibe.research_pipeline.publishing import forbidden_payload_path
from vibe.research_pipeline.recording import BacktestRunSink
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.trials import TrialTag

NY = ZoneInfo("America/New_York")


def _trade(day: date, pnl: float, *, reason="TARGET", mae=-0.5, mfe=1.0, symbol="AAA"):
    stamp = datetime(day.year, day.month, day.day, 10, 0, tzinfo=NY)
    return Trade(
        symbol=symbol, side="buy", quantity=1, entry_price=100.0,
        exit_price=100.0 + pnl, entry_time=stamp, exit_time=stamp,
        initial_risk=10.0, commission=0.0, exit_reason=reason,
        mae_r=mae, mfe_r=mfe,
    )


def _spec(**kw):
    base = dict(
        title="t", ruleset_path="r.yaml", symbols=("BBB", "AAA", "AAA"),
        start=date(2020, 1, 2), end=date(2021, 12, 31),
        evidence_role="exploration", bootstrap_replicates=200,
    )
    base.update(kw)
    return FixedRuleSpec(**base)


TRADES = {
    "AAA": [
        _trade(date(2020, 1, 2), 10, symbol="AAA"),             # +1R
        _trade(date(2020, 1, 3), -10, reason="STOP", symbol="AAA"),  # -1R
        _trade(date(2021, 1, 4), 50, symbol="AAA"),             # +5R
    ],
    "BBB": [
        _trade(date(2020, 1, 2), 5, symbol="BBB"),              # +0.5R
        _trade(date(2021, 1, 4), -40, reason="STOP", symbol="BBB"),  # -4R, underflow
        _trade(date(2021, 1, 5), 80, reason="EOD", symbol="BBB"),    # +8R, overflow
    ],
}


def _run(spec, trades=TRADES):
    return run_fixed_rule_study(
        spec, lambda s: SimpleNamespace(trades=trades[s])
    )


def test_pooled_statistics_match_estimate_mean_with_shared_clusters():
    spec = _spec()
    result = _run(spec)
    obs = [
        Observation(t.pnl / 10.0, cluster=t.entry_time.date().isoformat())
        for s in ("AAA", "BBB")
        for t in TRADES[s]
    ]
    expected = estimate_mean(
        obs, confidence_level=0.95, bootstrap_replicates=200,
        bootstrap_seed=spec.bootstrap_seed,
    )
    assert result.summary["pooled"] == expected.to_dict()
    assert result.summary["pooled"]["n_clusters"] == 4
    assert spec.symbols == ("AAA", "BBB")


def test_breakdowns_match_hand_counts():
    s = _run(_spec()).summary
    assert s["by_year"]["2020"]["n"] == 3 and s["by_year"]["2021"]["n"] == 3
    assert s["by_year"]["2020"]["mean_r"] == pytest.approx((1 - 1 + 0.5) / 3)
    assert s["by_symbol"]["AAA"]["n"] == 3
    assert s["by_symbol"]["BBB"]["mean_r"] == pytest.approx((0.5 - 4 + 8) / 3)
    assert s["symbols_positive"] == 2
    assert s["by_exit_reason"]["STOP"]["n"] == 2
    assert s["by_exit_reason"]["STOP"]["share"] == pytest.approx(2 / 6)
    assert s["by_exit_reason"]["EOD"]["mean_r"] == pytest.approx(8.0)
    h = s["r_histogram"]
    assert h["counts"][0] == 1 and h["counts"][-1] == 1  # -4R and +8R
    assert sum(h["counts"]) == 6
    assert s["excursions"]["n_with_values"] == 6
    assert s["excursions"]["share_mfe_ge_1r"] == 1.0
    assert s["per_day_mean_r"]["n_days"] == 4  # Jan 2 / Jan 3 2020, Jan 4 / Jan 5 2021


def test_histogram_edges_are_fixed():
    h = histogram([])
    assert h["edges"][0] == -3.0 and h["edges"][-1] == 6.0
    assert len(h["counts"]) == len(h["edges"]) + 1


def test_failing_symbol_makes_study_inconclusive_but_others_summarised():
    def run_symbol(symbol):
        if symbol == "BBB":
            raise RuntimeError("no data")
        return SimpleNamespace(trades=TRADES[symbol])

    result = run_fixed_rule_study(_spec(), run_symbol)
    assert result.state == "inconclusive"
    assert result.errors == {"BBB": "RuntimeError: no data"}
    assert result.per_symbol["BBB"] is None
    assert result.summary["pooled"]["n"] == 3
    assert result.summary["errors"] == {"BBB": "RuntimeError: no data"}


def test_holdout_is_checked_before_any_engine_run():
    calls = []
    spec = _spec(symbols=("QQQ",), end=date(2025, 2, 1))
    with pytest.raises(HoldoutViolation):
        run_fixed_rule_study(spec, lambda s: calls.append(s))
    assert calls == []


def test_confirmation_runs_are_refused():
    calls = []
    with pytest.raises(ConfirmationNotSupported, match="multi-symbol registration"):
        run_fixed_rule_study(
            _spec(evidence_role="confirmation"), lambda s: calls.append(s)
        )
    assert calls == []


def test_spec_validation():
    with pytest.raises(ValueError):
        _spec(symbols=())
    with pytest.raises(ValueError):
        _spec(start=date(2022, 1, 1))
    with pytest.raises(ValueError):
        _spec(evidence_role="bogus")
    with pytest.raises(ValueError):
        _spec(confidence_level=1.0)
    with pytest.raises(ValueError):
        _spec(min_edge_r=-0.1)


def test_result_json_is_aggregate_only_and_json_safe():
    s = _run(_spec()).summary
    assert forbidden_payload_path(s) is None
    json.dumps(s, allow_nan=False)
    empty = _run(_spec(), {"AAA": [], "BBB": []}).summary
    assert forbidden_payload_path(empty) is None
    json.dumps(empty, allow_nan=False)
    assert empty["outcome"] == "underpowered"


def test_same_inputs_and_seed_give_identical_result_json():
    a = json.dumps(_run(_spec()).summary, sort_keys=True)
    b = json.dumps(_run(_spec()).summary, sort_keys=True)
    assert a == b


def _performance_result(symbol, trades):
    first = trades[0].entry_time
    last = trades[-1].exit_time
    curve = [(first, 100_000.0)]
    equity = 100_000.0
    for t in trades:
        equity += t.pnl
        curve.append((t.exit_time.replace(hour=16), equity))
    result = PerformanceAnalyzer.analyze(
        trades, curve, 100_000, symbol, first, last, "fixed_rule", "1",
        execution_diagnostics={"min_cash": 99_000, "max_gross_exposure_ratio": 0.01},
    )
    result.engine_integrity_report = EngineIntegrityReport(
        status=EngineIntegrityStatus.PASS, engine_version=1,
        metric_calculation_version=2,
        checked_categories=tuple(EngineIntegrityCategory), findings=(),
    )
    return result


def test_recording_links_runs_by_role_and_dedupes_on_repeat(tmp_path):
    spec = _spec(trial=TrialTag(
        programme="p", phase="exploration", family="baseline", label="base"
    ))
    run_results = {
        s: _performance_result(s, TRADES[s]) for s in ("AAA", "BBB")
    }
    result = run_fixed_rule_study(spec, lambda s: run_results[s])
    store = SqliteResearchStore(tmp_path / "r.sqlite3")
    try:
        sinks = {
            s: BacktestRunSink(
                store, strategy_id=f"fr_{s}", symbol=s, data_snapshot_id="snap",
                execution_model_version=6, methodology_version="fixed_rule/v1",
                repo_root=".", starting_equity=100_000,
                retention=RetentionProfile.DIAGNOSTIC,
            )
            for s in spec.symbols
        }
        rulesets = {s: SimpleNamespace(model_dump=lambda **_: {"s": 1}) for s in spec.symbols}
        definition = study_definition(spec, effective_ruleset_hash="a" * 64)
        assert definition["trial"]["programme"] == "p"
        ids = []
        for _ in range(2):
            ids.append(record_fixed_rule_study(
                store, result, sinks=sinks, ruleset_for=rulesets,
                definition=definition,
            ))
        first = _read_experiment(tmp_path / "r.sqlite3", ids[0])
        second = _read_experiment(tmp_path / "r.sqlite3", ids[1])
    finally:
        store.close()
    assert first["result"].get("recording_errors") is None, first["result"]["recording_errors"]
    runs_a = sorted((r[0], r[1]) for r in first["runs"])
    runs_b = sorted((r[0], r[1]) for r in second["runs"])
    assert [i for i, _ in runs_a] == [0, 1]
    assert runs_a == runs_b
    assert all(r[2] == SegmentRole.TRAIN.value for r in first["runs"])
    assert first["state"] in ("completed", "inconclusive")
    assert first["definition"]["evidence_role"] == "exploration"
    assert first["result"]["kind"] == "fixed_rule"


def _read_experiment(path, experiment_id):
    with sqlite3.connect(path) as conn:
        state, definition, result = conn.execute(
            "SELECT state, definition_json, result_json FROM research_experiments "
            "WHERE experiment_id=?", (experiment_id,),
        ).fetchone()
        runs = conn.execute(
            "SELECT fold_index, run_id, segment_role FROM research_experiment_runs "
            "WHERE experiment_id=?", (experiment_id,),
        ).fetchall()
    return {
        "state": state, "definition": json.loads(definition),
        "result": json.loads(result), "runs": runs,
    }


def test_cli_smoke_without_recording(tmp_path, capsys):
    from scripts.run_fixed_rule_study import main

    idx = pd.date_range(
        "2024-01-02 09:30", "2024-01-31 15:55", freq="5min", tz=NY
    )
    idx = idx[(idx.time >= pd.Timestamp("09:30").time()) & (idx.time <= pd.Timestamp("15:55").time())]
    idx = idx[idx.dayofweek < 5]
    price = 100 + (pd.Series(range(len(idx)), index=idx) % 7) * 0.1
    pd.DataFrame(
        {"open": price, "high": price + 0.3, "low": price - 0.3,
         "close": price + 0.05, "volume": 1000.0},
        index=idx,
    ).to_parquet(tmp_path / "ZZZ.parquet")
    code = main([
        "--title", "smoke", "--symbols", "ZZZ", "--start", "2024-01-16",
        "--end", "2024-01-31", "--evidence-role", "platform_validation",
        "--data-dir", str(tmp_path), "--warmup-sessions", "5",
        "--bootstrap-replicates", "50", "--no-record",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Outcome vs" in out


def test_per_day_basis_is_labelled():
    assert (
        _run(_spec()).summary["per_day_mean_r"]["basis"]
        == "days_with_at_least_one_trade"
    )


def test_recording_failure_finishes_experiment_inconclusive(tmp_path):
    class BrokenSink:
        failures = ["boom"]

        def record(self, *a, **k):
            return None

    spec = _spec()
    result = _run(spec)
    assert result.state == "completed"
    store = SqliteResearchStore(tmp_path / "r.sqlite3")
    try:
        experiment_id = record_fixed_rule_study(
            store, result, sinks={s: BrokenSink() for s in spec.symbols},
            ruleset_for={s: None for s in spec.symbols}, definition=study_definition(spec, effective_ruleset_hash="a" * 64),
        )
        stored = _read_experiment(tmp_path / "r.sqlite3", experiment_id)
    finally:
        store.close()
    assert stored["state"] == "inconclusive"
    assert stored["result"]["recording_errors"] == ["boom", "boom"]
