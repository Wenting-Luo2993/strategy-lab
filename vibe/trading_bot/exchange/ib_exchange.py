"""ExecutionEngine adapter for Interactive Brokers paper/live execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from vibe.common.execution.base import ExecutionEngine, OrderResponse
from vibe.common.models import AccountState, Order, OrderStatus, Position
from vibe.trading_bot.brokers.base import BrokerOrder, BrokerPosition
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI


class InteractiveBrokersExecutionEngine(ExecutionEngine):
    """Adapt the broker-level IB API to the trading bot execution interface."""

    def __init__(
        self,
        broker: InteractiveBrokersAPI,
        fill_timeout_seconds: float = 60.0,
        quote_max_age_seconds: float = 5.0,
    ):
        self.broker = broker
        self.fill_timeout_seconds = fill_timeout_seconds
        self.quote_max_age_seconds = quote_max_age_seconds
        self._orders: dict[str, Order] = {}
        self._prices: dict[str, float] = {}
        self._execution_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._restored_open_symbols: set[str] = set()
        add_listener = getattr(self.broker, "add_execution_listener", None)
        if add_listener is not None:
            add_listener(self._on_broker_execution)

    async def initialize(self) -> None:
        await self.broker.connect()
        # Rebuild from every durable execId even when reconciliation found byte-
        # identical rows and therefore emitted no store-change callback.
        for execution in self.list_durable_executions():
            self._on_broker_execution(execution)
        list_open_orders = getattr(self.broker, "list_open_orders", None)
        if list_open_orders is not None:
            for item in list_open_orders():
                restored = item["order"]
                order_id = str(restored.broker_order_id)
                order = self._orders.get(order_id)
                if order is None:
                    reference_price = float(
                        restored.benchmark_price
                        or restored.expected_price
                        or restored.limit_price
                        or restored.stop_price
                        or item.get("avg_price")
                        or 0.01
                    )
                    order = Order(
                        order_id=order_id,
                        symbol=restored.symbol,
                        side=restored.side,
                        quantity=float(restored.quantity),
                        price=reference_price,
                        order_type=restored.order_type,
                        status=OrderStatus.SUBMITTED,
                        filled_qty=float(item.get("filled_qty") or 0.0),
                        avg_price=float(item.get("avg_price") or 0.0),
                        decision_at=restored.decision_at,
                        submitted_at=restored.submitted_at,
                        benchmark_type=restored.benchmark_type,
                        benchmark_price=restored.benchmark_price,
                        quote_bid=restored.quote_bid,
                        quote_ask=restored.quote_ask,
                        quote_midpoint=restored.quote_midpoint,
                        stop_price=restored.stop_price,
                        limit_price=restored.limit_price,
                        benchmark_version=restored.benchmark_version,
                        benchmark_valid=(
                            restored.benchmark_version == 2
                            and restored.benchmark_price not in (None, 0)
                        ),
                    )
                    self._orders[order_id] = order
                else:
                    order.quantity = max(
                        float(restored.quantity),
                        float(order.filled_qty),
                    )
                order.status = (
                    OrderStatus.PARTIAL
                    if float(order.filled_qty) > 0
                    else OrderStatus.SUBMITTED
                )
                self._restored_open_symbols.add(restored.symbol)

    async def close(self) -> None:
        await self.broker.disconnect()

    def add_execution_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        self._execution_listeners.append(listener)

    def list_durable_executions(self) -> List[Dict[str, Any]]:
        list_executions = getattr(self.broker, "list_durable_executions", None)
        return list_executions() if list_executions is not None else []

    def list_open_orders(self) -> List[Order]:
        """Return restored non-terminal orders for lifecycle monitoring."""
        return [
            order
            for order in self._orders.values()
            if order.status in {
                OrderStatus.CREATED,
                OrderStatus.PENDING,
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIAL,
            }
        ]

    def _on_broker_execution(self, execution: Dict[str, Any]) -> None:
        """Refresh the in-memory order from every durable exec/commission update."""
        broker_order_id = str(execution["broker_order_id"])
        metadata = execution.get("order_metadata") or {}
        order = self._orders.get(broker_order_id)
        if order is None:
            price = float(metadata.get("benchmark_price") or execution["price"])
            quantity = float(metadata.get("quantity") or execution["quantity"])
            order = Order(
                order_id=broker_order_id,
                symbol=execution["symbol"],
                side=execution["side"],
                quantity=quantity,
                price=price,
                order_type=metadata.get("order_type") or "market",
                status=OrderStatus.SUBMITTED,
                decision_at=self._as_datetime(metadata.get("decision_at")),
                submitted_at=self._as_datetime(metadata.get("submitted_at")),
                benchmark_type=metadata.get("benchmark_type"),
                benchmark_price=metadata.get("benchmark_price"),
                quote_bid=metadata.get("quote_bid"),
                quote_ask=metadata.get("quote_ask"),
                quote_midpoint=metadata.get("quote_midpoint"),
                stop_price=metadata.get("stop_price"),
                limit_price=metadata.get("limit_price"),
                benchmark_version=int(metadata.get("benchmark_version") or 1),
                benchmark_valid=(
                    int(metadata.get("benchmark_version") or 1) == 2
                    and metadata.get("benchmark_price") not in (None, 0)
                ),
                permanent_order_id=execution.get("permanent_order_id"),
                account_id=execution.get("account_id"),
                trade_currency=execution.get("trade_currency"),
            )
            self._orders[broker_order_id] = order

        executions_by_id = {
            str(item["execution_id"]): dict(item)
            for item in order.executions
        }
        executions_by_id[str(execution["execution_id"])] = dict(execution)
        executions = sorted(
            executions_by_id.values(),
            key=lambda item: (str(item.get("filled_at") or ""), str(item["execution_id"])),
        )
        filled_qty = sum(float(item.get("quantity") or 0.0) for item in executions)
        total_notional = sum(
            float(item.get("quantity") or 0.0) * float(item.get("price") or 0.0)
            for item in executions
        )
        order.executions = executions
        order.execution_ids = [str(item["execution_id"]) for item in executions]
        order.execution_id = order.execution_ids[0] if len(order.execution_ids) == 1 else None
        order.filled_qty = filled_qty
        order.avg_price = total_notional / filled_qty if filled_qty else 0.0
        order.commission = sum(
            float(item["commission"])
            for item in executions
            if item.get("commission") is not None
        )
        order.filled_at = max(
            (
                self._as_datetime(item.get("filled_at"))
                for item in executions
                if item.get("filled_at") is not None
            ),
            default=order.filled_at,
        )
        order.permanent_order_id = execution.get("permanent_order_id") or order.permanent_order_id
        order.account_id = execution.get("account_id") or order.account_id
        order.trade_currency = execution.get("trade_currency") or order.trade_currency
        commission_currencies = {
            item.get("commission_currency")
            for item in executions
            if item.get("commission_currency")
        }
        order.commission_currency = (
            next(iter(commission_currencies)) if len(commission_currencies) == 1 else None
        )
        order.status = OrderStatus.FILLED if filled_qty >= order.quantity else OrderStatus.PARTIAL
        if order.status == OrderStatus.FILLED:
            self._restored_open_symbols.discard(order.symbol)
        if order.avg_price > 0:
            self._prices[order.symbol] = order.avg_price

        for listener in list(self._execution_listeners):
            listener(dict(execution))

    @staticmethod
    def _as_datetime(value: Any) -> Optional[datetime]:
        if value is None or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    async def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        price: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        lifecycle_metadata: Optional[dict] = None,
    ) -> OrderResponse:
        if symbol in self._restored_open_symbols:
            list_open_orders = getattr(self.broker, "list_open_orders", None)
            still_open = (
                any(item["order"].symbol == symbol for item in list_open_orders())
                if list_open_orders is not None
                else True
            )
            if still_open:
                raise RuntimeError(
                    f"Open IB order already exists for {symbol}; refusing duplicate submission"
                )
            self._restored_open_symbols.discard(symbol)
        decision_at = datetime.now(timezone.utc)
        qualify_contract = getattr(self.broker, "qualify_contract", None)
        qualified_contract = (
            await qualify_contract(symbol) if qualify_contract is not None else None
        )
        quote = None
        if order_type in {"market", "stop"}:
            if qualified_contract is not None:
                quote = await self.broker.get_market_data(
                    symbol,
                    qualified_contract=qualified_contract,
                )
            else:
                quote = await self.broker.get_market_data(symbol)
            quote_time = quote.timestamp
            if quote_time is None:
                raise RuntimeError(f"Broker quote for {symbol} is missing exchange timestamp")
            if quote_time.tzinfo is None:
                quote_time = quote_time.replace(tzinfo=timezone.utc)
            received_at = datetime.now(timezone.utc)
            age_seconds = (received_at - quote_time.astimezone(timezone.utc)).total_seconds()
            if age_seconds > self.quote_max_age_seconds or age_seconds < -1.0:
                raise RuntimeError(f"Broker quote for {symbol} is stale ({age_seconds:.3f}s old)")
            executable_quote = quote.ask if side == "buy" else quote.bid
            if executable_quote is None or executable_quote <= 0:
                raise RuntimeError(f"Broker quote for {symbol} is missing executable {side} price")
            expected_price = executable_quote
            benchmark_type = "executable_quote" if order_type == "market" else "stop_quote"
        elif order_type == "limit":
            expected_price = limit_price if limit_price is not None else price
            if expected_price is None:
                raise ValueError("limit orders require a limit price")
            benchmark_type = "limit_price"
        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        broker_order = BrokerOrder(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            expected_price=expected_price,
            limit_price=limit_price if limit_price is not None else (price if order_type == "limit" else None),
            stop_price=stop_price if stop_price is not None else (price if order_type == "stop" else None),
            decision_at=decision_at,
            submitted_at=None,
            benchmark_type=benchmark_type,
            benchmark_price=expected_price,
            quote_bid=quote.bid if quote else None,
            quote_ask=quote.ask if quote else None,
            quote_midpoint=(
                (quote.bid + quote.ask) / 2.0
                if quote and quote.bid is not None and quote.ask is not None
                else None
            ),
            strategy_name=(lifecycle_metadata or {}).get("strategy_name"),
            strategy_stop_price=(lifecycle_metadata or {}).get("stop_price"),
            take_profit=(lifecycle_metadata or {}).get("take_profit"),
            exit_reason=(lifecycle_metadata or {}).get("exit_reason"),
        )
        # Validate immediately before the call that reaches placeOrder. There
        # must be no contract qualification after this point.
        if quote is not None:
            self._validate_quote_timestamp(symbol, quote.timestamp)
        submission_fallback = datetime.now(timezone.utc)
        if qualified_contract is not None:
            broker_order_id = await self.broker.submit_order(
                broker_order,
                qualified_contract=qualified_contract,
            )
        else:
            broker_order_id = await self.broker.submit_order(broker_order)
        get_submitted_order = getattr(self.broker, "get_submitted_order", None)
        submitted_order = (
            get_submitted_order(broker_order_id)
            if get_submitted_order is not None
            else None
        )
        submitted_at = (
            getattr(submitted_order, "submitted_at", None) or submission_fallback
        )

        try:
            fill = await self.broker.wait_for_fill(broker_order_id, timeout_seconds=self.fill_timeout_seconds)
        except TimeoutError:
            order = Order(
                order_id=broker_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=expected_price,
                order_type=order_type,
                status=OrderStatus.SUBMITTED,
                decision_at=decision_at,
                submitted_at=submitted_at,
                benchmark_type=benchmark_type,
                benchmark_price=expected_price,
                quote_bid=broker_order.quote_bid,
                quote_ask=broker_order.quote_ask,
                quote_midpoint=broker_order.quote_midpoint,
                stop_price=broker_order.stop_price,
                limit_price=broker_order.limit_price,
                benchmark_version=2,
                benchmark_valid=True,
            )
            self._orders[broker_order_id] = order
            return OrderResponse(
                order_id=broker_order_id,
                status=OrderStatus.SUBMITTED,
                filled_qty=0.0,
                avg_price=0.0,
                remaining_qty=quantity,
            )

        order = Order(
            order_id=broker_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=expected_price,
            order_type=order_type,
            status=OrderStatus.FILLED,
            decision_at=fill.decision_at,
            submitted_at=fill.submitted_at,
            filled_at=fill.filled_at,
            benchmark_type=fill.benchmark_type,
            benchmark_price=fill.benchmark_price,
            quote_bid=fill.quote_bid,
            quote_ask=fill.quote_ask,
            quote_midpoint=fill.quote_midpoint,
            stop_price=fill.stop_price,
            limit_price=fill.limit_price,
            benchmark_version=fill.benchmark_version,
            benchmark_valid=fill.benchmark_valid,
            execution_id=fill.execution_id,
            execution_ids=[item["execution_id"] for item in fill.executions],
            permanent_order_id=fill.permanent_order_id,
            account_id=fill.account_id,
            trade_currency=fill.instrument_currency,
            commission_currency=fill.commission_currency,
            executions=list(fill.executions),
        )
        order.filled_qty = fill.quantity
        order.avg_price = fill.avg_fill_price
        order.commission = fill.commission or 0.0
        order.status = OrderStatus.FILLED if fill.quantity >= quantity else OrderStatus.PARTIAL
        self._orders[broker_order_id] = order
        self._prices[symbol] = fill.avg_fill_price

        return OrderResponse(
            order_id=broker_order_id,
            status=OrderStatus.FILLED if fill.quantity >= quantity else OrderStatus.PARTIAL,
            filled_qty=fill.quantity,
            avg_price=fill.avg_fill_price,
            remaining_qty=max(quantity - fill.quantity, 0.0),
        )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        if order_id not in self._orders:
            raise ValueError(f"Unknown order: {order_id}")

        order = self._orders[order_id]
        acknowledged = await self.broker.cancel_order(order_id)
        if acknowledged is not True:
            raise RuntimeError(
                f"Cancellation state could not be confirmed for order {order_id}"
            )
        status = await self.broker.get_order_status(order_id)
        raw_status = str(status.get("status") or "")
        if raw_status not in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}:
            raise RuntimeError(
                f"Cancellation state could not be confirmed for order {order_id}: "
                f"{raw_status or 'unknown'}"
            )

        # Re-read the durable execution journal after terminal acknowledgement.
        # This catches a fill that raced the cancellation callback.
        executions = [
            execution
            for execution in self.list_durable_executions()
            if str(execution.get("broker_order_id")) == str(order_id)
        ]
        for execution in executions:
            self._on_broker_execution(execution)
        order = self._orders[order_id]
        status_filled = float(status.get("filled") or 0.0)
        filled_qty = max(status_filled, float(order.filled_qty or 0.0))
        remaining_qty = max(order.quantity - filled_qty, 0.0)
        avg_price = float(status.get("avg_fill_price") or order.avg_price or 0.0)

        terminal_status = (
            OrderStatus.FILLED
            if raw_status == "Filled" or filled_qty >= order.quantity
            else OrderStatus.CANCELLED
        )
        order.status = terminal_status
        order.filled_qty = filled_qty
        order.avg_price = avg_price

        return OrderResponse(
            order_id=order_id,
            status=terminal_status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            remaining_qty=remaining_qty,
        )

    def _validate_quote_timestamp(
        self,
        symbol: str,
        quote_time: Optional[datetime],
    ) -> None:
        if quote_time is None:
            raise RuntimeError(f"Broker quote for {symbol} is missing exchange timestamp")
        if quote_time.tzinfo is None:
            quote_time = quote_time.replace(tzinfo=timezone.utc)
        age_seconds = (
            datetime.now(timezone.utc) - quote_time.astimezone(timezone.utc)
        ).total_seconds()
        if age_seconds > self.quote_max_age_seconds or age_seconds < -1.0:
            raise RuntimeError(
                f"Broker quote for {symbol} is stale ({age_seconds:.3f}s old)"
            )

    async def get_position(self, symbol: str) -> Optional[Position]:
        broker_position = await self.get_position_snapshot(symbol)
        if broker_position is None:
            return None
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
            instrument_currency=broker_position.instrument_currency,
            # Position recalculates P&L from instrument prices locally, so the
            # result is denominated in the instrument currency. Broker account-
            # base P&L remains available on BrokerPosition snapshots.
            unrealized_pnl_currency=broker_position.instrument_currency,
        )

    async def get_position_snapshot(self, symbol: str) -> Optional[BrokerPosition]:
        """Return broker position fields without requesting execution market data."""
        positions = await self.broker.get_positions()
        return next(
            (
                position
                for position in positions
                if position.symbol == symbol and position.quantity != 0
            ),
            None,
        )

    async def get_account(self) -> AccountState:
        account = await self.broker.get_account_info()
        equity = account.net_liquidation if account.net_liquidation is not None else 0.0
        cash = account.cash if account.cash is not None else 0.0
        buying_power = account.buying_power if account.buying_power is not None else cash
        return AccountState(
            account_id=account.account_id,
            cash=max(cash, 0.0),
            equity=max(equity, 0.0),
            buying_power=max(buying_power, 0.0),
            portfolio_value=max(equity, 0.0),
            total_pnl=account.realized_pnl or 0.0,
            base_currency=account.currency,
            cash_currency=account.cash_currency,
            equity_currency=account.net_liquidation_currency,
            buying_power_currency=account.buying_power_currency,
            realized_pnl=account.realized_pnl,
            realized_pnl_currency=account.realized_pnl_currency,
            unrealized_pnl=account.unrealized_pnl,
            unrealized_pnl_currency=account.unrealized_pnl_currency,
            broker_cash=account.cash,
            broker_equity=account.net_liquidation,
            broker_buying_power=account.buying_power,
            timestamp=account.timestamp,
        )

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)