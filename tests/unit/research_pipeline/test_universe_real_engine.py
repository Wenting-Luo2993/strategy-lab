"""P5b against the real engine and the real corpus.

The unit tests use a stand-in result object, which proves the aggregation logic
but not that the loop actually drives ``BacktestEngine``. This file runs the
real single-symbol engine across every member of the local universe, which is
the only way to know that per-symbol and pooled metrics line up with real
``BacktestResult`` objects rather than with a convenient fake.

Marked ``slow``: it runs five backtests.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from vibe.research_pipeline.contracts import SurvivorshipBias
from vibe.research_pipeline.universe import run_universe, static_declared

pytestmark = pytest.mark.slow

ET = pytz.timezone("America/New_York")
START = ET.localize(datetime(2022, 1, 3))
END = ET.localize(datetime(2022, 6, 30, 23, 59))

#: The complete local corpus. Five names, all survivors, all still listed -
#: which is exactly why the spec must declare survivorship_bias=present.
LOCAL_UNIVERSE = ("AMZN", "GOOGL", "MSFT", "QQQ", "TSLA")


@pytest.fixture(scope="module")
def data_dir():
    from vibe.backtester.data.paths import resolve_market_data_dir

    return resolve_market_data_dir(None)


@pytest.fixture(scope="module")
def spec():
    return static_declared(
        LOCAL_UNIVERSE,
        rationale=(
            "Every symbol with complete local 1-minute history. Chosen in "
            "hindsight from names that still trade, so results are optimistic "
            "relative to a genuine point-in-time screen."
        ),
    )


@pytest.fixture(scope="module")
def result(spec, data_dir):
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.common.ruleset.loader import RuleSetLoader

    try:
        ruleset = RuleSetLoader.from_name("orb_production")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"ruleset unavailable: {exc}")

    def run_symbol(symbol: str):
        engine = BacktestEngine(
            ruleset=ruleset, data_dir=data_dir, initial_capital=100_000
        )
        return engine.run(symbol=symbol, start_date=START, end_date=END)

    return run_universe(spec, run_symbol)


class TestRealUniverseRun:
    def test_every_member_ran(self, result):
        assert result.failed_symbols == (), (
            f"members failed to run: {result.failed_symbols}. A dropped member "
            f"would make the pooled result describe a smaller, easier universe."
        )
        assert len(result.outcomes) == len(LOCAL_UNIVERSE)

    def test_result_is_conclusive(self, result):
        assert result.conclusive, result.inconclusive_reason()

    def test_members_actually_traded(self, result):
        # Non-vacuity: dispersion across five silent symbols would be an
        # impressive-looking table of nothing.
        assert len(result.contributing_symbols) >= 3
        total = sum(o.n_trades for o in result.outcomes)
        assert total > 50, f"only {total} trades across the universe"

    def test_survivorship_badge_is_present(self, result):
        assert result.survivorship_bias is SurvivorshipBias.PRESENT

    def test_universe_hash_is_stamped(self, result):
        assert len(result.universe_hash) == 64


class TestPooledAgainstRealResults:
    def test_pooled_trade_count_equals_sum_of_members(self, result):
        expected = sum(o.n_trades for o in result.outcomes)
        assert result.pooled.n_trades == expected, (
            "Pooled metrics must account for every member's trades; a mismatch "
            "means trades were dropped or double-counted in pooling."
        )

    def test_pooled_metrics_are_finite(self, result):
        import math

        assert math.isfinite(result.pooled.expectancy_r)
        assert math.isfinite(result.pooled.win_rate)

    def test_pooled_differs_from_single_symbol_qqq(self, result):
        """Pooling must actually change the answer, or it adds nothing."""
        qqq = next(o for o in result.outcomes if o.symbol == "QQQ")
        assert result.pooled.n_trades > qqq.n_trades, (
            "The pooled result has no more trades than QQQ alone, so the "
            "cross-sectional run is not aggregating anything."
        )


class TestDispersionAcrossRealSymbols:
    def test_expectancy_disperses_across_members(self, result):
        d = result.dispersion["expectancy_r"]
        assert d.n == len(result.contributing_symbols)
        assert d.spread is not None and d.spread > 0, (
            "Every member produced an identical expectancy, which is not a "
            "credible cross-sectional result and suggests the per-symbol "
            "results are not actually distinct."
        )

    def test_per_symbol_values_are_reported(self, result):
        d = result.dispersion["expectancy_r"]
        assert set(d.per_symbol) == set(result.contributing_symbols)

    def test_all_declared_metrics_have_dispersion(self, result):
        for metric, d in result.dispersion.items():
            assert d.n > 0, f"{metric} has no contributing members"


class TestDeterminismOnRealData:
    def test_rerunning_the_universe_is_identical(self, spec, data_dir):
        from vibe.backtester.core.engine import BacktestEngine
        from vibe.common.ruleset.loader import RuleSetLoader

        ruleset = RuleSetLoader.from_name("orb_production")
        # Two symbols is enough to prove order-independence without paying for
        # five more backtests.
        small = static_declared(["MSFT", "QQQ"], rationale="determinism probe")
        reversed_order = static_declared(
            ["QQQ", "MSFT"], rationale="determinism probe"
        )

        def run_symbol(symbol: str):
            return BacktestEngine(
                ruleset=ruleset, data_dir=data_dir, initial_capital=100_000
            ).run(symbol=symbol, start_date=START, end_date=END)

        a = run_universe(small, run_symbol)
        b = run_universe(reversed_order, run_symbol)

        assert a.universe_hash == b.universe_hash
        assert a.pooled.n_trades == b.pooled.n_trades
        assert a.pooled.expectancy_r == pytest.approx(b.pooled.expectancy_r)
        assert a.dispersion["expectancy_r"].per_symbol == (
            b.dispersion["expectancy_r"].per_symbol
        )
