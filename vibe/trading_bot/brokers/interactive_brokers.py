"""Interactive Brokers paper/live adapter built on ib_insync."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from vibe.trading_bot.brokers.base import (
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    BrokerQuote,
    FillEvent,
)

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
        self.readonly = readonly
        self.ib = IB()
        self._trades: Dict[str, Trade] = {}
        self._submitted_orders: Dict[str, BrokerOrder] = {}

    async def connect(self) -> bool:
        """Connect to TWS or IB Gateway."""
        if self.ib.isConnected():
            return True

        await self.ib.connectAsync(
            self.host,
            self.port,
            clientId=self.client_id,
            account=self.account_id or "",
        )
        logger.info("Connected to IB at %s:%s client_id=%s", self.host, self.port, self.client_id)
        return self.ib.isConnected()

    async def disconnect(self) -> bool:
        """Disconnect from IB."""
        if self.ib.isConnected():
            self.ib.disconnect()
        logger.info("Disconnected from IB")
        return True

    async def get_market_data(self, symbol: str, timeout_seconds: float = 15.0) -> BrokerQuote:
        """Request a market data snapshot for a stock symbol."""
        contract = await self._stock_contract(symbol)
        ticker = self.ib.reqMktData(contract, "", False, False)
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        try:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
                market_price = self._clean_number(ticker.marketPrice())
                bid = self._clean_number(ticker.bid)
                ask = self._clean_number(ticker.ask)
                last = self._clean_number(ticker.last)

                if market_price is not None:
                    return BrokerQuote(
                        symbol=symbol,
                        bid=bid,
                        ask=ask,
                        last=last,
                        market_price=market_price,
                    )

            raise TimeoutError(f"Timed out waiting for market data for {symbol}")
        finally:
            self.ib.cancelMktData(contract)

    async def submit_order(self, order: BrokerOrder) -> str:
        """Submit an order and return the IB order id."""
        if self.readonly:
            raise RuntimeError("IB adapter is in readonly mode; order submission is disabled")

        contract = await self._stock_contract(order.symbol)
        ib_order = self._to_ib_order(order)
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
            submitted_at=datetime.utcnow(),
        )
        self._trades[broker_order_id] = trade
        self._submitted_orders[broker_order_id] = submitted_order
        logger.info("Submitted IB order %s %s %s %s", broker_order_id, order.side, order.quantity, order.symbol)
        return broker_order_id

    async def wait_for_fill(self, broker_order_id: str, timeout_seconds: float = 60.0) -> FillEvent:
        """Wait for a submitted order to fill and return a normalized fill event."""
        trade = self._require_trade(broker_order_id)
        submitted_order = self._submitted_orders[broker_order_id]
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            status = getattr(trade.orderStatus, "status", "")
            filled = float(getattr(trade.orderStatus, "filled", 0.0) or 0.0)

            if status == "Filled" or filled >= submitted_order.quantity:
                avg_price = self._resolve_avg_fill_price(trade)
                commission = self._resolve_commission(trade)
                return FillEvent(
                    broker_order_id=broker_order_id,
                    symbol=submitted_order.symbol,
                    side=submitted_order.side,
                    quantity=filled or submitted_order.quantity,
                    avg_fill_price=avg_price,
                    expected_price=submitted_order.expected_price,
                    submitted_at=submitted_order.submitted_at or datetime.utcnow(),
                    filled_at=datetime.utcnow(),
                    commission=commission,
                    raw_status=status or "Filled",
                )

            if status in {"Cancelled", "ApiCancelled", "Inactive"}:
                raise RuntimeError(f"IB order {broker_order_id} reached terminal status {status}")

        raise TimeoutError(f"Timed out waiting for fill on IB order {broker_order_id}")

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open IB order."""
        trade = self._require_trade(broker_order_id)
        self.ib.cancelOrder(trade.order)
        await asyncio.sleep(0.25)
        return True

    async def get_account_info(self) -> BrokerAccount:
        """Return selected IB account summary values."""
        values = self.ib.accountSummary(self.account_id or "")
        summary = {item.tag: item.value for item in values if item.currency in {self.currency, ""}}

        return BrokerAccount(
            account_id=self.account_id,
            net_liquidation=self._parse_float(summary.get("NetLiquidation")),
            cash=self._parse_float(summary.get("TotalCashValue")),
            buying_power=self._parse_float(summary.get("BuyingPower")),
            currency=self.currency,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        """Return current stock positions."""
        positions: List[BrokerPosition] = []
        for position in self.ib.positions():
            contract = position.contract
            if getattr(contract, "secType", None) != "STK":
                continue
            positions.append(
                BrokerPosition(
                    symbol=contract.symbol,
                    quantity=float(position.position),
                    avg_cost=float(position.avgCost),
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
            return MarketOrder(action, order.quantity)
        if order.order_type == "limit":
            return LimitOrder(action, order.quantity, order.limit_price)
        if order.order_type == "stop":
            return StopOrder(action, order.quantity, order.stop_price)
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
    def _resolve_avg_fill_price(trade: Trade) -> float:
        avg_price = InteractiveBrokersAPI._parse_float(getattr(trade.orderStatus, "avgFillPrice", None))
        if avg_price and avg_price > 0:
            return avg_price

        prices = [fill.execution.price for fill in getattr(trade, "fills", []) if fill.execution.price]
        if prices:
            return sum(prices) / len(prices)
        raise RuntimeError("IB fill event did not include an average fill price")

    @staticmethod
    def _resolve_commission(trade: Trade) -> float:
        commissions = []
        for fill in getattr(trade, "fills", []):
            report = getattr(fill, "commissionReport", None)
            commission = getattr(report, "commission", None) if report else None
            parsed = InteractiveBrokersAPI._parse_float(commission)
            if parsed is not None:
                commissions.append(parsed)
        return sum(commissions) if commissions else 0.0
