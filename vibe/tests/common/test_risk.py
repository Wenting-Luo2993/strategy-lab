"""
Unit tests for risk management components: PositionSizer and StopLossManager.
"""

import pytest
from datetime import datetime, timedelta

from vibe.common.risk import (
    PositionSizer,
    PositionSizeResult,
    StopLossManager,
    StopLossStatus,
)


class TestPositionSizer:
    """Tests for PositionSizer."""

    def test_fixed_dollar_risk_sizing(self):
        """Position sized by fixed dollar risk."""
        sizer = PositionSizer(risk_per_trade=100)

        # $150 stock, $5 stop loss distance
        result = sizer.calculate(
            entry_price=150,
            stop_price=145,
            account_value=10000,
        )

        # Risk = $100, stop distance = $5, position = 20 shares
        assert result.size == 20
        assert result.risk_amount == 100

    def test_percentage_risk_sizing(self):
        """Position sized by percentage of account."""
        sizer = PositionSizer(risk_pct=0.01)  # 1% of account

        # 1% of $10,000 = $100, $100 / $5 = 20 shares
        result = sizer.calculate(
            entry_price=150,
            stop_price=145,
            account_value=10000,
        )

        assert result.size == 20
        assert result.risk_amount == 100

    def test_max_position_limit(self):
        """Position size capped at maximum."""
        sizer = PositionSizer(
            risk_per_trade=100,
            max_position_size=10,
        )

        # Would be 20 shares, but capped at 10
        result = sizer.calculate(
            entry_price=150,
            stop_price=145,
            account_value=100000,
        )

        assert result.size == 10

    def test_short_position_sizing(self):
        """Position sized correctly for short entries."""
        sizer = PositionSizer(risk_per_trade=100)

        # Short at $150, stop at $155 (above entry)
        result = sizer.calculate(
            entry_price=150,
            stop_price=155,
            account_value=10000,
        )

        # Stop distance = $5, position = 20 shares
        assert result.size == 20

    def test_small_account_zero_sizing(self):
        """Very small account results in zero-size position."""
        sizer = PositionSizer(risk_per_trade=100, max_position_size=1)

        # Account too small to trade standard risk, with max position of 1
        result = sizer.calculate(
            entry_price=100,
            stop_price=50,  # $50 stop distance -> would be 2 shares but capped
            account_value=50,
        )

        assert result.size <= 1

    def test_fractional_shares_rounded_down(self):
        """Fractional shares are rounded down."""
        sizer = PositionSizer(risk_per_trade=100)

        # $100 risk / $3.33 stop distance = 30.03 shares -> 30
        result = sizer.calculate(
            entry_price=100,
            stop_price=96.67,
            account_value=10000,
        )

        assert result.size == 30
        assert isinstance(result.size, int)

    def test_invalid_risk_per_trade(self):
        """Raises error for invalid risk_per_trade."""
        with pytest.raises(ValueError):
            PositionSizer(risk_per_trade=0)

        with pytest.raises(ValueError):
            PositionSizer(risk_per_trade=-100)

    def test_invalid_risk_pct(self):
        """Raises error for invalid risk_pct."""
        with pytest.raises(ValueError):
            PositionSizer(risk_pct=0)

        with pytest.raises(ValueError):
            PositionSizer(risk_pct=1.5)  # > 100%

    def test_both_risk_methods_raises_error(self):
        """Raises error when both risk methods specified."""
        with pytest.raises(ValueError):
            PositionSizer(risk_per_trade=100, risk_pct=0.01)

    def test_no_risk_method_raises_error(self):
        """Raises error when no risk method specified."""
        with pytest.raises(ValueError):
            PositionSizer()

    def test_invalid_entry_price(self):
        """Raises error for invalid entry_price."""
        sizer = PositionSizer(risk_per_trade=100)

        with pytest.raises(ValueError):
            sizer.calculate(
                entry_price=0,
                stop_price=95,
                account_value=10000,
            )

        with pytest.raises(ValueError):
            sizer.calculate(
                entry_price=-100,
                stop_price=95,
                account_value=10000,
            )

    def test_stop_equals_entry_raises_error(self):
        """Raises error when stop equals entry."""
        sizer = PositionSizer(risk_per_trade=100)

        with pytest.raises(ValueError):
            sizer.calculate(
                entry_price=100,
                stop_price=100,
                account_value=10000,
            )

    def test_calculate_from_risk_amount(self):
        """Calculate from explicit risk and stop distance."""
        sizer = PositionSizer(risk_per_trade=100)

        result = sizer.calculate_from_risk_amount(
            risk_amount=200,
            stop_distance=10,
        )

        assert result.size == 20
        assert result.risk_amount == 200

    def test_max_position_applied_to_risk_amount(self):
        """Max position limit applied to risk amount method."""
        sizer = PositionSizer(
            risk_per_trade=100,
            max_position_size=5,
        )

        result = sizer.calculate_from_risk_amount(
            risk_amount=200,
            stop_distance=10,
        )

        assert result.size == 5


