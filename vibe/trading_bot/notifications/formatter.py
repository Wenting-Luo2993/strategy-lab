"""Discord embed message formatter for order notifications."""

from datetime import datetime
from typing import Dict, List, Any
from vibe.trading_bot.notifications.payloads import OrderNotificationPayload


class DiscordNotificationFormatter:
    """Formats order notification payloads into Discord embed messages.

    Creates Discord webhook payloads with proper formatting, colors, and field layout.
    Messages are kept under 2000 character limit per Discord API.
    """

    # Color scheme (discord colors as 0x-prefixed integers)
    COLORS = {
        "ORDER_SENT": 0x3498db,      # Blue
        "ORDER_FILLED": 0x2ecc71,    # Green
        "ORDER_CANCELLED": 0xe74c3c,  # Red
    }

    def format(self, payload: OrderNotificationPayload) -> Dict[str, Any]:
        """Format notification payload into Discord webhook message.

        Args:
            payload: Order notification payload

        Returns:
            Dictionary with 'embeds' key containing Discord embed data
        """
        color = self.COLORS.get(payload.event_type, 0x95a5a6)

        if payload.event_type == "ORDER_SENT":
            embed = self._format_order_sent(payload, color)
        elif payload.event_type == "ORDER_FILLED":
            embed = self._format_order_filled(payload, color)
        elif payload.event_type == "ORDER_CANCELLED":
            embed = self._format_order_cancelled(payload, color)
        else:
            embed = self._format_generic(payload, color)

        return {"embeds": [embed]}

    def _format_order_sent(
        self,
        payload: OrderNotificationPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format ORDER_SENT notification."""
        fields = [
            {"name": "Symbol", "value": payload.symbol, "inline": True},
            {"name": "Side", "value": payload.side.upper(), "inline": True},
            {"name": "Type", "value": payload.order_type, "inline": True},
            {"name": "Quantity", "value": f"{payload.quantity:,.0f}", "inline": True},
            {"name": "Strategy", "value": payload.strategy_name, "inline": True},
        ]

        if payload.order_price is not None:
            fields.append({
                "name": "Order Price",
                "value": f"${payload.order_price:.2f}",
                "inline": True
            })

        if payload.signal_reason:
            fields.append({
                "name": "Signal Reason",
                "value": payload.signal_reason,
                "inline": False
            })

        return {
            "title": f"📤 Order Sent - {payload.symbol}",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": f"Order ID: {payload.order_id}"}
        }

    def _format_order_filled(
        self,
        payload: OrderNotificationPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format ORDER_FILLED notification with P&L for closing trades."""
        fields = [
            {"name": "Symbol", "value": payload.symbol, "inline": True},
            {"name": "Side", "value": payload.side.upper(), "inline": True},
            {"name": "Type", "value": payload.order_type, "inline": True},
            {
                "name": "Fill Price",
                "value": f"${payload.fill_price:.2f}",
                "inline": True
            },
            {
                "name": "Filled Qty",
                "value": f"{payload.filled_quantity:,.0f}",
                "inline": True
            },
            {"name": "Strategy", "value": payload.strategy_name, "inline": True},
        ]

        # Add slippage if calculable
        slippage = payload.get_slippage()
        if slippage is not None:
            slippage_str = f"${slippage:.2f}"
            if abs(slippage) > 0.01:  # Only show if significant
                fields.append({
                    "name": "Slippage",
                    "value": slippage_str,
                    "inline": True
                })

        # Add P&L if available (closing trade)
        if payload.realized_pnl is not None and payload.position_size == 0:
            pnl_value = f"${payload.realized_pnl:+.2f}"
            if payload.realized_pnl_pct is not None:
                pnl_value += f" ({payload.realized_pnl_pct:+.1f}%)"

            fields.append({
                "name": "P&L (Closed)",
                "value": pnl_value,
                "inline": True
            })

        # Add remaining position if open
        if payload.position_size is not None and payload.position_size > 0:
            fields.append({
                "name": "Remaining Position",
                "value": f"{payload.position_size:,.0f}",
                "inline": True
            })

        if payload.signal_reason:
            fields.append({
                "name": "Reason",
                "value": payload.signal_reason,
                "inline": False
            })

        return {
            "title": f"✅ Order Filled - {payload.symbol}",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": f"Order ID: {payload.order_id}"}
        }

    def _format_order_cancelled(
        self,
        payload: OrderNotificationPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format ORDER_CANCELLED notification."""
        fields = [
            {"name": "Symbol", "value": payload.symbol, "inline": True},
            {"name": "Side", "value": payload.side.upper(), "inline": True},
            {"name": "Type", "value": payload.order_type, "inline": True},
            {"name": "Quantity", "value": f"{payload.quantity:,.0f}", "inline": True},
            {"name": "Strategy", "value": payload.strategy_name, "inline": True},
        ]

        if payload.filled_quantity is not None and payload.filled_quantity > 0:
            fields.append({
                "name": "Filled",
                "value": f"{payload.filled_quantity:,.0f}",
                "inline": True
            })

        if payload.remaining_quantity is not None and payload.remaining_quantity > 0:
            fields.append({
                "name": "Unfilled",
                "value": f"{payload.remaining_quantity:,.0f}",
                "inline": True
            })

        if payload.cancel_reason:
            fields.append({
                "name": "Reason",
                "value": payload.cancel_reason,
                "inline": False
            })

        return {
            "title": f"❌ Order Cancelled - {payload.symbol}",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": f"Order ID: {payload.order_id}"}
        }

    def _format_generic(
        self,
        payload: OrderNotificationPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format generic notification for unknown event types."""
        fields = [
            {"name": "Event Type", "value": payload.event_type, "inline": True},
            {"name": "Symbol", "value": payload.symbol, "inline": True},
            {"name": "Side", "value": payload.side.upper(), "inline": True},
        ]

        return {
            "title": f"📋 Order Event - {payload.symbol}",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": f"Order ID: {payload.order_id}"}
        }
