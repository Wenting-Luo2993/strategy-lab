"""
Unit tests for execution simulator data models (Order, Fill).
"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from vibe.backtester.core.execution.models import Order, Fill

ET = ZoneInfo("America/New_York")


class TestOrder:
    """Test Order dataclass creation and validation."""
    
    def test_order_creation_with_valid_fields(self):
        """Test creating a valid market order."""
        order = Order(
            id="order-1",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
        )
        assert order.id == "order-1"
        assert order.symbol == "QQQ"
        assert order.side == "buy"
        assert order.size == 100
        assert order.order_type == "market"
        assert order.price_override is None
    
    def test_order_with_price_override(self):
        """Test creating order with price_override field."""
        order = Order(
            id="order-2",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
            price_override=101.50,
        )
        assert order.price_override == 101.50
    
    def test_order_rejects_invalid_side(self):
        """Test that invalid side raises ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            Order(
                id="order-3",
                symbol="QQQ",
                side="short",  # Invalid
                size=100,
                order_type="market",
                limit_price=None,
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_order_rejects_invalid_order_type(self):
        """Test that invalid order_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid order_type"):
            Order(
                id="order-4",
                symbol="QQQ",
                side="buy",
                size=100,
                order_type="stop",  # Invalid
                limit_price=None,
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_limit_order_without_limit_price_raises(self):
        """Test that limit order without limit_price raises ValueError."""
        with pytest.raises(ValueError, match="Limit orders must have a limit_price"):
            Order(
                id="order-5",
                symbol="QQQ",
                side="buy",
                size=100,
                order_type="limit",
                limit_price=None,  # Missing
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_market_order_with_limit_price_raises(self):
        """Test that market order with limit_price raises ValueError."""
        with pytest.raises(ValueError, match="Market orders cannot have a limit_price"):
            Order(
                id="order-6",
                symbol="QQQ",
                side="buy",
                size=100,
                order_type="market",
                limit_price=102.0,  # Should not have this
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_order_rejects_zero_size(self):
        """Test that zero size raises ValueError."""
        with pytest.raises(ValueError, match="Order size must be positive"):
            Order(
                id="order-7",
                symbol="QQQ",
                side="buy",
                size=0,  # Invalid
                order_type="market",
                limit_price=None,
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_order_rejects_negative_size(self):
        """Test that negative size raises ValueError."""
        with pytest.raises(ValueError, match="Order size must be positive"):
            Order(
                id="order-8",
                symbol="QQQ",
                side="buy",
                size=-100,  # Invalid
                order_type="market",
                limit_price=None,
                timestamp=datetime.now(ET),
                signal_bar_index=10,
            )
    
    def test_order_rejects_negative_signal_bar_index(self):
        """Test that negative signal_bar_index raises ValueError."""
        with pytest.raises(ValueError, match="signal_bar_index must be non-negative"):
            Order(
                id="order-9",
                symbol="QQQ",
                side="buy",
                size=100,
                order_type="market",
                limit_price=None,
                timestamp=datetime.now(ET),
                signal_bar_index=-1,  # Invalid
            )
    
    def test_order_remaining_reduces_size(self):
        """Test Order.remaining() with partial fill."""
        order = Order(
            id="order-10",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
        )
        
        remainder = order.remaining(filled_qty=30)
        
        assert remainder.size == 70
        assert remainder.id == "order-10"
    
    def test_order_remaining_preserves_other_fields(self):
        """Test that remaining() preserves all non-size fields."""
        ts = datetime.now(ET)
        order = Order(
            id="order-11",
            symbol="QQQ",
            side="sell",
            size=100,
            order_type="limit",
            limit_price=102.5,
            timestamp=ts,
            signal_bar_index=5,
            price_override=101.0,
        )
        
        remainder = order.remaining(filled_qty=50)
        
        assert remainder.id == "order-11"
        assert remainder.symbol == "QQQ"
        assert remainder.side == "sell"
        assert remainder.order_type == "limit"
        assert remainder.limit_price == 102.5
        assert remainder.timestamp == ts
        assert remainder.signal_bar_index == 5
        assert remainder.price_override == 101.0
        assert remainder.size == 50
    
    def test_order_remaining_raises_when_filled_exceeds_size(self):
        """Test that remaining() raises ValueError when filled_qty > size."""
        order = Order(
            id="order-12",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
        )
        
        with pytest.raises(ValueError, match="filled_qty .* exceeds order size"):
            order.remaining(filled_qty=150)
    
    def test_order_remaining_exact_partial(self):
        """Test remaining() with exact partial fill."""
        order = Order(
            id="order-13",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
        )
        
        # When order is exactly filled, don't call remaining() at higher level
        # But if called, it should raise error (size becomes 0)
        with pytest.raises(ValueError, match="Order size must be positive"):
            order.remaining(filled_qty=100)
    
    def test_order_remaining_one_share(self):
        """Test remaining() with minimal unfilled quantity."""
        order = Order(
            id="order-14",
            symbol="QQQ",
            side="buy",
            size=100,
            order_type="market",
            limit_price=None,
            timestamp=datetime.now(ET),
            signal_bar_index=10,
        )
        
        remainder = order.remaining(filled_qty=99)
        
        assert remainder.size == 1
        assert remainder.id == "order-14"


class TestFill:
    """Test Fill dataclass creation and validation."""
    
    def test_fill_creation(self):
        """Test creating a valid fill."""
        fill = Fill(
            order_id="order-1",
            symbol="QQQ",
            side="buy",
            price=100.50,
            qty=50,
            timestamp=datetime.now(ET),
            slippage=0.05,
            impact=0.02,
        )
        assert fill.order_id == "order-1"
        assert fill.symbol == "QQQ"
        assert fill.side == "buy"
        assert fill.price == 100.50
        assert fill.qty == 50
        assert fill.slippage == 0.05
        assert fill.impact == 0.02
    
    def test_fill_default_slippage_and_impact(self):
        """Test that slippage and impact default to 0.0."""
        fill = Fill(
            order_id="order-2",
            symbol="QQQ",
            side="sell",
            price=99.75,
            qty=100,
            timestamp=datetime.now(ET),
        )
        assert fill.slippage == 0.0
        assert fill.impact == 0.0
    
    def test_fill_rejects_invalid_side(self):
        """Test that invalid side raises ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            Fill(
                order_id="order-3",
                symbol="QQQ",
                side="long",  # Invalid
                price=100.0,
                qty=100,
                timestamp=datetime.now(ET),
            )
    
    def test_fill_rejects_zero_price(self):
        """Test that zero price raises ValueError."""
        with pytest.raises(ValueError, match="Fill price must be positive"):
            Fill(
                order_id="order-4",
                symbol="QQQ",
                side="buy",
                price=0,  # Invalid
                qty=100,
                timestamp=datetime.now(ET),
            )
    
    def test_fill_rejects_negative_price(self):
        """Test that negative price raises ValueError."""
        with pytest.raises(ValueError, match="Fill price must be positive"):
            Fill(
                order_id="order-5",
                symbol="QQQ",
                side="buy",
                price=-100.0,  # Invalid
                qty=100,
                timestamp=datetime.now(ET),
            )
    
    def test_fill_rejects_zero_quantity(self):
        """Test that zero quantity raises ValueError."""
        with pytest.raises(ValueError, match="Fill quantity must be positive"):
            Fill(
                order_id="order-6",
                symbol="QQQ",
                side="buy",
                price=100.0,
                qty=0,  # Invalid
                timestamp=datetime.now(ET),
            )
    
    def test_fill_rejects_negative_quantity(self):
        """Test that negative quantity raises ValueError."""
        with pytest.raises(ValueError, match="Fill quantity must be positive"):
            Fill(
                order_id="order-7",
                symbol="QQQ",
                side="buy",
                price=100.0,
                qty=-50,  # Invalid
                timestamp=datetime.now(ET),
            )
    
    def test_fill_with_negative_slippage(self):
        """Test that negative slippage is allowed (represents better execution)."""
        fill = Fill(
            order_id="order-8",
            symbol="QQQ",
            side="buy",
            price=99.95,  # Better than expected
            qty=100,
            timestamp=datetime.now(ET),
            slippage=-0.05,  # Negative slippage (good execution)
        )
        assert fill.slippage == -0.05
    
    def test_fill_with_zero_impact(self):
        """Test fill with zero impact."""
        fill = Fill(
            order_id="order-9",
            symbol="QQQ",
            side="sell",
            price=100.0,
            qty=100,
            timestamp=datetime.now(ET),
            impact=0.0,
        )
        assert fill.impact == 0.0
