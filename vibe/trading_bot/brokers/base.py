"""Broker-neutral contracts for paper and live execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Protocol

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]


@dataclass(frozen=True)
class BrokerQuote:
    """Broker quote snapshot used by execution and telemetry."""

    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    market_price: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


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
    submitted_at: Optional[datetime] = None

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
    commission: float = 0.0
    raw_status: str = "filled"

    @property
    def latency_ms(self) -> float:
        return max((self.filled_at - self.submitted_at).total_seconds() * 1000.0, 0.0)

    @property
    def slippage(self) -> Optional[float]:
        if self.expected_price is None:
            return None
        if self.side == "buy":
            return self.avg_fill_price - self.expected_price
        return self.expected_price - self.avg_fill_price

    @property
    def slippage_bps(self) -> Optional[float]:
        if self.expected_price in (None, 0):
            return None
        slippage = self.slippage
        if slippage is None:
            return None
        return (slippage / self.expected_price) * 10000.0


@dataclass(frozen=True)
class BrokerPosition:
    """Current broker position."""

    symbol: str
    quantity: float
    avg_cost: float
    market_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None


@dataclass(frozen=True)
class BrokerAccount:
    """Current broker account summary."""

    account_id: Optional[str]
    net_liquidation: Optional[float]
    cash: Optional[float]
    buying_power: Optional[float]
    currency: str = "USD"
    timestamp: datetime = field(default_factory=datetime.utcnow)


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
