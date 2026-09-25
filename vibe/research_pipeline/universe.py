"""Cross-sectional universe execution (P5b).

Runs one strategy independently across a declared set of symbols and reports
per-symbol metrics, pooled metrics, and the dispersion between them.

Three decisions worth stating
-----------------------------

**Pooled metrics pool trades, not per-symbol metrics.** Averaging each symbol's
``expectancy_r`` would weight a symbol that produced three trades exactly as
heavily as one that produced three hundred. Pooled metrics are therefore
computed by concatenating every trade and running the same
``PerformanceAnalyzer._calc_convexity`` the single-symbol path uses, so the P1
normalized definitions apply unchanged. The equal-weighted average across
symbols is *also* reported, as ``mean``, because the two answer different
questions: pooled asks "what did this strategy do", equal-weighted asks "what
did it do on a typical member". Where they disagree sharply, the result is
driven by one member and the dispersion fields say so.

**A failing symbol is not dropped.** Silently excluding a member that errored
is survivorship bias in miniature - the surviving members look better precisely
because the difficult one was removed. Failures are recorded on the result and
make it inconclusive.

**A symbol with zero trades is inconclusive, not zero.** A strategy that never
fired on a member has no evidence about that member, which is a different claim
from evidence of no edge. It is excluded from dispersion and counted separately.

Portfolio simulation - shared capital, contention, concentration caps - is
deliberately *not* here. That is P10b, and it needs a merged multi-symbol event
stream the engine cannot represent. Each symbol here gets its own full capital,
so summing notional across members would overstate deployable capital. The
result carries ``capital_is_independent_per_symbol=True`` to keep that explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median, pstdev
from typing import Callable, Mapping, Sequence

from vibe.research_pipeline.contracts import (
    SurvivorshipBias,
    UniverseSpec,
    UniverseType,
)
from vibe.research_pipeline.hashing import hash_object


class UniverseExecutionError(RuntimeError):
    """Raised when a universe run cannot produce a usable result."""


@dataclass(frozen=True)
class SymbolOutcome:
    """One member's result, or the reason it has none."""

    symbol: str
    result: object | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None

    @property
    def n_trades(self) -> int:
        if not self.ok:
            return 0
        return len(getattr(self.result, "trades", ()) or ())

    @property
    def has_evidence(self) -> bool:
        """A member only carries evidence if it actually traded."""
        return self.ok and self.n_trades > 0


@dataclass(frozen=True)
class Dispersion:
    """Spread of one metric across members that produced evidence."""

    metric: str
    n: int
    mean: float | None
    median: float | None
    stdev: float | None
    minimum: float | None
    maximum: float | None
    per_symbol: Mapping[str, float] = field(default_factory=dict)

    @property
    def spread(self) -> float | None:
        if self.minimum is None or self.maximum is None:
            return None
        return self.maximum - self.minimum

    def __str__(self) -> str:  # pragma: no cover - formatting only
        if self.n == 0:
            return f"{self.metric}: no members produced evidence"
        return (
            f"{self.metric}: mean={self.mean:.4f} median={self.median:.4f} "
            f"sd={self.stdev:.4f} range=[{self.minimum:.4f}, {self.maximum:.4f}] "
            f"n={self.n}"
        )


