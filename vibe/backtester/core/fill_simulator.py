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

    fill_mode=0: fill at bar close +/- slippage (default)
    fill_mode=1: fill at next bar open +/- slippage (more conservative)

    1 tick = $0.01 (US equity minimum). Default 5 ticks = $0.05/share.
    """

    def __init__(self, slippage_ticks: int = 5, fill_mode: int = 0) -> None:
        self.slippage_ticks = slippage_ticks
        self.fill_mode = fill_mode
        self._execution_sim = ExecutionSimulator(
            config=ExecutionConfig.legacy(slippage_ticks=slippage_ticks)
        )

    def execute(
        self,
        symbol: str,
        side: str,
        quantity: float,
        bar: Bar,
        next_bar: Optional[Bar] = None,
        price_override: Optional[float] = None,
    ) -> FillResult:
        if self.fill_mode == 1 and next_bar is not None and price_override is None:
            # Legacy next-bar mode: evaluate fill from next bar open.
            effective_bar = Bar(
                timestamp=next_bar.timestamp,
                open=next_bar.open,
                high=next_bar.high,
                low=next_bar.low,
                close=next_bar.open,
                volume=next_bar.volume,
            )
        else:
            effective_bar = bar

        order = Order(
            id=f"legacy_{symbol}_{datetime.now().timestamp()}",
            symbol=symbol,
            side=side,
            size=quantity,
            order_type="market",
            limit_price=None,
            timestamp=effective_bar.timestamp,
            signal_bar_index=0,
            price_override=price_override,
        )

        fill = self._execution_sim.execute_market_order(order=order, bar=effective_bar)

        return FillResult(
            symbol=symbol,
            side=side,
            filled_qty=fill.qty,
            avg_price=fill.price,
        )
