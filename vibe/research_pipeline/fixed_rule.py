"""Fixed-rule study: run one unchanged ruleset over a symbol set and summarise it.

The study answers "does this rule have an edge of at least ``min_edge_r``?" with
a clustered confidence interval on mean net R, not with a pass/fail threshold.
Only aggregates are put in ``result_json``; trade rows stay in the run store.

Recording maps evidence roles onto the existing ``SegmentRole`` enum without a
migration: ``exploration`` and ``platform_validation`` runs are linked as
``TRAIN``; ``confirmation`` runs would be linked as ``TEST``. Confirmation runs
need a multi-symbol registration that does not exist yet, so they are refused.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping, Optional, Sequence

from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.holdout import load_lock
from vibe.research_pipeline.inference import (
    MeanEstimate,
    Observation,
    capped_share,
    classify_outcome,
    cost_breakeven,
    distribution_robustness,
    dollar_summary,
    estimate_mean,
    minimum_detectable_effect,
    power_table,
    session_r_totals,
    trade_net_r,
    trade_session_date,
)
from vibe.research_pipeline.trials import TrialTag
from vibe.research_pipeline.provenance import apply_provenance
from vibe.research_pipeline.universe import run_universe, static_declared

SCHEMA_VERSION = 1
EVIDENCE_ROLES = ("exploration", "confirmation", "platform_validation")
HISTOGRAM_LOW = -3.0
HISTOGRAM_HIGH = 6.0
HISTOGRAM_STEP = 0.25


class ConfirmationNotSupported(NotImplementedError):
    pass


@dataclass(frozen=True)
class FixedRuleSpec:
    title: str
    ruleset_path: str
    symbols: tuple[str, ...]
    start: date
    end: date
    evidence_role: str
    min_edge_r: float = 0.05
    confidence_level: float = 0.95
    bootstrap_replicates: int = 1000
    bootstrap_seed: int = 20261031
    warmup_sessions: int = 60
    capital: float = 100_000.0
    trial: Optional[TrialTag] = None

    def __post_init__(self) -> None:
        symbols = tuple(sorted({s.strip().upper() for s in self.symbols if s.strip()}))
        if not symbols:
            raise ValueError("symbols must not be empty")
        object.__setattr__(self, "symbols", symbols)
        if self.start > self.end:
            raise ValueError(f"start ({self.start}) is after end ({self.end})")
        if self.evidence_role not in EVIDENCE_ROLES:
            raise ValueError(f"evidence_role must be one of {EVIDENCE_ROLES}")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must be in (0, 1)")
        if self.min_edge_r < 0:
            raise ValueError("min_edge_r must be >= 0")

    @property
    def segment_role(self) -> SegmentRole:
        if self.evidence_role == "confirmation":
            return SegmentRole.TEST
        return SegmentRole.TRAIN


@dataclass(frozen=True)
class FixedRuleResult:
    spec: FixedRuleSpec
    per_symbol: Mapping[str, Any]
    errors: Mapping[str, str]
    summary: Mapping[str, Any]

    @property
    def state(self) -> str:
        if self.errors or self.summary["pooled"]["n_clusters"] < 2:
            return "inconclusive"
        return "completed"


def check_holdout(spec: FixedRuleSpec) -> None:
    lock = load_lock()
    for symbol in spec.symbols:
        if lock.covers(symbol):
            lock.assert_within_dev(symbol=symbol, start=spec.start, end=spec.end)


def run_fixed_rule_study(
    spec: FixedRuleSpec, run_symbol: Callable[[str], Any]
) -> FixedRuleResult:
    if spec.evidence_role == "confirmation":
        raise ConfirmationNotSupported(
            "confirmation runs need a multi-symbol registration; not implemented yet"
        )
    check_holdout(spec)
    universe = static_declared(
        spec.symbols, rationale=f"Fixed-rule study: {spec.title}"
    )
    outcome = run_universe(universe, run_symbol, continue_on_error=True)
    per_symbol = {o.symbol: (o.result if o.ok else None) for o in outcome.outcomes}
    errors = {o.symbol: o.error for o in outcome.outcomes if not o.ok}
    trades_by_symbol = {
        s: list(getattr(r, "trades", ()) or ())
        for s, r in per_symbol.items()
        if r is not None
    }
    return FixedRuleResult(
        spec=spec,
        per_symbol=per_symbol,
        errors=errors,
        summary=summarise(
            spec, trades_by_symbol, errors, capped=capped_share(per_symbol.values())
        ),
    )


def _observations(trades: Sequence[Any]) -> list[Observation]:
    out = []
    for trade in trades:
        value = trade_net_r(trade)
        if value is not None:
            out.append(Observation(value, cluster=trade_session_date(trade)))
    return out


def _estimate(spec: FixedRuleSpec, trades: Sequence[Any], *, bootstrap: bool) -> MeanEstimate:
    return estimate_mean(
        _observations(trades),
        confidence_level=spec.confidence_level,
        bootstrap_replicates=spec.bootstrap_replicates if bootstrap else 0,
        bootstrap_seed=spec.bootstrap_seed if bootstrap else None,
    )


def _brief(est: MeanEstimate) -> dict[str, Any]:
    return {
        "n": est.n,
        "mean_r": est.mean_r,
        "ci_low_r": est.ci_low_r,
        "ci_high_r": est.ci_high_r,
    }


def histogram(values: Sequence[float]) -> dict[str, Any]:
    n_bins = int(round((HISTOGRAM_HIGH - HISTOGRAM_LOW) / HISTOGRAM_STEP))
    edges = [HISTOGRAM_LOW + i * HISTOGRAM_STEP for i in range(n_bins + 1)]
    counts = [0] * (n_bins + 2)  # [underflow, bins..., overflow]
    for value in values:
        if value < HISTOGRAM_LOW:
            counts[0] += 1
        elif value >= HISTOGRAM_HIGH:
            counts[-1] += 1
        else:
            counts[1 + int((value - HISTOGRAM_LOW) // HISTOGRAM_STEP)] += 1
    return {"edges": edges, "counts": counts}


def _per_day(trades_by_symbol: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    by_date: dict[str, float] = defaultdict(float)
    for (_symbol, day), value in session_r_totals(trades_by_symbol).items():
        by_date[day] += value
    values = list(by_date.values())
    n = len(values)
    se = (
        statistics.stdev(values) / math.sqrt(n) if n >= 2 else None
    )
    return {
        "basis": "days_with_at_least_one_trade",
        "n_days": n,
        "mean_r": statistics.fmean(values) if values else None,
        "se_r": se,
    }


def _excursions(trades: Sequence[Any]) -> dict[str, Any]:
    pairs = [
        (t.mae_r, t.mfe_r)
        for t in trades
        if getattr(t, "mae_r", None) is not None and getattr(t, "mfe_r", None) is not None
    ]
    n = len(pairs)
    if n == 0:
        return {
            "n_with_values": 0, "mean_mae_r": None, "mean_mfe_r": None,
            "share_mfe_ge_1r": None, "share_mfe_ge_2r": None,
        }
    return {
        "n_with_values": n,
        "mean_mae_r": statistics.fmean(p[0] for p in pairs),
        "mean_mfe_r": statistics.fmean(p[1] for p in pairs),
        "share_mfe_ge_1r": sum(p[1] >= 1.0 for p in pairs) / n,
        "share_mfe_ge_2r": sum(p[1] >= 2.0 for p in pairs) / n,
    }


def summarise(
    spec: FixedRuleSpec,
    trades_by_symbol: Mapping[str, Sequence[Any]],
    errors: Mapping[str, str],
    *,
    capped: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    all_trades = [t for s in sorted(trades_by_symbol) for t in trades_by_symbol[s]]
    observations = _observations(all_trades)
    pooled = _estimate(spec, all_trades, bootstrap=True)
    outcome = classify_outcome(
        pooled.ci_low_r, pooled.ci_high_r, min_edge_r=spec.min_edge_r
    )
    alpha = 1.0 - spec.confidence_level
    mde = (
        minimum_detectable_effect(pooled.se_cluster_r, alpha=alpha)
        if pooled.se_cluster_r
        else None
    )
    years = max((spec.end - spec.start).days / 365.25, 1e-9)
    trades_per_year = pooled.n / years
    r_per_year = (
        pooled.mean_r * trades_per_year if pooled.mean_r is not None else None
    )

    by_symbol = {}
    for symbol in sorted(trades_by_symbol):
        est = _estimate(spec, trades_by_symbol[symbol], bootstrap=False)
        by_symbol[symbol] = {
            **_brief(est),
            "outcome": classify_outcome(
                est.ci_low_r, est.ci_high_r, min_edge_r=spec.min_edge_r
            ).value,
        }

    by_year_trades: dict[str, list[Any]] = defaultdict(list)
    for trade in all_trades:
        by_year_trades[trade_session_date(trade)[:4]].append(trade)
    by_year = {
        year: _brief(_estimate(spec, by_year_trades[year], bootstrap=False))
        for year in sorted(by_year_trades)
    }

    reason_values: dict[str, list[float]] = defaultdict(list)
    for trade in all_trades:
        value = trade_net_r(trade)
        if value is not None:
            reason_values[str(getattr(trade, "exit_reason", None) or "UNKNOWN")].append(value)
    total = sum(len(v) for v in reason_values.values())
    by_exit_reason = {
        reason: {
            "n": len(values),
            "share": len(values) / total,
            "mean_r": statistics.fmean(values),
        }
        for reason, values in sorted(reason_values.items())
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "fixed_rule",
        "evidence_role": spec.evidence_role,
        "pooled": pooled.to_dict(),
        "outcome": outcome.value,
        "min_edge_r": spec.min_edge_r,
        "mde_r": mde,
        "per_day_mean_r": _per_day(trades_by_symbol),
        "trades_per_year": trades_per_year,
        "r_per_year": r_per_year,
        "cost": cost_breakeven(all_trades),
        "by_symbol": by_symbol,
        "by_year": by_year,
        "by_exit_reason": by_exit_reason,
        "r_histogram": histogram(
            [v for v in (trade_net_r(t) for t in all_trades) if v is not None]
        ),
        "excursions": _excursions(all_trades),
        "robustness": distribution_robustness(observations),
        "power": power_table(
            pooled.se_cluster_r,
            alpha=alpha,
            min_edge_r=spec.min_edge_r,
            mean=pooled.mean_r,
        ),
        "dollars": {
            **dollar_summary(
                trades_by_symbol,
                capital=spec.capital,
                years=years,
                confidence_level=spec.confidence_level,
            ),
            "sizing": dict(capped) if capped is not None else None,
        },
        "symbols_positive": sum(
            1 for v in by_symbol.values() if v["mean_r"] is not None and v["mean_r"] > 0
        ),
        "errors": dict(sorted(errors.items())),
    }


def study_definition(
    spec: FixedRuleSpec,
    *,
    effective_ruleset_hash: str,
    dataset_id_manifest_hash: Optional[str] = None,
    execution_identity: Optional[Mapping[str, Any]] = None,
    dataset_pin: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "fixed_rule",
        "evidence_role": spec.evidence_role,
        "ruleset_path": spec.ruleset_path,
        "effective_ruleset_sha256": effective_ruleset_hash,
        "symbols": list(spec.symbols),
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "warmup_sessions": spec.warmup_sessions,
        "capital": spec.capital,
        "min_edge_r": spec.min_edge_r,
        "confidence_level": spec.confidence_level,
        "bootstrap_replicates": spec.bootstrap_replicates,
        "bootstrap_seed": spec.bootstrap_seed,
        "trial": spec.trial.model_dump(mode="json") if spec.trial else None,
    }
    return apply_provenance(
        definition,
        execution_identity=execution_identity,
        dataset_pin=dataset_pin,
        dataset_id_manifest_hash=dataset_id_manifest_hash,
    )


def _enqueue_member_summary(store: Any, run_id: str, errors: list[str]) -> None:
    """Queue the member run's run_summary; a store without support is skipped."""
    enqueue = getattr(store, "enqueue_member_run_summary", None)
    if enqueue is None:
        return
    if enqueue(run_id) is None:
        errors.append(f"{run_id}: run_summary not enqueued (no local evidence)")


