from __future__ import annotations

from datetime import datetime, timezone

from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.integrity import (
    EngineIntegrityStatus,
    build_engine_integrity_report,
)
from vibe.backtester.core.reconciliation import (
    ReconciliationFinding,
    ReconciliationReport,
)


def _result():
    return PerformanceAnalyzer.analyze(
        trades=[],
        equity_curve=[
            (datetime(2026, 1, 2, tzinfo=timezone.utc), 10_000.0),
            (datetime(2026, 1, 5, tzinfo=timezone.utc), 10_000.0),
        ],
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
        ruleset_name="test",
        ruleset_version="1",
        execution_diagnostics={
            "total_costs": 0.0,
            "min_cash": 10_000.0,
            "max_funded_entry_leverage": 0.0,
            "execution_model_version": 6.0,
        },
    )


def _reconciliation(*findings):
    return ReconciliationReport(list(findings) or [
        ReconciliationFinding("flat_at_end_equity", True, "ok", 0.0),
        ReconciliationFinding("per_fill_cash_delta", True, "ok", 0.0),
        ReconciliationFinding("entry_exit_quantity_parity", True, "ok", 0.0),
    ])


def test_clean_engine_report_passes_and_is_immutable():
    report = build_engine_integrity_report(
        _result(), _reconciliation(),
        max_gross_leverage=1.0,
        enforce_buying_power=True,
        open_position_count=0,
        pending_order_count=0,
    )
    assert report.status is EngineIntegrityStatus.PASS
    try:
        report.status = EngineIntegrityStatus.FAIL
    except Exception:
        pass
    else:
        raise AssertionError("EngineIntegrityReport must be immutable")


def test_each_accounting_identity_failure_fails_engine_integrity():
    for identity in (
        "flat_at_end_equity",
        "per_fill_cash_delta",
        "entry_exit_quantity_parity",
    ):
        report = build_engine_integrity_report(
            _result(),
            _reconciliation(
                ReconciliationFinding(identity, False, "mutated", 1.0)
            ),
            max_gross_leverage=1.0,
            enforce_buying_power=True,
            open_position_count=0,
            pending_order_count=0,
        )
        assert report.status is EngineIntegrityStatus.FAIL
        assert any(
            finding.check_id.endswith(identity.upper())
            and not finding.passed
            for finding in report.findings
        )
