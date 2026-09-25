"""Buying power must gate sizing, in simulation and in live trading alike.

Risk-based sizing is unbounded by construction: as the stop tightens, implied
size grows without limit. Before this gate, a backtest of orb_production on
QQQ 2019-2023 reached -$26.7M of cash and 10.8x gross leverage, and the live
bot fetched IB's BuyingPower but never consulted it - relying on the broker to
reject. These tests pin the gate at the one place both paths share.
"""

from __future__ import annotations

import pytest

from vibe.common.risk.position_sizer import PositionSizer


def _sizer(**kwargs) -> PositionSizer:
    kwargs.setdefault("risk_pct", 0.01)
    return PositionSizer(**kwargs)


class TestBuyingPowerIsOptional:
    """None must mean "unenforced", never "broke"."""

    def test_omitting_buying_power_leaves_sizing_unbounded(self):
        result = _sizer().calculate(
            entry_price=100.0, stop_price=99.9, account_value=100_000.0
        )
        # $1000 risk over a $0.10 stop implies 10,000 shares = $1,000,000
        # of stock on $100k of capital. Unbounded, and deliberately so.
        assert result.size == 10_000
        assert result.capped_by is None

    def test_none_is_not_treated_as_zero(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.0,
            account_value=100_000.0,
            buying_power=None,
        )
        assert result.size > 0


class TestBuyingPowerClamps:
    def test_position_is_reduced_to_what_is_affordable(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            buying_power=50_000.0,
        )
        assert result.size == 500
        assert result.capped_by == "buying_power"

    def test_notional_never_exceeds_buying_power(self):
        buying_power = 12_345.0
        result = _sizer().calculate(
            entry_price=97.3,
            stop_price=97.0,
            account_value=100_000.0,
            buying_power=buying_power,
        )
        assert result.size * 97.3 <= buying_power

    def test_requested_size_is_preserved_for_comparison(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            buying_power=50_000.0,
        )
        assert result.requested_size == pytest.approx(10_000.0)
        assert result.size == 500

    def test_affordable_position_is_left_alone(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=90.0,
            account_value=100_000.0,
            buying_power=100_000.0,
        )
        assert result.capped_by is None
        assert result.size == 100

    def test_zero_buying_power_yields_no_position(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.0,
            account_value=100_000.0,
            buying_power=0.0,
        )
        assert result.size == 0

    def test_buying_power_below_one_share_yields_no_position(self):
        result = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.0,
            account_value=100_000.0,
            buying_power=99.0,
        )
        assert result.size == 0

    def test_negative_buying_power_is_rejected(self):
        with pytest.raises(ValueError, match="buying_power"):
            _sizer().calculate(
                entry_price=100.0,
                stop_price=99.0,
                account_value=100_000.0,
                buying_power=-1.0,
            )


class TestCapPrecedence:
    """Affordability is applied last, so it binds over policy caps."""

    def test_buying_power_overrides_a_looser_share_cap(self):
        result = _sizer(max_position_size=5_000).calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            buying_power=50_000.0,
        )
        assert result.size == 500
        assert result.capped_by == "buying_power"

    def test_tighter_share_cap_still_wins_on_size(self):
        result = _sizer(max_position_size=100).calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            buying_power=50_000.0,
        )
        assert result.size == 100
        assert result.capped_by == "max_position_size"

    def test_notional_cap_is_reported_when_it_binds(self):
        result = _sizer(max_position_pct=0.1).calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
        )
        assert result.size == 100
        assert result.capped_by == "max_position_pct"


class TestExistingPositionIsNotDoubleCounted:
    def test_existing_position_does_not_shrink_buying_power(self):
        """Broker buying power is already net of open positions.

        Subtracting them again would halve every follow-on position.
        """
        without = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            buying_power=50_000.0,
        )
        with_existing = _sizer().calculate(
            entry_price=100.0,
            stop_price=99.9,
            account_value=100_000.0,
            existing_position_size=250.0,
            buying_power=50_000.0,
        )
        assert with_existing.size == without.size
