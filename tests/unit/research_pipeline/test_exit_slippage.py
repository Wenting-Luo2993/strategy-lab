"""E4, exit side: slippage keyed on exit reason.

The claim under test is not "exits cost something" but "exits cost something
*appropriate to their order type*". A take-profit is a resting limit order and
cannot fill worse than its price; a stop becomes a market order and always can.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    ExecutionRealismConfig,
)
from vibe.backtester.core.exit_slippage import (
    ExitCost,
    ExitSlippageModel,
    FixedTickExitSlippage,
)
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar

BASE = datetime(2022, 3, 1, 14, 30, tzinfo=timezone.utc)


def _bar(o=50.0, h=51.0, l=49.0, c=50.5, v=1_000_000) -> Bar:
    return Bar(timestamp=BASE, open=o, high=h, low=l, close=c, volume=v)


class TestExitCost:
    def test_negative_ticks_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            ExitCost(ticks=-1)

    def test_liquidity_providing_exit_cannot_be_given_slippage(self):
        """The structural guard: a limit order cannot fill worse than its price."""
        with pytest.raises(ValueError, match="liquidity-providing"):
            ExitCost(ticks=2, takes_liquidity=False)

    def test_liquidity_providing_exit_at_zero_is_allowed(self):
        assert ExitCost(ticks=0, takes_liquidity=False).ticks == 0


class TestFixedTickExitSlippage:
    def test_stop_on_a_long_fills_lower(self):
        m = FixedTickExitSlippage.liquid_equity()
        assert m.adjust(
            50.0, reason="STOP", side="sell", quantity=100, bar=_bar()
        ) == pytest.approx(49.98)

    def test_stop_on_a_short_fills_higher(self):
        """Adverse in both directions, not merely 'lower'."""
        m = FixedTickExitSlippage.liquid_equity()
        assert m.adjust(
            50.0, reason="STOP", side="buy", quantity=100, bar=_bar()
        ) == pytest.approx(50.02)

    def test_take_profit_never_slips(self):
        m = FixedTickExitSlippage.liquid_equity()
        assert m.adjust(
            51.0, reason="TP", side="sell", quantity=100, bar=_bar()
        ) == pytest.approx(51.0)

    def test_eod_slips_less_than_a_stop(self):
        m = FixedTickExitSlippage.liquid_equity()
        stop = 50.0 - m.adjust(50.0, reason="STOP", side="sell", quantity=1, bar=_bar())
        eod = 50.0 - m.adjust(50.0, reason="EOD", side="sell", quantity=1, bar=_bar())
        assert stop > eod > 0

    def test_unknown_reason_costs_nothing_by_default(self):
        """A new exit path must not silently inherit a stop's cost."""
        m = FixedTickExitSlippage.liquid_equity()
        assert m.adjust(
            50.0, reason="TRAIL", side="sell", quantity=100, bar=_bar()
        ) == pytest.approx(50.0)

    def test_unknown_reason_default_is_configurable(self):
        m = FixedTickExitSlippage(
            rules={}, default=ExitCost(ticks=3, takes_liquidity=True)
        )
        assert m.adjust(
            50.0, reason="TRAIL", side="sell", quantity=100, bar=_bar()
        ) == pytest.approx(49.97)

    def test_tick_counts_are_configurable(self):
        m = FixedTickExitSlippage.liquid_equity(stop_ticks=10, eod_ticks=4)
        assert m.adjust(
            50.0, reason="STOP", side="sell", quantity=1, bar=_bar()
        ) == pytest.approx(49.90)

    def test_zero_model_moves_nothing(self):
        m = FixedTickExitSlippage.zero()
        for reason in ("STOP", "EOD", "TP"):
            assert m.adjust(
                50.0, reason=reason, side="sell", quantity=100, bar=_bar()
            ) == pytest.approx(50.0)

    def test_is_zero_reports_correctly(self):
        assert FixedTickExitSlippage.zero().is_zero
        assert not FixedTickExitSlippage.liquid_equity().is_zero

    def test_invalid_side_rejected(self):
        with pytest.raises(ValueError, match="Invalid side"):
            FixedTickExitSlippage.liquid_equity().adjust(
                50.0, reason="STOP", side="hold", quantity=1, bar=_bar()
            )

    def test_non_positive_tick_size_rejected(self):
        with pytest.raises(ValueError, match="tick_size"):
            FixedTickExitSlippage(tick_size=0.0)

    def test_satisfies_the_protocol(self):
        """The seam that lets a volume-scaled model drop in later."""
        assert isinstance(FixedTickExitSlippage.liquid_equity(), ExitSlippageModel)

    def test_identity_distinguishes_tick_settings(self):
        a = FixedTickExitSlippage.liquid_equity(stop_ticks=2).identity()
        b = FixedTickExitSlippage.liquid_equity(stop_ticks=5).identity()
        assert a != b

    def test_model_is_hashable(self):
        assert hash(FixedTickExitSlippage.liquid_equity()) is not None


