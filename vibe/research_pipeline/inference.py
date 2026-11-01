"""Statistics for fixed-rule and paired ORB edge studies.

Trades on the same session date share market conditions, so the unit of
inference is the session date (a cluster), not the trade. Standard errors use
the CR1 cluster-robust estimator and a day-block bootstrap. Only the standard
library and the Student-t helpers in ``reliability`` are used, and every
random draw takes an explicit seed with its own generator.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from vibe.research_pipeline.reliability import _percentile, _student_t_quantile

_EXCHANGE_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Observation:
    """One trade's net R, tagged with the cluster it belongs to."""

    value_r: float
    cluster: str  # session date "YYYY-MM-DD"; trades on the same date share it


class Outcome(str, Enum):
    """Confirmatory label for an interval against a minimum edge."""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNDERPOWERED = "underpowered"


def _finite_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


@dataclass(frozen=True)
class MeanEstimate:
    """Mean net R with iid and cluster-robust uncertainty."""

    n: int
    n_clusters: int
    mean_r: float | None
    sd_r: float | None
    se_iid_r: float | None
    se_cluster_r: float | None
    design_effect: float | None
    confidence_level: float
    ci_low_r: float | None
    ci_high_r: float | None
    bootstrap_ci_low_r: float | None
    bootstrap_ci_high_r: float | None
    bootstrap_replicates: int
    bootstrap_seed: int | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (``None`` for non-finite values)."""
        return {
            "n": self.n,
            "n_clusters": self.n_clusters,
            "mean_r": _finite_or_none(self.mean_r),
            "sd_r": _finite_or_none(self.sd_r),
            "se_iid_r": _finite_or_none(self.se_iid_r),
            "se_cluster_r": _finite_or_none(self.se_cluster_r),
            "design_effect": _finite_or_none(self.design_effect),
            "confidence_level": self.confidence_level,
            "ci_low_r": _finite_or_none(self.ci_low_r),
            "ci_high_r": _finite_or_none(self.ci_high_r),
            "bootstrap_ci_low_r": _finite_or_none(self.bootstrap_ci_low_r),
            "bootstrap_ci_high_r": _finite_or_none(self.bootstrap_ci_high_r),
            "bootstrap_replicates": self.bootstrap_replicates,
            "bootstrap_seed": self.bootstrap_seed,
        }


def _net_pnl(trade: Any) -> float | None:
    pnl = getattr(trade, "pnl", None)
    if pnl is None:
        return None
    return float(pnl) - float(getattr(trade, "commission", None) or 0.0)


def trade_net_r(trade: Any) -> float | None:
    """Net R of one trade: ``(pnl - commission) / initial_risk``.

    Matches ``PerformanceAnalyzer._net_pnl(trade) / trade.initial_risk``.
    Returns ``None`` when the risk denominator or the P&L is unusable.
    """
    risk = getattr(trade, "initial_risk", None)
    if risk is None or not math.isfinite(float(risk)) or float(risk) <= 0:
        return None
    net = _net_pnl(trade)
    if net is None:
        return None
    return net / float(risk)


def trade_session_date(trade: Any) -> str:
    """Entry session date (``YYYY-MM-DD``) in America/New_York.

    Naive datetimes are treated as already in exchange time.
    """
    entry = getattr(trade, "entry_time", None)
    if entry is None:
        raise ValueError("trade has no entry_time")
    if isinstance(entry, str):
        entry = datetime.fromisoformat(entry)
    if entry.tzinfo is not None:
        entry = entry.astimezone(_EXCHANGE_TZ)
    return entry.strftime("%Y-%m-%d")


def _empty_estimate(
    n: int,
    n_clusters: int,
    mean: float | None,
    confidence_level: float,
    replicates: int,
    seed: int | None,
) -> MeanEstimate:
    return MeanEstimate(
        n=n,
        n_clusters=n_clusters,
        mean_r=mean,
        sd_r=None,
        se_iid_r=None,
        se_cluster_r=None,
        design_effect=None,
        confidence_level=confidence_level,
        ci_low_r=None,
        ci_high_r=None,
        bootstrap_ci_low_r=None,
        bootstrap_ci_high_r=None,
        bootstrap_replicates=replicates,
        bootstrap_seed=seed,
    )


def estimate_mean(
    observations: Sequence[Observation],
    *,
    confidence_level: float = 0.95,
    bootstrap_replicates: int = 0,
    bootstrap_seed: int | None = None,
) -> MeanEstimate:
    """Mean with a cluster-robust t interval and optional day-block bootstrap."""
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    if bootstrap_replicates > 0 and bootstrap_seed is None:
        raise ValueError("bootstrap_seed is required when bootstrapping")

    clean = [
        o for o in observations if math.isfinite(float(o.value_r))
    ]
    n = len(clean)
    sums: dict[str, float] = defaultdict(float)
    sizes: dict[str, int] = defaultdict(int)
    for obs in clean:
        sums[obs.cluster] += float(obs.value_r)
        sizes[obs.cluster] += 1
    clusters = sorted(sums)
    d = len(clusters)
    if n == 0:
        return _empty_estimate(
            0, 0, None, confidence_level, bootstrap_replicates, bootstrap_seed
        )

    values = [float(o.value_r) for o in clean]
    mean = sum(values) / n
    if n == 1 or d == 1:
        return _empty_estimate(
            n, d, mean, confidence_level, bootstrap_replicates, bootstrap_seed
        )

    sd = statistics.stdev(values)
    se_iid = sd / math.sqrt(n)
    ssq = sum((sums[c] - sizes[c] * mean) ** 2 for c in clusters)
    # Identical values have exactly zero residuals; avoid float round-off noise.
    se_cluster = 0.0 if sd == 0.0 else math.sqrt(d / (d - 1) * ssq) / n
    design_effect = (se_cluster / se_iid) ** 2 if se_iid > 0 else None

    if se_cluster == 0.0:
        ci_low = ci_high = mean
    else:
        t = _student_t_quantile(1.0 - (1.0 - confidence_level) / 2.0, d - 1)
        ci_low = mean - t * se_cluster
        ci_high = mean + t * se_cluster

    boot_low: float | None = None
    boot_high: float | None = None
    if bootstrap_replicates > 0:
        rng = random.Random(bootstrap_seed)
        cluster_sums = [sums[c] for c in clusters]
        cluster_sizes = [sizes[c] for c in clusters]
        draws: list[float] = []
        for _ in range(bootstrap_replicates):
            total = 0.0
            count = 0
            for _ in range(d):
                index = rng.randrange(d)
                total += cluster_sums[index]
                count += cluster_sizes[index]
            draws.append(total / count)
        tail = (1.0 - confidence_level) / 2.0
        boot_low = _percentile(draws, tail)
        boot_high = _percentile(draws, 1.0 - tail)

    return MeanEstimate(
        n=n,
        n_clusters=d,
        mean_r=mean,
        sd_r=sd,
        se_iid_r=se_iid,
        se_cluster_r=se_cluster,
        design_effect=design_effect,
        confidence_level=confidence_level,
        ci_low_r=ci_low,
        ci_high_r=ci_high,
        bootstrap_ci_low_r=boot_low,
        bootstrap_ci_high_r=boot_high,
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )


def _z_sum(alpha: float, power: float, two_sided: bool) -> float:
    normal = statistics.NormalDist()
    z_alpha = normal.inv_cdf(1.0 - (alpha / 2.0 if two_sided else alpha))
    return z_alpha + normal.inv_cdf(power)


def minimum_detectable_effect(
    se: float, *, alpha: float = 0.05, power: float = 0.80, two_sided: bool = True
) -> float:
    """Smallest true mean detectable with the given power at standard error ``se``."""
    return _z_sum(alpha, power, two_sided) * se


def required_observations(
    sd: float,
    mde: float,
    *,
    design_effect: float = 1.0,
    alpha: float = 0.05,
    power: float = 0.80,
    two_sided: bool = True,
) -> int:
    """Observations needed to detect ``mde`` given SD and design effect."""
    if mde <= 0:
        raise ValueError("mde must be positive")
    if sd < 0:
        raise ValueError("sd must be non-negative")
    return math.ceil(
        design_effect * (_z_sum(alpha, power, two_sided) * sd / mde) ** 2
    )


def classify_outcome(
    ci_low: float | None, ci_high: float | None, *, min_edge_r: float
) -> Outcome:
    """supported if ``lo > m``, contradicted if ``hi < m``, else underpowered."""
    if ci_low is None or ci_high is None:
        return Outcome.UNDERPOWERED
    if ci_low > min_edge_r:
        return Outcome.SUPPORTED
    if ci_high < min_edge_r:
        return Outcome.CONTRADICTED
    return Outcome.UNDERPOWERED


def session_r_totals(
    trades_by_symbol: Mapping[str, Sequence[Any]],
) -> dict[tuple[str, str], float]:
    """Net R summed per ``(symbol, session_date)``; unusable trades are skipped."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for symbol, trades in trades_by_symbol.items():
        for trade in trades:
            value = trade_net_r(trade)
            if value is None:
                continue
            totals[(symbol, trade_session_date(trade))] += value
    return dict(totals)


