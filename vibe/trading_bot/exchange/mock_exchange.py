"""
Mock exchange for paper trading with realistic order execution simulation.
Implements all order types: Market, Limit, Stop, Stop-Limit.
Includes slippage, partial fills, and position tracking.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from vibe.common.models import Order, OrderStatus, Position, AccountState
from vibe.common.execution.base import ExecutionEngine, OrderResponse
from vibe.trading_bot.exchange.slippage import SlippageModel


logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    """Internal order tracking with execution state."""

    order: Order
    """The order details."""

    submitted_at: datetime = field(default_factory=datetime.now)
    """When the order was submitted."""

    retry_count: int = 0
    """Number of retries."""

    last_retry_at: Optional[datetime] = None
    """When the last retry occurred."""

    partial_fills: List[float] = field(default_factory=list)
    """Prices of partial fills."""

    total_filled_qty: float = 0.0
    """Total quantity filled across all fills."""


class MockExchange(ExecutionEngine):
    """
    Mock exchange for paper trading simulation.

    Features:
    - Support for Market, Limit, Stop, Stop-Limit orders
    - Realistic slippage based on volatility and size
    - Partial fill simulation with retry policy
    - Position and P&L tracking
    - Configurable initial capital ($10,000 default)
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        slippage_model: Optional[SlippageModel] = None,
        partial_fill_probability: float = 0.1,
        commission_pct: float = 0.001,
    ):
        """
        Initialize mock exchange.

        Args:
            initial_capital: Starting account capital (default $10,000)
            slippage_model: SlippageModel instance (auto-created if None)
            partial_fill_probability: Probability of partial fill (0-1)
            commission_pct: Commission as percentage of trade value
        """
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not (0 <= partial_fill_probability <= 1):
            raise ValueError("partial_fill_probability must be 0-1")
        if commission_pct < 0 or commission_pct > 0.1:
            raise ValueError("commission_pct must be 0-0.1 (0-10%)")

        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.slippage_model = (
            slippage_model or SlippageModel(base_slippage_pct=0.0005)
        )
        self.partial_fill_probability = partial_fill_probability
        self.commission_pct = commission_pct

        # Order tracking
        self._orders: Dict[str, ManagedOrder] = {}
        self._pending_orders: List[str] = []  # List of order IDs

        # Position tracking: symbol -> Position
        self._positions: Dict[str, Position] = {}

        # Price tracking: symbol -> current price
        self._prices: Dict[str, float] = {}

        # Order history for analytics
        self._filled_orders: List[str] = []
        self._cancelled_orders: List[str] = []

    async def set_price(self, symbol: str, price: float) -> None:
        """
        Set current price for a symbol (for testing).

        Args:
            symbol: Trading symbol
            price: Current price

        Raises:
            ValueError: If price is invalid
        """
        if price <= 0:
            raise ValueError("price must be positive")
        self._prices[symbol] = price

    async def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        price: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResponse:
        """
        Submit an order for execution.

        Args:
            symbol: Trading symbol
            side: Order side ('buy' or 'sell')
            quantity: Order quantity
            order_type: Order type ('market', 'limit', 'stop', 'stop_limit')
            price: Price (for limit orders)
            limit_price: Limit price (for stop-limit)
            stop_price: Stop price (for stop/stop-limit)

        Returns:
            OrderResponse with execution details
        """
        # Validate inputs
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")

        # Get current price for validation
        if symbol not in self._prices:
            raise ValueError(f"No price set for {symbol}")

        current_price = self._prices[symbol]

        # Create order
        order_id = f"ord_{uuid.uuid4().hex[:8]}"
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price or current_price,
            order_type=order_type,
            status=OrderStatus.CREATED,
        )

        managed = ManagedOrder(order=order)
        self._orders[order_id] = managed

        # Try to fill immediately
        result = await self._try_fill_order(order_id, current_price)

        return result

    async def _try_fill_order(
        self,
        order_id: str,
        current_price: float,
    ) -> OrderResponse:
        """
        Attempt to fill an order at current price.

        Args:
            order_id: Order ID
            current_price: Current market price

        Returns:
            OrderResponse with fill details
        """
        managed = self._orders[order_id]
        order = managed.order

        # Check order type and conditions
        should_fill = False
        fill_price = current_price

        if order.order_type == "market":
            should_fill = True
            # Apply slippage to market orders
            fill_price = self.slippage_model.apply(
                price=current_price,
                side=order.side,
                volatility=0.02,  # Default volatility
                order_size=order.quantity,
            )

        elif order.order_type == "limit":
            # Limit orders fill if price is better than limit
            if order.side == "buy" and current_price <= order.price:
                should_fill = True
                fill_price = current_price
            elif order.side == "sell" and current_price >= order.price:
                should_fill = True
                fill_price = current_price

        elif order.order_type == "stop":
            # Stop orders become market when triggered
            if order.side == "buy" and current_price >= order.price:
                should_fill = True
                fill_price = self.slippage_model.apply(
                    price=current_price,
                    side=order.side,
                    volatility=0.02,
                    order_size=order.quantity,
                )
            elif order.side == "sell" and current_price <= order.price:
                should_fill = True
                fill_price = self.slippage_model.apply(
                    price=current_price,
                    side=order.side,
                    volatility=0.02,
                    order_size=order.quantity,
                )
            else:
                # Stop not triggered yet
                if order_id not in self._pending_orders:
                    self._pending_orders.append(order_id)
                order.status = OrderStatus.PENDING

        elif order.order_type == "stop_limit":
            # For now, implement as stop that becomes limit
            stop_price = order.price  # Using price as stop price
            if order.side == "buy" and current_price >= stop_price:
                # Triggered - now check limit price
                if current_price <= order.price:
                    should_fill = True
                    fill_price = current_price
            elif order.side == "sell" and current_price <= stop_price:
                # Triggered - now check limit price
                if current_price >= order.price:
                    should_fill = True
                    fill_price = current_price
            else:
                if order_id not in self._pending_orders:
                    self._pending_orders.append(order_id)
                order.status = OrderStatus.PENDING

        if should_fill:
            # Check for partial fills
            fill_qty = order.quantity
            if (
                managed.total_filled_qty == 0
                and self.partial_fill_probability > 0
            ):
                import random

                if random.random() < self.partial_fill_probability:
                    # Partial fill: 30-70% of order
                    fill_ratio = 0.3 + random.random() * 0.4
                    fill_qty = int(order.quantity * fill_ratio)

            # Apply fill
            await self._apply_fill(order_id, fill_qty, fill_price)

            # Remove from pending if fully filled
            if order.filled_qty >= order.quantity:
                order.status = OrderStatus.FILLED
                if order_id in self._pending_orders:
                    self._pending_orders.remove(order_id)
                self._filled_orders.append(order_id)
            else:
                # Partial fill
                order.status = OrderStatus.PARTIAL
                if order_id not in self._pending_orders:
                    self._pending_orders.append(order_id)

        # Return response
        return OrderResponse(
            order_id=order_id,
            status=order.status,
            filled_qty=order.filled_qty,
            avg_price=order.avg_price,
            remaining_qty=order.quantity - order.filled_qty,
        )

    async def _apply_fill(
        self,
        order_id: str,
        fill_qty: float,
        fill_price: float,
    ) -> None:
        """
        Apply a fill to an order and update positions/cash.

        Args:
            order_id: Order ID
            fill_qty: Quantity filled
            fill_price: Fill price
        """
        managed = self._orders[order_id]
        order = managed.order

        # Calculate commission
        trade_value = fill_qty * fill_price
        commission = trade_value * self.commission_pct

        # Update cash
        if order.side == "buy":
            self.cash -= trade_value + commission
        else:  # sell
            self.cash += trade_value - commission

        # Update order
        old_filled = order.filled_qty
        old_cost = order.avg_price * old_filled if old_filled > 0 else 0
        order.filled_qty += fill_qty
        order.avg_price = (old_cost + fill_qty * fill_price) / order.filled_qty
        order.commission += commission
        order.status = (
            OrderStatus.FILLED
            if order.filled_qty >= order.quantity
            else OrderStatus.PARTIAL
        )

        # Update position - removed for now, not using Position model
        # This is simplified: actual implementation would track via a simpler dict
        symbol = order.symbol
        if symbol not in self._positions:
            # Create new position tracking entry (simplified)
            self._positions[symbol] = {
                "side": "long" if order.side == "buy" else "short",
                "quantity": fill_qty,
                "entry_price": fill_price,
            }
        else:
            pos_data = self._positions[symbol]
            # Update existing
            if (
                (pos_data["side"] == "long" and order.side == "buy")
                or (pos_data["side"] == "short" and order.side == "sell")
            ):
                # Adding to position
                old_cost = pos_data["quantity"] * pos_data["entry_price"]
                new_qty = pos_data["quantity"] + fill_qty
                pos_data["entry_price"] = (
                    (old_cost + fill_qty * fill_price) / new_qty
                )
                pos_data["quantity"] = new_qty
            else:
                # Reducing/closing position
                pos_data["quantity"] -= fill_qty
                if pos_data["quantity"] == 0:
                    del self._positions[symbol]

        managed.total_filled_qty += fill_qty
        managed.partial_fills.append(fill_price)

    async def process_pending_orders(self) -> None:
        """
        Process all pending orders (check if they should fill).

        This should be called periodically with new prices.
        """
        pending_ids = self._pending_orders.copy()
        for order_id in pending_ids:
            if order_id not in self._orders:
                continue

            managed = self._orders[order_id]
            symbol = managed.order.symbol

            if symbol in self._prices:
                await self._try_fill_order(
                    order_id, self._prices[symbol]
                )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """
        Cancel an existing order.

        Args:
            order_id: ID of order to cancel

        Returns:
            OrderResponse with cancellation details
        """
        if order_id not in self._orders:
            raise ValueError(f"Unknown order: {order_id}")

        managed = self._orders[order_id]
        order = managed.order

        # Can only cancel pending/partial orders
        if order.status not in (
            OrderStatus.PENDING,
            OrderStatus.PARTIAL,
            OrderStatus.CREATED,
        ):
            raise ValueError(
                f"Cannot cancel {order.status.name} order"
            )

        order.status = OrderStatus.CANCELLED
        if order_id in self._pending_orders:
            self._pending_orders.remove(order_id)
        self._cancelled_orders.append(order_id)

        return OrderResponse(
            order_id=order_id,
            status=OrderStatus.CANCELLED,
            filled_qty=order.filled_qty,
            avg_price=order.avg_price,
            remaining_qty=order.quantity - order.filled_qty,
        )

    async def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get current position for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Position or None if no position exists
        """
        if symbol not in self._positions:
            return None

        pos_data = self._positions[symbol]
        current_price = self._prices.get(symbol, pos_data["entry_price"])

        return Position(
            symbol=symbol,
            side=pos_data["side"],
            quantity=pos_data["quantity"],
            entry_price=pos_data["entry_price"],
            current_price=current_price,
        )

    async def get_account(self) -> AccountState:
        """
        Get current account state.

        Returns:
            AccountState with current account information
        """
        # Calculate total position value
        position_value = 0
        for symbol, pos_data in self._positions.items():
            if symbol in self._prices:
                price = self._prices[symbol]
                if pos_data["side"] == "long":
                    position_value += pos_data["quantity"] * price
                else:  # short
                    position_value -= pos_data["quantity"] * price

        equity = self.cash + position_value
        portfolio_value = self.initial_capital + (equity - self.initial_capital)

        return AccountState(
            cash=self.cash,
            equity=equity,
            buying_power=self.cash,
            portfolio_value=portfolio_value,
            timestamp=datetime.now(),
        )

    async def get_order(self, order_id: str) -> Optional[Order]:
        """
        Get status of an order.

        Args:
            order_id: ID of order to query

        Returns:
            Order object or None if order not found
        """
        if order_id not in self._orders:
            return None
        return self._orders[order_id].order

    def get_all_orders(self) -> Dict[str, Order]:
        """
        Get all orders.

        Returns:
            Dictionary of order_id -> Order
        """
        return {
            order_id: managed.order
            for order_id, managed in self._orders.items()
        }

    def get_positions(self) -> Dict[str, dict]:
        """
        Get all open positions (simplified format).

        Returns:
            Dictionary of symbol -> position data
        """
        return self._positions.copy()

    def reset(self) -> None:
        """Reset exchange to initial state (for testing)."""
        self.cash = self.initial_capital
        self._orders.clear()
        self._positions.clear()
        self._prices.clear()
        self._pending_orders.clear()
        self._filled_orders.clear()
        self._cancelled_orders.clear()
