"""E4 - cost model, and fixture F10.

Section 6 E4: the simulator applied no commission anywhere and no exit-side
cost, so a round trip was free on one side. F10 in section 12 is the same run
at zero and non-zero commission.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.commission import CommissionModel
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.trade import Trade

BASE = datetime(2022, 3, 1, 14, 30, tzinfo=timezone.utc)


class TestCommissionModel:
    def test_zero_model_charges_nothing(self):
        assert CommissionModel.zero().cost(100, 50.0) == 0.0

    def test_zero_model_is_reported_as_zero(self):
        assert CommissionModel.zero().is_zero
        assert not CommissionModel.ibkr_pro_tiered().is_zero

    def test_tiered_per_share_rate(self):
        # 1000 shares x $0.0035 = $3.50, above the $0.35 minimum and below
        # the 1% cap of $500.
        assert CommissionModel.ibkr_pro_tiered().cost(1000, 50.0) == pytest.approx(3.50)

    def test_per_order_minimum_applies_to_small_orders(self):
        # 10 shares x $0.0035 = $0.035, raised to the $0.35 minimum. The 1%
        # cap on $500 of notional is $5.00, so it does not bind.
        assert CommissionModel.ibkr_pro_tiered().cost(10, 50.0) == pytest.approx(0.35)

    def test_notional_cap_overrides_the_minimum(self):
        """A 1-share $10 trade cannot be charged the $0.35 minimum."""
        # 1% of $10 is $0.10, below the $0.35 minimum, so the cap wins.
        assert CommissionModel.ibkr_pro_tiered().cost(1, 10.0) == pytest.approx(0.10)

    def test_fixed_schedule_has_a_higher_minimum(self):
        assert CommissionModel.ibkr_pro_fixed().cost(10, 500.0) == pytest.approx(1.00)

    def test_side_does_not_change_cost(self):
        model = CommissionModel.ibkr_pro_tiered()
        assert model.cost(-500, 50.0) == model.cost(500, 50.0)

    def test_empty_fill_is_not_billed_a_minimum(self):
        assert CommissionModel.ibkr_pro_tiered().cost(0, 50.0) == 0.0

    def test_other_per_share_is_added(self):
        model = CommissionModel(per_share=0.001, other_per_share=0.002)
        assert model.cost(1000, 50.0) == pytest.approx(3.0)

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="per_share"):
            CommissionModel(per_share=-0.01)

    def test_identity_distinguishes_schedules(self):
        assert (
            CommissionModel.ibkr_pro_tiered().identity()
            != CommissionModel.ibkr_pro_fixed().identity()
        )


class TestRealismConfigWiring:
    def test_legacy_has_no_costs(self):
        assert ExecutionRealismConfig.legacy().commission_model.is_zero

    def test_realistic_defaults_to_ibkr_tiered(self):
        cfg = ExecutionRealismConfig.realistic()
        assert cfg.commission_model.name == "ibkr_pro_tiered"

    def test_commission_model_is_overridable(self):
        cfg = ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.zero()
        )
        assert cfg.commission_model.is_zero

    def test_identity_includes_the_schedule(self):
        assert "commission_model" in ExecutionRealismConfig.realistic().identity()

    def test_identity_differs_by_schedule(self):
        """Two runs differing only in costs must not share a fingerprint."""
        a = ExecutionRealismConfig.realistic().identity()
        b = ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.ibkr_pro_fixed()
        ).identity()
        assert a != b


def _round_trip(portfolio: PortfolioManager, qty=100.0, entry=50.0, exit_=51.0):
    portfolio.open_position(
        FillResult(symbol="QQQ", side="buy", filled_qty=qty, avg_price=entry),
        stop_price=entry - 1.0,
        timestamp=BASE,
    )
    portfolio.close_position(
        FillResult(symbol="QQQ", side="sell", filled_qty=qty, avg_price=exit_),
        exit_reason="EOD",
        timestamp=BASE + timedelta(hours=5),
    )


class TestPortfolioCharging:
    def test_costs_are_charged_on_both_legs(self):
        model = CommissionModel.ibkr_pro_tiered()
        p = PortfolioManager(
            100_000.0,
            execution_realism=ExecutionRealismConfig.realistic(
                commission_model=model
            ),
        )
        _round_trip(p)
        expected = model.cost(100, 50.0) + model.cost(100, 51.0)
        assert p.total_costs == pytest.approx(expected)
        assert p.trade_history[0].commission == pytest.approx(expected)

    def test_exit_side_is_not_free(self):
        """The defect E4 names: exits filled at exact levels at no cost."""
        model = CommissionModel.ibkr_pro_tiered()
        p = PortfolioManager(
            100_000.0,
            execution_realism=ExecutionRealismConfig.realistic(
                commission_model=model
            ),
        )
        _round_trip(p)
        assert model.cost(100, 51.0) > 0
        assert p.trade_history[0].commission > model.cost(100, 50.0)

    def test_cash_is_reduced_by_costs(self):
        p = PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.realistic()
        )
        _round_trip(p)
        gross = 100 * (51.0 - 50.0)
        assert p.cash == pytest.approx(100_000.0 + gross - p.total_costs)

    def test_legacy_run_pays_nothing(self):
        p = PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.legacy()
        )
        _round_trip(p)
        assert p.total_costs == 0.0
        assert p.trade_history[0].commission == 0.0


class TestBuyingPowerReservesCosts:
    """A funded gate must set aside the costs the position will incur."""

    @staticmethod
    def _portfolio(model: CommissionModel) -> PortfolioManager:
        return PortfolioManager(
            100_000.0,
            execution_realism=ExecutionRealismConfig.realistic(
                commission_model=model
            ),
        )

    def test_reserve_reduces_available_buying_power(self):
        p = self._portfolio(CommissionModel.ibkr_pro_tiered())
        assert p.available_buying_power(50.0) < p.available_buying_power()

    def test_zero_commission_reserves_nothing(self):
        p = self._portfolio(CommissionModel.zero())
        assert p.available_buying_power(50.0) == p.available_buying_power()

    def test_price_is_optional_and_skips_the_reserve(self):
        p = self._portfolio(CommissionModel.ibkr_pro_tiered())
        assert p.available_buying_power() == pytest.approx(100_000.0)

    def test_reserve_covers_both_legs(self):
        p = self._portfolio(CommissionModel.ibkr_pro_tiered())
        raw = p.available_buying_power()
        net = p.available_buying_power(50.0)
        one_leg = CommissionModel.ibkr_pro_tiered().cost(raw / 50.0, 50.0)
        assert raw - net == pytest.approx(2.0 * one_leg)

    def test_unenforced_account_still_returns_none(self):
        p = PortfolioManager(
            100_000.0, execution_realism=ExecutionRealismConfig.legacy()
        )
        assert p.available_buying_power(50.0) is None

    def test_max_size_position_does_not_overdraw(self):
        """The regression: a full-size entry left cash negative."""
        p = self._portfolio(CommissionModel.ibkr_pro_tiered())
        price = 50.0
        shares = int(p.available_buying_power(price) / price)
        p.open_position(
            FillResult(
                symbol="QQQ", side="buy", filled_qty=float(shares), avg_price=price
            ),
            stop_price=price - 1.0,
            timestamp=BASE,
        )
        p.close_position(
            FillResult(
                symbol="QQQ", side="sell", filled_qty=float(shares), avg_price=price
            ),
            exit_reason="EOD",
            timestamp=BASE + timedelta(hours=5),
        )
        assert p.min_cash >= 0.0


def _trade(pnl_per_share: float, commission: float, qty: float = 100.0) -> Trade:
    return Trade(
        symbol="QQQ", side="buy", quantity=qty,
        entry_price=50.0, exit_price=50.0 + pnl_per_share,
        entry_time=BASE, exit_time=BASE + timedelta(hours=1),
        initial_risk=100.0, exit_reason="EOD", commission=commission,
    )


class TestCostMetrics:
    def test_reconciliation_identity_holds(self):
        """gross_pnl - total_costs == net_pnl, the E4 invariant."""
        m = PerformanceAnalyzer._calc_convexity(
            [_trade(1.0, 3.5), _trade(-0.5, 3.5)]
        )
        assert m.gross_pnl - m.total_costs == pytest.approx(m.total_pnl)

    def test_total_costs_is_positive_when_trades_exist(self):
        m = PerformanceAnalyzer._calc_convexity([_trade(1.0, 3.5)])
        assert m.total_costs > 0

    def test_r_multiples_are_net_of_costs(self):
        """A 100-unit gross win with 100 of costs is a breakeven trade."""
        m = PerformanceAnalyzer._calc_convexity([_trade(1.0, 100.0)])
        assert m.r_multiples[0] == pytest.approx(0.0)
        assert m.breakeven_trades == 1

    def test_costs_can_turn_a_marginal_win_into_a_loss(self):
        m = PerformanceAnalyzer._calc_convexity([_trade(0.5, 60.0)])
        assert m.winning_trades == 0
        assert m.losing_trades == 1

    def test_zero_cost_run_reports_gross_equal_to_net(self):
        m = PerformanceAnalyzer._calc_convexity([_trade(1.0, 0.0)])
        assert m.total_costs == 0.0
        assert m.gross_pnl == pytest.approx(m.total_pnl)

    def test_no_trades_reports_zero_costs(self):
        m = PerformanceAnalyzer._calc_convexity([])
        assert m.total_costs == 0.0
        assert m.gross_pnl == 0.0


class TestF10ZeroVersusNonZeroCommission:
    """F10: the same run at zero and non-zero commission."""

    @staticmethod
    def _run(model: CommissionModel) -> PortfolioManager:
        p = PortfolioManager(
            100_000.0,
            execution_realism=ExecutionRealismConfig.realistic(
                commission_model=model
            ),
        )
        for day in range(5):
            p.open_position(
                FillResult(symbol="QQQ", side="buy", filled_qty=100.0, avg_price=50.0),
                stop_price=49.0,
                timestamp=BASE + timedelta(days=day),
            )
            p.close_position(
                FillResult(symbol="QQQ", side="sell", filled_qty=100.0, avg_price=51.0),
                exit_reason="EOD",
                timestamp=BASE + timedelta(days=day, hours=5),
            )
        return p

    def test_trades_are_identical_but_costs_are_not(self):
        free = self._run(CommissionModel.zero())
        paid = self._run(CommissionModel.ibkr_pro_tiered())

        assert len(free.trade_history) == len(paid.trade_history)
        for a, b in zip(free.trade_history, paid.trade_history):
            assert a.entry_price == b.entry_price
            assert a.exit_price == b.exit_price
            assert a.quantity == b.quantity
        assert free.total_costs == 0.0
        assert paid.total_costs > 0.0

    def test_costs_reduce_net_pnl_by_exactly_total_costs(self):
        free = PerformanceAnalyzer._calc_convexity(
            self._run(CommissionModel.zero()).trade_history
        )
        paid_portfolio = self._run(CommissionModel.ibkr_pro_tiered())
        paid = PerformanceAnalyzer._calc_convexity(paid_portfolio.trade_history)

        assert paid.gross_pnl == pytest.approx(free.gross_pnl)
        assert paid.total_pnl == pytest.approx(
            free.total_pnl - paid_portfolio.total_costs
        )

    def test_expectancy_is_monotonically_worse_with_costs(self):
        free = PerformanceAnalyzer._calc_convexity(
            self._run(CommissionModel.zero()).trade_history
        )
        paid = PerformanceAnalyzer._calc_convexity(
            self._run(CommissionModel.ibkr_pro_tiered()).trade_history
        )
        assert paid.expectancy_r < free.expectancy_r
