"""Accounting reconciliation and the dangerous-failure fixtures F5-F7.

Section 12 of the implementation plan specifies each of these as a small
synthetic case targeting one specific way the simulator can produce a
confident wrong answer. They are grouped here with the reconciliation
identities because all three fixtures assert the same thing at the end: that
after the dangerous event, the books still balance.

F5  one bar where ``low <= stop`` and ``high >= take_profit``  (E1)
F6  long entry 100, stop 99, next bar opens 95                 (E2)
F7  a duplicated fill and a partial exit fill                  (conservation)
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vibe.backtester.core.commission import CommissionModel
from vibe.backtester.core.execution_realism import (
    ExecutionRealismConfig,
    GapFillPolicy,
    IntrabarExitResolution,
)
from vibe.backtester.core.exit_slippage import FixedTickExitSlippage
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.core.reconciliation import (
    AccountingError,
    CashLedgerEntry,
    ReconciliationReport,
    check_entry_exit_quantity_parity,
    check_flat_at_end_equity,
    check_per_fill_cash_delta,
    reconcile_portfolio,
)
from vibe.common.models.bar import Bar

NY = ZoneInfo("America/New_York")
T0 = datetime(2023, 6, 1, 9, 45, tzinfo=NY)


class FrozenClock:
    def __init__(self, ts: datetime) -> None:
        self._ts = ts

    def now(self) -> datetime:
        return self._ts

    def advance(self, **kw) -> None:
        self._ts = self._ts + timedelta(**kw)


def bar(o: float, h: float, l: float, c: float, ts: datetime | None = None) -> Bar:
    return Bar(
        timestamp=ts or T0, open=o, high=h, low=l, close=c, volume=1_000_000
    )


def fill(side: str, qty: float, price: float, symbol: str = "TEST") -> FillResult:
    return FillResult(symbol=symbol, side=side, filled_qty=qty, avg_price=price)


def make_portfolio(
    capital: float = 100_000.0,
    *,
    realism: ExecutionRealismConfig | None = None,
) -> PortfolioManager:
    return PortfolioManager(
        initial_capital=capital,
        execution_realism=realism or ExecutionRealismConfig.legacy(),
    )


def ledger_entry(**overrides) -> CashLedgerEntry:
    """A self-consistent entry; override one field to make it inconsistent."""
    base = dict(
        timestamp=T0,
        symbol="TEST",
        side="buy",
        quantity=100.0,
        price=50.0,
        commission=1.0,
        cash_before=100_000.0,
        cash_after=94_999.0,
        kind="open",
    )
    base.update(overrides)
    return CashLedgerEntry(**base)


# ---------------------------------------------------------------- identities


class TestPerFillCashDelta:
    def test_a_consistent_fill_reconciles(self):
        assert check_per_fill_cash_delta([ledger_entry()]).passed

    def test_a_sell_takes_cash_in_and_still_pays_commission(self):
        entry = ledger_entry(
            side="sell", cash_before=0.0, cash_after=100.0 * 50.0 - 1.0
        )
        assert check_per_fill_cash_delta([entry]).passed

    def test_an_uncharged_commission_is_caught(self):
        # Cash moved by the notional only; the fill claims a commission.
        entry = ledger_entry(cash_after=100_000.0 - 5_000.0)
        finding = check_per_fill_cash_delta([entry])
        assert not finding.passed
        assert finding.residual == pytest.approx(1.0)

    def test_a_double_charged_commission_is_caught(self):
        entry = ledger_entry(cash_after=94_998.0)
        assert not check_per_fill_cash_delta([entry]).passed

    def test_a_sign_error_is_caught(self):
        # Cash went up on a buy.
        entry = ledger_entry(cash_after=104_999.0)
        assert not check_per_fill_cash_delta([entry]).passed

    def test_the_report_names_the_first_offending_fill(self):
        good = ledger_entry()
        bad = ledger_entry(symbol="BROKEN", cash_after=1.0)
        detail = check_per_fill_cash_delta([good, bad, bad]).detail
        assert "2 of 3" in detail
        assert "BROKEN" in detail

    def test_an_empty_ledger_is_vacuously_consistent(self):
        assert check_per_fill_cash_delta([]).passed

    def test_float_noise_is_tolerated(self):
        entry = ledger_entry(cash_after=94_999.0 + 1e-12)
        assert check_per_fill_cash_delta([entry]).passed


class TestFlatAtEndEquity:
    def test_a_balanced_run_reconciles(self):
        finding = check_flat_at_end_equity(
            equity_final=101_000.0,
            initial_capital=100_000.0,
            trade_pnls=[600.0, 600.0],
            total_costs=200.0,
            open_position_count=0,
        )
        assert finding.passed

    def test_an_open_position_makes_it_inapplicable_not_failed(self):
        finding = check_flat_at_end_equity(
            equity_final=1.0,
            initial_capital=100_000.0,
            trade_pnls=[],
            total_costs=0.0,
            open_position_count=1,
        )
        assert not finding.applicable
        # The distinction that matters: an unevaluated check is not a pass,
        # but it must not be counted as a failure either.
        assert not finding.failed
        assert not finding.passed

    def test_unexplained_equity_is_caught(self):
        finding = check_flat_at_end_equity(
            equity_final=150_000.0,
            initial_capital=100_000.0,
            trade_pnls=[1_000.0],
            total_costs=0.0,
            open_position_count=0,
        )
        assert not finding.passed
        assert finding.residual == pytest.approx(49_000.0)

    def test_costs_are_subtracted_because_trade_pnl_is_gross(self):
        # The same trades with costs charged must land lower.
        kw = dict(
            equity_final=101_000.0,
            initial_capital=100_000.0,
            trade_pnls=[1_000.0],
            open_position_count=0,
        )
        assert check_flat_at_end_equity(total_costs=0.0, **kw).passed
        assert not check_flat_at_end_equity(total_costs=50.0, **kw).passed

    def test_a_run_that_never_traded_reconciles(self):
        finding = check_flat_at_end_equity(
            equity_final=100_000.0,
            initial_capital=100_000.0,
            trade_pnls=[],
            total_costs=0.0,
            open_position_count=0,
        )
        assert finding.passed


class TestEntryExitQuantityParity:
    def test_a_flat_symbol_reconciles(self):
        entries = [
            ledger_entry(kind="open", quantity=100.0),
            ledger_entry(kind="close", side="sell", quantity=100.0),
        ]
        assert check_entry_exit_quantity_parity(entries, {}).passed

    def test_an_open_position_reconciles_against_its_quantity(self):
        entries = [ledger_entry(kind="open", quantity=100.0)]
        assert check_entry_exit_quantity_parity(entries, {"TEST": 100.0}).passed

    def test_scale_ins_accumulate_on_the_entry_side(self):
        entries = [
            ledger_entry(kind="open", quantity=60.0),
            ledger_entry(kind="add", quantity=40.0),
            ledger_entry(kind="close", side="sell", quantity=100.0),
        ]
        assert check_entry_exit_quantity_parity(entries, {}).passed

    def test_a_duplicated_entry_fill_is_caught(self):
        entries = [
            ledger_entry(kind="open", quantity=100.0),
            ledger_entry(kind="open", quantity=100.0),
            ledger_entry(kind="close", side="sell", quantity=100.0),
        ]
        finding = check_entry_exit_quantity_parity(entries, {})
        assert not finding.passed
        assert finding.residual == pytest.approx(100.0)

    def test_an_exit_larger_than_the_entry_is_caught(self):
        entries = [
            ledger_entry(kind="open", quantity=100.0),
            ledger_entry(kind="close", side="sell", quantity=150.0),
        ]
        assert not check_entry_exit_quantity_parity(entries, {}).passed

    def test_symbols_are_checked_independently(self):
        entries = [
            ledger_entry(kind="open", symbol="AAA", quantity=100.0),
            ledger_entry(kind="close", symbol="AAA", side="sell", quantity=100.0),
            ledger_entry(kind="open", symbol="BBB", quantity=100.0),
        ]
        finding = check_entry_exit_quantity_parity(entries, {})
        assert not finding.passed
        # AAA balances, so only BBB should be named.
        assert "BBB" in finding.detail
        assert "AAA" not in finding.detail


class TestReconciliationReport:
    def test_an_inapplicable_finding_does_not_count_as_a_failure(self):
        report = ReconciliationReport(
            findings=[
                check_flat_at_end_equity(
                    equity_final=0.0, initial_capital=1.0, trade_pnls=[],
                    total_costs=0.0, open_position_count=1,
                )
            ]
        )
        assert report.ok
        assert report.failures == []

    def test_raise_if_failed_is_quiet_on_a_clean_report(self):
        ReconciliationReport(findings=[]).raise_if_failed()

    def test_raise_if_failed_names_every_broken_identity(self):
        report = ReconciliationReport(
            findings=[
                check_per_fill_cash_delta([ledger_entry(cash_after=1.0)]),
                check_entry_exit_quantity_parity(
                    [ledger_entry(kind="open", quantity=5.0)], {}
                ),
            ]
        )
        with pytest.raises(AccountingError) as exc:
            report.raise_if_failed()
        assert "per_fill_cash_delta" in str(exc.value)
        assert "entry_exit_quantity_parity" in str(exc.value)


# ------------------------------------------------- the ledger is independent


class TestLedgerIsNotTautological:
    """The identity must be able to fail, or it asserts nothing.

    This is the whole reason the ledger stores observed cash snapshots instead
    of a computed delta. These tests corrupt the recorded facts and require the
    check to notice -- a reconciliation suite that cannot be broken on purpose
    is not evidence of anything.
    """

    def test_a_real_round_trip_reconciles(self):
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)
        assert reconcile_portfolio(p).ok

    def test_corrupting_a_recorded_cash_snapshot_is_detected(self):
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)

        p.cash_ledger[0] = dataclasses.replace(
            p.cash_ledger[0], cash_after=p.cash_ledger[0].cash_after - 1.0
        )
        report = reconcile_portfolio(p)
        assert not report.ok
        assert report.failures[0].identity == "per_fill_cash_delta"

    def test_an_untracked_cash_movement_is_detected(self):
        """Cash that moves with no fill to justify it breaks the equity identity."""
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.cash += 500.0  # a leak with no corresponding trade
        p.update_equity({}, T0)

        report = reconcile_portfolio(p)
        assert not report.ok
        assert any(f.identity == "flat_at_end_equity" for f in report.failures)

    def test_commission_flows_into_the_equity_identity(self):
        p = make_portfolio(
            realism=ExecutionRealismConfig.legacy(),
        )
        p.execution_realism = dataclasses.replace(
            p.execution_realism,
            commission_model=CommissionModel(
                per_share=0.005, minimum_per_order=1.0
            ),
        )
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)

        assert p.total_costs > 0
        assert reconcile_portfolio(p).ok


# --------------------------------------------------------------- F5, F6, F7


class TestF5AmbiguousIntrabarExit:
    """One bar where ``low <= stop`` and ``high >= take_profit``.

    OHLC cannot say which came first. The fixture pins that the choice is a
    *declared policy* rather than an accident of evaluation order, and that
    the bar is counted either way.
    """

    @staticmethod
    def _run(resolution: IntrabarExitResolution) -> PortfolioManager:
        p = make_portfolio(
            realism=dataclasses.replace(
                ExecutionRealismConfig.legacy(),
                intrabar_exit_resolution=resolution,
            )
        )
        p.open_position(
            fill("buy", 100, 100.0), stop_price=99.0,
            timestamp=T0, take_profit=101.0,
        )
        # Touches both levels in the same bar.
        p.check_exits({"TEST": bar(100.0, 101.5, 98.5, 100.0)}, FrozenClock(T0))
        p.update_equity({}, T0)
        return p

    def test_conservative_resolution_takes_the_stop(self):
        p = self._run(IntrabarExitResolution.CONSERVATIVE)
        assert p.trade_history[0].exit_reason == "STOP"
        assert p.trade_history[0].pnl < 0

    def test_optimistic_resolution_takes_the_target(self):
        p = self._run(IntrabarExitResolution.OPTIMISTIC)
        assert p.trade_history[0].exit_reason == "TP"
        assert p.trade_history[0].pnl > 0

    @pytest.mark.parametrize(
        "resolution",
        [IntrabarExitResolution.CONSERVATIVE, IntrabarExitResolution.OPTIMISTIC],
    )
    def test_the_ambiguity_is_counted_in_both_modes(self, resolution):
        # The count is evidence, not a setting: an optimistic run still has to
        # disclose how much of its result rests on the favourable assumption.
        assert self._run(resolution).ambiguous_exit_bars == 1

    @pytest.mark.parametrize(
        "resolution",
        [IntrabarExitResolution.CONSERVATIVE, IntrabarExitResolution.OPTIMISTIC],
    )
    def test_the_books_balance_under_either_resolution(self, resolution):
        assert reconcile_portfolio(self._run(resolution)).ok

    def test_the_two_resolutions_disagree_materially(self):
        cons = self._run(IntrabarExitResolution.CONSERVATIVE)
        opt = self._run(IntrabarExitResolution.OPTIMISTIC)
        # If these ever agree, the fixture has stopped testing anything.
        assert cons.trade_history[0].pnl != opt.trade_history[0].pnl


class TestF6GapThroughStop:
    """Long entry 100, stop 99, next bar opens 95.

    A resting stop does not fill at 99 when the market never traded there.
    Legacy mode fills at 99 anyway -- that is E2's defect, preserved
    deliberately under ADR-015 -- so the fixture pins both behaviours and the
    gap between them.
    """

    @staticmethod
    def _run(policy: GapFillPolicy) -> PortfolioManager:
        p = make_portfolio(
            realism=dataclasses.replace(
                ExecutionRealismConfig.legacy(), gap_fill_policy=policy
            )
        )
        p.open_position(fill("buy", 100, 100.0), stop_price=99.0, timestamp=T0)
        # Opens below the stop and never trades back up to it.
        p.check_exits({"TEST": bar(95.0, 96.0, 94.0, 95.5)}, FrozenClock(T0))
        p.update_equity({}, T0)
        return p

    def test_legacy_fills_at_a_price_that_never_traded(self):
        p = self._run(GapFillPolicy.AT_LEVEL)
        trade = p.trade_history[0]
        assert trade.exit_price == pytest.approx(99.0)
        # 99 is above the bar's high of 96 -- a price that did not exist.
        assert trade.exit_price > 96.0

    def test_at_open_policy_fills_at_the_gap(self):
        p = self._run(GapFillPolicy.AT_OPEN)
        assert p.trade_history[0].exit_price == pytest.approx(95.0)

    def test_the_gap_is_counted_in_both_modes(self):
        for policy in (GapFillPolicy.AT_LEVEL, GapFillPolicy.AT_OPEN):
            assert self._run(policy).gap_through_exits == 1

    def test_the_optimistic_fill_overstates_the_result(self):
        legacy_pnl = self._run(GapFillPolicy.AT_LEVEL).trade_history[0].pnl
        honest_pnl = self._run(GapFillPolicy.AT_OPEN).trade_history[0].pnl
        assert honest_pnl < legacy_pnl
        # 4 points of gap risk on 100 shares, invisible in legacy mode.
        assert legacy_pnl - honest_pnl == pytest.approx(400.0)

    def test_the_loss_exceeds_one_r_once_the_gap_is_modelled(self):
        """The defining property of gap risk: the stop stops bounding the loss."""
        p = self._run(GapFillPolicy.AT_OPEN)
        trade = p.trade_history[0]
        assert abs(trade.pnl) / trade.initial_risk > 1.0

    def test_the_books_balance_under_either_policy(self):
        for policy in (GapFillPolicy.AT_LEVEL, GapFillPolicy.AT_OPEN):
            assert reconcile_portfolio(self._run(policy)).ok

    def test_gap_and_slippage_compose(self):
        """A stop that gaps through still pays the spread on the way out."""
        p = make_portfolio(
            realism=dataclasses.replace(
                ExecutionRealismConfig.legacy(),
                gap_fill_policy=GapFillPolicy.AT_OPEN,
                exit_slippage=FixedTickExitSlippage.liquid_equity(stop_ticks=2),
            )
        )
        p.open_position(fill("buy", 100, 100.0), stop_price=99.0, timestamp=T0)
        p.check_exits({"TEST": bar(95.0, 96.0, 94.0, 95.5)}, FrozenClock(T0))
        p.update_equity({}, T0)

        # Gap to 95.00, then two ticks worse.
        assert p.trade_history[0].exit_price == pytest.approx(94.98)
        assert reconcile_portfolio(p).ok


class TestF7QuantityAndCashConservation:
    """A duplicated fill and a partial exit fill.

    Both are shapes the ledger has to survive: a duplicated entry must show up
    as an imbalance, and a partial exit must leave the remaining quantity
    accounted for rather than silently vanishing.
    """

    def test_a_scale_in_conserves_quantity_and_cash(self):
        p = make_portfolio()
        p.open_position(fill("buy", 60, 50.0), stop_price=49.0, timestamp=T0)
        p.add_to_position(fill("buy", 40, 51.0), timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)

        report = reconcile_portfolio(p)
        assert report.ok
        assert len(p.cash_ledger) == 3

    def test_a_duplicated_entry_fill_breaks_parity(self):
        """The duplicate is applied to the ledger only, so the position is intact.

        This simulates the accounting-layer failure the identity exists to
        catch: a fill recorded twice. A duplicate that also mutated the
        position would be a *different* bug, and would show up as an equity
        mismatch instead.
        """
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.close_position(fill("sell", 100, 52.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)
        assert reconcile_portfolio(p).ok

        p.cash_ledger.append(dataclasses.replace(p.cash_ledger[0]))
        report = reconcile_portfolio(p)
        assert not report.ok
        failed = {f.identity for f in report.failures}
        assert "entry_exit_quantity_parity" in failed

    def test_an_open_position_leaves_quantity_accounted_for(self):
        """A position still open is not an imbalance; it is a known holding."""
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        p.update_equity({"TEST": bar(50.0, 51.0, 49.5, 50.5)}, T0)

        report = reconcile_portfolio(p)
        parity = next(
            f for f in report.findings if f.identity == "entry_exit_quantity_parity"
        )
        assert parity.passed
        # But the equity identity cannot be evaluated while it is open.
        equity = next(
            f for f in report.findings if f.identity == "flat_at_end_equity"
        )
        assert not equity.applicable
        assert report.ok

    def test_a_partial_exit_is_not_representable_today(self):
        """Closing pops the whole position, so a partial exit is not expressible.

        Recorded rather than skipped. F7 asks for a partial exit fill, and the
        honest answer is that ``close_position`` has no notion of one: it pops
        the position whatever quantity the fill carries. The identity that
        would catch a mis-handled partial exit is in place and tested above via
        the ledger; this test pins the current limitation so that adding
        partial exits has to confront it.
        """
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        # A "partial" exit of 40 shares.
        p.close_position(fill("sell", 40, 52.0), exit_reason="TP", timestamp=T0)

        # The other 60 shares are gone: not held, not exited.
        assert "TEST" not in p.positions
        report = reconcile_portfolio(p)
        parity = next(
            f for f in report.findings if f.identity == "entry_exit_quantity_parity"
        )
        assert not parity.passed
        assert parity.residual == pytest.approx(60.0)

    def test_short_round_trip_conserves_cash(self):
        p = make_portfolio()
        p.open_position(fill("sell", 100, 50.0), stop_price=51.0, timestamp=T0)
        p.close_position(fill("buy", 100, 48.0), exit_reason="TP", timestamp=T0)
        p.update_equity({}, T0)
        assert reconcile_portfolio(p).ok

    def test_an_eod_close_conserves_cash(self):
        p = make_portfolio()
        p.open_position(fill("buy", 100, 50.0), stop_price=49.0, timestamp=T0)
        eod = datetime(2023, 6, 1, 15, 56, tzinfo=NY)
        p.check_exits({"TEST": bar(50.0, 50.5, 49.5, 50.2, ts=eod)}, FrozenClock(eod))
        p.update_equity({}, eod)

        assert p.trade_history[0].exit_reason == "EOD"
        assert reconcile_portfolio(p).ok
