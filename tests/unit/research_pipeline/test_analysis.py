"""Focused tests for the adapter-neutral research analysis contract."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vibe.research_pipeline.analysis import (
    AnalysisCostExceeded,
    AnalysisPolicy,
    AnalysisQueryError,
    SqliteAnalysisAdapter,
)
from vibe.research_pipeline.contracts import (
    RetentionProfile,
    RunEvidence,
    ValidationCategory,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.validation import ValidationReport, ValidationScope


def _fingerprint(index: int, *, version: int = 2) -> RunFingerprint:
    return RunFingerprint(
        strategy_id="orb",
        ruleset_content_sha256=f"{index:x}".rjust(64, "0"),
        parameters={
            "opening_range_minutes": 5 + index * 5,
            "risk": {"reward_multiple": 1.5 + index / 10},
        },
        universe_hash="b" * 64,
        split_manifest_hash="c" * 64,
        data_snapshot_id="qqq-approved-2026",
        feature_set_version=3,
        execution_model_version=6,
        metric_calculation_version=version,
        code_commit="1234567abcdef",
        code_dirty=False,
    )


def _report(target: RunState = RunState.COMPLETED) -> ValidationReport:
    return ValidationReport(
        profile_name="analysis",
        scope=ValidationScope.RUN,
        target_state=target,
        findings=(),
        checked_categories=tuple(ValidationCategory),
        registry_hash_at_execution="d" * 64,
        current_registry_hash="d" * 64,
        leakage_passed=True,
    )


def _evidence() -> RunEvidence:
    return RunEvidence(
        trade_count=2,
        trade_ledger_sha256="1" * 64,
        equity_curve_sha256="2" * 64,
        equity_points=20,
        first_session=date(2026, 1, 2),
        last_session=date(2026, 1, 5),
        starting_equity=100_000,
        ending_equity=100_200,
        gross_pnl=250,
        total_costs=50,
        net_pnl=200,
        max_observed_leverage=1,
        min_cash=50_000,
        retention_profile=RetentionProfile.SUMMARY,
    )


def _terminal_run(
    store: SqliteResearchStore,
    index: int,
    *,
    version: int = 2,
    methodology: str = "research-store-v1",
) -> str:
    run_id = store.register_run(
        fingerprint=_fingerprint(index, version=version),
        methodology_version=methodology,
        lineage_id=f"family-{index % 2}",
    )
    token = store.start_run(run_id, owner="analysis-test", lease_seconds=60)
    store.record_metrics(
        run_id,
        {"expectancy_r": index / 10, "net_pnl": index * 100},
        calculation_version=version,
    )
    store.record_evidence(run_id, _evidence())
    store.transition(run_id, target=RunState.VALIDATING, lease_token=token)
    store.finalize_validation(run_id, _report())
    return run_id


@pytest.fixture
def analysis_db(tmp_path: Path) -> Path:
    path = tmp_path / "research.sqlite3"
    store = SqliteResearchStore(path)
    _terminal_run(store, 1)
    _terminal_run(store, 2)
    _terminal_run(store, 3, methodology="legacy-uncontrolled")
    store.close()
    return path


def _query(**updates):
    query = {
        "population": {
            "includeLegacy": False,
            "trustStates": ["current_validated"],
        },
        "filters": {"op": "and", "children": []},
        "screeningColumns": [
            "identity.display_alias",
            "parameter.opening_range_minutes",
            "metric.expectancy_r",
        ],
        "groupBy": [],
        "measures": [],
        "orderBy": [],
        "chart": {"kind": "table"},
    }
    query.update(updates)
    return query


def _recorded_run(store: SqliteResearchStore, index: int) -> str:
    """A run as the backtest recorder leaves it: metrics and evidence, no verdict."""
    run_id = store.register_run(
        fingerprint=_fingerprint(index),
        methodology_version="research-store-v1",
        lineage_id=f"family-{index % 2}",
    )
    token = store.start_run(run_id, owner="analysis-test", lease_seconds=60)
    store.record_metrics(
        run_id,
        {"expectancy_r": index / 10, "net_pnl": index * 100},
        calculation_version=2,
    )
    store.record_evidence(run_id, _evidence())
    store.transition(run_id, target=RunState.VALIDATING, lease_token=token)
    return run_id


def test_runs_awaiting_validation_are_analysable_but_never_pass_as_validated(
    tmp_path: Path,
):
    path = tmp_path / "research.sqlite3"
    store = SqliteResearchStore(path)
    _recorded_run(store, 7)
    store.close()

    adapter = SqliteAnalysisAdapter(path)
    try:
        # The catalog is derived from rows, so a store holding only recorded
        # runs must still describe its fields -- otherwise the workspace is
        # dead no matter how much data was produced.
        catalog = {field["field"] for field in adapter.field_catalog()}
        assert "metric.expectancy_r" in catalog

        default = adapter.query(_query())
        assert default["rows"] == []

        attention = adapter.query(
            _query(
                population={
                    "includeLegacy": False,
                    "trustStates": ["validation_attention"],
                }
            )
        )
        assert [row["identity.display_alias"] for row in attention["rows"]] == [
            "RUN-000001"
        ]
    finally:
        adapter.close()


def test_field_catalog_and_screening_support_current_run_shape(analysis_db: Path):
    adapter = SqliteAnalysisAdapter(analysis_db)
    catalog = {field["field"]: field for field in adapter.field_catalog()}
    result = adapter.query(_query())

    assert catalog["parameter.opening_range_minutes"]["dataType"] == "integer"
    assert "mean" in catalog["metric.expectancy_r"]["aggregates"]
    assert catalog["trust.state"]["trustRestricted"] is True
    assert [row["identity.display_alias"] for row in result["rows"]] == [
        "RUN-000001",
        "RUN-000002",
    ]
    assert result["metadata"]["aggregated"] is False
    assert result["metadata"]["truncated"] is False
    adapter.close()


def test_nested_typed_filters_grouping_and_percentiles(analysis_db: Path):
    adapter = SqliteAnalysisAdapter(analysis_db)
    result = adapter.query(
        _query(
            filters={
                "op": "and",
                "children": [
                    {
                        "op": "gte",
                        "field": "parameter.opening_range_minutes",
                        "value": 10,
                    },
                    {
                        "op": "or",
                        "children": [
                            {
                                "op": "contains",
                                "field": "identity.strategy_id",
                                "value": "OR",
                            },
                            {
                                "op": "eq",
                                "field": "identity.strategy_id",
                                "value": "unused",
                            },
                        ],
                    },
                ],
            },
            groupBy=["identity.strategy_id", "dataset.snapshot_id"],
            measures=[
                {"field": "metric.expectancy_r", "aggregate": "mean"},
                {"field": "metric.net_pnl", "aggregate": "p90"},
                {"field": "identity.run_id", "aggregate": "count"},
            ],
            orderBy=[{"field": "mean:metric.expectancy_r", "direction": "desc"}],
        )
    )

    assert result["rows"] == [
        {
            "identity.strategy_id": "orb",
            "dataset.snapshot_id": "qqq-approved-2026",
            "mean:metric.expectancy_r": pytest.approx(0.15),
            "p90:metric.net_pnl": pytest.approx(190),
            "count:identity.run_id": 2,
            "analysis.run_count": 2,
        }
    ]
    assert result["metadata"]["aggregated"] is True
    adapter.close()


def test_legacy_population_requires_explicit_overlay(analysis_db: Path):
    adapter = SqliteAnalysisAdapter(analysis_db)
    default = adapter.query(_query())
    overlay = adapter.query(
        _query(
            population={
                "includeLegacy": True,
                "trustStates": ["current_validated", "legacy"],
            }
        )
    )

    assert len(default["rows"]) == 2
    assert len(overlay["rows"]) == 3
    adapter.close()


def test_cost_and_response_limits_are_explicit(analysis_db: Path):
    adapter = SqliteAnalysisAdapter(
        analysis_db,
        policy=AnalysisPolicy(max_groups=1, max_response_rows=1),
    )
    with pytest.raises(AnalysisCostExceeded, match="Estimated groups"):
        adapter.query(_query(groupBy=["parameter.opening_range_minutes"]))

    ungrouped = adapter.query(_query())
    assert len(ungrouped["rows"]) == 1
    assert ungrouped["metadata"]["truncated"] is True
    assert ungrouped["metadata"]["limits"]["maxResponseRows"] == 1
    adapter.close()


def test_invalid_fields_filter_shapes_and_mixed_metric_versions_are_rejected(
    analysis_db: Path,
):
    adapter = SqliteAnalysisAdapter(analysis_db)
    with pytest.raises(AnalysisQueryError, match="Unknown groupBy"):
        adapter.query(_query(groupBy=["parameter.not_published"]))
    with pytest.raises(AnalysisQueryError, match="array value"):
        adapter.query(
            _query(
                filters={
                    "op": "between",
                    "field": "metric.expectancy_r",
                    "value": 1,
                }
            )
        )
    adapter.close()

    store = SqliteResearchStore(analysis_db)
    _terminal_run(store, 4, version=3)
    store.close()
    adapter = SqliteAnalysisAdapter(analysis_db)
    with pytest.raises(AnalysisQueryError, match="incompatible calculation versions"):
        adapter.query(
            _query(
                measures=[{"field": "metric.expectancy_r", "aggregate": "mean"}]
            )
        )
    adapter.close()


def test_adapter_requires_one_read_source(tmp_path: Path):
    with pytest.raises(ValueError, match="exactly one"):
        SqliteAnalysisAdapter()
    with pytest.raises(ValueError, match="exactly one"):
        SqliteAnalysisAdapter(
            tmp_path / "missing.sqlite3",
            connection=object(),  # type: ignore[arg-type]
        )
