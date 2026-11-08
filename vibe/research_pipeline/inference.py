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
        if net_r is None or net is None:
            continue
        net_pnl_total += net
        notional += float(trade.entry_price) * float(trade.quantity)
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


def trade_net_pnl(trade: Any) -> float | None:
    """Net dollar P&L of one trade: ``pnl - commission``."""
    return _net_pnl(trade)


# Step 0 null calibration: observed z-scores were ~0.81 of nominal, so a true
# edge produces a smaller test statistic than the nominal SE implies.
DEFAULT_POWER_DEFLATION = 0.81


def power_table(
    se: float | None,
    edges: Sequence[float] = (0.03, 0.05, 0.08),
    *,
    alpha: float = 0.05,
    deflation: float = DEFAULT_POWER_DEFLATION,
    min_edge_r: float = 0.05,
    mean: float | None = None,
) -> dict[str, Any] | None:
    """Power to detect each true edge at standard error ``se`` (normal approx).

    ``power_nonzero`` is two-sided power to reject a zero mean;
    ``power_supported`` is the chance the lower bound clears ``min_edge_r``,
    i.e. that ``classify_outcome`` says "supported". ``*_deflated`` versions
    shrink the expected z by ``deflation`` (Step 0 calibration). When
    ``mean`` is given, ``excluded_edge_r`` is the one-sided ``1 - alpha/2``
    upper bound: every true edge at or above it is excluded.
    """
    if se is None or not math.isfinite(se) or se <= 0:
        return None
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not 0.0 < deflation <= 1.0:
        raise ValueError("deflation must be in (0, 1]")
    normal = statistics.NormalDist()
    z = normal.inv_cdf(1.0 - alpha / 2.0)

    def two_sided(shift: float) -> float:
        return normal.cdf(shift - z) + normal.cdf(-shift - z)

    rows = []
    for edge in edges:
        e = float(edge)
        rows.append({
            "edge_r": e,
            "power_nonzero": two_sided(e / se),
            "power_nonzero_deflated": two_sided(deflation * e / se),
            "power_supported": normal.cdf((e - min_edge_r) / se - z),
            "power_supported_deflated": normal.cdf(deflation * (e - min_edge_r) / se - z),
        })
    excluded = excluded_deflated = None
    if mean is not None and math.isfinite(mean):
        excluded = mean + z * se
        excluded_deflated = mean + z * se / deflation
    return {
        "se_r": se,
        "alpha": alpha,
        "deflation": deflation,
        "min_edge_r": min_edge_r,
        "rows": rows,
        "excluded_edge_r": excluded,
        "excluded_edge_deflated_r": excluded_deflated,
        "basis": "normal approximation; deflation scales expected z by the Step 0 calibration factor",
    }


def trimmed_mean(values: Sequence[float], proportion: float) -> float | None:
    """Mean after cutting ``proportion`` of observations from each tail."""
    if not 0.0 <= proportion < 0.5:
        raise ValueError("proportion must be in [0, 0.5)")
    data = sorted(float(v) for v in values)
    cut = int(len(data) * proportion)
    kept = data[cut: len(data) - cut]
    return statistics.fmean(kept) if kept else None


def sample_skewness(values: Sequence[float]) -> float | None:
    """Adjusted Fisher-Pearson skewness (G1); ``None`` below 3 values or zero SD."""
    data = [float(v) for v in values]
    n = len(data)
    if n < 3:
        return None
    mean = statistics.fmean(data)
    m2 = sum((x - mean) ** 2 for x in data) / n
    if m2 == 0:
        return None
    m3 = sum((x - mean) ** 3 for x in data) / n
    g1 = m3 / m2 ** 1.5
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)


def mean_without_top_values(values: Sequence[float], k: int) -> float | None:
    """Mean after removing the ``k`` largest values."""
    data = sorted(float(v) for v in values)
    kept = data[: max(0, len(data) - k)]
    return statistics.fmean(kept) if kept else None


def mean_without_top_clusters(observations: Sequence[Observation], k: int) -> float | None:
    """Per-observation mean after removing the ``k`` clusters with the largest sums."""
    sums: dict[str, float] = defaultdict(float)
    for obs in observations:
        sums[obs.cluster] += float(obs.value_r)
    top = set(sorted(sums, key=lambda c: (-sums[c], c))[:k])
    kept = [float(o.value_r) for o in observations if o.cluster not in top]
    return statistics.fmean(kept) if kept else None


