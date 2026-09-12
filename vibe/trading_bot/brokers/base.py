"""Broker-neutral contracts for paper and live execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Protocol

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]
BenchmarkType = Literal["executable_quote", "limit_price", "stop_quote", "legacy"]


@dataclass(frozen=True)
class BrokerQuote:
    """Broker quote snapshot used by execution and telemetry."""

    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    market_price: float
    # The exchange/provider observation time. Missing is intentionally distinct
    # from "received now" because an undated quote is not a valid benchmark.
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class BrokerOrder:
    """Broker-neutral order request and status payload."""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = "market"
    expected_price: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    strategy_order_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    status: str = "created"
    decision_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    benchmark_type: Optional[BenchmarkType] = None
    benchmark_price: Optional[float] = None
    quote_bid: Optional[float] = None
    quote_ask: Optional[float] = None
    quote_midpoint: Optional[float] = None
    benchmark_version: int = 2
    strategy_name: Optional[str] = None
    strategy_stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    exit_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type == "stop" and self.stop_price is None:
            raise ValueError("stop orders require stop_price")


@dataclass(frozen=True)
class FillEvent:
    """Fill event emitted by a broker after execution."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    avg_fill_price: float
    expected_price: Optional[float]
    submitted_at: datetime
    filled_at: datetime
    commission: Optional[float] = None
    execution_id: Optional[str] = None
    permanent_order_id: Optional[str] = None
    account_id: Optional[str] = None
    instrument_currency: Optional[str] = None
    commission_currency: Optional[str] = None
    decision_at: Optional[datetime] = None
    benchmark_type: Optional[BenchmarkType] = None
    benchmark_price: Optional[float] = None
    quote_bid: Optional[float] = None
    quote_ask: Optional[float] = None
    quote_midpoint: Optional[float] = None
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None
    benchmark_version: int = 2
    benchmark_valid: bool = False
    executions: tuple[Dict[str, Any], ...] = ()
    raw_status: str = "filled"

    @property
    def latency_ms(self) -> float:
        return max((self.filled_at - self.submitted_at).total_seconds() * 1000.0, 0.0)

    @property
    def decision_to_submission_latency_ms(self) -> Optional[float]:
        if self.decision_at is None:
            return None
        return max((self.submitted_at - self.decision_at).total_seconds() * 1000.0, 0.0)

    @property
    def submission_to_fill_latency_ms(self) -> float:
        return self.latency_ms

    @property
    def slippage(self) -> Optional[float]:
        benchmark = self.benchmark_price
        if (
            self.benchmark_version != 2
            or benchmark is None
            or not self.benchmark_valid
        ):
            return None
        if self.side == "buy":
            return self.avg_fill_price - benchmark
        return benchmark - self.avg_fill_price

    @property
    def slippage_bps(self) -> Optional[float]:
        benchmark = self.benchmark_price
        if (
            self.benchmark_version != 2
            or benchmark in (None, 0)
            or not self.benchmark_valid
        ):
            return None
        slippage = self.slippage
        if slippage is None:
            return None
        return (slippage / benchmark) * 10000.0


@dataclass(frozen=True)
class BrokerPosition:
    """Current broker position."""

    symbol: str
    quantity: float
    avg_cost: float
    market_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    instrument_currency: Optional[str] = None
    unrealized_pnl_currency: Optional[str] = None


@dataclass(frozen=True)
class BrokerAccount:
    """Current broker account summary."""

    account_id: Optional[str]
    net_liquidation: Optional[float]
    cash: Optional[float]
    buying_power: Optional[float]
    currency: Optional[str] = None
    net_liquidation_currency: Optional[str] = None
    cash_currency: Optional[str] = None
    buying_power_currency: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_pnl_currency: Optional[str] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_currency: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BrokerAPI(Protocol):
    """Broker interface used by paper/live execution adapters."""

    async def connect(self) -> bool:
        """Connect to the broker."""
        ...

    async def disconnect(self) -> bool:
        """Disconnect from the broker."""
        ...

    async def get_market_data(self, symbol: str) -> BrokerQuote:
        """Get a current market data snapshot."""
        ...

    async def submit_order(self, order: BrokerOrder) -> str:
        """Submit an order and return the broker order id."""
        ...

    async def wait_for_fill(self, broker_order_id: str, timeout_seconds: float = 60.0) -> FillEvent:
        """Wait for an order fill event."""
        ...

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order."""
        ...

    async def get_account_info(self) -> BrokerAccount:
        """Get account summary."""
        ...

    async def get_positions(self) -> List[BrokerPosition]:
        """Get current positions."""
        ...

    async def get_order_status(self, broker_order_id: str) -> Dict[str, Any]:
        """Get raw broker order status."""
        ...
