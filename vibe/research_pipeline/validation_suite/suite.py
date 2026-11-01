"""V6 null calibration and V7 planted-edge drivers.

Both run the real ``BacktestEngine`` on synthetic tick-walk sessions with
commission and slippage set to zero, so that on a martingale the expected net
R is zero and any bias is the platform's.
"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, time
from pathlib import Path
from statistics import NormalDist, fmean
from typing import Any
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
    obs = []
    for trade in trades:
        value = trade_net_r(trade)
        if value is not None:
            obs.append(Observation(value, cluster=trade_session_date(trade)))
    return estimate_mean(obs, confidence_level=confidence_level)


def run_null_calibration(
    n_studies: int,
    n_sessions: int,
    seed: int,
    ruleset_path: str,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    false_positives = covered = degenerate = 0
    means, counts = [], []
    for i in range(n_studies):
        trades = run_study_trades(
            ruleset_path=ruleset_path, seed=seed + i, n_sessions=n_sessions,
            up_probability_with_trend=0.5,
        )
        est = estimate_trades(trades, confidence_level)
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


def run_planted_edge(
    n_studies: int,
    n_sessions: int,
    seed: int,
    ruleset_path: str,
    up_probability_with_trend: float = 0.502,
    truth_sessions: int = 20000,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    truth_est = estimate_trades(
        run_study_trades(
            ruleset_path=ruleset_path, seed=seed + TRUTH_SEED_OFFSET,
            n_sessions=truth_sessions,
            up_probability_with_trend=up_probability_with_trend,
        ),
        confidence_level,
    )
    truth = truth_est.mean_r
    detected = covered = degenerate = 0
    ses = []
    for i in range(n_studies):
        est = estimate_trades(
            run_study_trades(
                ruleset_path=ruleset_path, seed=seed + i, n_sessions=n_sessions,
                up_probability_with_trend=up_probability_with_trend,
            ),
            confidence_level,
        )
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
        "truth_r": truth,
        "truth_trades": truth_est.n,
        "degenerate_studies": degenerate,
        "mean_se_r": mean_se,
        "predicted_power": predicted,
        "detection_rate": detected / valid if valid else None,
        "coverage_of_truth": covered / valid if valid else None,
    }
