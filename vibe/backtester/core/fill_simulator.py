from dataclasses import dataclass
from typing import Optional

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

    def execute(
        self,
        symbol: str,
        side: str,
        quantity: float,
        bar: Bar,
        next_bar: Optional[Bar] = None,
    ) -> FillResult:
        slippage = self.slippage_ticks * TICK_SIZE

        if self.fill_mode == 1 and next_bar is not None:
            base_price = next_bar.open
        else:
            base_price = bar.close

        fill_price = base_price + slippage if side == "buy" else base_price - slippage

        return FillResult(
            symbol=symbol,
            side=side,
            filled_qty=quantity,
            avg_price=fill_price,
        )
