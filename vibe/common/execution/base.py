"""
Abstract execution engine interface for order execution and management.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from vibe.common.models import Order, OrderStatus, Position, AccountState


@dataclass
class OrderResponse:
    """Standardized response from order operations."""

    order_id: str
    status: OrderStatus
    filled_qty: float
    avg_price: float
    remaining_qty: float


@dataclass(frozen=True)
class RestingEntryLeg:
    """One side of a resting stop entry pair with its protective stop."""

    side: str
    """``buy`` (stop at the range high) or ``sell`` (stop at the range low)."""
    quantity: int
    trigger_price: float
    protective_stop_price: float
    capped_by: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.trigger_price <= 0 or self.protective_stop_price <= 0:
            raise ValueError("prices must be positive")
        protective_on_wrong_side = (
            self.protective_stop_price >= self.trigger_price
            if self.side == "buy"
            else self.protective_stop_price <= self.trigger_price
        )
        if protective_on_wrong_side:
            raise ValueError("protective stop must be on the losing side of the entry")

    def triggered_by(self, price: Optional[float]) -> Optional[bool]:
        """Whether ``price`` is already at or beyond the trigger (a gap fill)."""
        if price is None or price <= 0:
            return None
        if self.side == "buy":
            return price >= self.trigger_price
        return price <= self.trigger_price


class ExecutionEngine(ABC):
    """
    Abstract base class for execution engines.
    Both live and backtest implementations must provide these methods.
    """

    @abstractmethod
    async def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        price: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        lifecycle_metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderResponse:
        """
        Submit an order for execution.

        Args:
            symbol: Trading symbol (e.g., 'AAPL')
            side: Order side ('buy' or 'sell')
            quantity: Order quantity
            order_type: Order type ('limit', 'market', 'stop')
            price: Price for limit/stop orders

        Returns:
            OrderResponse with submission details
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResponse:
        """
        Cancel an existing order.

        Args:
            order_id: ID of order to cancel

        Returns:
            OrderResponse with cancellation details
        """
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get current position for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Position object or None if no position exists
        """
        pass

    @abstractmethod
    async def get_account(self) -> AccountState:
        """
        Get current account state.

        Returns:
            AccountState with current account information
        """
        pass

    @abstractmethod
    async def get_order(self, order_id: str) -> Optional[Order]:
        """
        Get status of an order.

        Args:
            order_id: ID of order to query

        Returns:
            Order object or None if order not found
        """
        pass
