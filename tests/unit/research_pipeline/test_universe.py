"""Tests for cross-sectional universe execution (P5b)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from vibe.common.models.trade import Trade
from vibe.research_pipeline.contracts import (
    SurvivorshipBias,
    UniverseSpec,
    UniverseType,
)
from vibe.research_pipeline.universe import (
    DISPERSION_METRICS,
    SymbolOutcome,
    UniverseExecutionError,
    run_universe,
    single_symbol,
    static_declared,
    universe_hash,
)


def make_trade(symbol: str, when: datetime, pnl_r: float) -> Trade:
    """A real Trade, so pooled metrics exercise the real P1 definitions.

    ``initial_risk`` is fixed at 1.0 so ``pnl / initial_risk`` is exactly the
    requested R-multiple, which keeps the arithmetic in these tests checkable
    by hand.
    """
    return Trade(
        symbol=symbol,
        side="buy",
        quantity=10.0,
        entry_price=100.0,
        exit_price=100.0 + pnl_r,
        entry_time=when,
        exit_time=when,
        pnl=pnl_r,
        initial_risk=1.0,
        exit_reason="test",
    )


@dataclass
class FakeOverall:
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    max_loss_r: float = 0.0
    skewness: float = 0.0


@dataclass
class FakeResult:
    """Stands in for BacktestResult: only .trades and .overall are consumed."""

    trades: list
    overall: FakeOverall


def make_result(symbol: str, n: int, expectancy: float) -> FakeResult:
    trades = [
        make_trade(symbol, datetime(2024, 1, 2 + (i % 20), 10, 0), expectancy)
        for i in range(n)
    ]
    return FakeResult(trades=trades, overall=FakeOverall(expectancy_r=expectancy))


@pytest.fixture
def five_symbols() -> UniverseSpec:
    return static_declared(
        ["QQQ", "MSFT", "AMZN", "GOOGL", "TSLA"],
        rationale="The five symbols with full local 1-minute history.",
    )


class TestUniverseSpecDisclosure:
    def test_static_declared_forces_survivorship_present(self, five_symbols):
        assert five_symbols.survivorship_bias is SurvivorshipBias.PRESENT

    def test_static_declared_cannot_claim_absent_bias(self):
        with pytest.raises(ValueError, match="survivorship_bias=present"):
            UniverseSpec(
                universe_type=UniverseType.STATIC_DECLARED,
                symbols=("QQQ", "MSFT"),
                survivorship_bias=SurvivorshipBias.ABSENT,
                selection_rationale="wishful",
            )

    def test_point_in_time_screened_is_rejected(self):
        with pytest.raises(ValueError):
            UniverseSpec(
                universe_type=UniverseType.POINT_IN_TIME_SCREENED,
                symbols=("QQQ",),
                survivorship_bias=SurvivorshipBias.ABSENT,
                selection_rationale="not possible yet",
            )

    def test_single_symbol_is_not_applicable(self):
        spec = single_symbol("QQQ")
        assert spec.survivorship_bias is SurvivorshipBias.NOT_APPLICABLE

    def test_rationale_is_mandatory(self):
        with pytest.raises(ValueError):
            static_declared(["QQQ"], rationale="")


class TestUniverseHash:
    def test_member_order_does_not_change_the_hash(self):
        a = static_declared(["QQQ", "MSFT", "AMZN"], rationale="r")
        b = static_declared(["AMZN", "QQQ", "MSFT"], rationale="r")
        assert universe_hash(a) == universe_hash(b)

    def test_different_members_change_the_hash(self):
        a = static_declared(["QQQ", "MSFT"], rationale="r")
        b = static_declared(["QQQ", "AMZN"], rationale="r")
        assert universe_hash(a) != universe_hash(b)

    def test_adding_a_member_changes_the_hash(self):
        a = static_declared(["QQQ", "MSFT"], rationale="r")
        b = static_declared(["QQQ", "MSFT", "TSLA"], rationale="r")
        assert universe_hash(a) != universe_hash(b)

    def test_rationale_wording_does_not_change_the_hash(self):
        a = static_declared(["QQQ", "MSFT"], rationale="liquid megacaps")
        b = static_declared(["QQQ", "MSFT"], rationale="Liquid mega-caps.")
        assert universe_hash(a) == universe_hash(b), (
            "Prose churn must not change universe identity, or the hash stops "
            "being usable as one."
        )

    def test_universe_type_changes_the_hash(self):
        a = single_symbol("QQQ")
        b = static_declared(["QQQ"], rationale="r")
        assert universe_hash(a) != universe_hash(b)

    def test_hash_is_hex_and_stable(self):
        spec = static_declared(["QQQ"], rationale="r")
        h = universe_hash(spec)
        assert h == universe_hash(spec)
        assert len(h) == 64
        int(h, 16)


class TestRunUniverse:
    def test_runs_every_member(self, five_symbols):
        seen = []

        def run(symbol):
            seen.append(symbol)
            return make_result(symbol, 10, 0.5)

        result = run_universe(five_symbols, run)
        assert sorted(seen) == sorted(five_symbols.symbols)
        assert len(result.outcomes) == 5

    def test_result_stamps_universe_identity(self, five_symbols):
        result = run_universe(five_symbols, lambda s: make_result(s, 5, 0.3))
        assert result.universe_hash == universe_hash(five_symbols)
        assert result.survivorship_bias is SurvivorshipBias.PRESENT
        assert result.symbols == five_symbols.symbols

    def test_member_order_does_not_change_the_result(self):
        a = static_declared(["QQQ", "MSFT", "AMZN"], rationale="r")
        b = static_declared(["AMZN", "MSFT", "QQQ"], rationale="r")

        def run(symbol):
            return make_result(symbol, 4, {"QQQ": 0.4, "MSFT": 0.2, "AMZN": -0.1}[symbol])

        ra = run_universe(a, run)
        rb = run_universe(b, run)
        assert ra.universe_hash == rb.universe_hash
        assert ra.dispersion["expectancy_r"].per_symbol == (
            rb.dispersion["expectancy_r"].per_symbol
        )
        assert ra.pooled.n_trades == rb.pooled.n_trades

    def test_capital_independence_is_explicit(self, five_symbols):
        result = run_universe(five_symbols, lambda s: make_result(s, 3, 0.1))
        assert result.capital_is_independent_per_symbol is True


class TestPooledVersusEqualWeighted:
    """Pooling trades and averaging metrics are different questions."""

    def test_pooled_is_trade_weighted_not_symbol_weighted(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        # AAA: 100 trades at -0.1. BBB: 2 trades at +5.0.
        # Equal-weighted mean expectancy is positive; pooled is negative.
        def run(symbol):
            return make_result(symbol, 100, -0.1) if symbol == "AAA" else make_result(symbol, 2, 5.0)

        result = run_universe(spec, run)

        equal_weighted = result.dispersion["expectancy_r"].mean
        assert equal_weighted > 0, "equal-weighted average is dominated by BBB"

        assert result.pooled.n_trades == 102
        assert result.pooled.expectancy_r < equal_weighted, (
            "Pooled metrics must be trade-weighted. If they matched the "
            "equal-weighted mean, a two-trade symbol would count as much as a "
            "hundred-trade one."
        )

    def test_pooled_uses_the_shared_metric_definitions(self):
        from vibe.backtester.analysis.performance import PerformanceAnalyzer

        spec = static_declared(["AAA", "BBB"], rationale="r")
        result = run_universe(spec, lambda s: make_result(s, 5, 0.25))

        expected = PerformanceAnalyzer._calc_convexity(
            [t for o in result.outcomes for t in o.result.trades]
        )
        assert result.pooled.n_trades == expected.n_trades

    def test_pooled_is_none_when_nothing_traded(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")
        result = run_universe(spec, lambda s: make_result(s, 0, 0.0))
        assert result.pooled is None


class TestDispersion:
    def test_reports_every_declared_metric(self, five_symbols):
        result = run_universe(five_symbols, lambda s: make_result(s, 5, 0.2))
        assert set(result.dispersion) == set(DISPERSION_METRICS)

    def test_spread_is_reported(self):
        spec = static_declared(["AAA", "BBB", "CCC"], rationale="r")
        vals = {"AAA": -0.5, "BBB": 0.0, "CCC": 1.5}
        result = run_universe(spec, lambda s: make_result(s, 10, vals[s]))

        d = result.dispersion["expectancy_r"]
        assert d.n == 3
        assert d.minimum == pytest.approx(-0.5)
        assert d.maximum == pytest.approx(1.5)
        assert d.spread == pytest.approx(2.0)
        assert d.stdev > 0

    def test_single_contributor_has_zero_stdev_not_none(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            return make_result(symbol, 5 if symbol == "AAA" else 0, 0.3)

        d = run_universe(spec, run).dispersion["expectancy_r"]
        assert d.n == 1
        assert d.stdev == 0.0

    def test_silent_symbols_are_excluded_from_dispersion(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            return make_result(symbol, 5, 0.4) if symbol == "AAA" else make_result(symbol, 0, 0.0)

        result = run_universe(spec, run)
        d = result.dispersion["expectancy_r"]
        assert set(d.per_symbol) == {"AAA"}, (
            "A symbol that never traded has no expectancy; including its 0.0 "
            "would drag the mean toward zero and understate dispersion."
        )

    def test_no_contributors_yields_empty_dispersion(self):
        spec = static_declared(["AAA"], rationale="r")
        d = run_universe(spec, lambda s: make_result(s, 0, 0.0)).dispersion["expectancy_r"]
        assert d.n == 0
        assert d.mean is None
        assert d.spread is None


class TestSilentAndFailedMembers:
    def test_zero_trade_symbol_is_silent_not_failed(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            return make_result(symbol, 5 if symbol == "AAA" else 0, 0.3)

        result = run_universe(spec, run)
        assert result.silent_symbols == ("BBB",)
        assert result.failed_symbols == ()
        assert result.contributing_symbols == ("AAA",)

    def test_failing_member_is_recorded_not_dropped(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            if symbol == "BBB":
                raise RuntimeError("no data")
            return make_result(symbol, 5, 0.3)

        result = run_universe(spec, run)
        assert result.failed_symbols == ("BBB",)
        assert len(result.outcomes) == 2, "the failing member must still appear"

    def test_failure_makes_the_result_inconclusive(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            if symbol == "BBB":
                raise RuntimeError("no data")
            return make_result(symbol, 5, 0.3)

        result = run_universe(spec, run)
        assert not result.conclusive
        assert "BBB" in result.inconclusive_reason()

    def test_failure_reason_explains_the_survivorship_risk(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            if symbol == "BBB":
                raise RuntimeError("boom")
            return make_result(symbol, 5, 0.3)

        reason = run_universe(spec, run).inconclusive_reason()
        assert "different universe than the one declared" in reason

    def test_strict_mode_raises_on_first_failure(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")

        def run(symbol):
            raise RuntimeError("boom")

        with pytest.raises(UniverseExecutionError, match="failed"):
            run_universe(spec, run, continue_on_error=False)

    def test_all_silent_universe_is_inconclusive(self):
        spec = static_declared(["AAA", "BBB"], rationale="r")
        result = run_universe(spec, lambda s: make_result(s, 0, 0.0))
        assert not result.conclusive
        assert "absence of evidence" in result.inconclusive_reason()

    def test_clean_universe_is_conclusive(self, five_symbols):
        result = run_universe(five_symbols, lambda s: make_result(s, 5, 0.2))
        assert result.conclusive
        assert result.inconclusive_reason() is None


class TestPooledDeterminism:
    def test_shared_timestamps_do_not_reorder_pooled_trades(self):
        """Two members trading at the same instant must pool deterministically."""
        spec = static_declared(["AAA", "BBB"], rationale="r")
        ts = datetime(2024, 3, 1, 10, 0)

        def run(symbol):
            return FakeResult(
                trades=[
                    make_trade(symbol, ts, 0.5),
                    make_trade(symbol, ts, -0.2),
                ],
                overall=FakeOverall(expectancy_r=0.15),
            )

        first = run_universe(spec, run)
        second = run_universe(spec, run)
        assert first.pooled.n_trades == second.pooled.n_trades == 4
        assert first.pooled.expectancy_r == pytest.approx(second.pooled.expectancy_r)


class TestSymbolOutcome:
    def test_ok_requires_a_result(self):
        assert not SymbolOutcome("AAA", error="boom").ok
        assert not SymbolOutcome("AAA").ok

    def test_has_evidence_requires_trades(self):
        empty = SymbolOutcome("AAA", result=make_result("AAA", 0, 0.0))
        assert empty.ok
        assert not empty.has_evidence

    def test_failed_outcome_reports_zero_trades(self):
        assert SymbolOutcome("AAA", error="boom").n_trades == 0
