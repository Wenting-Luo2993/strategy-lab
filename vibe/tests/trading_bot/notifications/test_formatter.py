"""Tests for Discord message formatter."""

import pytest
from datetime import datetime

from vibe.trading_bot.notifications.payloads import OrderNotificationPayload
from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter


class TestDiscordNotificationFormatter:
    """Test Discord message formatter."""

    @pytest.fixture
    def formatter(self):
        """Create formatter."""
        return DiscordNotificationFormatter()

    def test_format_order_sent(self, formatter):
        """Test ORDER_SENT message formatting."""
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

        message = formatter.format(payload)

        assert "embeds" in message
        assert len(message["embeds"]) == 1

        embed = message["embeds"][0]
        assert embed["color"] == 0x3498db  # Blue
        assert "Order Sent" in embed["title"]
        assert "AAPL" in embed["title"]

        # Check fields
        field_names = [f["name"] for f in embed["fields"]]
        assert "Symbol" in field_names
        assert "Side" in field_names
        assert "Quantity" in field_names

    def test_format_order_filled(self, formatter):
        """Test ORDER_FILLED message formatting."""
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

        message = formatter.format(payload)

        embed = message["embeds"][0]
        assert embed["color"] == 0x2ecc71  # Green
        assert "Order Filled" in embed["title"]

    def test_format_order_filled_with_pnl(self, formatter):
        """Test ORDER_FILLED shows P&L for closing trades."""
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

        message = formatter.format(payload)

        embed = message["embeds"][0]

        # Check for P&L field
        pnl_field = next(
            (f for f in embed["fields"] if "P&L" in f["name"]),
            None
        )
        assert pnl_field is not None
        assert "83.00" in pnl_field["value"]

    def test_format_order_cancelled(self, formatter):
        """Test ORDER_CANCELLED message formatting."""
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

        message = formatter.format(payload)

        embed = message["embeds"][0]
        assert embed["color"] == 0xe74c3c  # Red
        assert "Order Cancelled" in embed["title"]

        # Check for reason field
        reason_field = next(
            (f for f in embed["fields"] if f["name"] == "Reason"),
            None
        )
        assert reason_field is not None
        assert "Timeout" in reason_field["value"]

    def test_message_has_order_id_footer(self, formatter):
        """Test message includes order ID in footer."""
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

        message = formatter.format(payload)

        embed = message["embeds"][0]
        assert "footer" in embed
        assert "ord_123" in embed["footer"]["text"]

    def test_message_has_timestamp(self, formatter):
        """Test message includes timestamp."""
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

        message = formatter.format(payload)

        embed = message["embeds"][0]
        assert "timestamp" in embed

    def test_message_under_2000_chars(self, formatter):
        """Test messages stay under Discord's 2000 character limit."""
        payload = OrderNotificationPayload(
            event_type="ORDER_SENT",
            timestamp=datetime.now(),
            order_id="ord_123",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=50,
            strategy_name="ORB",
            signal_reason="This is a very long signal reason " * 10
        )

        message = formatter.format(payload)

        embed = message["embeds"][0]

        # Calculate total message size
        import json
        message_str = json.dumps(message)
        assert len(message_str) < 2000

    def test_color_scheme(self, formatter):
        """Test correct colors for each event type."""
        assert formatter.COLORS["ORDER_SENT"] == 0x3498db  # Blue
        assert formatter.COLORS["ORDER_FILLED"] == 0x2ecc71  # Green
        assert formatter.COLORS["ORDER_CANCELLED"] == 0xe74c3c  # Red

    def test_cancelled_shows_filled_unfilled(self, formatter):
        """Test cancelled order shows filled and unfilled quantities."""
        payload = OrderNotificationPayload(
            event_type="ORDER_CANCELLED",
            timestamp=datetime.now(),
            order_id="ord_789",
            symbol="GOOGL",
            side="buy",
            order_type="limit",
            quantity=100,
            filled_quantity=30,
            remaining_quantity=70,
            cancel_reason="Timeout",
            strategy_name="ORB"
        )

        message = formatter.format(payload)

        embed = message["embeds"][0]
        field_values = " ".join(f["value"] for f in embed["fields"])

        assert "30" in field_values  # Filled qty
        assert "70" in field_values  # Remaining qty

    def test_all_required_fields_in_order_sent(self, formatter):
        """Test ORDER_SENT includes all required fields."""
        payload = OrderNotificationPayload(
            event_type="ORDER_SENT",
            timestamp=datetime.now(),
            order_id="ord_123",
            symbol="AAPL",
            side="buy",
            order_type="market",
            quantity=50,
            strategy_name="ORB",
            signal_reason="Test reason"
        )

        message = formatter.format(payload)

        embed = message["embeds"][0]
        field_names = [f["name"] for f in embed["fields"]]

        assert "Symbol" in field_names
        assert "Side" in field_names
        assert "Type" in field_names
        assert "Quantity" in field_names
        assert "Strategy" in field_names
