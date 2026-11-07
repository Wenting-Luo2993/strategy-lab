"""Reproducibility provenance: execution identity, dataset pin, member run
summaries, orphan report, external trial ledger."""

from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.research_pipeline.contracts import RetentionProfile
from vibe.research_pipeline.dataset_pin import dataset_pin, default_cache_path
from vibe.research_pipeline.fixed_rule import (
    record_fixed_rule_study,
    run_fixed_rule_study,
    study_definition,
)
from vibe.research_pipeline.paired import paired_definition
from vibe.research_pipeline.provenance import (
    definition_dataset_id_manifest_hash,
    definition_dataset_pin,
    definition_execution_identity,
    study_definition_hash,
)
from vibe.research_pipeline.publishing import (
    PublicationKind,
    StoreError,
    validate_publication_payload,
)
from vibe.research_pipeline.recording import BacktestRunSink
from vibe.research_pipeline.storage import run_summary_reports
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.trial_ledger import (
    combine_ledger,
    default_ledger_path,
    load_external_trials,
)
from vibe.research_pipeline.trials import TrialTag

from test_fixed_rule import (
    TRADES,
    _performance_result,
    _spec,
)

RESTING = ExecutionRealismConfig.realistic(entry_fill_policy="resting_stop").identity()


# --- dataset pin -------------------------------------------------------------


def _write(path, payload: bytes):
    path.write_bytes(payload)
    return path


