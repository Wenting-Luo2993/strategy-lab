"""ExecutionEngine adapter for Interactive Brokers paper/live execution."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from vibe.common.execution.base import ExecutionEngine, OrderResponse
from vibe.common.models import AccountState, Order, OrderStatus, Position
from vibe.trading_bot.brokers.base import BrokerOrder
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI


class InteractiveBrokersExecutionEngine(ExecutionEngine):
    """Adapt the broker-level IB API to the trading bot execution interface."""

    def __init__(self, broker: InteractiveBrokersAPI, fill_timeout_seconds: float = 60.0):
        self.broker = broker
        self.fill_timeout_seconds = fill_timeout_seconds
        self._orders: dict[str, Order] = {}
        self._prices: dict[str, float] = {}

    async def initialize(self) -> None:
        await self.broker.connect()

    async def close(self) -> None:
        await self.broker.disconnect()

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
        expected_price = price
        if expected_price is None:
            quote = await self.broker.get_market_data(symbol)
            expected_price = quote.market_price

        broker_order = BrokerOrder(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            expected_price=expected_price,
            limit_price=limit_price if limit_price is not None else (price if order_type == "limit" else None),
            stop_price=stop_price if stop_price is not None else (price if order_type == "stop" else None),
        )
        broker_order_id = await self.broker.submit_order(broker_order)

        order = Order(
            order_id=broker_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=expected_price,
            order_type=order_type,
            status=OrderStatus.SUBMITTED,
        )
        self._orders[broker_order_id] = order

        try:
            fill = await self.broker.wait_for_fill(broker_order_id, timeout_seconds=self.fill_timeout_seconds)
        except TimeoutError:
            return OrderResponse(
                order_id=broker_order_id,
                status=OrderStatus.SUBMITTED,
                filled_qty=0.0,
                avg_price=0.0,
                remaining_qty=quantity,
            )

        order.status = OrderStatus.FILLED
        order.filled_qty = fill.quantity
        order.avg_price = fill.avg_fill_price
        order.commission = fill.commission
        self._prices[symbol] = fill.avg_fill_price

        return OrderResponse(
            order_id=broker_order_id,
            status=OrderStatus.FILLED,
            filled_qty=fill.quantity,
            avg_price=fill.avg_fill_price,
            remaining_qty=max(quantity - fill.quantity, 0.0),
        )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        if order_id not in self._orders:
            raise ValueError(f"Unknown order: {order_id}")

        order = self._orders[order_id]
        await self.broker.cancel_order(order_id)
        status = await self.broker.get_order_status(order_id)
        filled_qty = float(status.get("filled") or order.filled_qty or 0.0)
        remaining_qty = float(status.get("remaining") or max(order.quantity - filled_qty, 0.0))
        avg_price = float(status.get("avg_fill_price") or order.avg_price or 0.0)

        order.status = OrderStatus.CANCELLED
        order.filled_qty = filled_qty
        order.avg_price = avg_price

        return OrderResponse(
            order_id=order_id,
            status=OrderStatus.CANCELLED,
            filled_qty=filled_qty,
            avg_price=avg_price,
            remaining_qty=remaining_qty,
        )

    async def get_position(self, symbol: str) -> Optional[Position]:
        positions = await self.broker.get_positions()
        for broker_position in positions:
            if broker_position.symbol != symbol or broker_position.quantity == 0:
                continue
            current_price = broker_position.market_price or self._prices.get(symbol)
            if current_price is None:
                quote = await self.broker.get_market_data(symbol)
                current_price = quote.market_price
            self._prices[symbol] = current_price
            return Position(
                symbol=symbol,
                side="long" if broker_position.quantity > 0 else "short",
                quantity=abs(broker_position.quantity),
                entry_price=broker_position.avg_cost,
                current_price=current_price,
            )
        return None

    async def get_account(self) -> AccountState:
        account = await self.broker.get_account_info()
        equity = account.net_liquidation or 0.0
        cash = account.cash or 0.0
        buying_power = account.buying_power or cash
        return AccountState(
            cash=max(cash, 0.0),
            equity=max(equity, 0.0),
            buying_power=max(buying_power, 0.0),
            portfolio_value=max(equity, 0.0),
            timestamp=datetime.now(),
        )

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)