@dataclass(frozen=True)
class UniverseResult:
    """Cross-sectional evidence across a declared universe."""

    spec: UniverseSpec
    universe_hash: str
    outcomes: tuple[SymbolOutcome, ...]
    pooled: object | None
    dispersion: Mapping[str, Dispersion]
    capital_is_independent_per_symbol: bool = True

    @property
    def survivorship_bias(self) -> SurvivorshipBias:
        return self.spec.survivorship_bias

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.spec.symbols

    @property
    def failed_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(o.symbol for o in self.outcomes if o.error is not None))

    @property
    def silent_symbols(self) -> tuple[str, ...]:
        """Members that ran cleanly but produced no trades."""
        return tuple(
            sorted(o.symbol for o in self.outcomes if o.ok and o.n_trades == 0)
        )

    @property
    def contributing_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(o.symbol for o in self.outcomes if o.has_evidence))

    @property
    def conclusive(self) -> bool:
        """A universe result is conclusive only if nothing was lost.

        Any failure makes the pooled number unrepresentative of the declared
        universe, and a universe where no member traded has nothing to say.
        """
        return not self.failed_symbols and bool(self.contributing_symbols)

    def inconclusive_reason(self) -> str | None:
        if self.failed_symbols:
            return (
                f"{len(self.failed_symbols)} member(s) failed to run "
                f"({', '.join(self.failed_symbols)}), so the pooled result "
                f"describes a different universe than the one declared."
            )
        if not self.contributing_symbols:
            return (
                "No member produced a trade. This is absence of evidence, not "
                "evidence of no edge."
            )
        return None

    def summary(self) -> str:  # pragma: no cover - formatting only
        lines = [
            f"universe: {self.spec.universe_type.value} "
            f"({len(self.symbols)} members, hash {self.universe_hash[:12]})",
            f"survivorship_bias: {self.survivorship_bias.value}",
            f"contributing: {len(self.contributing_symbols)}/{len(self.symbols)}",
        ]
        if self.silent_symbols:
            lines.append(f"no trades: {', '.join(self.silent_symbols)}")
        if self.failed_symbols:
            lines.append(f"FAILED: {', '.join(self.failed_symbols)}")
        lines.extend(str(d) for d in self.dispersion.values())
        reason = self.inconclusive_reason()
        if reason:
            lines.append(f"INCONCLUSIVE: {reason}")
        return "\n".join(lines)


def universe_hash(spec: UniverseSpec) -> str:
    """Stable identity for a universe definition.

    Covers the type, the sorted member list, and the declared bias, because all
    three change what the result means. ``selection_rationale`` is excluded: it
    is prose, and letting a reworded sentence change the hash would make the
    hash useless as a universe identity.
    """
    return hash_object(
        {
            "universe_type": spec.universe_type.value,
            "symbols": list(spec.symbols),
            "survivorship_bias": spec.survivorship_bias.value,
        }
    )


#: Metrics reported with cross-sectional dispersion. Each is a per-symbol
#: quantity whose spread across members is informative; totals like
#: ``total_pnl`` are deliberately excluded because their spread mostly measures
#: how many trades each member happened to produce.
DISPERSION_METRICS: tuple[str, ...] = (
    "expectancy_r",
    "win_rate",
    "avg_win_r",
    "avg_loss_r",
    "max_loss_r",
    "skewness",
)


def _dispersion_for(
    metric: str, outcomes: Sequence[SymbolOutcome]
) -> Dispersion:
    values: dict[str, float] = {}
    for outcome in outcomes:
        if not outcome.has_evidence:
            continue
        overall = getattr(outcome.result, "overall", None)
        raw = getattr(overall, metric, None)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        values[outcome.symbol] = value

    if not values:
        return Dispersion(metric, 0, None, None, None, None, None, {})

    series = list(values.values())
    return Dispersion(
        metric=metric,
        n=len(series),
        mean=sum(series) / len(series),
        median=median(series),
        stdev=pstdev(series) if len(series) > 1 else 0.0,
        minimum=min(series),
        maximum=max(series),
        # Sorted so the mapping cannot depend on member iteration order.
        per_symbol=dict(sorted(values.items())),
    )


def _pool_trades(outcomes: Sequence[SymbolOutcome]) -> list:
    """Concatenate trades across members in a deterministic order.

    Sorted by entry time with the symbol as tiebreaker, so two members whose
    trades share a timestamp cannot reorder the pooled sequence depending on
    which symbol happened to run first.
    """
    pooled = []
    for outcome in outcomes:
        if not outcome.ok:
            continue
        for trade in getattr(outcome.result, "trades", ()) or ():
            pooled.append((outcome.symbol, trade))

    def key(item):
        symbol, trade = item
        entry = getattr(trade, "entry_time", None)
        return (entry is None, str(entry), symbol)

    pooled.sort(key=key)
    return [trade for _symbol, trade in pooled]