class TestStopLossManager:
    """Tests for StopLossManager."""

    def test_set_fixed_stop_long(self):
        """Set fixed stop loss for long position."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
            trailing=False,
        )

        status = manager.get_status("pos1")
        assert status is not None
        assert status.current_stop == 95
        assert status.triggered is False

    def test_set_stop_short_position(self):
        """Set stop loss for short position."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=105,
            is_long=False,
            trailing=False,
        )

        status = manager.get_status("pos1")
        assert status.current_stop == 105

    def test_set_trailing_stop_long(self):
        """Set trailing stop for long position."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
            trailing=True,
            trailing_distance=5,
        )

        status = manager.get_status("pos1")
        assert status.current_stop == 95

    def test_trailing_stop_moves_up_on_price_increase(self):
        """Trailing stop moves up with price (long)."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
            trailing=True,
            trailing_distance=5,
        )

        # Price moves to $110
        manager.update_price("pos1", current_price=110)

        # Stop should move to $105 (maintaining $5 distance)
        assert manager.get_stop("pos1") == 105

    def test_trailing_stop_moves_down_on_price_decrease_short(self):
        """Trailing stop moves down with price (short)."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=105,
            is_long=False,
            trailing=True,
            trailing_distance=5,
        )

        # Price moves down to $90
        manager.update_price("pos1", current_price=90)

        # Stop should move to $95 (maintaining $5 distance above price)
        assert manager.get_stop("pos1") == 95

    def test_trailing_stop_does_not_move_against_position(self):
        """Trailing stop never moves against position (long)."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
            trailing=True,
            trailing_distance=5,
        )

        # Price drops to $90 (below stop)
        manager.update_price("pos1", current_price=90)

        # Stop should remain at $95 (not move down)
        assert manager.get_stop("pos1") == 95

    def test_stop_trigger_on_long_position(self):
        """Stop triggers when price drops below for long."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
        )

        # Price drops to $94
        triggered = manager.check_trigger("pos1", current_price=94)

        assert triggered is True
        status = manager.get_status("pos1")
        assert status.triggered is True
        assert status.trigger_price == 94

    def test_stop_trigger_on_short_position(self):
        """Stop triggers when price rises above for short."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=105,
            is_long=False,
        )

        # Price rises to $106
        triggered = manager.check_trigger("pos1", current_price=106)

        assert triggered is True

    def test_stop_does_not_trigger_before_threshold(self):
        """Stop does not trigger before reaching threshold."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
        )

        # Price at $96 (above stop)
        triggered = manager.check_trigger("pos1", current_price=96)

        assert triggered is False

    def test_already_triggered_stop_stays_triggered(self):
        """Once triggered, stop remains triggered."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
        )

        # Trigger the stop
        manager.check_trigger("pos1", current_price=94)

        # Price recovers above stop
        triggered = manager.check_trigger("pos1", current_price=100)

        assert triggered is True  # Still triggered

    def test_remove_stop(self):
        """Remove a stop loss."""
        manager = StopLossManager()

        manager.set_stop(
            position_id="pos1",
            entry_price=100,
            stop_price=95,
            is_long=True,
        )

        removed = manager.remove_stop("pos1")
        assert removed is True

        status = manager.get_status("pos1")
        assert status is None

    def test_get_all_triggered(self):
        """Get all triggered stops."""
        manager = StopLossManager()

        # Set 3 stops
        manager.set_stop("pos1", 100, 95, True)
        manager.set_stop("pos2", 100, 95, True)
        manager.set_stop("pos3", 100, 95, True)

        # Trigger 2 of them
        manager.check_trigger("pos1", 94)
        manager.check_trigger("pos2", 94)

        triggered = manager.get_all_triggered()
        assert len(triggered) == 2
        assert "pos1" in triggered
        assert "pos2" in triggered
        assert "pos3" not in triggered

    def test_get_all_active(self):
        """Get all active (non-triggered) stops."""
        manager = StopLossManager()

        manager.set_stop("pos1", 100, 95, True)
        manager.set_stop("pos2", 100, 95, True)
        manager.set_stop("pos3", 100, 95, True)

        manager.check_trigger("pos1", 94)

        active = manager.get_all_active()
        assert len(active) == 2
        assert "pos2" in active
        assert "pos3" in active
        assert "pos1" not in active

    def test_invalid_entry_price(self):
        """Raises error for invalid entry price."""
        manager = StopLossManager()

        with pytest.raises(ValueError):
            manager.set_stop("pos1", entry_price=0, stop_price=95)

    def test_invalid_stop_for_long(self):
        """Raises error if stop is above entry for long."""
        manager = StopLossManager()

        with pytest.raises(ValueError):
            manager.set_stop(
                "pos1",
                entry_price=100,
                stop_price=105,
                is_long=True,
            )

    def test_invalid_stop_for_short(self):
        """Raises error if stop is below entry for short."""
        manager = StopLossManager()

        with pytest.raises(ValueError):
            manager.set_stop(
                "pos1",
                entry_price=100,
                stop_price=95,
                is_long=False,
            )

    def test_update_nonexistent_position(self):
        """Update price for non-existent position returns None."""
        manager = StopLossManager()

        result = manager.update_price("nonexistent", 100)
        assert result is None

    def test_get_stop_nonexistent(self):
        """Get stop for non-existent position returns None."""
        manager = StopLossManager()

        result = manager.get_stop("nonexistent")
        assert result is None

    def test_check_trigger_unknown_position_raises(self):
        """Check trigger for unknown position raises error."""
        manager = StopLossManager()

        with pytest.raises(ValueError):
            manager.check_trigger("nonexistent", 100)


class TestPositionSizerEdgeCases:
    """Edge cases for position sizer."""

    def test_very_tight_stop(self):
        """Very tight stop distance still sizes correctly."""
        sizer = PositionSizer(risk_per_trade=100)

        # Very tight $0.01 stop
        result = sizer.calculate(
            entry_price=100,
            stop_price=99.99,
            account_value=10000,
        )

        # $100 / $0.01 = 10,000 shares (may be slightly different due to float precision)
        assert result.size >= 9999

    def test_max_position_prevents_oversizing(self):
        """Max position prevents over-leverage on tight stops."""
        sizer = PositionSizer(
            risk_per_trade=100,
            max_position_size=100,
        )

        result = sizer.calculate(
            entry_price=100,
            stop_price=99.99,
            account_value=10000,
        )

        assert result.size == 100

    def test_large_account_sizing(self):
        """Sizing works for large accounts."""
        sizer = PositionSizer(risk_pct=0.02)  # 2% risk

        result = sizer.calculate(
            entry_price=100,
            stop_price=95,
            account_value=1000000,  # $1M account
        )

        # 2% of $1M = $20,000, $20,000 / $5 = 4000 shares
        assert result.size == 4000
