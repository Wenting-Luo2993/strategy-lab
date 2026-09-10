"""Execution realism tests (plan section 6, fixtures F5-F8, F10).

The central claim under test: legacy mode is bit-identical to the old
behaviour, while the *measurement* of optimistic assumptions is always on.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    BuyingPowerError,
    ExecutionRealismConfig,
    GapFillPolicy,
    IntrabarExitResolution,
    clamp_to_bar,
)
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar

_ET = ZoneInfo("America/New_York")


class FakeClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


def midday() -> datetime:
    return datetime(2024, 3, 5, 11, 0, tzinfo=_ET)


def make_bar(symbol: str, o: float, h: float, l: float, c: float,
             ts: datetime | None = None) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts or midday(),
        open=o, high=h, low=l, close=c,
        volume=100_000,
    )


def open_long(pm: PortfolioManager, *, entry: float, stop: float,
              tp: float | None, qty: float = 10) -> None:
    pm.open_position(
        FillResult(symbol="TEST", side="buy", filled_qty=qty, avg_price=entry),
        stop_price=stop,
        timestamp=midday(),
        take_profit=tp,
    )


def open_short(pm: PortfolioManager, *, entry: float, stop: float,
               tp: float | None, qty: float = 10) -> None:
    pm.open_position(
        FillResult(symbol="TEST", side="sell", filled_qty=qty, avg_price=entry),
        stop_price=stop,
        timestamp=midday(),
        take_profit=tp,
    )


# --------------------------------------------------------------------------
# Config surface
# --------------------------------------------------------------------------

class TestExecutionRealismConfig:
    def test_default_is_legacy(self):
        assert ExecutionRealismConfig().is_legacy
        assert ExecutionRealismConfig.legacy().is_legacy

    def test_legacy_defaults_match_historical_behaviour(self):
        cfg = ExecutionRealismConfig.legacy()
        assert cfg.intrabar_exit_resolution is IntrabarExitResolution.OPTIMISTIC
        assert cfg.gap_fill_policy is GapFillPolicy.AT_LEVEL
        assert cfg.enforce_buying_power is False

    def test_realistic_is_not_legacy(self):
        assert not ExecutionRealismConfig.realistic().is_legacy

    def test_identity_carries_settings_not_just_code_version(self):
        a = ExecutionRealismConfig.legacy().identity()
        b = ExecutionRealismConfig.realistic().identity()
        assert a != b, "two incomparable runs must not share an identity"
        assert a["execution_model_version"] == EXECUTION_MODEL_VERSION

    def test_rejects_nonpositive_leverage(self):
        with pytest.raises(ValueError, match="max_gross_leverage"):
            ExecutionRealismConfig(max_gross_leverage=0)

    def test_config_is_frozen(self):
        cfg = ExecutionRealismConfig()
        with pytest.raises(Exception):
            cfg.enforce_buying_power = True  # type: ignore[misc]


class TestClampToBar:
    def test_passes_through_price_inside_range(self):
        assert clamp_to_bar(95.0, 90.0, 100.0) == 95.0

    def test_clamps_above_high(self):
        assert clamp_to_bar(120.0, 90.0, 100.0) == 100.0

    def test_clamps_below_low(self):
        assert clamp_to_bar(10.0, 90.0, 100.0) == 90.0

    def test_rejects_inverted_bar(self):
        with pytest.raises(ValueError, match="below low"):
            clamp_to_bar(95.0, 100.0, 90.0)


# --------------------------------------------------------------------------
# F5 / F6: intrabar ambiguity
# --------------------------------------------------------------------------

class TestIntrabarAmbiguity:
    """A bar touching both stop and target cannot be resolved from OHLC."""

    def _ambiguous_long_bar(self) -> Bar:
        # Entry 100, stop 99, target 102. Bar spans both.
        return make_bar("TEST", o=100.0, h=102.5, l=98.5, c=100.0)

    def test_legacy_books_the_win(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        pm.check_exits({"TEST": self._ambiguous_long_bar()},
                       FakeClock(midday()))
        assert pm.trade_history[0].exit_reason == "TP"

    def test_conservative_books_the_loss(self):
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE
            ),
        )
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        pm.check_exits({"TEST": self._ambiguous_long_bar()},
                       FakeClock(midday()))
        assert pm.trade_history[0].exit_reason == "STOP"

    def test_ambiguity_is_counted_even_in_legacy_mode(self):
        """The whole point: an optimistic run still discloses its optimism."""
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        pm.check_exits({"TEST": self._ambiguous_long_bar()},
                       FakeClock(midday()))
        assert pm.ambiguous_exit_bars == 1

    def test_unambiguous_bar_is_not_counted(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        # Touches target only.
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 102.5, 99.5, 102.0)},
                       FakeClock(midday()))
        assert pm.ambiguous_exit_bars == 0
        assert pm.trade_history[0].exit_reason == "TP"

    def test_short_ambiguity_resolves_by_policy(self):
        bar = make_bar("TEST", o=100.0, h=101.5, l=97.5, c=100.0)
        optimistic = PortfolioManager(100_000)
        open_short(optimistic, entry=100.0, stop=101.0, tp=98.0)
        optimistic.check_exits({"TEST": bar}, FakeClock(midday()))
        assert optimistic.trade_history[0].exit_reason == "TP"

        conservative = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE
            ),
        )
        open_short(conservative, entry=100.0, stop=101.0, tp=98.0)
        conservative.check_exits({"TEST": bar}, FakeClock(midday()))
        assert conservative.trade_history[0].exit_reason == "STOP"
        assert conservative.ambiguous_exit_bars == 1

    def test_resolution_choice_changes_pnl_sign(self):
        """Quantifies the assumption rather than just naming it."""
        bar = self._ambiguous_long_bar()

        optimistic = PortfolioManager(100_000)
        open_long(optimistic, entry=100.0, stop=99.0, tp=102.0)
        optimistic.check_exits({"TEST": bar}, FakeClock(midday()))

        conservative = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE
            ),
        )
        open_long(conservative, entry=100.0, stop=99.0, tp=102.0)
        conservative.check_exits({"TEST": bar}, FakeClock(midday()))

        assert optimistic.trade_history[0].exit_price == 102.0
        assert conservative.trade_history[0].exit_price == 99.0


# --------------------------------------------------------------------------
# F7: gap-through fills
# --------------------------------------------------------------------------

class TestGapThroughFills:
    def _gap_down_bar(self) -> Bar:
        # Long stop at 99, but the bar opened at 95 - straight through.
        return make_bar("TEST", o=95.0, h=95.5, l=94.0, c=94.5)

    def test_legacy_fills_at_the_untraded_stop_price(self):
        """Documents the defect: 99 was never traded in this bar."""
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=None)
        bar = self._gap_down_bar()
        pm.check_exits({"TEST": bar}, FakeClock(midday()))
        assert pm.trade_history[0].exit_price == 99.0
        assert pm.trade_history[0].exit_price > bar.high

    def test_gap_is_counted_even_in_legacy_mode(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=None)
        pm.check_exits({"TEST": self._gap_down_bar()}, FakeClock(midday()))
        assert pm.gap_through_exits == 1

    def test_at_open_policy_fills_at_the_open(self):
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                gap_fill_policy=GapFillPolicy.AT_OPEN
            ),
        )
        open_long(pm, entry=100.0, stop=99.0, tp=None)
        pm.check_exits({"TEST": self._gap_down_bar()}, FakeClock(midday()))
        assert pm.trade_history[0].exit_price == 95.0

    def test_realistic_fill_never_lies_outside_the_bar(self):
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                gap_fill_policy=GapFillPolicy.AT_OPEN
            ),
        )
        open_long(pm, entry=100.0, stop=99.0, tp=None)
        bar = self._gap_down_bar()
        pm.check_exits({"TEST": bar}, FakeClock(midday()))
        exit_price = pm.trade_history[0].exit_price
        assert bar.low <= exit_price <= bar.high

    def test_no_gap_means_no_count_and_no_reprice(self):
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                gap_fill_policy=GapFillPolicy.AT_OPEN
            ),
        )
        open_long(pm, entry=100.0, stop=99.0, tp=None)
        # Opens above the stop, then trades down through it.
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 100.2, 98.5, 98.8)},
                       FakeClock(midday()))
        assert pm.gap_through_exits == 0
        assert pm.trade_history[0].exit_price == 99.0

    def test_short_gap_up_through_stop(self):
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                gap_fill_policy=GapFillPolicy.AT_OPEN
            ),
        )
        open_short(pm, entry=100.0, stop=101.0, tp=None)
        pm.check_exits({"TEST": make_bar("TEST", 105.0, 106.0, 104.5, 105.5)},
                       FakeClock(midday()))
        assert pm.gap_through_exits == 1
        assert pm.trade_history[0].exit_price == 105.0

    def test_favourable_gap_through_target_fills_at_open(self):
        """A sell limit gapped above its target fills better, not at the level."""
        pm = PortfolioManager(
            100_000,
            execution_realism=ExecutionRealismConfig(
                gap_fill_policy=GapFillPolicy.AT_OPEN
            ),
        )
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        pm.check_exits({"TEST": make_bar("TEST", 105.0, 106.0, 104.5, 105.5)},
                       FakeClock(midday()))
        assert pm.trade_history[0].exit_price == 105.0


# --------------------------------------------------------------------------
# F8: buying power
# --------------------------------------------------------------------------

class TestBuyingPower:
    def test_legacy_allows_unfunded_position(self):
        """Documents E3: sizing has no cash bound today."""
        pm = PortfolioManager(10_000)
        open_long(pm, entry=100.0, stop=99.0, tp=None, qty=10_000)
        assert pm.cash < 0

    def test_enforcement_rejects_unfunded_position(self):
        pm = PortfolioManager(
            10_000, execution_realism=ExecutionRealismConfig(
                enforce_buying_power=True
            )
        )
        with pytest.raises(BuyingPowerError, match="exceeds buying power"):
            open_long(pm, entry=100.0, stop=99.0, tp=None, qty=10_000)

    def test_enforcement_allows_funded_position(self):
        pm = PortfolioManager(
            10_000, execution_realism=ExecutionRealismConfig(
                enforce_buying_power=True
            )
        )
        open_long(pm, entry=100.0, stop=99.0, tp=None, qty=50)
        assert pm.cash == pytest.approx(5_000.0)

    def test_leverage_allowance_is_explicit(self):
        pm = PortfolioManager(
            10_000, execution_realism=ExecutionRealismConfig(
                enforce_buying_power=True, max_gross_leverage=2.0
            )
        )
        open_long(pm, entry=100.0, stop=99.0, tp=None, qty=150)
        assert pm.positions["TEST"].quantity == 150

    def test_min_cash_is_tracked(self):
        pm = PortfolioManager(10_000)
        open_long(pm, entry=100.0, stop=99.0, tp=None, qty=50)
        assert pm.min_cash == pytest.approx(5_000.0)
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 100.2, 98.5, 98.8)},
                       FakeClock(midday()))
        # Closing restores cash; the trough is what is retained.
        assert pm.min_cash == pytest.approx(5_000.0)

    def test_peak_leverage_is_recorded_without_enforcement(self):
        pm = PortfolioManager(10_000)
        open_long(pm, entry=100.0, stop=99.0, tp=None, qty=150)
        pm.update_equity({"TEST": make_bar("TEST", 100.0, 100.0, 100.0, 100.0)},
                         midday())
        assert pm.max_gross_exposure_ratio == pytest.approx(1.5)


# --------------------------------------------------------------------------
# F10: legacy equivalence
# --------------------------------------------------------------------------

class TestLegacyEquivalence:
    """Default construction must reproduce prior results exactly."""

    def _scenario(self, pm: PortfolioManager) -> list[float]:
        clock = FakeClock(midday())
        bars = [
            make_bar("TEST", 100.0, 100.5, 99.6, 100.2),
            make_bar("TEST", 100.2, 102.4, 99.8, 102.0),
        ]
        open_long(pm, entry=100.0, stop=99.5, tp=102.0)
        for bar in bars:
            pm.check_exits({"TEST": bar}, clock)
            pm.update_equity({"TEST": bar}, midday())
        return [t.exit_price for t in pm.trade_history]

    def test_default_and_explicit_legacy_agree(self):
        assert self._scenario(PortfolioManager(100_000)) == self._scenario(
            PortfolioManager(100_000,
                             execution_realism=ExecutionRealismConfig.legacy())
        )

    def test_default_exit_price_is_the_target(self):
        assert self._scenario(PortfolioManager(100_000)) == [102.0]

    def test_eod_exit_still_uses_close(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=95.0, tp=110.0)
        eod = datetime(2024, 3, 5, 15, 56, tzinfo=_ET)
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 101.0, 99.0, 100.5)},
                       FakeClock(eod))
        assert pm.trade_history[0].exit_reason == "EOD"
        assert pm.trade_history[0].exit_price == 100.5

    def test_triggered_exit_takes_priority_over_eod(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=110.0)
        eod = datetime(2024, 3, 5, 15, 56, tzinfo=_ET)
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 100.5, 98.0, 98.5)},
                       FakeClock(eod))
        assert pm.trade_history[0].exit_reason == "STOP"

    def test_missing_bar_leaves_position_open(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=102.0)
        pm.check_exits({}, FakeClock(midday()))
        assert "TEST" in pm.positions

    def test_r_multiple_denominator_still_anchored_to_entry_risk(self):
        pm = PortfolioManager(100_000)
        open_long(pm, entry=100.0, stop=99.0, tp=102.0, qty=10)
        pm.check_exits({"TEST": make_bar("TEST", 100.0, 102.5, 99.8, 102.0)},
                       FakeClock(midday()))
        assert pm.trade_history[0].initial_risk == pytest.approx(10.0)


# --------------------------------------------------------------------------
# Reconciliation identity
# --------------------------------------------------------------------------

class TestReconciliation:
    def test_equity_change_matches_realised_pnl_after_flat(self):
        pm = PortfolioManager(100_000)
        clock = FakeClock(midday())
        open_long(pm, entry=100.0, stop=99.0, tp=102.0, qty=10)
        bar = make_bar("TEST", 100.0, 102.5, 99.8, 102.0)
        pm.check_exits({"TEST": bar}, clock)
        pm.update_equity({}, midday() + timedelta(minutes=5))

        realised = sum(
            (t.exit_price - t.entry_price) * t.quantity
            for t in pm.trade_history
        )
        assert pm.equity_curve[-1][1] - pm.initial_capital == pytest.approx(
            realised
        )
        assert pm.cash == pytest.approx(pm.initial_capital + realised)
