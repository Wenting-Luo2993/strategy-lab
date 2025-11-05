"""
Data structures for representing orders and trades in the exchange system.
"""

from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any, List, Union
from datetime import datetime
import pandas as pd
import uuid
from enum import Enum


class OrderStatus(str, Enum):
    """Enumeration of possible order lifecycle states."""
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass
class Order:
    """
    Represents an order to be sent to an exchange.
    """
    ticker: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["market", "limit"]
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    limit_price: Optional[float] = None
    timestamp: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.now())

    def __post_init__(self):
        # Validate order type and limit price combination
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("Limit orders must specify a limit_price")

        # Ensure positive quantity
        if self.qty <= 0:
            raise ValueError("Order quantity must be positive")

        # Convert timestamp if provided as a different type
        if not isinstance(self.timestamp, pd.Timestamp):
            self.timestamp = pd.Timestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        """Convert order to a dictionary representation."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "timestamp": self.timestamp
        }


@dataclass
class OrderResponse:
    """
    Represents a response from the exchange after submitting an order.
    """
    order_id: str
    status: Union[OrderStatus, str]
    filled_qty: int
    avg_fill_price: Optional[float] = None
    commission: float = 0.0
    timestamp: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.now())
    message: Optional[str] = None

    def __post_init__(self):
        # Validate status and fill information
        # Normalize status to enum
        if isinstance(self.status, str):
            try:
                self.status = OrderStatus(self.status)
            except ValueError:
                raise ValueError(f"Unsupported order status: {self.status}")
        if self.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and self.avg_fill_price is None:
            raise ValueError(f"Orders with status '{self.status}' must have an average fill price")

        # Ensure filled quantity is non-negative
        if self.filled_qty < 0:
            raise ValueError("Filled quantity cannot be negative")

        # Convert timestamp if provided as a different type
        if not isinstance(self.timestamp, pd.Timestamp):
            self.timestamp = pd.Timestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        """Convert order response to a dictionary representation."""
        return {
            "order_id": self.order_id,
            "status": self.status.value if isinstance(self.status, OrderStatus) else self.status,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "commission": self.commission,
            "timestamp": self.timestamp,
            "message": self.message
        }


@dataclass
class Position:
    """
    Represents a position held in a security.
    """
    ticker: str
    qty: int  # Positive for long, negative for short
    avg_price: float
    market_price: float

    @property
    def market_value(self) -> float:
        """Calculate the current market value of the position."""
        return self.qty * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        """Calculate the unrealized profit/loss for this position."""
        return self.qty * (self.market_price - self.avg_price)

    def to_dict(self) -> Dict[str, Any]:
        """Convert position to a dictionary representation."""
        return {
            "ticker": self.ticker,
            "qty": self.qty,
            "avg_price": self.avg_price,
            "market_price": self.market_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl
        }


@dataclass
class Trade:
    """
    Represents a completed trade resulting from an order.
    """
    order_id: str
    ticker: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    timestamp: pd.Timestamp
    commission: float
    run_id: Optional[str] = None  # For tracking in orchestrator
    order_status: Optional[Union[OrderStatus, str]] = None  # Execution status at log time

    def __post_init__(self):
        # Convert timestamp if provided as a different type
        if not isinstance(self.timestamp, pd.Timestamp):
            self.timestamp = pd.Timestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        """Convert trade to a dictionary representation for logging/CSV output."""
        # Normalize status to enum for output
        status_val = None
        if self.order_status is not None:
            if isinstance(self.order_status, str):
                try:
                    status_val = OrderStatus(self.order_status).value
                except ValueError:
                    status_val = self.order_status  # leave raw if unexpected
            else:
                status_val = self.order_status.value
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "ticker": self.ticker,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "commission": self.commission,
            "order_id": self.order_id,
            "order_status": status_val
        }


@dataclass
class AccountState:
    """
    Represents the current state of a trading account.
    """
    cash: float
    positions_value: float = 0.0
    initial_capital: float = field(default=None)

    def __post_init__(self):
        # If initial capital isn't provided, use starting cash value
        if self.initial_capital is None:
            self.initial_capital = self.cash

    @property
    def equity(self) -> float:
        """Calculate total account equity (cash + positions value)."""
        return self.cash + self.positions_value

    @property
    def pnl_percentage(self) -> float:
        """Calculate profit/loss percentage relative to initial capital."""
        if self.initial_capital == 0:
            return 0.0
        return (self.equity - self.initial_capital) / self.initial_capital * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert account state to a dictionary representation."""
        return {
            "timestamp": pd.Timestamp.now(),
            "cash": self.cash,
            "positions_value": self.positions_value,
            "total_equity": self.equity,
            "pnl_percentage": self.pnl_percentage
        }
