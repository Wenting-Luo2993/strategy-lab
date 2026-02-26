"""Discord notification payload schemas."""

from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, Dict, Any
import json


@dataclass
class OrderNotificationPayload:
    """Notification payload for order lifecycle events.

    Supports ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED events with appropriate
    fields per event type.
    """

    # Required fields
    event_type: str  # ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED
    timestamp: datetime
    order_id: str
    symbol: str
    side: str  # buy or sell
    order_type: str  # market, limit, etc.
    quantity: float
    strategy_name: str

    # Optional fields - present for most events
    signal_reason: Optional[str] = None

    # ORDER_FILLED specific fields
    fill_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    remaining_quantity: Optional[float] = None

    # ORDER_FILLED P&L fields (for closing trades)
    realized_pnl: Optional[float] = None
    realized_pnl_pct: Optional[float] = None
    position_size: Optional[float] = None  # Position size after fill (0 if closed)

    # ORDER_CANCELLED specific field
    cancel_reason: Optional[str] = None

    # Price tracking for slippage calculation
    order_price: Optional[float] = None  # Price at order submission

    # Additional metadata
    exchange: str = field(default="ALPACA")
    account_id: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        """Validate payload per event type."""
        valid_event_types = {"ORDER_SENT", "ORDER_FILLED", "ORDER_CANCELLED"}
        if self.event_type not in valid_event_types:
            raise ValueError(
                f"Invalid event_type: {self.event_type}. "
                f"Must be one of {valid_event_types}"
            )

        valid_sides = {"buy", "sell"}
        if self.side not in valid_sides:
            raise ValueError(
                f"Invalid side: {self.side}. Must be 'buy' or 'sell'"
            )

        # Validate event-specific fields
        if self.event_type == "ORDER_FILLED":
            if self.fill_price is None:
                raise ValueError("ORDER_FILLED requires fill_price")
            if self.filled_quantity is None:
                raise ValueError("ORDER_FILLED requires filled_quantity")
        elif self.event_type == "ORDER_CANCELLED":
            if self.cancel_reason is None:
                raise ValueError("ORDER_CANCELLED requires cancel_reason")

    def to_dict(self) -> dict:
        """Convert payload to dictionary, handling datetime serialization."""
        data = asdict(self)
        # Convert datetime to ISO format string
        data["timestamp"] = self.timestamp.isoformat()
        return data

    def to_json(self) -> str:
        """Convert payload to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    def get_slippage(self) -> Optional[float]:
        """Calculate slippage in dollars if applicable.

        Returns:
            Slippage amount or None if not calculable
        """
        if (self.event_type != "ORDER_FILLED" or
            self.order_price is None or
            self.fill_price is None):
            return None

        if self.side == "buy":
            # For buy orders, positive slippage means paid more
            slippage = (self.fill_price - self.order_price) * self.filled_quantity
        else:
            # For sell orders, positive slippage means received less
            slippage = (self.order_price - self.fill_price) * self.filled_quantity

        return slippage

    def get_slippage_pct(self) -> Optional[float]:
        """Calculate slippage as percentage if applicable.

        Returns:
            Slippage percentage or None if not calculable
        """
        if self.order_price is None:
            return None

        if self.order_price == 0:
            return None

        slippage = self.get_slippage()
        if slippage is None:
            return None

        return (slippage / (self.order_price * self.filled_quantity)) * 100


@dataclass
class SystemStatusPayload:
    """Notification payload for system status events (market start/close).

    Supports MARKET_START, MARKET_CLOSE events with health check information.
    """

    # Required fields
    event_type: str  # MARKET_START, MARKET_CLOSE
    timestamp: datetime

    # Health status fields
    overall_status: str  # healthy, degraded, unhealthy
    warmup_completed: Optional[bool] = None
    primary_provider_status: Optional[str] = None  # connected, disconnected, error
    primary_provider_name: Optional[str] = None
    secondary_provider_status: Optional[str] = None
    secondary_provider_name: Optional[str] = None
    websocket_ping_received: Optional[bool] = None  # For MARKET_START - confirms ping/pong

    # Market status
    market_status: Optional[str] = None  # open, closed, pre_market, after_hours

    # Additional details
    details: Optional[Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate payload."""
        valid_event_types = {"MARKET_START", "MARKET_CLOSE"}
        if self.event_type not in valid_event_types:
            raise ValueError(
                f"Invalid event_type: {self.event_type}. "
                f"Must be one of {valid_event_types}"
            )

    def to_dict(self) -> dict:
        """Convert payload to dictionary, handling datetime serialization."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    def to_json(self) -> str:
        """Convert payload to JSON string."""
        return json.dumps(self.to_dict(), default=str)