class TestRealismConfigWiring:
    def test_legacy_has_no_exit_slippage(self):
        assert ExecutionRealismConfig.legacy().exit_slippage.is_zero

    def test_realistic_slips_exits_by_default(self):
        assert not ExecutionRealismConfig.realistic().exit_slippage.is_zero

    def test_exit_slippage_is_overridable(self):
        cfg = ExecutionRealismConfig.realistic(
            exit_slippage=FixedTickExitSlippage.zero()
        )
        assert cfg.exit_slippage.is_zero

    def test_identity_includes_exit_slippage(self):
        assert "exit_slippage_ticks" in ExecutionRealismConfig.realistic().identity()

    def test_identity_differs_by_slippage_setting(self):
        """Two runs differing only in slippage must not share a fingerprint."""
        a = ExecutionRealismConfig.realistic().identity()
        b = ExecutionRealismConfig.realistic(
            exit_slippage=FixedTickExitSlippage.liquid_equity(stop_ticks=9)
        ).identity()
        assert a != b

    def test_execution_model_version_bumped_for_exit_slippage(self):
        assert EXECUTION_MODEL_VERSION >= 4


class _Clock:
    def __init__(self, ts: datetime) -> None:
        self._ts = ts

    def now(self) -> datetime:
        return self._ts


def _long_at(portfolio: PortfolioManager, entry: float, stop: float, qty=100.0):
    portfolio.open_position(
        FillResult(symbol="QQQ", side="buy", filled_qty=qty, avg_price=entry),
        stop_price=stop,
        timestamp=BASE,
    )


