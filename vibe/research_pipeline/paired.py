"""Paired comparison: a variant rule against the baseline on identical sessions.

The unit of analysis is the ``(symbol, session)``; the estimate is the mean
per-session difference in net R, clustered by session date. Pairing removes the
market noise both rules share, which is why it is far tighter than comparing two
separate expectancies. Evidence-role to ``SegmentRole`` mapping is the same as
in :mod:`vibe.research_pipeline.fixed_rule`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.fixed_rule import (
    EVIDENCE_ROLES,
    ConfirmationNotSupported,
    _estimate,
    check_holdout,
)
from vibe.research_pipeline.inference import (
    MeanEstimate,
    Observation,
    classify_outcome,
    estimate_mean,
    minimum_detectable_effect,
    paired_session_differences,
    session_r_totals,
)
from vibe.research_pipeline.trials import TrialTag
from vibe.research_pipeline.universe import run_universe, static_declared

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PairedSpec:
    title: str
    baseline_ruleset: Path | str
    variant_ruleset: Path | str
    lever: str
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
class PairedResult:
    spec: PairedSpec
    baseline: Mapping[str, Any]
    variant: Mapping[str, Any]
    errors: Mapping[str, str]
    summary: Mapping[str, Any]

    @property
    def state(self) -> str:
        if self.errors or self.summary["difference"]["n_clusters"] < 2:
            return "inconclusive"
        return "completed"


def _trades(results: Mapping[str, Any]) -> dict[str, list[Any]]:
    return {
        s: list(getattr(r, "trades", ()) or ()) for s, r in results.items()
    }


def run_paired_comparison(
    spec: PairedSpec,
    run_baseline: Callable[[str], Any],
    run_variant: Callable[[str], Any],
    sessions_for: Callable[[str], Sequence[str]],
) -> PairedResult:
    if spec.evidence_role == "confirmation":
        raise ConfirmationNotSupported(
            "confirmation runs need a multi-symbol registration; not implemented yet"
        )
    check_holdout(spec)
    universe = static_declared(
        spec.symbols, rationale=f"Paired comparison: {spec.title}"
    )
    outcome = run_universe(
        universe,
        lambda symbol: (run_baseline(symbol), run_variant(symbol)),
        continue_on_error=True,
    )
    baseline = {o.symbol: o.result[0] for o in outcome.outcomes if o.ok}
    variant = {o.symbol: o.result[1] for o in outcome.outcomes if o.ok}
    errors = {o.symbol: o.error for o in outcome.outcomes if not o.ok}
    sessions = {s: list(sessions_for(s)) for s in baseline}
    return PairedResult(
        spec=spec,
        baseline=baseline,
        variant=variant,
        errors=errors,
        summary=summarise_paired(spec, _trades(baseline), _trades(variant), sessions, errors),
    )


def _group_estimate(spec: PairedSpec, obs: Sequence[Observation]) -> dict[str, Any]:
    est = estimate_mean(obs, confidence_level=spec.confidence_level)
    return {
        "n_sessions": est.n,
        "mean_diff_r": est.mean_r,
        "ci_low_r": est.ci_low_r,
        "ci_high_r": est.ci_high_r,
    }


def _rule_block(spec: PairedSpec, trades_by_symbol: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    all_trades = [t for s in sorted(trades_by_symbol) for t in trades_by_symbol[s]]
    est: MeanEstimate = _estimate(spec, all_trades, bootstrap=False)
    years = max((spec.end - spec.start).days / 365.25, 1e-9)
    return {
        "per_trade": est.to_dict(),
        "n_trades": est.n,
        "trades_per_year": est.n / years,
    }


def summarise_paired(
    spec: PairedSpec,
    baseline_trades: Mapping[str, Sequence[Any]],
    variant_trades: Mapping[str, Sequence[Any]],
    sessions: Mapping[str, Sequence[str]],
    errors: Mapping[str, str],
) -> dict[str, Any]:
    keys = [(s, d) for s in sorted(sessions) for d in sorted(set(sessions[s]))]
    base_totals = session_r_totals(baseline_trades)
    var_totals = session_r_totals(variant_trades)
    observations = paired_session_differences(base_totals, var_totals, keys)
    diff = estimate_mean(
        observations,
        confidence_level=spec.confidence_level,
        bootstrap_replicates=spec.bootstrap_replicates,
        bootstrap_seed=spec.bootstrap_seed,
    )

    baseline_block = _rule_block(spec, baseline_trades)
    variant_block = _rule_block(spec, variant_trades)
    n_sessions = len(keys)
    min_edge_per_session = (
        spec.min_edge_r * baseline_block["n_trades"] / n_sessions if n_sessions else 0.0
    )
    outcome = classify_outcome(
        diff.ci_low_r, diff.ci_high_r, min_edge_r=min_edge_per_session
    )
    mde = (
        minimum_detectable_effect(diff.se_cluster_r, alpha=1.0 - spec.confidence_level)
        if diff.se_cluster_r
        else None
    )
    years = max((spec.end - spec.start).days / 365.25, 1e-9)

    by_year: dict[str, list[Observation]] = defaultdict(list)
    by_symbol: dict[str, list[Observation]] = defaultdict(list)
    for key, obs in zip(keys, observations):
        by_year[key[1][:4]].append(obs)
        by_symbol[key[0]].append(obs)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "paired_comparison",
        "lever": spec.lever,
        "evidence_role": spec.evidence_role,
        "n_symbol_sessions": n_sessions,
        "n_dates": len({d for _s, d in keys}),
        "difference": diff.to_dict(),
        "difference_per_year_r": (
            diff.mean_r * n_sessions / years if diff.mean_r is not None else None
        ),
        "min_edge_per_session_r": min_edge_per_session,
        "min_edge_r": spec.min_edge_r,
        "outcome": outcome.value,
        "mde_per_session_r": mde,
        "baseline": baseline_block,
        "variant": variant_block,
        "sessions_changed": sum(1 for o in observations if not math.isclose(o.value_r, 0.0, abs_tol=1e-12)),
        "by_year": {y: _group_estimate(spec, by_year[y]) for y in sorted(by_year)},
        "by_symbol": {s: _group_estimate(spec, by_symbol[s]) for s in sorted(by_symbol)},
        "errors": dict(sorted(errors.items())),
    }


def ruleset_set_hash(rulesets_by_symbol: Mapping[str, Any]) -> str:
    """One deterministic hash for the per-symbol rulesets of a candidate.

    Each symbol runs a copy of the ruleset with ``instruments.symbols``
    narrowed to itself, so every per-symbol hash differs. Hashing whichever
    symbol happened to be inserted first made the published hash depend on
    run order; this hashes the symbol-sorted mapping of per-symbol hashes
    instead, so it is independent of order and still changes if any symbol's
    effective configuration does. Empty input hashes to ``""``.
    """
    from vibe.research_pipeline.hashing import hash_object
    from vibe.research_pipeline.recording import hash_effective_ruleset

    if not rulesets_by_symbol:
        return ""
    return hash_object(
        {
            symbol: hash_effective_ruleset(ruleset)
            for symbol, ruleset in sorted(rulesets_by_symbol.items())
        }
    )


def paired_definition(
    spec: PairedSpec,
    *,
    baseline_ruleset_hash: str,
    variant_ruleset_hash: str,
    dataset_manifest_hash: Optional[str] = None,
) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "paired_comparison",
        "lever": spec.lever,
        "evidence_role": spec.evidence_role,
        "baseline_ruleset_path": str(spec.baseline_ruleset),
        "variant_ruleset_path": str(spec.variant_ruleset),
        "baseline_ruleset_sha256": baseline_ruleset_hash,
        "variant_ruleset_sha256": variant_ruleset_hash,
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
    if dataset_manifest_hash:
        definition["dataset_manifest_hash"] = dataset_manifest_hash
    return definition


def record_paired_comparison(
    store: Any,
    result: PairedResult,
    *,
    sinks: Mapping[str, Any],
    baseline_ruleset_for: Mapping[str, Any],
    variant_ruleset_for: Mapping[str, Any],
    definition: Mapping[str, Any],
) -> str:
    spec = result.spec
    experiment_id = store.start_experiment(spec.title, dict(definition))
    recording_errors: list[str] = []
    for index, symbol in enumerate(spec.symbols):
        if symbol not in result.baseline:
            continue
        for candidate, run, rulesets in (
            ("baseline", result.baseline[symbol], baseline_ruleset_for),
            ("variant", result.variant[symbol], variant_ruleset_for),
        ):
            sink = sinks[symbol]
            outcome = sink.record(
                run,
                ruleset=rulesets[symbol],
                parameters={"candidate": candidate},
                segment_role=spec.segment_role,
            )
            if outcome is None:
                recording_errors.extend(
                    sink.failures[-1:] or [f"{symbol} {candidate}: not recorded"]
                )
                continue
            store.link_experiment_run(
                experiment_id, index, candidate, spec.segment_role, outcome.run_id
            )
    payload = dict(result.summary)
    if recording_errors:
        payload["recording_errors"] = recording_errors
    state = "inconclusive" if recording_errors else result.state
    store.finish_experiment(experiment_id, state, payload)
    return experiment_id
