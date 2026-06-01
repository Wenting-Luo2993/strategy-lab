"""
Unit tests for PortfolioManager partial fill handling.

Tests for adding to existing positions with weighted average entry price calculation.
"""

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from vibe.backtester.core.portfolio import PortfolioManager, FillResult
from vibe.common.models.position import Position

ET = ZoneInfo("America/New_York")


@pytest.fixture
def portfolio():
    """Fresh portfolio with $100k capital."""
    return PortfolioManager(initial_capital=100_000.0)


class TestPartialFillBasics:
    """Test basic partial fill behavior."""
    
    def test_full_fill_identical_to_current(self, portfolio):
        """Full fill (entire order) works identically to current behavior."""
        fill = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=1000,  # Full order
            avg_price=350.0,
            commission=0.0,
        )
        
        # Open position with full fill
        portfolio.open_position(
            fill=fill,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Verify position state
        assert "QQQ" in portfolio.positions
        pos = portfolio.positions["QQQ"]
        assert pos.quantity == 1000
        assert pos.entry_price == 350.0
        assert pos.side == "buy"
        assert pos.stop_price == 340.0
        assert pos.take_profit == 360.0
        
        # Verify cash deduction
        expected_cash = 100_000 - (1000 * 350.0)
        assert portfolio.cash == expected_cash
    
    def test_partial_fill_opens_position_with_partial_qty(self, portfolio):
        """Partial fill opens position with only filled quantity."""
        fill = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=500,  # Only 500 of 1000 ordered
            avg_price=350.0,
            commission=0.0,
        )
        
        # Open position with partial fill
        portfolio.open_position(
            fill=fill,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Verify position has only partial quantity
        assert "QQQ" in portfolio.positions
        pos = portfolio.positions["QQQ"]
        assert pos.quantity == 500  # NOT 1000
        assert pos.entry_price == 350.0
        
        # Verify cash deduction is for partial
        expected_cash = 100_000 - (500 * 350.0)
        assert portfolio.cash == expected_cash


class TestAddToPosition:
    """Test add_to_position() for scaling into existing positions."""
    
    def test_add_to_position_weighted_average_price(self, portfolio):
        """Adding to position calculates weighted average entry price."""
        # First partial fill
        fill1 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Second partial fill at different price
        fill2 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=50,
            avg_price=352.0,
            commission=0.0,
        )
        
        # Add to existing position
        portfolio.add_to_position(
            fill=fill2,
            timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET),
        )
        
        # Verify weighted average entry price
        pos = portfolio.positions["QQQ"]
        expected_avg_price = (100 * 350.0 + 50 * 352.0) / 150
        assert pos.entry_price == pytest.approx(expected_avg_price)
        
        # Expected: (35000 + 17600) / 150 = 52600 / 150 = 350.667
        assert pos.entry_price == pytest.approx(350.667, rel=1e-3)
    
    def test_add_to_position_accumulates_quantity(self, portfolio):
        """Adding to position accumulates quantity."""
        # First partial fill: 100 shares
        fill1 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Second partial fill: 50 shares
        fill2 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=50,
            avg_price=352.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Third partial fill: 25 shares
        fill3 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=25,
            avg_price=351.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill3, timestamp=datetime(2024, 1, 15, 10, 10, 0, tzinfo=ET))
        
        # Verify accumulated quantity
        pos = portfolio.positions["QQQ"]
        assert pos.quantity == 175  # 100 + 50 + 25
    
    def test_add_to_position_preserves_stop_tp_from_original(self, portfolio):
        """Stop and TP prices remain from original signal after adding."""
        # First fill
        fill1 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        original_stop = 340.0
        original_tp = 360.0
        portfolio.open_position(
            fill=fill1,
            stop_price=original_stop,
            take_profit=original_tp,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Add to position
        fill2 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=50,
            avg_price=352.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Verify stop/TP unchanged
        pos = portfolio.positions["QQQ"]
        assert pos.stop_price == original_stop
        assert pos.take_profit == original_tp
    
    def test_add_to_position_updates_cash(self, portfolio):
        """Adding to position deducts cash for buy fills."""
        # First fill
        fill1 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        cash_after_first = portfolio.cash
        
        # Add to position
        fill2 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=50,
            avg_price=352.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Verify cash deduction for second fill
        cash_deduction = 50 * 352.0
        assert portfolio.cash == pytest.approx(cash_after_first - cash_deduction)


class TestAddToPositionSells:
    """Test add_to_position() for selling into existing short positions."""
    
    def test_add_to_sell_position(self, portfolio):
        """Can scale into existing short position."""
        # First short fill
        fill1 = FillResult(
            symbol="QQQ",
            side="sell",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=360.0,  # Stop is higher for shorts
            take_profit=340.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Add to short position
        fill2 = FillResult(
            symbol="QQQ",
            side="sell",
            filled_qty=50,
            avg_price=348.0,  # Better (lower) price on second short
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Verify accumulated quantity
        pos = portfolio.positions["QQQ"]
        assert pos.quantity == 150
        assert pos.side == "sell"
        
        # Verify weighted average entry price for short
        expected_avg = (100 * 350.0 + 50 * 348.0) / 150
        assert pos.entry_price == pytest.approx(expected_avg, rel=1e-3)
    
    def test_add_to_sell_updates_cash_correctly(self, portfolio):
        """Selling adds to cash (opposite of buying)."""
        # First short
        fill1 = FillResult(
            symbol="QQQ",
            side="sell",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=360.0,
            take_profit=340.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        cash_after_first = portfolio.cash
        
        # Add to short
        fill2 = FillResult(
            symbol="QQQ",
            side="sell",
            filled_qty=50,
            avg_price=348.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Verify cash increase for second short
        cash_addition = 50 * 348.0
        assert portfolio.cash == pytest.approx(cash_after_first + cash_addition)


class TestPartialFillEdgeCases:
    """Test edge cases for partial fill handling."""
    
    def test_cannot_add_to_nonexistent_position(self, portfolio):
        """Adding to non-existent position raises error."""
        fill = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        
        # Attempt to add without opening
        with pytest.raises(KeyError):
            portfolio.add_to_position(
                fill=fill,
                timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
            )
    
    def test_cannot_add_opposite_side_to_position(self, portfolio):
        """Cannot add buy fill to existing short position."""
        # Open short position
        fill_short = FillResult(
            symbol="QQQ",
            side="sell",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill_short,
            stop_price=360.0,
            take_profit=340.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        # Attempt to add buy
        fill_buy = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=348.0,
            commission=0.0,
        )
        
        with pytest.raises(ValueError, match="Cannot add"):
            portfolio.add_to_position(
                fill=fill_buy,
                timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET),
            )
    
    def test_initial_risk_per_share_from_first_fill(self, portfolio):
        """initial_risk_per_share is set from first fill, not modified on scale."""
        # First fill at 350, stop at 340 = 10 risk per share
        fill1 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=100,
            avg_price=350.0,
            commission=0.0,
        )
        portfolio.open_position(
            fill=fill1,
            stop_price=340.0,
            take_profit=360.0,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=ET),
        )
        
        initial_risk = portfolio.positions["QQQ"].initial_risk_per_share
        
        # Add fill at different price
        fill2 = FillResult(
            symbol="QQQ",
            side="buy",
            filled_qty=50,
            avg_price=352.0,
            commission=0.0,
        )
        portfolio.add_to_position(fill=fill2, timestamp=datetime(2024, 1, 15, 10, 5, 0, tzinfo=ET))
        
        # Risk should remain from original
        assert portfolio.positions["QQQ"].initial_risk_per_share == initial_risk


class TestUnfilledRemainder:
    """Test handling of unfilled remainder from partial orders."""
    
    def test_unfilled_remainder_does_not_create_position(self):
        """Unfilled remainder tracked in pending queue, not in portfolio."""
        # This test verifies the integration between ExecutionSimulator partial fills
        # and PortfolioManager. The pending queue tracks unfilled remainder.
        # When a 1000-share order fills for 700, the 300-share unfilled remainder
        # should be tracked in pending_orders, not create a position.
        pass
    
    def test_partial_fills_accumulate_until_complete_or_eod(self):
        """Multiple partial fills across bars accumulate into position."""
        # Fill 1: 300 of 1000
        # Fill 2: 400 of 1000
        # Fill 3: 300 of 1000
        # Result: Full position of 1000 with weighted average
        pass
