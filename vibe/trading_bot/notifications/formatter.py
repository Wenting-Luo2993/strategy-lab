"""Discord embed message formatter for order notifications."""

from datetime import datetime
from typing import Dict, List, Any
from vibe.trading_bot.notifications.payloads import OrderNotificationPayload, SystemStatusPayload


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
        "MARKET_START": 0x2ecc71,    # Green
        "MARKET_CLOSE": 0xf39c12,    # Orange
    }

    STATUS_COLORS = {
        "healthy": 0x2ecc71,      # Green
        "degraded": 0xf39c12,     # Orange
        "unhealthy": 0xe74c3c,    # Red
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

    def format_system_status(self, payload: SystemStatusPayload) -> Dict[str, Any]:
        """Format system status payload into Discord webhook message.

        Args:
            payload: System status notification payload

        Returns:
            Dictionary with 'embeds' key containing Discord embed data
        """
        # Choose color based on overall status
        color = self.STATUS_COLORS.get(payload.overall_status, 0x95a5a6)

        if payload.event_type == "MARKET_START":
            embed = self._format_market_start(payload, color)
        elif payload.event_type == "MARKET_CLOSE":
            embed = self._format_market_close(payload, color)
        else:
            embed = self._format_generic_status(payload, color)

        return {"embeds": [embed]}

    def _format_market_start(
        self,
        payload: SystemStatusPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format MARKET_START notification."""
        # Status emoji
        status_emoji = {
            "healthy": "✅",
            "degraded": "⚠️",
            "unhealthy": "❌"
        }.get(payload.overall_status, "❓")

        fields = [
            {
                "name": "Overall Status",
                "value": f"{status_emoji} **{payload.overall_status.upper()}**",
                "inline": True
            }
        ]

        # Warm-up status
        if payload.warmup_completed is not None:
            warmup_emoji = "✅" if payload.warmup_completed else "❌"
            fields.append({
                "name": "Warm-up Phase",
                "value": f"{warmup_emoji} {'Completed' if payload.warmup_completed else 'Failed'}",
                "inline": True
            })

        # Primary provider
        if payload.primary_provider_name:
            provider_status = payload.primary_provider_status or "unknown"
            provider_emoji = {
                "connected": "🟢",
                "disconnected": "🔴",
                "error": "❌"
            }.get(provider_status, "❓")

            fields.append({
                "name": "Primary Data Source",
                "value": f"{provider_emoji} {payload.primary_provider_name} ({provider_status})",
                "inline": False
            })

        # WebSocket ping status
        if payload.websocket_ping_received is not None:
            ping_emoji = "✅" if payload.websocket_ping_received else "⚠️"
            ping_status = "Verified" if payload.websocket_ping_received else "Waiting"
            fields.append({
                "name": "WebSocket Ping/Pong",
                "value": f"{ping_emoji} {ping_status}",
                "inline": True
            })

        # Secondary provider (if exists)
        if payload.secondary_provider_name:
            secondary_status = payload.secondary_provider_status or "unknown"
            secondary_emoji = {
                "connected": "🟢",
                "disconnected": "🔴",
                "error": "❌"
            }.get(secondary_status, "❓")

            fields.append({
                "name": "Fallback Data Source",
                "value": f"{secondary_emoji} {payload.secondary_provider_name} ({secondary_status})",
                "inline": False
            })

        # Additional details
        if payload.details:
            for key, value in payload.details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })

        # Footer with version info
        footer_text = "Trading Bot Status"
        if payload.version:
            footer_text = f"Trading Bot {payload.version}"

        return {
            "title": "🔔 Market Open - Trading Bot Ready",
            "description": "Bot has completed warm-up and is ready for trading",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": footer_text}
        }

    def _format_market_close(
        self,
        payload: SystemStatusPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format MARKET_CLOSE notification."""
        fields = []

        # Add any daily summary info from details
        if payload.details:
            for key, value in payload.details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })

        # Footer with version info
        footer_text = "Trading Bot Status"
        if payload.version:
            footer_text = f"Trading Bot {payload.version}"

        return {
            "title": "🌙 Market Closed - Cooldown Phase",
            "description": "Market has closed. Bot entering 5-minute cooldown for final processing.",
            "color": color,
            "fields": fields if fields else [{"name": "Status", "value": "Cooldown in progress", "inline": False}],
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": footer_text}
        }

    def _format_generic_status(
        self,
        payload: SystemStatusPayload,
        color: int
    ) -> Dict[str, Any]:
        """Format generic system status notification."""
        fields = [
            {
                "name": "Event Type",
                "value": payload.event_type,
                "inline": True
            },
            {
                "name": "Status",
                "value": payload.overall_status,
                "inline": True
            }
        ]

        if payload.details:
            for key, value in payload.details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })

        return {
            "title": "📊 System Status Update",
            "color": color,
            "fields": fields,
            "timestamp": payload.timestamp.isoformat(),
            "footer": {"text": "Trading Bot Status"}
        }
