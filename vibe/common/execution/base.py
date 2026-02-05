"""
Abstract execution engine interface for order execution and management.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from vibe.common.models import Order, OrderStatus, Position, AccountState


@dataclass
class OrderResponse:
    """Standardized response from order operations."""

    order_id: str
    status: OrderStatus
    filled_qty: float
    avg_price: float
    remaining_qty: float


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
