"""Immutable engine-integrity evidence owned by the simulation kernel."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

import pandas as pd

from vibe.backtester.analysis.metrics import METRIC_CALCULATION_VERSION
from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
from vibe.backtester.core.reconciliation import ReconciliationReport

ENGINE_INTEGRITY_VERSION = 1


class EngineIntegrityStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class EngineIntegrityCategory(str, Enum):
    METRIC_MATHEMATICS = "metric_mathematics"
    CROSS_METRIC_CONSISTENCY = "cross_metric_consistency"
    EQUITY_SERIES_CONSISTENCY = "equity_series_consistency"
    ACCOUNTING = "accounting"
    EXECUTION_MECHANICS = "execution_mechanics"
    CASH_LEVERAGE_POSITION = "cash_leverage_position"
    SESSION_END_STATE = "session_end_state"
    EXECUTION_MODEL_IDENTITY = "execution_model_identity"


@dataclass(frozen=True)
class EngineIntegrityFinding:
    check_id: str
    category: EngineIntegrityCategory
    passed: bool
    observed: Any
    expected: Any
    residual: float | None = None
    applicable: bool = True
    context: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class EngineIntegrityReport:
    status: EngineIntegrityStatus
    engine_version: int
    metric_calculation_version: int
    checked_categories: tuple[EngineIntegrityCategory, ...]
    findings: tuple[EngineIntegrityFinding, ...]

    @property
    def passed(self) -> bool:
        return self.status is EngineIntegrityStatus.PASS


def _finding(
    check_id: str,
    category: EngineIntegrityCategory,
    passed: bool,
    observed: Any,
    expected: Any,
    *,
    residual: float | None = None,
    applicable: bool = True,
    context: dict[str, Any] | None = None,
) -> EngineIntegrityFinding:
    return EngineIntegrityFinding(
        check_id=check_id,
        category=category,
        passed=bool(passed),
        observed=observed,
        expected=expected,
        residual=residual,
        applicable=applicable,
        context=tuple(sorted((context or {}).items())),
    )


def _close(left: float, right: float, *, absolute: float = 1e-8) -> bool:
    return math.isclose(left, right, rel_tol=1e-7, abs_tol=absolute)


def _session_sharpe(curve: pd.Series) -> tuple[float, int]:
    if curve.empty or not isinstance(curve.index, pd.DatetimeIndex):
        return 0.0, 0
    closes = curve.groupby(curve.index.normalize()).last()
    returns = (closes / closes.shift(1) - 1).iloc[1:].tolist()
    if len(returns) < 2:
        return 0.0, len(returns)
    deviation = statistics.stdev(returns)
    return (
        statistics.mean(returns) / deviation * math.sqrt(252)
        if deviation > 0
        else 0.0,
        len(returns),
    )


def build_engine_integrity_report(
    result: Any,
    reconciliation: ReconciliationReport,
    *,
    max_gross_leverage: float,
    enforce_buying_power: bool,
    open_position_count: int,
    pending_order_count: int,
) -> EngineIntegrityReport:
    """Evaluate only simulator-owned facts; never infer economic reliability."""

    findings: list[EngineIntegrityFinding] = []
    overall = result.overall
    equity = result.equity
    diagnostics = result.execution_diagnostics

    finite_metrics = {
        "win_rate": overall.win_rate,
        "expectancy_r": overall.expectancy_r,
        "gross_pnl": overall.gross_pnl,
        "total_costs": overall.total_costs,
        "total_pnl": overall.total_pnl,
        "sharpe_ratio": equity.sharpe_ratio,
        "max_drawdown": equity.max_drawdown,
    }
    non_finite = tuple(
        key for key, value in finite_metrics.items() if not math.isfinite(float(value))
    )
    findings.append(_finding(
        "ENG-MATH-001", EngineIntegrityCategory.METRIC_MATHEMATICS,
        not non_finite, non_finite, "all published engine metrics are finite",
    ))
    findings.append(_finding(
        "ENG-MATH-002", EngineIntegrityCategory.METRIC_MATHEMATICS,
        0.0 <= overall.win_rate <= 1.0, overall.win_rate, "0 <= win_rate <= 1",
    ))
    findings.append(_finding(
        "ENG-MATH-003", EngineIntegrityCategory.METRIC_MATHEMATICS,
        -1.0 <= equity.max_drawdown <= 0.0,
        equity.max_drawdown, "-1 <= max_drawdown <= 0",
    ))

    ledger_count = len(result.trades)
    findings.append(_finding(
        "ENG-CROSS-001", EngineIntegrityCategory.CROSS_METRIC_CONSISTENCY,
        overall.n_trades == ledger_count,
        {"metric": overall.n_trades, "ledger": ledger_count},
        "metric trade count equals ledger length",
    ))
    partition = (
        overall.winning_trades
        + overall.losing_trades
        + overall.breakeven_trades
    )
    findings.append(_finding(
        "ENG-CROSS-002", EngineIntegrityCategory.CROSS_METRIC_CONSISTENCY,
        partition == overall.r_sample_size, partition, overall.r_sample_size,
        residual=float(partition - overall.r_sample_size),
    ))
    dropped_expected = overall.n_trades - overall.r_sample_size
    findings.append(_finding(
        "ENG-CROSS-003", EngineIntegrityCategory.CROSS_METRIC_CONSISTENCY,
        overall.dropped_trade_count == dropped_expected,
        overall.dropped_trade_count, dropped_expected,
        residual=float(overall.dropped_trade_count - dropped_expected),
    ))
    r_values = list(overall.r_multiples)
    expected_expectancy = statistics.mean(r_values) if r_values else 0.0
    findings.append(_finding(
        "ENG-CROSS-004", EngineIntegrityCategory.CROSS_METRIC_CONSISTENCY,
        len(r_values) == overall.r_sample_size
        and all(math.isfinite(value) for value in r_values),
        len(r_values), overall.r_sample_size,
    ))
    findings.append(_finding(
        "ENG-CROSS-005", EngineIntegrityCategory.CROSS_METRIC_CONSISTENCY,
        _close(overall.expectancy_r, expected_expectancy),
        overall.expectancy_r, expected_expectancy,
        residual=overall.expectancy_r - expected_expectancy,
    ))

    curve = equity.equity_curve
    curve_valid = (
        isinstance(curve, pd.Series)
        and (curve.empty or isinstance(curve.index, pd.DatetimeIndex))
        and not getattr(curve.index, "hasnans", False)
        and getattr(curve.index, "is_monotonic_increasing", True)
        and not getattr(curve.index, "has_duplicates", False)
        and all(math.isfinite(float(point)) and float(point) > 0 for point in curve)
    )
    findings.append(_finding(
        "ENG-EQUITY-001", EngineIntegrityCategory.EQUITY_SERIES_CONSISTENCY,
        curve_valid, {"points": len(curve)}, "positive finite ordered unique equity series",
    ))
    if curve_valid:
        session_count = (
            len(curve.groupby(curve.index.normalize()).last()) if not curve.empty else 0
        )
        findings.append(_finding(
            "ENG-EQUITY-002", EngineIntegrityCategory.EQUITY_SERIES_CONSISTENCY,
            equity.n_sessions == session_count,
            equity.n_sessions, session_count,
            residual=float(equity.n_sessions - session_count),
        ))
        expected_sharpe, return_count = _session_sharpe(curve)
        findings.append(_finding(
            "ENG-EQUITY-003", EngineIntegrityCategory.EQUITY_SERIES_CONSISTENCY,
            _close(equity.sharpe_ratio, expected_sharpe),
            equity.sharpe_ratio, expected_sharpe,
            residual=equity.sharpe_ratio - expected_sharpe,
            context={"session_return_count": return_count},
        ))

    net_expected = overall.gross_pnl - overall.total_costs
    findings.append(_finding(
        "ENG-ACC-001", EngineIntegrityCategory.ACCOUNTING,
        _close(overall.total_pnl, net_expected, absolute=0.01),
        overall.total_pnl, net_expected,
        residual=overall.total_pnl - net_expected,
    ))
    for item in reconciliation.findings:
        findings.append(_finding(
            f"ENG-ACC-{item.identity.upper()}",
            EngineIntegrityCategory.ACCOUNTING,
            item.passed,
            item.detail,
            "identity reconciles",
            residual=item.residual,
            applicable=item.applicable,
        ))

    findings.append(_finding(
        "ENG-EXEC-001", EngineIntegrityCategory.EXECUTION_MECHANICS,
        diagnostics.get("total_costs") is not None
        and _close(float(diagnostics["total_costs"]), overall.total_costs, absolute=0.01),
        diagnostics.get("total_costs"), overall.total_costs,
        residual=(
            None if diagnostics.get("total_costs") is None
            else float(diagnostics["total_costs"]) - overall.total_costs
        ),
    ))
    min_cash = diagnostics.get("min_cash")
    cash_ok = min_cash is not None and (
        not enforce_buying_power or float(min_cash) >= -0.01
    )
    findings.append(_finding(
        "ENG-RISK-001", EngineIntegrityCategory.CASH_LEVERAGE_POSITION,
        cash_ok, min_cash,
        "min_cash >= -0.01 when buying power is enforced",
        applicable=enforce_buying_power,
        context={"enforce_buying_power": enforce_buying_power},
    ))
    leverage = diagnostics.get("max_funded_entry_leverage")
    leverage_ok = leverage is not None and (
        not enforce_buying_power
        or float(leverage) <= float(max_gross_leverage) + 1e-9
    )
    findings.append(_finding(
        "ENG-RISK-002", EngineIntegrityCategory.CASH_LEVERAGE_POSITION,
        leverage_ok, leverage, max_gross_leverage,
        residual=(
            None if leverage is None else float(leverage) - float(max_gross_leverage)
        ),
        applicable=enforce_buying_power,
    ))
    findings.append(_finding(
        "ENG-SESSION-001", EngineIntegrityCategory.SESSION_END_STATE,
        open_position_count == 0, open_position_count, 0,
        residual=float(open_position_count),
    ))
    findings.append(_finding(
        "ENG-SESSION-002", EngineIntegrityCategory.SESSION_END_STATE,
        pending_order_count == 0, pending_order_count, 0,
        residual=float(pending_order_count),
    ))
    version = diagnostics.get("execution_model_version")
    findings.append(_finding(
        "ENG-VERSION-001", EngineIntegrityCategory.EXECUTION_MODEL_IDENTITY,
        version == float(EXECUTION_MODEL_VERSION),
        version, EXECUTION_MODEL_VERSION,
    ))
    findings.append(_finding(
        "ENG-VERSION-002", EngineIntegrityCategory.EXECUTION_MODEL_IDENTITY,
        overall.calculation_version == METRIC_CALCULATION_VERSION
        and equity.calculation_version == METRIC_CALCULATION_VERSION,
        {
            "overall": overall.calculation_version,
            "equity": equity.calculation_version,
        },
        METRIC_CALCULATION_VERSION,
    ))

    categories = tuple(EngineIntegrityCategory)
    failed = [item for item in findings if item.applicable and not item.passed]
    return EngineIntegrityReport(
        status=(
            EngineIntegrityStatus.FAIL if failed else EngineIntegrityStatus.PASS
        ),
        engine_version=ENGINE_INTEGRITY_VERSION,
        metric_calculation_version=METRIC_CALCULATION_VERSION,
        checked_categories=categories,
        findings=tuple(findings),
    )


def failed_findings(
    report: EngineIntegrityReport,
) -> tuple[EngineIntegrityFinding, ...]:
    return tuple(
        finding
        for finding in report.findings
        if finding.applicable and not finding.passed
    )
