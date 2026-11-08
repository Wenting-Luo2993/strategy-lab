"""V6 null calibration and V7 planted-edge drivers.

Both run the real ``BacktestEngine`` on synthetic tick-walk sessions with
commission and slippage set to zero, so that on a martingale the expected net
R is zero and any bias is the platform's.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, time
from pathlib import Path
from statistics import NormalDist, fmean
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

import yaml

from vibe.research_pipeline.inference import (
    Observation,
    estimate_mean,
    trade_net_r,
    trade_session_date,
)
from vibe.research_pipeline.validation_suite.synthetic import tick_walk_sessions

SYMBOL = "SYN"
NY = ZoneInfo("America/New_York")
TRUTH_SEED_OFFSET = 10_000_000


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_study_trades(
    *,
    ruleset_path: str,
    seed: int,
    n_sessions: int,
    up_probability_with_trend: float,
    capital: float = 1_000_000.0,
) -> list[Any]:
    """Generate one synthetic dataset and return the engine's trades.

    Commission is zero (``CommissionModel.zero``), exit slippage is zero, and
    entry slippage is zero (``slippage_ticks=0``); fills otherwise follow the
    realistic execution settings.
    """
    from vibe.backtester.core.commission import CommissionModel
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.backtester.core.exit_slippage import FixedTickExitSlippage
    from vibe.backtester.core.execution_realism import ExecutionRealismConfig
    from vibe.common.ruleset.models import StrategyRuleSet

    bars = tick_walk_sessions(
        n_sessions=n_sessions,
        seed=seed,
        up_probability_with_trend=up_probability_with_trend,
    )
    cfg = yaml.safe_load(Path(ruleset_path).read_text(encoding="utf-8"))
    cfg = json.loads(json.dumps(cfg, default=str))
    cfg["instruments"]["symbols"] = [SYMBOL]
    ruleset = StrategyRuleSet(**cfg)
    first = bars.index[0].date()
    last = bars.index[-1].date()
    with tempfile.TemporaryDirectory() as tmp:
        bars.to_parquet(Path(tmp) / f"{SYMBOL}.parquet")
        engine = BacktestEngine(
            ruleset=ruleset,
            data_dir=tmp,
            initial_capital=capital,
            slippage_ticks=0,
            execution_realism=ExecutionRealismConfig.realistic(
                commission_model=CommissionModel.zero(),
                exit_slippage=FixedTickExitSlippage.zero(),
            ),
        )
        result = engine.run(
            SYMBOL,
            datetime.combine(first, time(0, 0), tzinfo=NY),
            datetime.combine(last, time(23, 59, 59), tzinfo=NY),
        )
    return list(result.trades)


def estimate_trades(trades, confidence_level: float):
    return estimate_mean(_observations(trades), confidence_level=confidence_level)


def _observations(trades, cluster_prefix: str = "") -> list[Observation]:
    obs = []
    for trade in trades:
        value = trade_net_r(trade)
        if value is not None:
            obs.append(
                Observation(value, cluster=cluster_prefix + trade_session_date(trade))
            )
    return obs


def study_observations(
    ruleset_path: str,
    seed: int,
    n_sessions: int,
    up_probability_with_trend: float,
) -> list[tuple[float, str]]:
    """One synthetic study as (net R, session date) pairs.

    Module-level and returning plain tuples so it can run in a worker process.
    """
    trades = run_study_trades(
        ruleset_path=ruleset_path, seed=seed, n_sessions=n_sessions,
        up_probability_with_trend=up_probability_with_trend,
    )
    return [(o.value_r, o.cluster) for o in _observations(trades)]


def default_workers() -> int:
    """Half the logical CPUs, at least one."""
    return max(1, (os.cpu_count() or 2) // 2)


def _run_studies(
    tasks: Sequence[tuple[str, int, int, float]],
    workers: int,
    progress: Callable[[int, int], None] | None,
) -> list[list[tuple[float, str]]]:
    """Run ``study_observations`` for each task, returning results in task order.

    Each task carries its own seed, so the results do not depend on how many
    workers ran them.
    """
    total = len(tasks)
    if workers <= 1 or total <= 1:
        results = []
        for done, task in enumerate(tasks, 1):
            results.append(study_observations(*task))
            if progress:
                progress(done, total)
        return results
    results: list[Any] = [None] * total
    with ProcessPoolExecutor(max_workers=min(workers, total)) as pool:
        futures = {pool.submit(study_observations, *task): k for k, task in enumerate(tasks)}
        for done, future in enumerate(as_completed(futures), 1):
            results[futures[future]] = future.result()
            if progress:
                progress(done, total)
    return results


def _estimate(pairs: Sequence[tuple[float, str]], confidence_level: float, prefix: str = ""):
    return estimate_mean(
        [Observation(v, cluster=prefix + c) for v, c in pairs],
        confidence_level=confidence_level,
    )


def run_null_calibration(
    n_studies: int,
    n_sessions: int,
    seed: int,
    ruleset_path: str,
    confidence_level: float = 0.95,
    *,
    workers: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    false_positives = covered = degenerate = 0
    means, counts = [], []
    studies = _run_studies(
        [(ruleset_path, seed + i, n_sessions, 0.5) for i in range(n_studies)],
        workers, progress,
    )
    for pairs in studies:
        est = _estimate(pairs, confidence_level)
        counts.append(est.n)
        if est.ci_low_r is None or est.ci_high_r is None:
            degenerate += 1
            continue
        means.append(est.mean_r)
        false_positives += est.ci_low_r > 0
        covered += est.ci_low_r <= 0 <= est.ci_high_r
    valid = n_studies - degenerate
    lo, hi = wilson_interval(false_positives, valid, confidence_level)
    return {
        "kind": "null_calibration",
        "n_studies": n_studies,
        "n_sessions": n_sessions,
        "seed": seed,
        "confidence_level": confidence_level,
        "degenerate_studies": degenerate,
        "false_positive_count": false_positives,
        "false_positive_rate": false_positives / valid if valid else None,
        "false_positive_wilson": [lo, hi],
        "expected_false_positive_rate": (1 - confidence_level) / 2,
        "coverage_rate": covered / valid if valid else None,
        "mean_of_means_r": fmean(means) if means else None,
        "mean_trades_per_study": fmean(counts) if counts else 0.0,
    }


def truth_chunk_sizes(truth_sessions: int, chunk_sessions: int) -> list[int]:
    """Split the truth run into independent chunks of at most ``chunk_sessions``."""
    if truth_sessions <= 0 or chunk_sessions <= 0:
        raise ValueError("truth_sessions and chunk_sessions must be positive")
    full, rest = divmod(truth_sessions, chunk_sessions)
    return [chunk_sessions] * full + ([rest] if rest else [])


def run_planted_edge(
    n_studies: int,
    n_sessions: int,
    seed: int,
    ruleset_path: str,
    up_probability_with_trend: float = 0.502,
    truth_sessions: int = 20000,
    confidence_level: float = 0.95,
    *,
    truth_chunk_sessions: int | None = None,
    workers: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    # The truth is measured over many short independent runs rather than one
    # long one. Synthetic sessions carry no state between days, so pooling
    # chunks estimates the same quantity, and engine cost grows faster than
    # linearly with run length. Chunk k uses seed + TRUTH_SEED_OFFSET + k and
    # its session dates are prefixed with k so clusters never merge across
    # chunks that share a calendar.
    chunks = truth_chunk_sizes(truth_sessions, truth_chunk_sessions or n_sessions)
    truth_tasks = [
        (ruleset_path, seed + TRUTH_SEED_OFFSET + k, size, up_probability_with_trend)
        for k, size in enumerate(chunks)
    ]
    study_tasks = [
        (ruleset_path, seed + i, n_sessions, up_probability_with_trend)
        for i in range(n_studies)
    ]
    results = _run_studies(truth_tasks + study_tasks, workers, progress)
    truth_obs = [
        Observation(v, cluster=f"{k}:{c}")
        for k, pairs in enumerate(results[: len(chunks)])
        for v, c in pairs
    ]
    truth_est = estimate_mean(truth_obs, confidence_level=confidence_level)
    truth = truth_est.mean_r
    detected = covered = degenerate = 0
    ses = []
    for pairs in results[len(chunks):]:
        est = _estimate(pairs, confidence_level)
        if est.ci_low_r is None or est.ci_high_r is None or not est.se_cluster_r:
            degenerate += 1
            continue
        ses.append(est.se_cluster_r)
        detected += est.ci_low_r > 0
        covered += truth is not None and est.ci_low_r <= truth <= est.ci_high_r
    valid = n_studies - degenerate
    mean_se = fmean(ses) if ses else None
    predicted = None
    if truth is not None and mean_se:
        z = NormalDist().inv_cdf(1 - (1 - confidence_level) / 2)
        predicted = 1 - NormalDist().cdf(z - truth / mean_se)
    return {
        "kind": "planted_edge",
        "n_studies": n_studies,
        "n_sessions": n_sessions,
        "seed": seed,
        "confidence_level": confidence_level,
        "up_probability_with_trend": up_probability_with_trend,
        "truth_sessions": truth_sessions,
        "truth_chunks": len(chunks),
        "truth_chunk_sessions": chunks[0],
        "truth_r": truth,
        "truth_se_r": truth_est.se_cluster_r,
        "truth_trades": truth_est.n,
        "degenerate_studies": degenerate,
        "mean_se_r": mean_se,
        "predicted_power": predicted,
        "detection_rate": detected / valid if valid else None,
        "coverage_of_truth": covered / valid if valid else None,
    }
