"""
Order lifecycle management with retry policy and partial fill handling.
Orchestrates order submission, monitoring, and notification.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional

from vibe.common.execution.base import ExecutionEngine, OrderResponse
from vibe.common.models import Order, OrderStatus


logger = logging.getLogger(__name__)


@dataclass
class OrderRetryPolicy:
    """Policy for retrying unfilled or partially filled orders."""

    max_retries: int = 3
    """Maximum number of retries."""

    base_delay_seconds: float = 1.0
    """Initial delay in seconds."""

    backoff_multiplier: float = 2.0
    """Multiplier for exponential backoff."""

    max_delay_seconds: float = 30.0
    """Maximum delay between retries."""

    cancel_after_seconds: float = 60.0
    """Cancel order if not filled after this duration."""

    def should_retry(
        self,
        retry_count: int,
        elapsed_seconds: float,
    ) -> bool:
        """
        Determine if an order should be retried.

        Args:
            retry_count: Number of retries so far
            elapsed_seconds: Elapsed time since submission

        Returns:
            True if should retry, False otherwise
        """
        # Check max retries
        if retry_count >= self.max_retries:
            return False

        # Check timeout
        if elapsed_seconds >= self.cancel_after_seconds:
            return False

        return True

    def get_delay(self, retry_count: int) -> float:
        """
        Get delay before next retry using exponential backoff.

        Args:
            retry_count: Current retry count (0-indexed)

        Returns:
            Delay in seconds
        """
        delay = (
            self.base_delay_seconds
            * (self.backoff_multiplier ** retry_count)
        )
        return min(delay, self.max_delay_seconds)


@dataclass
class ManagedOrder:
    """Internal tracking of an order through its lifecycle."""

    order_id: str
    """Unique order ID."""

    order: Order
    """The order details."""

    submitted_at: datetime = field(default_factory=datetime.now)
    """When order was submitted."""

    retry_count: int = 0
    """Number of retries."""

    filled_qty: float = 0.0
    """Quantity filled so far."""

    completed_at: Optional[datetime] = None
    """When order reached terminal state."""

    terminal_status: Optional[OrderStatus] = None
    """Final status (FILLED, CANCELLED, REJECTED)."""

    cancel_after_seconds: Optional[float] = None
    """Optional order-specific timeout override."""

    restored: bool = False
    """Whether monitoring resumed from broker state after process recovery."""


class OrderManager:
    """
    Manages order lifecycle with retry policy and notifications.

    Responsibilities:
    - Submit orders to exchange
    - Monitor partial fills and retry
    - Handle timeouts and cancellations
    - Emit notifications for key events
    - Track order history
    """

    def __init__(
        self,
        exchange: ExecutionEngine,
        retry_policy: Optional[OrderRetryPolicy] = None,
        on_order_created: Optional[Callable[[str], Awaitable[None]]] = None,
        on_order_filled: Optional[Callable[[str], Awaitable[None]]] = None,
        on_order_cancelled: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        Initialize order manager.

        Args:
            exchange: ExecutionEngine to submit orders to
            retry_policy: OrderRetryPolicy (uses defaults if None)
            on_order_created: Callback for ORDER_SENT event
            on_order_filled: Callback for ORDER_FILLED event
            on_order_cancelled: Callback for ORDER_CANCELLED event
        """
        self.exchange = exchange
        self.retry_policy = retry_policy or OrderRetryPolicy()

        # Callbacks
        self._on_order_created = on_order_created
        self._on_order_filled = on_order_filled
        self._on_order_cancelled = on_order_cancelled

        # Order tracking
        self._orders: Dict[str, ManagedOrder] = {}
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}

    def restore_open_orders(self, orders: List[Order]) -> int:
        """Hydrate recovered broker orders without replaying creation/fill events.

        The current filled quantity is the callback baseline. Only fills that
        arrive after recovery are emitted into the normal lifecycle callbacks.
        """
        restored = 0
        for order in orders:
            if order.order_id in self._orders:
                continue
            if order.status not in {
                OrderStatus.CREATED,
                OrderStatus.PENDING,
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIAL,
            }:
                continue
            self._orders[order.order_id] = ManagedOrder(
                order_id=order.order_id,
                order=order,
                submitted_at=datetime.now(),
                filled_qty=float(order.filled_qty or 0.0),
                restored=True,
            )
            self._monitoring_tasks[order.order_id] = asyncio.create_task(
                self._monitor_order(order.order_id)
            )
            restored += 1
        return restored

    def _emit_callback(self, callback: Optional[Callable[[str], Awaitable[None]]], order_id: str) -> None:
        if callback is None:
            return
        result = callback(order_id)
        if inspect.isawaitable(result):
            asyncio.create_task(result)

    async def _emit_callback_ordered(
        self,
        callback: Optional[Callable[[str], Awaitable[None]]],
        order_id: str,
    ) -> None:
        """Run lifecycle callbacks in order when terminal events race fills."""
        if callback is None:
            return
        result = callback(order_id)
        if inspect.isawaitable(result):
            await result

    async def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        cancel_after_seconds: Optional[float] = None,
        lifecycle_metadata: Optional[dict] = None,
    ) -> OrderResponse:
        """
        Submit an order and manage its lifecycle.

        Args:
            symbol: Trading symbol
            side: Order side ('buy' or 'sell')
            quantity: Order quantity
            order_type: Order type
            price: Price (for limit/stop)
            limit_price: Limit price (for stop-limit)
            stop_price: Stop price (for stop/stop-limit)
            cancel_after_seconds: Optional timeout override for this order

        Returns:
            OrderResponse from initial submission
        """
        # Submit to exchange
        submission = dict(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        if lifecycle_metadata is not None:
            submission["lifecycle_metadata"] = lifecycle_metadata
        response = await self.exchange.submit_order(**submission)

        # Track the order
        managed = ManagedOrder(
            order_id=response.order_id,
            order=await self.exchange.get_order(response.order_id),
            filled_qty=float(response.filled_qty or 0.0),
            cancel_after_seconds=cancel_after_seconds,
        )
        self._orders[response.order_id] = managed

        # Emit creation event
        self._emit_callback(self._on_order_created, response.order_id)

        logger.info(
            f"Order submitted: {response.order_id} "
            f"{side} {quantity} {symbol}"
        )

        # For immediately-filled orders (e.g. MockExchange market orders), fire
        # on_order_filled here since _monitor_order never starts for them.
        if response.status == OrderStatus.FILLED:
            managed.completed_at = datetime.now()
            managed.terminal_status = OrderStatus.FILLED
            self._emit_callback(self._on_order_filled, response.order_id)
        else:
            if response.filled_qty > 0:
                self._emit_callback(self._on_order_filled, response.order_id)
            task = asyncio.create_task(
                self._monitor_order(response.order_id)
            )
            self._monitoring_tasks[response.order_id] = task

        return response

    async def _monitor_order(self, order_id: str) -> None:
        """
        Monitor an order for fills and retries.

        Args:
            order_id: Order ID to monitor
        """
        managed = self._orders[order_id]

        while True:
            # Check elapsed time
            elapsed = (
                datetime.now() - managed.submitted_at
            ).total_seconds()

            # Get current order status from exchange
            order = await self.exchange.get_order(order_id)
            if order is None:
                logger.warning(f"Order not found: {order_id}")
                break

            previous_filled = managed.filled_qty
            current_filled = order.filled_qty
            managed.filled_qty = current_filled
            if current_filled > previous_filled:
                await self._emit_callback_ordered(
                    self._on_order_filled,
                    order_id,
                )

            # Check if fully filled
            if current_filled >= order.quantity:
                managed.completed_at = datetime.now()
                managed.terminal_status = OrderStatus.FILLED

                logger.info(f"Order filled: {order_id}")
                break

            # Check if should retry
            cancel_after_seconds = managed.cancel_after_seconds or self.retry_policy.cancel_after_seconds
            should_retry = (
                managed.retry_count < self.retry_policy.max_retries
                and elapsed < cancel_after_seconds
            )
            if not should_retry:
                # A fill may win the race with cancellation. The exchange
                # reconstructs its order from the durable execution journal
                # before returning the terminal cancellation response.
                cancellation = await self.exchange.cancel_order(order_id)
                refreshed = await self.exchange.get_order(order_id)
                terminal_order = refreshed or order
                confirmed_filled = max(
                    float(getattr(cancellation, "filled_qty", 0.0) or 0.0),
                    float(terminal_order.filled_qty or 0.0),
                )
                if confirmed_filled > managed.filled_qty:
                    managed.filled_qty = confirmed_filled
                    await self._emit_callback_ordered(
                        self._on_order_filled,
                        order_id,
                    )
                managed.completed_at = datetime.now()
                if (
                    getattr(cancellation, "status", None) == OrderStatus.FILLED
                    or confirmed_filled >= float(terminal_order.quantity)
                    or getattr(terminal_order, "status", None)
                    == OrderStatus.FILLED
                ):
                    managed.terminal_status = OrderStatus.FILLED
                    logger.info(
                        "Order filled while cancellation was in flight: %s",
                        order_id,
                    )
                    break

                managed.terminal_status = OrderStatus.CANCELLED
                await self._emit_callback_ordered(
                    self._on_order_cancelled,
                    order_id,
                )

                logger.info(
                    f"Order cancelled after timeout: {order_id}"
                )
                break

            # Retry if partial fill
            if current_filled > 0 and (
                not managed.restored or current_filled > previous_filled
            ):
                delay = self.retry_policy.get_delay(
                    managed.retry_count
                )
                managed.retry_count += 1

                logger.info(
                    f"Partial fill detected: {order_id} "
                    f"{current_filled}/{order.quantity}, "
                    f"retrying in {delay}s"
                )

                await asyncio.sleep(delay)

                try:
                    # Do not calculate a replacement from the pre-cancel
                    # snapshot. A fill can arrive while cancellation is in
                    # flight, so cancellation must be terminal and executions
                    # must be re-read by the exchange first.
                    cancellation = await self.exchange.cancel_order(order_id)
                    if cancellation.status not in {
                        OrderStatus.CANCELLED,
                        OrderStatus.FILLED,
                    }:
                        raise RuntimeError(
                            f"Cancellation not terminal for order {order_id}"
                        )
                    refreshed = await self.exchange.get_order(order_id)
                    if refreshed is None:
                        raise RuntimeError(
                            f"Cannot confirm post-cancel state for order {order_id}"
                        )
                    confirmed_filled = max(
                        float(cancellation.filled_qty or 0.0),
                        float(refreshed.filled_qty or 0.0),
                    )
                    if confirmed_filled > current_filled:
                        await self._emit_callback_ordered(
                            self._on_order_filled,
                            order_id,
                        )
                    managed.filled_qty = confirmed_filled
                    remaining = max(float(order.quantity) - confirmed_filled, 0.0)
                    if remaining <= 0:
                        managed.completed_at = datetime.now()
                        managed.terminal_status = OrderStatus.FILLED
                        break

                    # Submit via OrderManager (proper tracking + new monitoring task)
                    await self.submit_order(
                        symbol=order.symbol,
                        side=order.side,
                        quantity=remaining,
                        order_type=order.order_type,
                        price=order.price,
                    )
                    # Break — new order has its own monitoring task
                    break
                except Exception as e:
                    logger.error(f"Error retrying order: {e}")
                    break
            else:
                # No fills yet, wait before checking again
                delay = self.retry_policy.get_delay(
                    managed.retry_count
                )
                await asyncio.sleep(min(delay, 5.0))  # Max 5s wait

    def get_order(self, order_id: str) -> Optional[ManagedOrder]:
        """
        Get managed order by ID.

        Args:
            order_id: Order ID

        Returns:
            ManagedOrder or None
        """
        return self._orders.get(order_id)

    def get_orders_by_status(
        self,
        status: OrderStatus,
    ) -> List[ManagedOrder]:
        """
        Get all orders with a specific status.

        Args:
            status: Order status to filter by

        Returns:
            List of matching ManagedOrders
        """
        return [
            managed
            for managed in self._orders.values()
            if managed.order.status == status
        ]

    def get_orders_by_symbol(self, symbol: str) -> List[ManagedOrder]:
        """
        Get all orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of matching ManagedOrders
        """
        return [
            managed
            for managed in self._orders.values()
            if managed.order.symbol == symbol
        ]

    def get_all_orders(self) -> List[ManagedOrder]:
        """
        Get all tracked orders.

        Returns:
            List of all ManagedOrders
        """
        return list(self._orders.values())

    async def cancel_all_orders(self) -> int:
        """
        Cancel all pending/partial orders.

        Returns:
            Number of orders cancelled
        """
        cancelled_count = 0
        for managed in list(self._orders.values()):
            if managed.order.status in (
                OrderStatus.PENDING,
                OrderStatus.PARTIAL,
            ):
                try:
                    await self.exchange.cancel_order(
                        managed.order_id
                    )
                    cancelled_count += 1
                except Exception as e:
                    logger.error(
                        f"Error cancelling order: {e}"
                    )

        return cancelled_count

    async def shutdown(self) -> None:
        """Cancel all monitoring tasks and orders."""
        # Cancel all monitoring tasks
        for task in self._monitoring_tasks.values():
            if not task.done():
                task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(
            *self._monitoring_tasks.values(),
            return_exceptions=True,
        )

        # Cancel all pending orders
        await self.cancel_all_orders()