def distribution_robustness(
    observations: Sequence[Observation],
    *,
    top_days: Sequence[int] = (10, 20, 50),
    top_trades: Sequence[int] = (50, 100),
) -> dict[str, Any]:
    """How much of the mean is carried by the right tail."""
    values = [float(o.value_r) for o in observations if math.isfinite(float(o.value_r))]
    clean = [o for o in observations if math.isfinite(float(o.value_r))]
    n = len(values)
    return {
        "n": n,
        "mean_r": statistics.fmean(values) if values else None,
        "median_r": statistics.median(values) if values else None,
        "trimmed_mean_1pct_r": trimmed_mean(values, 0.01) if values else None,
        "trimmed_mean_5pct_r": trimmed_mean(values, 0.05) if values else None,
        "skew": sample_skewness(values),
        "win_rate": sum(v > 0 for v in values) / n if n else None,
        "mean_without_top_days_r": {
            str(k): mean_without_top_clusters(clean, k) for k in top_days
        },
        "mean_without_top_trades_r": {
            str(k): mean_without_top_values(values, k) for k in top_trades
        },
    }


def session_pnl_totals(
    trades_by_symbol: Mapping[str, Sequence[Any]],
) -> dict[tuple[str, str], float]:
    """Net dollar P&L summed per ``(symbol, session_date)``."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for symbol, trades in trades_by_symbol.items():
        for trade in trades:
            net = _net_pnl(trade)
            if net is None or not math.isfinite(net):
                continue
            totals[(symbol, trade_session_date(trade))] += net
    return dict(totals)


def _usd_interval(est: MeanEstimate) -> dict[str, Any]:
    return {
        "n": est.n,
        "n_clusters": est.n_clusters,
        "mean_usd": _finite_or_none(est.mean_r),
        "se_cluster_usd": _finite_or_none(est.se_cluster_r),
        "ci_low_usd": _finite_or_none(est.ci_low_r),
        "ci_high_usd": _finite_or_none(est.ci_high_r),
        "confidence_level": est.confidence_level,
    }


def dollar_summary(
    trades_by_symbol: Mapping[str, Sequence[Any]],
    *,
    capital: float | None = None,
    years: float | None = None,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Dollar view of a trade set, since sizing caps make R and $ diverge.

    ``per_symbol_session`` covers (symbol, date) pairs with at least one trade
    and is clustered by date. ``return_on_equity_per_year`` assumes each
    symbol ran on its own ``capital`` account; it is simple, not compounded.
    """
    trade_obs: list[Observation] = []
    bps: list[float] = []
    risks: list[float] = []
    total = 0.0
    for symbol in sorted(trades_by_symbol):
        for trade in trades_by_symbol[symbol]:
            net = _net_pnl(trade)
            if net is None or not math.isfinite(net):
                continue
            total += net
            trade_obs.append(Observation(net, cluster=trade_session_date(trade)))
            notional = float(trade.entry_price) * float(trade.quantity)
            if notional > 0:
                bps.append(net / notional * 1e4)
            risk = getattr(trade, "initial_risk", None)
            if risk is not None and math.isfinite(float(risk)) and float(risk) > 0:
                risks.append(float(risk))
    session_obs = [
        Observation(v, cluster=day)
        for (_symbol, day), v in sorted(session_pnl_totals(trades_by_symbol).items())
    ]
    n_symbols = len(trades_by_symbol)
    roe = None
    if capital and years and n_symbols and capital > 0 and years > 0:
        roe = total / (capital * n_symbols) / years
    return {
        "total_net_pnl_usd": total,
        "per_trade": _usd_interval(estimate_mean(trade_obs, confidence_level=confidence_level)),
        "per_symbol_session": {
            **_usd_interval(estimate_mean(session_obs, confidence_level=confidence_level)),
            "basis": "symbol_sessions_with_at_least_one_trade",
        },
        "mean_bps_of_notional": statistics.fmean(bps) if bps else None,
        "median_initial_risk_usd": statistics.median(risks) if risks else None,
        "mean_initial_risk_usd": statistics.fmean(risks) if risks else None,
        "return_on_equity_per_year": roe,
    }


def capped_share(results: Iterable[Any]) -> dict[str, Any]:
    """Share of entries whose size was capped by buying power, from engine diagnostics."""
    capped = 0
    trades = 0
    reported = False
    for result in results:
        if result is None:
            continue
        diag = getattr(result, "execution_diagnostics", None) or {}
        trades += len(getattr(result, "trades", ()) or ())
        value = diag.get("orders_capped_by_buying_power")
        if value is not None:
            reported = True
            capped += int(value)
    return {
        "orders_capped_by_buying_power": capped if reported else None,
        "n_trades": trades,
        "share_capped_by_buying_power": (
            capped / trades if reported and trades else None
        ),
    }


def split_alpha(total_alpha: float, n_tests: int) -> float:
    """Bonferroni share of ``total_alpha`` for each of ``n_tests`` tests."""
    if n_tests < 1:
        raise ValueError("n_tests must be at least 1")
    return total_alpha / n_tests