def paired_session_differences(
    baseline: Mapping[tuple[str, str], float],
    variant: Mapping[tuple[str, str], float],
    sessions: Iterable[tuple[str, str]],
) -> list[Observation]:
    """Per-session ``variant - baseline`` net R, clustered by session date."""
    keys = list(sessions)
    allowed = set(keys)
    for name, series in (("baseline", baseline), ("variant", variant)):
        stray = sorted(k for k in series if k not in allowed)
        if stray:
            raise ValueError(
                f"{name} has sessions outside the graded set, e.g. {stray[0]}; "
                "the two rules were not run on the same sessions"
            )
    return [
        Observation(variant.get(k, 0.0) - baseline.get(k, 0.0), cluster=k[1])
        for k in keys
    ]


def cost_breakeven(trades: Sequence[Any]) -> dict[str, float | None]:
    """Cost headroom of a trade set.

    ``mean_net_r`` is the extra per-trade cost (in R) that zeroes expectancy.
    ``breakeven_extra_cost_bps`` is the extra round-trip cost, in basis points
    of entry notional, that zeroes total net P&L. Slippage is already inside
    fill prices and cannot be separated here.
    """
    net_rs: list[float] = []
    commission_rs: list[float] = []
    net_pnl_total = 0.0
    notional = 0.0
    for trade in trades:
        net_r = trade_net_r(trade)
        net = _net_pnl(trade)
        if net is not None:
            net_pnl_total += net
            notional += float(trade.entry_price) * float(trade.quantity)
        if net_r is None:
            continue
        net_rs.append(net_r)
        commission_rs.append(
            float(getattr(trade, "commission", None) or 0.0)
            / float(trade.initial_risk)
        )
    return {
        "mean_net_r": statistics.fmean(net_rs) if net_rs else None,
        "mean_commission_r": statistics.fmean(commission_rs) if commission_rs else None,
        "breakeven_extra_cost_bps": (
            net_pnl_total / notional * 1e4 if notional > 0 else None
        ),
    }


def split_alpha(total_alpha: float, n_tests: int) -> float:
    """Bonferroni share of ``total_alpha`` for each of ``n_tests`` tests."""
    if n_tests < 1:
        raise ValueError("n_tests must be at least 1")
    return total_alpha / n_tests
