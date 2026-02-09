"""Tests for Discord notification payloads."""

import pytest
from datetime import datetime

from vibe.trading_bot.notifications.payloads import OrderNotificationPayload


class TestOrderNotificationPayload:
    """Test order notification payload."""

    def test_order_sent_payload(self):
        """Test ORDER_SENT payload creation."""
        payload = OrderNotificationPayload(
            event_type="ORDER_SENT",
            timestamp=datetime.now(),
            order_id="ord_123",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=50,
            strategy_name="ORB",
            signal_reason="ORB breakout above $185.50"
        )

        assert payload.event_type == "ORDER_SENT"
        assert payload.symbol == "AAPL"
        assert payload.side == "buy"
        assert payload.quantity == 50
        assert payload.strategy_name == "ORB"

    def test_order_filled_payload(self):
        """Test ORDER_FILLED payload creation."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_456",
            symbol="AAPL",
            side="sell",
            order_type="market",
            quantity=50,
            fill_price=187.25,
            filled_quantity=50,
            strategy_name="ORB",
            signal_reason="Take profit hit"
        )

        assert payload.event_type == "ORDER_FILLED"
        assert payload.fill_price == 187.25
        assert payload.filled_quantity == 50

    def test_order_filled_with_pnl(self):
        """Test ORDER_FILLED with P&L calculation."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_456",
            symbol="AAPL",
            side="sell",
            order_type="market",
            quantity=50,
            fill_price=187.25,
            filled_quantity=50,
            realized_pnl=83.00,
            realized_pnl_pct=0.90,
            position_size=0,  # Closed
            strategy_name="ORB",
            signal_reason="Take profit hit"
        )

        assert payload.realized_pnl == 83.00
        assert payload.realized_pnl_pct == 0.90
        assert payload.position_size == 0

    def test_order_cancelled_payload(self):
        """Test ORDER_CANCELLED payload creation."""
        payload = OrderNotificationPayload(
            event_type="ORDER_CANCELLED",
            timestamp=datetime.now(),
            order_id="ord_789",
            symbol="GOOGL",
            side="buy",
            order_type="limit",
            quantity=100,
            cancel_reason="Timeout after 60s",
            strategy_name="ORB"
        )

        assert payload.event_type == "ORDER_CANCELLED"
        assert payload.cancel_reason == "Timeout after 60s"

    def test_invalid_event_type(self):
        """Test error on invalid event type."""
        with pytest.raises(ValueError, match="Invalid event_type"):
            OrderNotificationPayload(
                event_type="INVALID",
                timestamp=datetime.now(),
                order_id="ord_1",
                symbol="AAPL",
                side="buy",
                order_type="market",
                quantity=50,
                strategy_name="ORB"
            )

    def test_invalid_side(self):
        """Test error on invalid side."""
        with pytest.raises(ValueError, match="Invalid side"):
            OrderNotificationPayload(
                event_type="ORDER_SENT",
                timestamp=datetime.now(),
                order_id="ord_1",
                symbol="AAPL",
                side="invalid",
                order_type="market",
                quantity=50,
                strategy_name="ORB"
            )

    def test_order_filled_requires_fill_price(self):
        """Test ORDER_FILLED requires fill_price."""
        with pytest.raises(ValueError, match="requires fill_price"):
            OrderNotificationPayload(
                event_type="ORDER_FILLED",
                timestamp=datetime.now(),
                order_id="ord_1",
                symbol="AAPL",
                side="buy",
                order_type="market",
                quantity=50,
                strategy_name="ORB"
            )

    def test_order_filled_requires_filled_quantity(self):
        """Test ORDER_FILLED requires filled_quantity."""
        with pytest.raises(ValueError, match="requires filled_quantity"):
            OrderNotificationPayload(
                event_type="ORDER_FILLED",
                timestamp=datetime.now(),
                order_id="ord_1",
                symbol="AAPL",
                side="buy",
                order_type="market",
                quantity=50,
                fill_price=180.0,
                strategy_name="ORB"
            )

    def test_order_cancelled_requires_reason(self):
        """Test ORDER_CANCELLED requires cancel_reason."""
        with pytest.raises(ValueError, match="requires cancel_reason"):
            OrderNotificationPayload(
                event_type="ORDER_CANCELLED",
                timestamp=datetime.now(),
                order_id="ord_1",
                symbol="AAPL",
                side="buy",
                order_type="market",
                quantity=50,
                strategy_name="ORB"
            )

    def test_to_dict(self):
        """Test payload serialization to dict."""
        now = datetime.now()
        payload = OrderNotificationPayload(
            event_type="ORDER_SENT",
            timestamp=now,
            order_id="ord_123",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=50,
            strategy_name="ORB"
        )

        data = payload.to_dict()

        assert data["event_type"] == "ORDER_SENT"
        assert data["order_id"] == "ord_123"
        assert isinstance(data["timestamp"], str)

    def test_to_json(self):
        """Test payload serialization to JSON."""
        payload = OrderNotificationPayload(
            event_type="ORDER_SENT",
            timestamp=datetime.now(),
            order_id="ord_123",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=50,
            strategy_name="ORB"
        )

        json_str = payload.to_json()

        assert isinstance(json_str, str)
        assert "ORDER_SENT" in json_str
        assert "AAPL" in json_str

    def test_get_slippage_buy(self):
        """Test slippage calculation for buy order."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=100,
            order_price=180.00,
            fill_price=180.50,
            filled_quantity=100,
            strategy_name="ORB"
        )

        slippage = payload.get_slippage()

        # Slippage = (fill_price - order_price) * quantity
        # = (180.50 - 180.00) * 100 = 50
        assert slippage == pytest.approx(50.0)

    def test_get_slippage_sell(self):
        """Test slippage calculation for sell order."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_1",
            symbol="AAPL",
            side="sell",
            order_type="market",
            quantity=100,
            order_price=180.00,
            fill_price=179.50,
            filled_quantity=100,
            strategy_name="ORB"
        )

        slippage = payload.get_slippage()

        # Slippage = (order_price - fill_price) * quantity
        # = (180.00 - 179.50) * 100 = 50
        assert slippage == pytest.approx(50.0)

    def test_get_slippage_none_without_order_price(self):
        """Test slippage returns None without order_price."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=100,
            fill_price=180.50,
            filled_quantity=100,
            strategy_name="ORB"
        )

        assert payload.get_slippage() is None

    def test_get_slippage_pct(self):
        """Test slippage percentage calculation."""
        payload = OrderNotificationPayload(
            event_type="ORDER_FILLED",
            timestamp=datetime.now(),
            order_id="ord_1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=100,
            order_price=180.00,
            fill_price=181.80,
            filled_quantity=100,
            strategy_name="ORB"
        )

        slippage_pct = payload.get_slippage_pct()

        # Slippage % = (slippage / (order_price * quantity)) * 100
        # = (180 / (180 * 100)) * 100 = 1%
        assert slippage_pct == pytest.approx(1.0)
