"""ExecutionEngine adapter for Interactive Brokers paper/live execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from vibe.common.execution.base import (
    ExecutionEngine,
    OrderResponse,
    PartialPlacementError,
    RestingEntryLeg,
)
from vibe.common.models import AccountState, Order, OrderStatus, Position
from vibe.trading_bot.brokers.base import BrokerOrder, BrokerPosition
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.storage.ib_execution_store import (
    canonical_execution,
    dedupe_corrections,
)


logger = logging.getLogger(__name__)


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
                        strategy_name=restored.strategy_name,
                        strategy_stop_price=restored.strategy_stop_price,
                        take_profit=restored.take_profit,
                        entry_cancel_after_seconds=restored.entry_cancel_after_seconds,
                        entry_expiry_bars=restored.entry_expiry_bars,
                        entry_signal_bar_timestamp=restored.entry_signal_bar_timestamp,
                        entry_bar_interval_seconds=restored.entry_bar_interval_seconds,
                        # Journals written before submit_order carried the role
                        # still mark a protective stop through its exit reason.
                        order_role=restored.order_role or (
                            "protective_stop"
                            if restored.order_type == "stop"
                            and restored.exit_reason == "protective_stop"
                            else None
                        ),
                        oca_group=restored.oca_group,
                        parent_order_id=restored.parent_order_id,
                        gap_fill=restored.gap_fill,
                        placement_latency_s=restored.placement_latency_s,
                        capped_by=restored.capped_by,
                        or_source=restored.or_source,
                        or_high=restored.or_high,
                        or_low=restored.or_low,
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
        """Durable executions, each IBKR correction collapsed onto its latest revision."""
        list_executions = getattr(self.broker, "list_durable_executions", None)
        return dedupe_corrections(list_executions()) if list_executions is not None else []

    async def reconcile_todays_executions(self) -> int:
        """Pull the broker's execution history into the durable journal.

        A protective stop can fill while the bot is down; without this the
        journal would miss it. Raises when the history cannot be read.
        """
        reconcile = getattr(self.broker, "reconcile_executions", None)
        if reconcile is None:
            raise RuntimeError("broker cannot report execution history")
        ingested = await reconcile(strict=True)
        for execution in self.list_durable_executions():
            self._on_broker_execution(execution)
        return ingested

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
        """Refresh the in-memory order from every durable exec/commission update.

        An IBKR correction replaces the fill it corrects (same canonical id);
        an older revision arriving late is ignored, so a corrected fill is
        never counted twice.
        """
        execution = canonical_execution(execution)
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
                strategy_name=metadata.get("strategy_name"),
                strategy_stop_price=metadata.get("strategy_stop_price"),
                take_profit=metadata.get("take_profit"),
                entry_cancel_after_seconds=metadata.get("entry_cancel_after_seconds"),
                entry_expiry_bars=metadata.get("entry_expiry_bars"),
                entry_signal_bar_timestamp=self._as_datetime(
                    metadata.get("entry_signal_bar_timestamp")
                ),
                entry_bar_interval_seconds=metadata.get("entry_bar_interval_seconds"),
                order_role=metadata.get("order_role"),
                oca_group=metadata.get("oca_group"),
                parent_order_id=metadata.get("parent_order_id"),
                gap_fill=(
                    None if metadata.get("gap_fill") is None else bool(metadata.get("gap_fill"))
                ),
                placement_latency_s=metadata.get("placement_latency_s"),
                capped_by=metadata.get("capped_by"),
                or_source=metadata.get("or_source"),
                or_high=metadata.get("or_high"),
                or_low=metadata.get("or_low"),
            )
            self._orders[broker_order_id] = order

        executions_by_id = {
            str(item["execution_id"]): dict(item)
            for item in order.executions
        }
        existing = executions_by_id.get(str(execution["execution_id"]))
        if existing is not None and int(existing.get("execution_revision") or 0) > int(
            execution.get("execution_revision") or 0
        ):
            return
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

    def _refuse_if_restored_orders_open(self, symbol: str) -> None:
        """Refuse a new order while a restored broker order for ``symbol`` is open."""
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
            self._refuse_if_restored_orders_open(symbol)
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
            order_role=(lifecycle_metadata or {}).get("order_role"),
            entry_cancel_after_seconds=(lifecycle_metadata or {}).get(
                "entry_cancel_after_seconds"
            ),
            entry_expiry_bars=(lifecycle_metadata or {}).get("entry_expiry_bars"),
            entry_signal_bar_timestamp=(lifecycle_metadata or {}).get(
                "entry_signal_bar_timestamp"
            ),
            entry_bar_interval_seconds=(lifecycle_metadata or {}).get(
                "entry_bar_interval_seconds"
            ),
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
            initial_fill_wait = (
                min(self.fill_timeout_seconds, 0.5)
                if order_type == "stop"
                else self.fill_timeout_seconds
            )
            fill = await self.broker.wait_for_fill(
                broker_order_id,
                timeout_seconds=initial_fill_wait,
            )
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
                strategy_name=broker_order.strategy_name,
                strategy_stop_price=broker_order.strategy_stop_price,
                take_profit=broker_order.take_profit,
                entry_cancel_after_seconds=broker_order.entry_cancel_after_seconds,
                entry_expiry_bars=broker_order.entry_expiry_bars,
                entry_signal_bar_timestamp=broker_order.entry_signal_bar_timestamp,
                entry_bar_interval_seconds=broker_order.entry_bar_interval_seconds,
                order_role=broker_order.order_role,
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
            strategy_name=broker_order.strategy_name,
            strategy_stop_price=broker_order.strategy_stop_price,
            take_profit=broker_order.take_profit,
            entry_cancel_after_seconds=broker_order.entry_cancel_after_seconds,
            entry_expiry_bars=broker_order.entry_expiry_bars,
            entry_signal_bar_timestamp=broker_order.entry_signal_bar_timestamp,
            entry_bar_interval_seconds=broker_order.entry_bar_interval_seconds,
            order_role=broker_order.order_role,
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

    async def list_positions(self) -> List[Position]:
        """Every non-flat stock position of the account.

        Prices come from the broker's portfolio marks (or the last fill), never
        from a fresh market-data request, so this is cheap enough to call
        before every entry.
        """
        positions: List[Position] = []
        for broker_position in await self.broker.get_positions():
            if broker_position.quantity == 0:
                continue
            current_price = (
                broker_position.market_price
                or self._prices.get(broker_position.symbol)
                or broker_position.avg_cost
            )
            positions.append(
                Position(
                    symbol=broker_position.symbol,
                    side="long" if broker_position.quantity > 0 else "short",
                    quantity=abs(broker_position.quantity),
                    entry_price=broker_position.avg_cost,
                    current_price=current_price,
                    instrument_currency=broker_position.instrument_currency,
                    unrealized_pnl_currency=broker_position.instrument_currency,
                )
            )
        return positions

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
            available_funds=getattr(account, "available_funds", None),
            timestamp=account.timestamp,
        )

    async def fetch_session_minute_bars(self, symbol: str, session_date: Any):
        """The session's regular-hours 1-minute bars as a tz-aware OHLCV frame."""
        import pandas as pd

        fetch = getattr(self.broker, "get_session_minute_bars", None)
        if fetch is None:
            raise RuntimeError("broker cannot provide historical minute bars")
        rows = await fetch(symbol, session_date)
        return self._minute_frame(rows)

    @staticmethod
    def _minute_frame(rows: List[Dict[str, Any]]):
        import pandas as pd

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(rows)
        index = pd.to_datetime(frame.pop("timestamp"), utc=True).dt.tz_convert("America/New_York")
        frame.index = pd.DatetimeIndex(index)
        return frame.sort_index()

    async def fetch_opening_range_bars(self, symbol: str, or_start: datetime, or_end: datetime):
        """IBKR's 1-minute TRADES bars (RTH) covering ``[or_start, or_end)``.

        The same source the parity replay uses, so live levels and replayed
        levels are identical by construction.
        """
        fetch = getattr(self.broker, "get_minute_bars_between", None)
        if fetch is None:
            raise RuntimeError("broker cannot provide historical minute bars")
        frame = self._minute_frame(await fetch(symbol, or_start, or_end))
        if frame.empty:
            return frame
        start = or_start.astimezone(frame.index.tz)
        end = or_end.astimezone(frame.index.tz)
        return frame[(frame.index >= start) & (frame.index < end)]

    async def session_net_quantity(self, symbol: str) -> Optional[float]:
        """Signed quantity opened by today's resting entry and not yet closed, from fills.

        Counts today's executions (corrections collapsed) from the first
        resting-entry fill on. None when ``symbol`` has no resting-entry fill
        today: then fills say nothing about the position (e.g. a carryover).
        """
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        today = datetime.now(tz).date()
        todays = []
        for execution in self.list_durable_executions():
            if str(execution.get("symbol")) != symbol:
                continue
            filled_at = self._as_datetime(execution.get("filled_at"))
            if filled_at is None:
                continue
            if filled_at.tzinfo is None:
                filled_at = filled_at.replace(tzinfo=timezone.utc)
            if filled_at.astimezone(tz).date() == today:
                todays.append((filled_at, execution))
        todays.sort(key=lambda item: item[0])
        net: Optional[float] = None
        for _, execution in todays:
            metadata = execution.get("order_metadata") or {}
            if net is None:
                if metadata.get("order_role") != "entry" or not metadata.get("oca_group"):
                    continue
                net = 0.0
            quantity = abs(float(execution.get("quantity") or 0.0))
            net += quantity if str(execution.get("side")) == "buy" else -quantity
        return net

    def has_submitted_entry(self, symbol: str, since: datetime) -> bool:
        """Whether a resting-stop entry for ``symbol`` was submitted at/after ``since``.

        Read from the durable submission journal, so a restart cannot place a
        second OCA pair for a session whose first pair already filled or ended.
        """
        store = getattr(self.broker, "execution_store", None)
        if store is None or not hasattr(store, "list_submitted_orders"):
            return False
        return bool(
            store.list_submitted_orders(
                symbol=symbol, order_role="entry", submitted_after=since
            )
        )

    def order_lifecycle(self, order_id: str) -> Dict[str, Any]:
        """Role, OCA and gap-fill tags of an order, for fill telemetry."""
        order = self._orders.get(str(order_id))
        if order is None:
            return {}
        return {
            "order_role": order.order_role,
            "oca_group": order.oca_group,
            "gap_fill": order.gap_fill,
            "benchmark_type": order.benchmark_type,
            "placement_latency_s": order.placement_latency_s,
        }

    async def get_order(self, order_id: str) -> Optional[Order]:
        order = self._orders.get(order_id)
        if order is not None and (
            order.order_role in ("entry", "protective_stop") or order.oca_group
        ) and order.status in {
            OrderStatus.CREATED,
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL,
        }:
            # Linked (OCA / bracket) orders can be ended by the broker on its
            # own: an OCA sibling filled, a bracket parent was cancelled, or
            # the order was rejected. Other orders keep their historical path.
            snapshot = getattr(self.broker, "order_status_snapshot", None)
            raw_status = snapshot(order_id) if snapshot is not None else None
            if (
                raw_status in {"Cancelled", "ApiCancelled", "Inactive"}
                and float(order.filled_qty or 0.0) < float(order.quantity)
            ):
                order.status = OrderStatus.CANCELLED
        return order

    async def submit_resting_entry_pair(
        self,
        symbol: str,
        legs: List[RestingEntryLeg],
        oca_group: str,
        lifecycle_metadata: Optional[dict] = None,
    ) -> List[tuple[Order, Order]]:
        """Place resting stop entries in one OCA group, each with a protective stop.

        The slippage benchmark of every leg is its own trigger level
        (``benchmark_type="stop_trigger"``), so a fill's slippage is measured
        against the price the backtest assumes, not a quote.
        """
        submit = getattr(self.broker, "submit_oca_brackets", None)
        if submit is None:
            raise RuntimeError("broker does not support OCA bracket orders")
        if symbol in self._restored_open_symbols:
            self._refuse_if_restored_orders_open(symbol)
        metadata = lifecycle_metadata or {}
        decision_at = datetime.now(timezone.utc)
        qualify_contract = getattr(self.broker, "qualify_contract", None)
        qualified_contract = (
            await qualify_contract(symbol) if qualify_contract is not None else None
        )
        reference_price = await self._fresh_reference_price(symbol, qualified_contract)
        or_end = metadata.get("or_end")
        placement_latency_s = (
            ((metadata.get("clock") or (lambda: datetime.now(timezone.utc)))() - or_end).total_seconds()
            if isinstance(or_end, datetime)
            else metadata.get("placement_latency_s")
        )

        def broker_order(leg: RestingEntryLeg, role: str) -> BrokerOrder:
            entry = role == "entry"
            side = leg.side if entry else ("sell" if leg.side == "buy" else "buy")
            level = leg.trigger_price if entry else leg.protective_stop_price
            return BrokerOrder(
                symbol=symbol,
                side=side,
                quantity=leg.quantity,
                order_type="stop",
                expected_price=level,
                stop_price=level,
                decision_at=decision_at,
                benchmark_type="stop_trigger",
                benchmark_price=level,
                strategy_name=metadata.get("strategy_name"),
                strategy_stop_price=leg.protective_stop_price,
                exit_reason=None if entry else "protective_stop",
                entry_cancel_after_seconds=(
                    metadata.get("entry_cancel_after_seconds") if entry else None
                ),
                entry_signal_bar_timestamp=metadata.get("entry_signal_bar_timestamp"),
                order_role=role,
                oca_group=oca_group if entry else None,
                gap_fill=(
                    leg.triggered_by(reference_price)
                    if entry and reference_price is not None
                    else None
                ),
                placement_latency_s=placement_latency_s if entry else None,
                capped_by=leg.capped_by if entry else None,
                or_source=metadata.get("or_source"),
                or_high=metadata.get("or_high"),
                or_low=metadata.get("or_low"),
            )

        brackets = [(broker_order(leg, "entry"), broker_order(leg, "protective_stop")) for leg in legs]
        failure: Optional[PartialPlacementError] = None
        try:
            ids = await submit(brackets, qualified_contract=qualified_contract)
        except PartialPlacementError as exc:
            failure, ids = exc, list(exc.placed)

        placed: List[tuple[Order, Order]] = []
        for (entry_bo, stop_bo), (entry_id, stop_id) in zip(brackets, ids):
            pair = []
            for bo, order_id, parent_id in ((entry_bo, entry_id, None), (stop_bo, stop_id, entry_id)):
                order = Order(
                    order_id=order_id,
                    symbol=symbol,
                    side=bo.side,
                    quantity=float(bo.quantity),
                    price=float(bo.stop_price),
                    order_type="stop",
                    status=OrderStatus.SUBMITTED,
                    decision_at=decision_at,
                    submitted_at=datetime.now(timezone.utc),
                    benchmark_type=bo.benchmark_type,
                    benchmark_price=bo.benchmark_price,
                    stop_price=bo.stop_price,
                    benchmark_version=2,
                    benchmark_valid=True,
                    strategy_name=bo.strategy_name,
                    strategy_stop_price=bo.strategy_stop_price,
                    entry_cancel_after_seconds=bo.entry_cancel_after_seconds,
                    entry_signal_bar_timestamp=bo.entry_signal_bar_timestamp,
                    order_role=bo.order_role,
                    oca_group=bo.oca_group,
                    parent_order_id=parent_id,
                    gap_fill=bo.gap_fill,
                    placement_latency_s=bo.placement_latency_s,
                    capped_by=bo.capped_by,
                    or_source=bo.or_source,
                    or_high=bo.or_high,
                    or_low=bo.or_low,
                )
                self._orders[order_id] = order
                pair.append(order)
            placed.append((pair[0], pair[1]))
        if failure is not None:
            raise PartialPlacementError(str(failure), placed) from failure
        return placed

    async def _fresh_reference_price(
        self, symbol: str, qualified_contract: Any, timeout_s: float = 1.0
    ) -> Optional[float]:
        """Last trade (else midpoint) from a live IB quote, for the gap-fill flag.

        Bounded to ``timeout_s`` so placement is barely delayed. None when no
        live quote arrives: a stale bar close would mislabel the fill.
        """
        get_quote = getattr(self.broker, "get_market_data", None)
        if get_quote is None:
            return None
        try:
            quote = await asyncio.wait_for(
                get_quote(symbol, timeout_seconds=timeout_s, qualified_contract=qualified_contract),
                timeout=timeout_s + 0.5,
            )
        except Exception as exc:
            logger.info("No live quote for %s at placement (%s); gap_fill unset", symbol, exc)
            return None
        last = getattr(quote, "last", None)
        return float(last) if last else getattr(quote, "market_price", None)

    async def submit_protective_stop(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        strategy_name: Optional[str] = None,
    ) -> Order:
        """Place a standalone protective stop (replaces a lost bracket child)."""
        submit = getattr(self.broker, "submit_standalone_stop", None)
        if submit is None:
            raise RuntimeError("broker cannot place a standalone stop")
        now = datetime.now(timezone.utc)
        broker_order = BrokerOrder(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="stop",
            expected_price=stop_price,
            stop_price=stop_price,
            decision_at=now,
            benchmark_type="stop_trigger",
            benchmark_price=stop_price,
            strategy_name=strategy_name,
            strategy_stop_price=stop_price,
            exit_reason="protective_stop",
            order_role="protective_stop",
        )
        order_id = await submit(broker_order)
        order = Order(
            order_id=str(order_id),
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            price=float(stop_price),
            order_type="stop",
            status=OrderStatus.SUBMITTED,
            decision_at=now,
            submitted_at=datetime.now(timezone.utc),
            benchmark_type="stop_trigger",
            benchmark_price=stop_price,
            stop_price=stop_price,
            benchmark_version=2,
            benchmark_valid=True,
            strategy_name=strategy_name,
            strategy_stop_price=stop_price,
            order_role="protective_stop",
        )
        self._orders[order.order_id] = order
        return order