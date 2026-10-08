from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.simulator import ExecutionSimulator
from vibe.common.models.bar import Bar

TICK_SIZE = 0.01  # US equity minimum price increment


@dataclass
class FillResult:
    symbol: str
    side: str
    filled_qty: float
    avg_price: float
    commission: float = 0.0


class FillSimulator:
    """
    Simulates order fills using tick-based slippage. Zero commission.

    Fills are priced off ``bar.close`` +/- slippage. *When* that fill happens
    is not this class's decision: the engine chooses the bar via E6
    ``EntryFillPolicy`` and hands the right one in. ``AT_NEXT_BAR_OPEN``, for
    instance, passes a bar whose ``close`` is the next bar's open.

    This class previously carried its own ``fill_mode=1`` next-bar-open
    switch. It was removed rather than kept: no caller ever passed ``next_bar``,
    so it was unreachable, and keeping it would have left two independent
    places deciding the same thing -- the exact defect E6 exists to remove.

    1 tick = $0.01 (US equity minimum). Default 5 ticks = $0.05/share.
    """

    def __init__(self, slippage_ticks: int = 5) -> None:
        self.slippage_ticks = slippage_ticks
        self._execution_sim = ExecutionSimulator(
            config=ExecutionConfig.legacy(slippage_ticks=slippage_ticks)
        )

    def execute(
        self,
        symbol: str,
        side: str,
        quantity: float,
        bar: Bar,
        price_override: Optional[float] = None,
    ) -> FillResult:
        order = Order(
            id=f"legacy_{symbol}_{datetime.now().timestamp()}",
            symbol=symbol,
            side=side,
            size=quantity,
            order_type="market",
            limit_price=None,
            timestamp=bar.timestamp,
            signal_bar_index=0,
            price_override=price_override,
        )

        fill = self._execution_sim.execute_market_order(order=order, bar=bar)

        return FillResult(
            symbol=symbol,
            side=side,
            filled_qty=fill.qty,
            avg_price=fill.price,
        )