def record_fixed_rule_study(
    store: Any,
    result: FixedRuleResult,
    *,
    sinks: Mapping[str, Any],
    ruleset_for: Mapping[str, Any],
    definition: Mapping[str, Any],
) -> str:
    """Record per-symbol runs, link them to a new experiment, and finish it.

    ``sinks`` maps each symbol to a ``BacktestRunSink`` bound to ``store``;
    ``ruleset_for`` maps each symbol to the effective ruleset it ran.
    A duplicate fingerprint links the run that already exists.
    """
    spec = result.spec
    experiment_id = store.start_experiment(spec.title, dict(definition))
    recording_errors: list[str] = []
    for index, symbol in enumerate(spec.symbols):
        run = result.per_symbol.get(symbol)
        if run is None:
            continue
        sink = sinks[symbol]
        outcome = sink.record(
            run,
            ruleset=ruleset_for[symbol],
            parameters={},
            segment_role=spec.segment_role,
        )
        if outcome is None:
            recording_errors.extend(sink.failures[-1:] or [f"{symbol}: not recorded"])
            continue
        store.link_experiment_run(
            experiment_id, index, "rule", spec.segment_role, outcome.run_id
        )
        _enqueue_member_summary(store, outcome.run_id, recording_errors)
    payload = dict(result.summary)
    if recording_errors:
        payload["recording_errors"] = recording_errors
    state = "inconclusive" if recording_errors else result.state
    store.finish_experiment(experiment_id, state, payload)
    return experiment_id
