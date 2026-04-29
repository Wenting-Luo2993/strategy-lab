from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from vibe.backtester.core.fill_simulator import FillResult
from vibe.common.models.bar import Bar
from vibe.common.models.trade import Trade

_ET = ZoneInfo("America/New_York")
_EOD_CUTOFF = time(15, 55)


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    stop_price: float
    side: str           # "buy" | "sell"
    entry_time: datetime


class PortfolioManager:
    """
    Tracks cash, open positions, equity curve, and closed trade history.
    Records initial_risk and exit_reason on every closed Trade.
    """

    def __init__(self, initial_capital: float) -> None:
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []

    def open_position(
        self, fill: FillResult, stop_price: float, timestamp: datetime
    ) -> None:
        self.positions[fill.symbol] = Position(
            symbol=fill.symbol,
            quantity=fill.filled_qty,
            entry_price=fill.avg_price,
            stop_price=stop_price,
            side=fill.side,
            entry_time=timestamp,
        )
        self.cash -= fill.filled_qty * fill.avg_price

    def close_position(
        self, fill: FillResult, exit_reason: str, timestamp: datetime
    ) -> None:
        pos = self.positions.pop(fill.symbol)
        initial_risk = abs(pos.entry_price - pos.stop_price) * pos.quantity

        self.trade_history.append(Trade(
            symbol=fill.symbol,
            side=pos.side,
            quantity=fill.filled_qty,
            entry_price=pos.entry_price,
            exit_price=fill.avg_price,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            initial_risk=initial_risk,
            exit_reason=exit_reason,
        ))
        self.cash += fill.filled_qty * fill.avg_price

    def check_exits(
        self, current_bars: Dict[str, Bar], clock
    ) -> None:
        """
        Check stop-loss hits and EOD exit for all open positions.
        clock must have a .now() method returning a timezone-aware datetime.
        """
        local_time = clock.now().astimezone(_ET).time()
        is_eod = local_time >= _EOD_CUTOFF

        for symbol in list(self.positions.keys()):
            bar = current_bars.get(symbol)
            if bar is None:
                continue
            pos = self.positions[symbol]

            if pos.side == "buy" and bar.low <= pos.stop_price:
                fill = FillResult(
                    symbol=symbol, side="sell",
                    filled_qty=pos.quantity, avg_price=pos.stop_price,
                )
                self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
            elif is_eod:
                fill = FillResult(
                    symbol=symbol, side="sell",
                    filled_qty=pos.quantity, avg_price=bar.close,
                )
                self.close_position(fill, exit_reason="EOD", timestamp=clock.now())

    def update_equity(
        self, current_bars: Dict[str, Bar], timestamp: datetime
    ) -> None:
        position_value = sum(
            current_bars[sym].close * pos.quantity
            for sym, pos in self.positions.items()
            if sym in current_bars
        )
        self.equity_curve.append((timestamp, self.cash + position_value))
