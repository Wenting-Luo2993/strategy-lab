"""Interactive Brokers paper/live adapter built on ib_insync."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from vibe.trading_bot.brokers.base import (
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    BrokerQuote,
    FillEvent,
)
from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore

try:
    from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopOrder, Trade
except ImportError:  # pragma: no cover - exercised by environments without ib_insync
    IB = None
    LimitOrder = None
    MarketOrder = None
    Stock = None
    StopOrder = None
    Trade = Any

logger = logging.getLogger(__name__)

_ACCOUNT_SUMMARY_TAGS = (
    "AccountType,NetLiquidation,TotalCashValue,SettledCash,"
    "AccruedCash,BuyingPower,EquityWithLoanValue,"
    "PreviousDayEquityWithLoanValue,GrossPositionValue,RegTEquity,"
    "RegTMargin,SMA,InitMarginReq,MaintMarginReq,AvailableFunds,"
    "ExcessLiquidity,Cushion,FullInitMarginReq,FullMaintMarginReq,"
    "FullAvailableFunds,FullExcessLiquidity,LookAheadNextChange,"
    "LookAheadInitMarginReq,LookAheadMaintMarginReq,"
    "LookAheadAvailableFunds,LookAheadExcessLiquidity,"
    "HighestSeverity,DayTradesRemaining,DayTradesRemainingT+1,"
    "DayTradesRemainingT+2,DayTradesRemainingT+3,"
    "DayTradesRemainingT+4,Leverage,$LEDGER:ALL"
)
_IB_UNSET_DOUBLE = 1.7976931348623157e308


class IBOperatorActionRequired(RuntimeError):
    """Raised when IB Gateway requires manual operator action before API use."""


class IBConnectionFailed(RuntimeError):
    """Raised when IB Gateway connection fails after bounded retries."""


class InteractiveBrokersAPI:
    """Interactive Brokers adapter for TWS or IB Gateway."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        account_id: Optional[str] = None,
        exchange: str = "SMART",
        currency: str = "USD",
        account_base_currency: Optional[str] = None,
        market_data_type: int = 1,
        connect_timeout: float = 20.0,
        connect_max_retries: int = 3,
        connect_retry_delay_seconds: float = 2.0,
        commission_report_timeout_seconds: float = 2.0,
        account_data_timeout_seconds: float = 5.0,
        model_code: str = "",
        execution_db_path: str = "./data/local/ib_executions.db",
        readonly: bool = False,
    ):
        if IB is None:
            raise ImportError("ib_insync is required for InteractiveBrokersAPI")

        self.host = host
        self.port = port
        self.client_id = client_id
        self.account_id = account_id
        self.exchange = exchange
        self.currency = currency
        self.account_base_currency = account_base_currency
        self.market_data_type = market_data_type
        self.connect_timeout = connect_timeout
        self.connect_max_retries = max(connect_max_retries, 1)
        self.connect_retry_delay_seconds = max(connect_retry_delay_seconds, 0.0)
        self.commission_report_timeout_seconds = max(commission_report_timeout_seconds, 0.0)
        self.account_data_timeout_seconds = max(account_data_timeout_seconds, 0.1)
        self.model_code = model_code
        self.readonly = readonly
        self.execution_store = IBExecutionStore(execution_db_path)
        self.ib = IB()
        self._last_error_code: Optional[int] = None
        self._last_error_message: Optional[str] = None
        self.ib.errorEvent += self._handle_ib_error
        if hasattr(self.ib, "execDetailsEvent"):
            self.ib.execDetailsEvent += self._handle_execution_details
        if hasattr(self.ib, "commissionReportEvent"):
            self.ib.commissionReportEvent += self._handle_commission_report
        self._trades: Dict[str, Any] = {}
        self._submitted_orders: Dict[str, BrokerOrder] = {}
        self._execution_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._pnl_request_lock = asyncio.Lock()

    def _handle_ib_error(self, req_id: int, error_code: int, error_string: str, *args: Any) -> None:
        """Capture IB API errors emitted during async connection and requests."""
        self._last_error_code = error_code
        self._last_error_message = error_string
        if error_code == 10141:
            logger.error("IB Gateway requires paper trading disclaimer acceptance before API use")

    async def connect(self) -> bool:
        """Connect to TWS or IB Gateway."""
        if self.ib.isConnected():
            self._reconcile_open_orders()
            await self.reconcile_executions()
            return True

        last_exception: Optional[BaseException] = None
        for attempt in range(1, self.connect_max_retries + 1):
            self._last_error_code = None
            self._last_error_message = None
            try:
                await self.ib.connectAsync(
                    self.host,
                    self.port,
                    clientId=self.client_id,
                    account=self.account_id or "",
                    timeout=self.connect_timeout,
                )
                break
            except Exception as exc:
                last_exception = exc
                if self._requires_operator_action(exc):
                    await self.disconnect()
                    raise IBOperatorActionRequired(
                        "IB Gateway rejected API access because the paper trading disclaimer "
                        "has not been accepted. Accept the disclaimer in Gateway, then restart "
                        "the trading bot service."
                    ) from exc

                await self.disconnect()
                if attempt >= self.connect_max_retries:
                    raise IBConnectionFailed(
                        f"Failed to connect to IB Gateway at {self.host}:{self.port} "
                        f"after {self.connect_max_retries} attempts"
                    ) from exc

                logger.warning(
                    "IB connection attempt %s/%s failed: %s; retrying in %.1fs",
                    attempt,
                    self.connect_max_retries,
                    exc,
                    self.connect_retry_delay_seconds,
                )
                if self.connect_retry_delay_seconds > 0:
                    await asyncio.sleep(self.connect_retry_delay_seconds)

        if last_exception and not self.ib.isConnected():
            raise IBConnectionFailed(
                f"Failed to connect to IB Gateway at {self.host}:{self.port} "
                f"after {self.connect_max_retries} attempts"
            ) from last_exception

        if self._requires_operator_action(None):
            await self.disconnect()
            raise IBOperatorActionRequired(
                "IB Gateway rejected API access because the paper trading disclaimer has not been accepted."
            )

        self._reconcile_open_orders()
        await self.reconcile_executions()
        logger.info("Connected to IB at %s:%s client_id=%s", self.host, self.port, self.client_id)
        return self.ib.isConnected()

    def _requires_operator_action(self, exc: Optional[BaseException]) -> bool:
        """Return True when the last IB error indicates a manual Gateway action is needed."""
        message = self._last_error_message or ""
        if exc is not None:
            message = f"{message} {exc}"
        return self._last_error_code == 10141 or "Paper trading disclaimer" in message

    async def disconnect(self) -> bool:
        """Disconnect from IB."""
        if self.ib.isConnected():
            self.ib.disconnect()
        logger.info("Disconnected from IB")
        return True

    async def qualify_contract(self, symbol: str):
        """Qualify a stock contract before any execution benchmark is captured."""
        return await self._stock_contract(symbol)

    async def get_market_data(
        self,
        symbol: str,
        timeout_seconds: float = 15.0,
        qualified_contract: Any = None,
    ) -> BrokerQuote:
        """Request a live quote carrying IB's exchange timestamp.

        ``Ticker.time`` is only the local packet-receipt time in ib_insync, so
        it must not be used for execution freshness checks. Tick-by-tick
        BidAsk updates include IB's market timestamp and executable prices.
        """
        if self.market_data_type != 1:
            raise RuntimeError(
                "Delayed/frozen IB market data cannot be used for execution benchmarking"
            )
        contract = qualified_contract or await self.qualify_contract(symbol)
        self.ib.reqMarketDataType(self.market_data_type)
        ticker = None
        timestamped_ticker = None
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        try:
            ticker = self.ib.reqMktData(contract, "", False, False)
            timestamped_ticker = self.ib.reqTickByTickData(
                contract,
                "BidAsk",
                0,
                False,
            )
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
                bid_ask_ticks = list(
                    getattr(timestamped_ticker, "tickByTicks", None) or []
                )
                latest = next(
                    (
                        tick
                        for tick in reversed(bid_ask_ticks)
                        if getattr(tick, "time", None) is not None
                    ),
                    None,
                )
                if latest is None:
                    continue
                bid = self._clean_number(getattr(latest, "bidPrice", None))
                ask = self._clean_number(getattr(latest, "askPrice", None))
                last = self._clean_number(ticker.last)
                if bid is not None and ask is not None:
                    return BrokerQuote(
                        symbol=symbol,
                        bid=bid,
                        ask=ask,
                        last=last,
                        market_price=(bid + ask) / 2.0,
                        timestamp=latest.time,
                    )

            raise TimeoutError(
                f"Timed out waiting for timestamped live market data for {symbol}"
            )
        finally:
            if ticker is not None:
                self.ib.cancelMktData(contract)
            if timestamped_ticker is not None:
                self.ib.cancelTickByTickData(contract, "BidAsk")

    async def submit_order(
        self,
        order: BrokerOrder,
        qualified_contract: Any = None,
    ) -> str:
        """Submit an order and return the IB order id."""
        if self.readonly:
            raise RuntimeError("IB adapter is in readonly mode; order submission is disabled")

        contract = qualified_contract or await self.qualify_contract(order.symbol)
        ib_order = self._to_ib_order(order)
        # This timestamp deliberately brackets the synchronous API call. Contract
        # qualification and benchmark acquisition have already completed.
        submitted_at = datetime.now(timezone.utc)
        trade = self.ib.placeOrder(contract, ib_order)
        broker_order_id = str(trade.order.orderId)

        submitted_order = BrokerOrder(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            expected_price=order.expected_price,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            strategy_order_id=order.strategy_order_id,
            broker_order_id=broker_order_id,
            status="submitted",
            decision_at=order.decision_at,
            submitted_at=submitted_at,
            benchmark_type=order.benchmark_type,
            benchmark_price=order.benchmark_price,
            quote_bid=order.quote_bid,
            quote_ask=order.quote_ask,
            quote_midpoint=order.quote_midpoint,
            benchmark_version=order.benchmark_version,
            strategy_name=order.strategy_name,
            strategy_stop_price=order.strategy_stop_price,
            take_profit=order.take_profit,
            exit_reason=order.exit_reason,
        )
        self._trades[broker_order_id] = trade
        self._submitted_orders[broker_order_id] = submitted_order
        self.execution_store.upsert_submitted_order(
            self._submitted_order_payload(
                submitted_order,
                permanent_order_id=getattr(trade.order, "permId", None),
            )
        )
        logger.info("Submitted IB order %s %s %s %s", broker_order_id, order.side, order.quantity, order.symbol)
        return broker_order_id

    def get_submitted_order(self, broker_order_id: str) -> Optional[BrokerOrder]:
        """Return the broker-captured submission metadata for an order."""
        return self._submitted_orders.get(str(broker_order_id))

    async def wait_for_fill(self, broker_order_id: str, timeout_seconds: float = 60.0) -> FillEvent:
        """Wait for a submitted order to fill and return a normalized fill event."""
        trade = self._require_trade(broker_order_id)
        submitted_order = self._submitted_orders[broker_order_id]
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            status = getattr(trade.orderStatus, "status", "")
            filled = float(getattr(trade.orderStatus, "filled", 0.0) or 0.0)

            if getattr(trade, "fills", None) and (
                status == "Filled" or filled >= submitted_order.quantity
            ):
                if self.commission_report_timeout_seconds > 0:
                    commission_deadline = min(
                        deadline,
                        asyncio.get_running_loop().time() + self.commission_report_timeout_seconds,
                    )
                    while (
                        asyncio.get_running_loop().time() < commission_deadline
                        and not self._all_commission_reports_received(trade)
                    ):
                        await asyncio.sleep(0.05)
                return self._fill_event_from_trade(
                    broker_order_id,
                    trade,
                    submitted_order,
                    status or "Filled",
                )

            if status in {"Cancelled", "ApiCancelled", "Inactive"}:
                raise RuntimeError(f"IB order {broker_order_id} reached terminal status {status}")

        if getattr(trade, "fills", None):
            return self._fill_event_from_trade(
                broker_order_id,
                trade,
                submitted_order,
                getattr(trade.orderStatus, "status", "") or "Partial",
            )
        raise TimeoutError(f"Timed out waiting for fill on IB order {broker_order_id}")

    def add_execution_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Register a listener invoked only after an execution is durable locally."""
        self._execution_listeners.append(listener)

    def list_durable_executions(self) -> List[Dict[str, Any]]:
        """Return all known executions for idempotent startup recovery."""
        return self.execution_store.list_executions()

    def list_open_orders(self) -> List[Dict[str, Any]]:
        """Return synchronized non-terminal orders restored from TWS/Gateway."""
        return [
            {
                "order": order,
                "filled_qty": float(
                    getattr(self._trades[order_id].orderStatus, "filled", 0.0)
                    or 0.0
                ),
                "avg_price": float(
                    getattr(self._trades[order_id].orderStatus, "avgFillPrice", 0.0)
                    or 0.0
                ),
            }
            for order_id, order in self._submitted_orders.items()
            if order_id in self._trades
            and str(
                getattr(self._trades[order_id].orderStatus, "status", "") or ""
            )
            not in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
        ]

    def _reconcile_open_orders(self) -> None:
        """Restore API/metadata mappings for open orders synchronized at connect."""
        open_trades = list(getattr(self.ib, "openTrades", lambda: [])())
        for trade in open_trades:
            status = str(getattr(trade.orderStatus, "status", "") or "")
            if status in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}:
                continue
            ib_order = trade.order
            contract = trade.contract
            broker_order_id = str(getattr(ib_order, "orderId", ""))
            if not broker_order_id:
                continue
            stored = self.execution_store.get_submitted_order(broker_order_id) or {}
            action = str(getattr(ib_order, "action", "")).upper()
            ib_order_type = str(getattr(ib_order, "orderType", "MKT")).upper()
            order_type = {"LMT": "limit", "STP": "stop"}.get(
                ib_order_type,
                "market",
            )
            limit_price = (
                stored.get("limit_price")
                if stored.get("limit_price") is not None
                else self._clean_number(getattr(ib_order, "lmtPrice", None))
            )
            stop_price = (
                stored.get("stop_price")
                if stored.get("stop_price") is not None
                else self._clean_number(getattr(ib_order, "auxPrice", None))
            )
            restored = BrokerOrder(
                symbol=stored.get("symbol") or contract.symbol,
                side=stored.get("side") or ("buy" if action == "BUY" else "sell"),
                quantity=float(
                    stored.get("quantity")
                    or getattr(ib_order, "totalQuantity", 0.0)
                ),
                order_type=stored.get("order_type") or order_type,
                expected_price=stored.get("benchmark_price"),
                limit_price=limit_price,
                stop_price=stop_price,
                broker_order_id=broker_order_id,
                status=status.lower() or "submitted",
                decision_at=self._as_optional_datetime(stored.get("decision_at")),
                submitted_at=self._as_optional_datetime(stored.get("submitted_at")),
                benchmark_type=stored.get("benchmark_type"),
                benchmark_price=stored.get("benchmark_price"),
                quote_bid=stored.get("quote_bid"),
                quote_ask=stored.get("quote_ask"),
                quote_midpoint=stored.get("quote_midpoint"),
                benchmark_version=int(stored.get("benchmark_version") or 1),
                strategy_name=stored.get("strategy_name"),
                strategy_stop_price=stored.get("strategy_stop_price"),
                take_profit=stored.get("take_profit"),
                exit_reason=stored.get("exit_reason"),
            )
            self._trades[broker_order_id] = trade
            self._submitted_orders[broker_order_id] = restored

    async def reconcile_executions(self) -> int:
        """Request historical executions without placing or modifying any orders."""
        request = getattr(self.ib, "reqExecutionsAsync", None)
        if request is None or not self.ib.isConnected():
            return 0
        try:
            fills = await request()
        except Exception as exc:
            logger.warning("IB execution reconciliation failed: %s", exc)
            return 0
        ingested = 0
        for fill in fills or []:
            if self._ingest_fill(None, fill):
                ingested += 1
        # ib_insync keeps late/historical CommissionReport objects on its fill
        # cache. Re-read that cache explicitly because reqExecutionsAsync may
        # complete before the matching commission callback arrives.
        cached_fills = getattr(self.ib, "fills", None)
        if callable(cached_fills):
            for fill in cached_fills() or []:
                if self._ingest_fill(None, fill):
                    ingested += 1
        if ingested:
            logger.info("Reconciled %s IB executions from broker history", ingested)
        return ingested

    def _handle_execution_details(self, trade: Any, fill: Any) -> None:
        self._ingest_fill(trade, fill)

    def _handle_commission_report(self, trade: Any, fill: Any, report: Any) -> None:
        execution_id = str(
            getattr(getattr(fill, "execution", None), "execId", "") or ""
        )
        report_execution_id = str(getattr(report, "execId", "") or "")
        if (
            not execution_id
            or report_execution_id != execution_id
            or self._parse_float(getattr(report, "commission", None)) is None
        ):
            logger.warning(
                "Ignoring unmatched IB commission report execution_id=%s report_execution_id=%s",
                execution_id or "<missing>",
                report_execution_id or "<missing>",
            )
            return
        self._ingest_fill(trade, fill, commission_report=report)

    def _ingest_fill(
        self,
        trade: Any,
        fill: Any,
        *,
        commission_report: Any = None,
    ) -> bool:
        execution = getattr(fill, "execution", None)
        broker_order_id = str(getattr(execution, "orderId", "") or "")
        submitted_order = self._submitted_orders.get(broker_order_id)
        stored_order = self.execution_store.get_submitted_order(broker_order_id)
        payload = self._execution_payload(
            trade,
            fill,
            submitted_order=submitted_order,
            stored_order=stored_order,
            fallback_filled_at=datetime.now(timezone.utc),
            commission_report=commission_report,
        )
        changed = self.execution_store.upsert_execution(payload)
        if changed:
            durable = self.execution_store.get_execution(payload["execution_id"]) or payload
            for listener in list(self._execution_listeners):
                try:
                    listener(durable)
                except Exception:
                    logger.exception("IB execution listener failed for %s", payload["execution_id"])
        return changed

    def _fill_event_from_trade(
        self,
        broker_order_id: str,
        trade: Any,
        submitted_order: BrokerOrder,
        raw_status: str,
    ) -> FillEvent:
        filled_at = datetime.now(timezone.utc)
        executions = self._resolve_executions(trade, submitted_order, filled_at)
        execution_quantity = sum(float(execution["quantity"]) for execution in executions)
        avg_price = (
            sum(float(item["price"]) * float(item["quantity"]) for item in executions)
            / execution_quantity
        )
        commissions = [
            float(item["commission"])
            for item in executions
            if item.get("commission") is not None
        ]
        commission_currencies = {
            item["commission_currency"]
            for item in executions
            if item.get("commission_currency")
        }
        return FillEvent(
            broker_order_id=broker_order_id,
            symbol=submitted_order.symbol,
            side=submitted_order.side,
            quantity=execution_quantity,
            avg_fill_price=avg_price,
            expected_price=submitted_order.expected_price,
            submitted_at=submitted_order.submitted_at or filled_at,
            filled_at=max(
                (
                    item["filled_at"]
                    if isinstance(item["filled_at"], datetime)
                    else datetime.fromisoformat(item["filled_at"])
                )
                for item in executions
            ),
            commission=sum(commissions) if commissions else None,
            execution_id=executions[0]["execution_id"] if len(executions) == 1 else None,
            permanent_order_id=executions[0].get("permanent_order_id"),
            account_id=executions[0].get("account_id") or self.account_id,
            instrument_currency=executions[0].get("trade_currency") or self.currency,
            commission_currency=next(iter(commission_currencies)) if len(commission_currencies) == 1 else None,
            decision_at=submitted_order.decision_at,
            benchmark_type=submitted_order.benchmark_type,
            benchmark_price=submitted_order.benchmark_price,
            quote_bid=submitted_order.quote_bid,
            quote_ask=submitted_order.quote_ask,
            quote_midpoint=submitted_order.quote_midpoint,
            stop_price=submitted_order.stop_price,
            limit_price=submitted_order.limit_price,
            benchmark_version=submitted_order.benchmark_version,
            benchmark_valid=(
                submitted_order.benchmark_version == 2
                and submitted_order.benchmark_price not in (None, 0)
            ),
            executions=executions,
            raw_status=raw_status,
        )

    async def cancel_order(
        self,
        broker_order_id: str,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Cancel an order and wait until IB reports a terminal state.

        A cancellation request is not an acknowledgement. Executions arriving
        while cancellation is in flight are durably ingested before returning.
        """
        trade = self._require_trade(broker_order_id)
        self.ib.cancelOrder(trade.order)
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0.1)
        terminal_statuses = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
        while asyncio.get_running_loop().time() < deadline:
            status = str(getattr(trade.orderStatus, "status", "") or "")
            if status in terminal_statuses:
                for fill in list(getattr(trade, "fills", None) or []):
                    self._ingest_fill(trade, fill)
                return True
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"Timed out waiting for terminal cancellation status on IB order "
            f"{broker_order_id}"
        )

    async def get_account_info(self) -> BrokerAccount:
        """Return account values, with P&L sourced from IB's reqPnL feed.

        IB account summary is retained for balances only. Account-wide P&L from
        reqPnL is denominated in the account base currency.
        """
        values = await self._request_account_summary()
        account_id = self.account_id or self._single_summary_account(values)
        net_liquidation, net_liquidation_currency = self._find_account_summary_value(
            values, "NetLiquidation", account_id
        )
        cash, cash_currency = self._find_account_summary_value(values, "TotalCashValue", account_id)
        buying_power, buying_power_currency = self._find_account_summary_value(values, "BuyingPower", account_id)
        # NetLiquidation may be reported in a ledger currency that is not the
        # account base. reqPnL has no currency field, so only an independently
        # configured/discovered base currency may label it.
        account_currency = self.account_base_currency
        realized_pnl = None
        unrealized_pnl = None
        if account_id:
            realized_pnl, unrealized_pnl = await self._request_account_pnl(account_id)

        return BrokerAccount(
            account_id=account_id,
            net_liquidation=self._parse_float(net_liquidation),
            cash=self._parse_float(cash),
            buying_power=self._parse_float(buying_power),
            currency=account_currency,
            net_liquidation_currency=net_liquidation_currency,
            cash_currency=cash_currency,
            buying_power_currency=buying_power_currency,
            realized_pnl=realized_pnl,
            realized_pnl_currency=account_currency if realized_pnl is not None else None,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_currency=account_currency if unrealized_pnl is not None else None,
        )

    async def _request_account_summary(self) -> List[Any]:
        """Read a bounded account summary and cancel the owned IB request.

        ib_insync's convenience ``accountSummaryAsync`` leaves its subscription
        active and does not expose the request id. Use the underlying supported
        IB request when available so a timeout cannot leak subscriptions.
        Lightweight broker doubles may implement only the convenience method.
        """
        client = getattr(self.ib, "client", None)
        wrapper = getattr(self.ib, "wrapper", None)
        if (
            client is None
            or wrapper is None
            or not hasattr(client, "getReqId")
            or not hasattr(client, "reqAccountSummary")
            or not hasattr(client, "cancelAccountSummary")
            or not hasattr(wrapper, "startReq")
        ):
            return await asyncio.wait_for(
                self.ib.accountSummaryAsync(self.account_id or ""),
                timeout=self.account_data_timeout_seconds,
            )

        req_id = client.getReqId()
        future = wrapper.startReq(req_id)
        client.reqAccountSummary(req_id, "All", _ACCOUNT_SUMMARY_TAGS)
        try:
            await asyncio.wait_for(
                future,
                timeout=self.account_data_timeout_seconds,
            )
            values = list(getattr(wrapper, "acctSummary", {}).values())
            if self.account_id:
                values = [
                    value
                    for value in values
                    if getattr(value, "account", None) == self.account_id
                ]
            return values
        finally:
            client.cancelAccountSummary(req_id)

    async def _request_account_pnl(
        self,
        account_id: str,
    ) -> tuple[Optional[float], Optional[float]]:
        """Read one bounded account/model P&L update and always cancel it."""
        async with self._pnl_request_lock:
            pnl = self.ib.reqPnL(account_id, self.model_code)
            deadline = asyncio.get_running_loop().time() + self.account_data_timeout_seconds
            try:
                while asyncio.get_running_loop().time() < deadline:
                    returned_account = getattr(pnl, "account", account_id)
                    returned_model = getattr(pnl, "modelCode", self.model_code)
                    if returned_account == account_id and returned_model == self.model_code:
                        realized = self._clean_pnl_number(getattr(pnl, "realizedPnL", None))
                        unrealized = self._clean_pnl_number(getattr(pnl, "unrealizedPnL", None))
                        if realized is not None and unrealized is not None:
                            return realized, unrealized
                    await asyncio.sleep(0.05)
                logger.warning(
                    "Timed out waiting for IB account P&L account=%s model=%s",
                    account_id,
                    self.model_code or "<all>",
                )
                return None, None
            finally:
                self.ib.cancelPnL(account_id, self.model_code)

    async def get_positions(self) -> List[BrokerPosition]:
        """Return current stock positions."""
        positions: List[BrokerPosition] = []
        portfolio_items = list(getattr(self.ib, "portfolio", lambda: [])())
        source_positions = portfolio_items or self.ib.positions()
        for position in source_positions:
            contract = position.contract
            if (
                self.account_id
                and getattr(position, "account", None)
                and position.account != self.account_id
            ):
                continue
            if getattr(contract, "secType", None) != "STK":
                continue
            positions.append(
                BrokerPosition(
                    symbol=contract.symbol,
                    quantity=float(position.position),
                    avg_cost=float(
                        getattr(position, "averageCost", None)
                        or getattr(position, "avgCost", 0.0)
                    ),
                    market_price=self._clean_number(
                        getattr(position, "marketPrice", None)
                    ),
                    unrealized_pnl=self._clean_pnl_number(
                        getattr(position, "unrealizedPNL", None)
                    ),
                    instrument_currency=getattr(contract, "currency", None) or None,
                    unrealized_pnl_currency=(
                        self.account_base_currency
                        if self._clean_pnl_number(
                            getattr(position, "unrealizedPNL", None)
                        ) is not None
                        else None
                    ),
                )
            )
        return positions

    async def get_order_status(self, broker_order_id: str) -> Dict[str, Any]:
        """Return raw IB order status details for diagnostics."""
        trade = self._require_trade(broker_order_id)
        status = trade.orderStatus
        return {
            "broker_order_id": broker_order_id,
            "status": getattr(status, "status", None),
            "filled": getattr(status, "filled", None),
            "remaining": getattr(status, "remaining", None),
            "avg_fill_price": getattr(status, "avgFillPrice", None),
        }

    async def _stock_contract(self, symbol: str):
        contract = Stock(symbol, self.exchange, self.currency)
        qualified = await self.ib.qualifyContractsAsync(contract)
        return qualified[0] if qualified else contract

    def _to_ib_order(self, order: BrokerOrder):
        action = "BUY" if order.side == "buy" else "SELL"
        if order.order_type == "market":
            return MarketOrder(action, order.quantity, tif="DAY")
        if order.order_type == "limit":
            return LimitOrder(action, order.quantity, order.limit_price, tif="DAY")
        if order.order_type == "stop":
            return StopOrder(action, order.quantity, order.stop_price, tif="DAY")
        raise ValueError(f"Unsupported IB order_type: {order.order_type}")

    def _require_trade(self, broker_order_id: str):
        trade = self._trades.get(broker_order_id)
        if trade is None:
            raise KeyError(f"Unknown IB order id: {broker_order_id}")
        return trade

    @staticmethod
    def _clean_number(value: Any) -> Optional[float]:
        parsed = InteractiveBrokersAPI._parse_float(value)
        if parsed is None or math.isnan(parsed) or parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _parse_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_optional_datetime(value: Any) -> Optional[datetime]:
        if value in (None, "") or isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _clean_pnl_number(value: Any) -> Optional[float]:
        parsed = InteractiveBrokersAPI._parse_float(value)
        if (
            parsed is None
            or not math.isfinite(parsed)
            or abs(parsed) >= _IB_UNSET_DOUBLE
        ):
            return None
        return parsed

    def _find_account_summary_value(self, values: Any, tag: str, account_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        candidates = [
            item
            for item in values
            if item.tag == tag
            and (not account_id or item.account in {account_id, "All"})
        ]
        if account_id:
            exact_account_candidates = [
                item for item in candidates if item.account == account_id
            ]
            if exact_account_candidates:
                candidates = exact_account_candidates
        # BASE is the account-total row even when its ISO currency has not yet
        # been configured/discovered. Never substitute a single explicit
        # currency ledger component for that total.
        preferred_currencies = ["BASE"]
        if self.account_base_currency:
            preferred_currencies.append(self.account_base_currency)
        for currency in preferred_currencies:
            for item in candidates:
                if item.currency == currency:
                    resolved_currency = self.account_base_currency if item.currency == "BASE" else (item.currency or None)
                    return item.value, resolved_currency
        explicit_candidates = [
            item for item in candidates if item.currency not in (None, "", "BASE")
        ]
        explicit_currencies = {item.currency for item in explicit_candidates}
        if len(explicit_currencies) == 1 and explicit_candidates:
            item = explicit_candidates[0]
            return item.value, item.currency
        if len(candidates) == 1:
            item = candidates[0]
            return (
                item.value,
                None if item.currency == "BASE" else item.currency or None,
            )
        return None, None

    @staticmethod
    def _single_summary_account(values: Any) -> Optional[str]:
        accounts = {
            item.account
            for item in values
            if getattr(item, "account", None) not in (None, "", "All")
        }
        if len(accounts) > 1:
            raise RuntimeError(
                "IB account ID is required when account summary contains multiple accounts"
            )
        return next(iter(accounts), None)

    @staticmethod
    def _resolve_avg_fill_price(trade: Any) -> float:
        avg_price = InteractiveBrokersAPI._parse_float(getattr(trade.orderStatus, "avgFillPrice", None))
        if avg_price and avg_price > 0:
            return avg_price

        fills = [
            (
                float(fill.execution.price),
                float(getattr(fill.execution, "shares", 0.0) or 0.0),
            )
            for fill in getattr(trade, "fills", [])
            if getattr(fill.execution, "price", None)
        ]
        total_quantity = sum(quantity for _, quantity in fills)
        if fills and total_quantity > 0:
            return sum(price * quantity for price, quantity in fills) / total_quantity
        if fills:
            return sum(price for price, _ in fills) / len(fills)
        raise RuntimeError("IB fill event did not include an average fill price")

    @staticmethod
    def _resolve_commission(trade: Any) -> Optional[float]:
        commissions = []
        for fill in getattr(trade, "fills", []):
            report = InteractiveBrokersAPI._commission_report_for_fill(fill)
            commission = getattr(report, "commission", None) if report else None
            parsed = InteractiveBrokersAPI._parse_float(commission)
            if parsed is not None:
                commissions.append(parsed)
        return sum(commissions) if commissions else None

    @staticmethod
    def _all_commission_reports_received(trade: Any) -> bool:
        fills = list(getattr(trade, "fills", []))
        return bool(fills) and all(
            InteractiveBrokersAPI._commission_report_for_fill(fill) is not None
            for fill in fills
        )

    @staticmethod
    def _commission_report_for_fill(fill: Any) -> Optional[Any]:
        """Return only a populated report for this exact broker execution."""
        execution_id = str(
            getattr(getattr(fill, "execution", None), "execId", "") or ""
        )
        report = getattr(fill, "commissionReport", None)
        report_execution_id = str(getattr(report, "execId", "") or "")
        commission = InteractiveBrokersAPI._parse_float(
            getattr(report, "commission", None)
        )
        if (
            not execution_id
            or not report_execution_id
            or report_execution_id != execution_id
            or commission is None
        ):
            return None
        return report

    def _resolve_executions(
        self,
        trade: Any,
        submitted_order: BrokerOrder,
        fallback_filled_at: datetime,
    ) -> tuple[Dict[str, Any], ...]:
        executions: List[Dict[str, Any]] = []
        for fill in getattr(trade, "fills", []):
            payload = self._execution_payload(
                trade,
                fill,
                submitted_order=submitted_order,
                stored_order=None,
                fallback_filled_at=fallback_filled_at,
            )
            self.execution_store.upsert_execution(payload)
            executions.append(payload)
        if not executions:
            raise RuntimeError("IB filled order did not include execution details")
        return tuple(executions)

    def _execution_payload(
        self,
        trade: Any,
        fill: Any,
        *,
        submitted_order: Optional[BrokerOrder],
        stored_order: Optional[Dict[str, Any]],
        fallback_filled_at: datetime,
        commission_report: Any = None,
    ) -> Dict[str, Any]:
        execution = getattr(fill, "execution", None)
        execution_id = getattr(execution, "execId", None)
        if not execution_id:
            raise RuntimeError("IB fill did not include an execution ID")
        broker_order_id = str(
            getattr(execution, "orderId", None)
            or (submitted_order.broker_order_id if submitted_order else None)
            or (stored_order or {}).get("broker_order_id")
            or ""
        )
        contract = getattr(fill, "contract", None) or getattr(trade, "contract", None)
        report = (
            commission_report
            if commission_report is not None
            else self._commission_report_for_fill(fill)
        )
        execution_time = getattr(execution, "time", None) or fallback_filled_at
        if isinstance(execution_time, str):
            execution_time = datetime.fromisoformat(execution_time)
        if execution_time.tzinfo is None:
            execution_time = execution_time.replace(tzinfo=timezone.utc)

        if submitted_order is not None:
            order_metadata = self._submitted_order_payload(submitted_order)
        else:
            order_metadata = dict(stored_order or {})
        raw_side = str(getattr(execution, "side", "") or "").upper()
        side = (
            submitted_order.side
            if submitted_order is not None
            else (stored_order or {}).get("side")
            or ("buy" if raw_side in {"BOT", "BUY"} else "sell")
        )
        symbol = (
            submitted_order.symbol
            if submitted_order is not None
            else (stored_order or {}).get("symbol")
            or getattr(contract, "symbol", None)
        )
        if not symbol:
            raise RuntimeError(f"IB execution {execution_id} did not include a symbol")
        return {
            "execution_id": str(execution_id),
            "broker_order_id": broker_order_id,
            "permanent_order_id": str(
                getattr(execution, "permId", None)
                or getattr(getattr(trade, "order", None), "permId", None)
                or (stored_order or {}).get("permanent_order_id")
                or ""
            ) or None,
            "account_id": getattr(execution, "acctNumber", None)
            or (stored_order or {}).get("account_id")
            or self.account_id,
            "symbol": symbol,
            "side": side,
            "quantity": float(getattr(execution, "shares", 0.0) or 0.0),
            "price": float(getattr(execution, "price", 0.0) or 0.0),
            "filled_at": execution_time,
            "trade_currency": getattr(contract, "currency", None) or self.currency,
            "commission": self._parse_float(getattr(report, "commission", None)),
            "commission_currency": getattr(report, "currency", None) or None,
            "order_metadata": {
                key: value
                for key, value in order_metadata.items()
                if key not in {"created_at", "updated_at"}
            },
        }

    def _submitted_order_payload(
        self,
        order: BrokerOrder,
        *,
        permanent_order_id: Any = None,
    ) -> Dict[str, Any]:
        return {
            "broker_order_id": str(order.broker_order_id or ""),
            "permanent_order_id": (
                str(permanent_order_id) if permanent_order_id not in (None, "") else None
            ),
            "account_id": self.account_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "order_type": order.order_type,
            "submitted_at": order.submitted_at,
            "decision_at": order.decision_at,
            "benchmark_type": order.benchmark_type,
            "benchmark_price": order.benchmark_price,
            "quote_bid": order.quote_bid,
            "quote_ask": order.quote_ask,
            "quote_midpoint": order.quote_midpoint,
            "stop_price": order.stop_price,
            "limit_price": order.limit_price,
            "benchmark_version": order.benchmark_version,
            "strategy_name": order.strategy_name,
            "strategy_stop_price": order.strategy_stop_price,
            "take_profit": order.take_profit,
            "exit_reason": order.exit_reason,
        }