def test_dataset_pin_hash_is_stable_ignores_mtime_and_detects_drift(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write(data / "AAA.parquet", b"aaa-bars")
    _write(data / "BBB.parquet", b"bbb-bars")
    cache = tmp_path / "db" / "cache.json"

    first = dataset_pin(data, ["BBB", "AAA", "AAA"], cache_path=cache)
    assert [f["path"] for f in first["dataset_files"]] == ["AAA.parquet", "BBB.parquet"]
    assert first["dataset_pin_schema_version"] == 1
    assert len(first["dataset_manifest_hash"]) == 64
    assert cache.is_file()

    stat = (data / "AAA.parquet").stat()
    os.utime(data / "AAA.parquet", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    touched = dataset_pin(data, ["AAA", "BBB"], cache_path=cache)
    assert touched["dataset_manifest_hash"] == first["dataset_manifest_hash"]

    _write(data / "AAA.parquet", b"aaa-bars+1")
    drifted = dataset_pin(data, ["AAA", "BBB"], cache_path=cache)
    assert drifted["dataset_manifest_hash"] != first["dataset_manifest_hash"]


def test_dataset_pin_uses_cache_keyed_by_size_and_mtime(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _write(data / "AAA.parquet", b"aaa")
    cache = tmp_path / "cache.json"
    first = dataset_pin(data, ["AAA"], cache_path=cache)

    import vibe.research_pipeline.dataset_pin as module

    def boom(*_a, **_k):
        raise AssertionError("content re-hashed despite cache hit")

    monkeypatch.setattr(module, "hash_file", boom)
    assert dataset_pin(data, ["AAA"], cache_path=cache) == first


def test_dataset_pin_records_missing_symbols(tmp_path):
    _write(tmp_path / "AAA.parquet", b"a")
    pin = dataset_pin(tmp_path, ["AAA", "ZZZ"])
    assert pin["dataset_missing_symbols"] == ["ZZZ"]
    assert [f["path"] for f in pin["dataset_files"]] == ["AAA.parquet"]


def test_default_cache_path_is_next_to_research_db(tmp_path):
    assert default_cache_path(tmp_path / "x" / "r.sqlite3").parent == tmp_path / "x"


# --- definitions -------------------------------------------------------------


def test_study_definition_carries_execution_identity_and_pin(tmp_path):
    _write(tmp_path / "AAA.parquet", b"a")
    _write(tmp_path / "BBB.parquet", b"b")
    pin = dataset_pin(tmp_path, ["AAA", "BBB"])
    definition = study_definition(
        _spec(), effective_ruleset_hash="a" * 64,
        execution_identity=RESTING, dataset_pin=pin,
        dataset_id_manifest_hash="m" * 64,
    )
    assert definition["execution_identity"]["entry_fill_policy"] == "resting_stop"
    assert definition["dataset_manifest_hash"] == pin["dataset_manifest_hash"]
    assert definition["dataset_files"] == pin["dataset_files"]
    assert definition_dataset_pin(definition) == pin["dataset_manifest_hash"]
    assert definition_dataset_id_manifest_hash(definition) == "m" * 64
    assert definition["definition_sha256"] == study_definition_hash(definition)
    assert definition_execution_identity(definition)["source"] == "recorded"

    default = study_definition(
        _spec(), effective_ruleset_hash="a" * 64,
        execution_identity=ExecutionRealismConfig.realistic().identity(),
        dataset_pin=pin,
    )
    assert default["definition_sha256"] != definition["definition_sha256"]


def test_definition_without_provenance_is_unchanged_legacy_shape():
    legacy = study_definition(_spec(), effective_ruleset_hash="a" * 64)
    assert "provenance_schema_version" not in legacy
    assert "definition_sha256" not in legacy
    assert "execution_identity" not in legacy


def test_legacy_definitions_read_back_with_legacy_defaults():
    legacy = {"kind": "fixed_rule", "dataset_manifest_hash": "f" * 64}
    identity = definition_execution_identity(legacy, execution_model_version=7)
    assert identity == {
        "entry_fill_policy": "at_next_bar_open",
        "source": "legacy_default",
        "execution_model_version": 7,
        "min_risk_bps": 5.0,
        "take_profit_trade_through_ticks": 1,
    }
    assert definition_execution_identity(legacy, execution_model_version=6)[
        "min_risk_bps"
    ] == 0.0
    assert definition_dataset_pin(legacy) is None
    assert definition_dataset_id_manifest_hash(legacy) == "f" * 64


def test_paired_definition_carries_execution_identity_and_pin():
    from test_paired import _spec as paired_spec

    definition = paired_definition(
        paired_spec(), baseline_ruleset_hash="a" * 64, variant_ruleset_hash="b" * 64,
        execution_identity=RESTING,
        dataset_pin={"dataset_manifest_hash": "d" * 64, "dataset_files": []},
    )
    assert definition["execution_identity"]["entry_fill_policy"] == "resting_stop"
    assert definition["dataset_manifest_hash"] == "d" * 64
    assert definition["definition_sha256"] == study_definition_hash(definition)


# --- run sink + member run_summary -----------------------------------------


def _sinks(store, symbols, execution_identity):
    return {
        s: BacktestRunSink(
            store, strategy_id=f"fr_{s}", symbol=s, data_snapshot_id="snap",
            execution_model_version=7, methodology_version="fixed_rule/v1",
            repo_root=".", starting_equity=100_000,
            retention=RetentionProfile.DIAGNOSTIC,
            execution_identity=execution_identity,
        )
        for s in symbols
    }


def _record(tmp_path, execution_identity, name="r.sqlite3"):
    spec = _spec(trial=TrialTag(
        programme="p", phase="exploration", family="baseline", label="base"
    ))
    run_results = {s: _performance_result(s, TRADES[s]) for s in ("AAA", "BBB")}
    result = run_fixed_rule_study(spec, lambda s: run_results[s])
    path = tmp_path / name
    store = SqliteResearchStore(path)
    try:
        rulesets = {s: SimpleNamespace(model_dump=lambda **_: {"s": 1}) for s in spec.symbols}
        definition = study_definition(
            spec, effective_ruleset_hash="a" * 64,
            execution_identity=execution_identity,
        )
        experiment_id = record_fixed_rule_study(
            store, result, sinks=_sinks(store, spec.symbols, execution_identity),
            ruleset_for=rulesets, definition=definition,
        )
    finally:
        store.close()
    return path, experiment_id


def _summaries(path):
    with sqlite3.connect(path) as conn:
        return [
            (key, aggregate_id, json.loads(payload))
            for key, aggregate_id, payload in conn.execute(
                "SELECT idempotency_key, aggregate_id, payload_json "
                "FROM publication_outbox WHERE aggregate_type='run_summary' "
                "ORDER BY idempotency_key"
            )
        ]


def test_recorded_member_runs_get_run_summary_with_execution_identity(tmp_path):
    path, experiment_id = _record(tmp_path, RESTING)
    with sqlite3.connect(path) as conn:
        result = json.loads(conn.execute(
            "SELECT result_json FROM research_experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()[0])
        members = {r[0] for r in conn.execute(
            "SELECT run_id FROM research_experiment_runs WHERE experiment_id=?",
            (experiment_id,),
        )}
    assert result.get("recording_errors") is None
    summaries = _summaries(path)
    assert {aggregate for _, aggregate, _ in summaries} == members
    for key, run_id, payload in summaries:
        assert key == f"run:{run_id}:validating"
        assert payload["identity"]["execution_identity"]["entry_fill_policy"] == "resting_stop"
        validate_publication_payload(PublicationKind.RUN_SUMMARY, payload)

    with run_summary_reports.connect_read_only(path) as conn:
        assert run_summary_reports.member_run_summary_status(conn) == []
        assert run_summary_reports.orphan_run_summaries(conn)["by_kind"][
            "no_local_membership"
        ] == 0


def test_execution_identity_changes_run_fingerprint(tmp_path):
    def fingerprints(identity, name):
        path, _ = _record(tmp_path, identity, name)
        with sqlite3.connect(path) as conn:
            return {r[0] for r in conn.execute("SELECT fingerprint FROM runs")}

    default = ExecutionRealismConfig.realistic().identity()
    assert fingerprints(RESTING, "a.sqlite3").isdisjoint(
        fingerprints(default, "b.sqlite3")
    )


def test_backfill_member_run_summaries_for_legacy_recorded_runs(tmp_path):
    path, experiment_id = _record(tmp_path, None)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM publication_outbox WHERE aggregate_type='run_summary'")
    with run_summary_reports.connect_read_only(path) as conn:
        pending = run_summary_reports.member_run_summary_status(conn, experiment_id)
    assert len(pending) == 2 and all(p["eligible"] for p in pending)

    store = SqliteResearchStore(path)
    try:
        keys = store.backfill_member_run_summaries(experiment_id)
        again = store.backfill_member_run_summaries(experiment_id)
    finally:
        store.close()
    assert len(keys) == 2 and again == []
    for _, _, payload in _summaries(path):
        assert "execution_identity" not in payload["identity"]
        validate_publication_payload(PublicationKind.RUN_SUMMARY, payload)


def test_orphan_report_groups_non_member_summaries(tmp_path):
    path, experiment_id = _record(tmp_path, RESTING)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "DELETE FROM research_experiment_runs WHERE experiment_id=? "
            "AND fold_index=0", (experiment_id,),
        )
        conn.execute("DELETE FROM publication_outbox WHERE aggregate_type='experiment_membership'")
    with run_summary_reports.connect_read_only(path) as conn:
        report = run_summary_reports.orphan_run_summaries(conn)
    assert report["by_kind"] == {"no_local_membership": 1, "membership_not_in_outbox": 1}
    assert {g["source"] for g in report["groups"]} <= {
        "fr_AAA / fixed_rule/v1", "fr_BBB / fixed_rule/v1"
    }


def test_publication_validator_rejects_unknown_execution_identity_key():
    payload_identity = {"execution_identity": {**RESTING, "bogus": 1}}
    from vibe.research_pipeline.publishing import _validate_execution_identity

    _validate_execution_identity(RESTING)
    with pytest.raises(StoreError):
        _validate_execution_identity(payload_identity["execution_identity"])


def test_publish_research_dry_runs_do_not_touch_db(tmp_path, capsys):
    from scripts.publish_research import _parser, _read_only_report

    path, _ = _record(tmp_path, RESTING)
    before = path.read_bytes()

    def run(*argv):
        args = _parser().parse_args(["--db", str(path), *argv])
        assert _read_only_report(args, path) == 0
        return json.loads(capsys.readouterr().out)

    assert run("backfill-member-runs")["eligible"] == 0
    assert run("orphan-run-summaries")["total"] == 0
    assert path.read_bytes() == before


# --- external trial ledger ---------------------------------------------------


def test_repo_trial_ledger_loads_and_labels_sources():
    entries = load_external_trials(
        default_ledger_path("orb_edge_2026"), programme="orb_edge_2026"
    )
    by_id = {e.id: e for e in entries}
    assert by_id["pre_rebuild_baseline"].trials == 1
    assert by_id["pre_rebuild_conditions"].trials == 6
    assert by_id["october_same_data_legacy"].runs == {
        "nested_walk_forward": 172, "sensitivity_runner": 65, "exp073": 3,
    }
    assert "same-data legacy" in by_id["october_same_data_legacy"].source


def test_combine_ledger_skips_entries_already_in_db(tmp_path):
    ledger = tmp_path / "l.yaml"
    ledger.write_text(
        "programme: p\nentries:\n"
        "  - {id: a, source: pre, trials: 2, experiment_ids: [E1]}\n"
        "  - {id: b, source: legacy, trials: 5}\n"
        "  - {id: c, source: ctx, trials: 9, counts_toward_total: false}\n",
        encoding="utf-8",
    )
    entries = load_external_trials(ledger, programme="p")
    combined = combine_ledger(
        {"programme": "p", "total_trials": 3, "experiment_ids": ["E1"]}, entries
    )
    assert combined["external_trials"] == 5
    assert combined["total_trials"] == 8
    assert [line["counted"] for line in combined["external"]] == [False, True, False]
    fresh = combine_ledger({"programme": "p", "total_trials": 0, "experiment_ids": []}, entries)
    assert fresh["total_trials"] == 7
    with pytest.raises(ValueError):
        load_external_trials(ledger, programme="other")