def run_universe(
    spec: UniverseSpec,
    run_symbol: Callable[[str], object],
    *,
    continue_on_error: bool = True,
) -> UniverseResult:
    """Run one strategy independently across every member of ``spec``.

    Args:
        spec: The declared universe. ``point_in_time_screened`` is rejected by
            ``UniverseSpec`` itself, so it cannot reach here.
        run_symbol: Callable taking a symbol and returning a ``BacktestResult``.
            Point this at ``BacktestEngine.run`` or, once folds are involved, at
            ``run_segment`` so the universe exercises the same warmup-aware path
            research uses.
        continue_on_error: If True, a failing member is recorded and the run
            continues, producing an inconclusive result that still shows what
            the other members did. If False, the first failure raises.

    Returns:
        A :class:`UniverseResult`. Member order never affects the outcome:
        ``UniverseSpec`` sorts symbols, pooled trades are sorted, and dispersion
        maps are sorted.

    Raises:
        UniverseExecutionError: A member failed and ``continue_on_error`` is
            False.
    """
    if spec.universe_type is UniverseType.POINT_IN_TIME_SCREENED:
        # Defensive: UniverseSpec rejects this at construction. Repeated here
        # so a future loosening of the contract cannot silently enable it.
        raise UniverseExecutionError(
            "point_in_time_screened universes require point-in-time membership "
            "and delisted history, neither of which the local corpus has."
        )

    outcomes: list[SymbolOutcome] = []
    for symbol in spec.symbols:
        try:
            result = run_symbol(symbol)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            if not continue_on_error:
                raise UniverseExecutionError(
                    f"Member {symbol!r} failed: {exc}"
                ) from exc
            outcomes.append(SymbolOutcome(symbol=symbol, error=f"{type(exc).__name__}: {exc}"))
            continue
        outcomes.append(SymbolOutcome(symbol=symbol, result=result))

    frozen = tuple(outcomes)

    pooled = None
    pooled_trades = _pool_trades(frozen)
    if pooled_trades:
        from vibe.backtester.analysis.performance import PerformanceAnalyzer

        # Reuse the single-symbol metric definitions so pooled numbers mean
        # exactly what per-symbol numbers mean (P1).
        pooled = PerformanceAnalyzer._calc_convexity(pooled_trades)

    dispersion = {m: _dispersion_for(m, frozen) for m in DISPERSION_METRICS}

    return UniverseResult(
        spec=spec,
        universe_hash=universe_hash(spec),
        outcomes=frozen,
        pooled=pooled,
        dispersion=dispersion,
    )


def static_declared(
    symbols: Sequence[str], *, rationale: str
) -> UniverseSpec:
    """Build a ``static_declared`` universe with the bias disclosure attached.

    A fixed hand-picked list is always survivorship-biased: every member is a
    name that still exists and still met the bar when the list was written.
    That is a weaker claim than "works across the market", and the contract
    forces it to be stated rather than assumed.
    """
    return UniverseSpec(
        universe_type=UniverseType.STATIC_DECLARED,
        symbols=tuple(symbols),
        survivorship_bias=SurvivorshipBias.PRESENT,
        selection_rationale=rationale,
    )


def single_symbol(symbol: str, *, rationale: str = "Single-instrument study") -> UniverseSpec:
    """Build a ``single_symbol`` universe."""
    return UniverseSpec(
        universe_type=UniverseType.SINGLE_SYMBOL,
        symbols=(symbol,),
        survivorship_bias=SurvivorshipBias.NOT_APPLICABLE,
        selection_rationale=rationale,
    )