class TestPortfolioExitSlippage:
    @staticmethod
    def _portfolio(**kw) -> PortfolioManager:
        return PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.realistic(**kw)
        )

    def test_stop_exit_fills_worse_than_the_stop_price(self):
        p = self._portfolio(commission_model=None)
        _long_at(p, entry=50.0, stop=49.5)
        # Bar trades down through the stop but does not gap.
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.0, c=49.2),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(49.48)

    def test_target_exit_fills_exactly_at_the_target(self):
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=51.5, l=49.9, c=51.2),
            level=51.0, reason="TP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(51.0)

    def test_slippage_never_leaves_the_bar(self):
        """A slipped fill must still be a price that actually traded."""
        p = self._portfolio(
            exit_slippage=FixedTickExitSlippage.liquid_equity(stop_ticks=500)
        )
        bar = _bar(o=50.0, h=50.2, l=49.4, c=49.5)
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=bar, level=49.5, reason="STOP",
            clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(bar.low)

    def test_gap_and_slippage_compose(self):
        """A stop that gaps through still pays the spread on the way out."""
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5)
        # Opens below the stop: E2 reprices to the open, then slippage applies.
        bar = _bar(o=49.0, h=49.3, l=48.5, c=48.8)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=bar, level=49.5, reason="STOP",
            clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.gap_through_exits == 1
        assert p.trade_history[0].exit_price == pytest.approx(48.98)

    def test_events_are_counted(self):
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.0, c=49.2),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.exit_slippage_events == 1

    def test_slippage_cost_is_measured(self):
        """Two ticks on 100 shares is $2, whatever the ledger calls it."""
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5, qty=100.0)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.0, c=49.2),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.exit_slippage_cost == pytest.approx(2.00)

    def test_slippage_cost_counts_only_what_the_clamp_allowed(self):
        """A clamped fill cost less than the model asked for."""
        p = self._portfolio(
            exit_slippage=FixedTickExitSlippage.liquid_equity(stop_ticks=500)
        )
        _long_at(p, entry=50.0, stop=49.5, qty=100.0)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.4, c=49.5),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        # Clamped to bar.low of 49.40, so $0.10/share, not the $5.00 requested.
        assert p.exit_slippage_cost == pytest.approx(10.00)

    def test_slippage_cost_is_not_double_counted_in_total_costs(self):
        """Slippage is in the fill price; commission is a separate debit."""
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.0, c=49.2),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.exit_slippage_cost > 0
        assert p.total_costs == pytest.approx(
            sum(t.commission for t in p.trade_history)
        )

    def test_target_exit_is_not_counted_as_a_slippage_event(self):
        p = self._portfolio()
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=51.5, l=49.9, c=51.2),
            level=51.0, reason="TP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.exit_slippage_events == 0

    def test_legacy_exits_fill_exactly_at_the_level(self):
        p = PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.legacy()
        )
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=50.2, l=49.0, c=49.2),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(49.5)
        assert p.exit_slippage_events == 0

    def test_legacy_still_fills_at_an_untraded_level(self):
        """Legacy's gap defect is preserved, not quietly corrected.

        Filling at 49.5 when the bar never traded above 49.0 is wrong, and it
        is E2's job to fix it via GapFillPolicy. Clamping it here would change
        legacy results and break the promise that they stay bit-identical.
        """
        p = PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.legacy()
        )
        _long_at(p, entry=50.0, stop=49.5)
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=49.0, h=49.0, l=48.0, c=48.5),
            level=49.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(49.5)

    def test_short_stop_fills_higher(self):
        p = self._portfolio()
        p.open_position(
            FillResult(symbol="QQQ", side="sell", filled_qty=100.0, avg_price=50.0),
            stop_price=50.5,
            timestamp=BASE,
        )
        p._close_at_level(
            pos=p.positions["QQQ"], bar=_bar(o=50.0, h=51.0, l=49.9, c=50.8),
            level=50.5, reason="STOP", clock=_Clock(BASE + timedelta(hours=1)),
        )
        assert p.trade_history[0].exit_price == pytest.approx(50.52)


class TestSlippageMonotonicity:
    """Fixture F8: worse assumptions must never produce better results."""

    @staticmethod
    def _net_pnl(stop_ticks: int) -> float:
        p = PortfolioManager(
            100_000.0,
            execution_realism=ExecutionRealismConfig.realistic(
                exit_slippage=FixedTickExitSlippage.liquid_equity(
                    stop_ticks=stop_ticks
                )
            ),
        )
        for day in range(5):
            ts = BASE + timedelta(days=day)
            p.open_position(
                FillResult(symbol="QQQ", side="buy", filled_qty=100.0, avg_price=50.0),
                stop_price=49.5,
                timestamp=ts,
            )
            p._close_at_level(
                pos=p.positions["QQQ"],
                bar=_bar(o=50.0, h=50.2, l=48.0, c=49.0),
                level=49.5, reason="STOP", clock=_Clock(ts + timedelta(hours=1)),
            )
        return sum(t.pnl - t.commission for t in p.trade_history)

    def test_more_slippage_never_improves_pnl(self):
        results = [self._net_pnl(t) for t in (0, 2, 5, 10, 20)]
        assert results == sorted(results, reverse=True)

    def test_zero_slippage_is_the_best_case(self):
        assert self._net_pnl(0) > self._net_pnl(2)
