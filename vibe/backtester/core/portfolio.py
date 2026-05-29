from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
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
    take_profit: Optional[float]  # None = no TP
    side: str           # "buy" | "sell"
    entry_time: datetime
    initial_stop_price: float
    initial_risk_per_share: float


class PortfolioManager:
    """
    Tracks cash, open positions, equity curve, and closed trade history.
    Records initial_risk and exit_reason on every closed Trade.
    """

    def __init__(self, initial_capital: float, trailing_stop_config: Optional[Dict[str, Any]] = None) -> None:
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []
        self.trailing_stop_config = trailing_stop_config

    def open_position(
        self, fill: FillResult, stop_price: float, take_profit: Optional[float], timestamp: datetime
    ) -> None:
        self.positions[fill.symbol] = Position(
            symbol=fill.symbol,
            quantity=fill.filled_qty,
            entry_price=fill.avg_price,
            stop_price=stop_price,
            take_profit=take_profit,
            side=fill.side,
            entry_time=timestamp,
            initial_stop_price=stop_price,
            initial_risk_per_share=abs(fill.avg_price - stop_price),
        )
        if fill.side == "buy":
            self.cash -= fill.filled_qty * fill.avg_price
        else:  # short (sell)
            self.cash += fill.filled_qty * fill.avg_price

    def close_position(
        self, fill: FillResult, exit_reason: str, timestamp: datetime
    ) -> None:
        pos = self.positions.pop(fill.symbol)
        # R-multiple denominator must remain anchored to entry-time risk.
        # Do not use the moved trailing stop at exit time.
        initial_risk = abs(pos.entry_price - pos.initial_stop_price) * pos.quantity

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
        if fill.side == "sell":  # closing long
            self.cash += fill.filled_qty * fill.avg_price
        else:  # closing short (buy back)
            self.cash -= fill.filled_qty * fill.avg_price

    def check_exits(
        self, current_bars: Dict[str, Bar], clock
    ) -> None:
        """
        Check take-profit, stop-loss, and EOD exit for all open positions.
        Exit priority: TP > Stop > EOD
        Stop/TP trigger: bar.close must cross the level (not intrabar wick).
        clock must have a .now() method returning a timezone-aware datetime.
        """
        local_time = clock.now().astimezone(_ET).time()
        is_eod = local_time >= _EOD_CUTOFF

        for symbol in list(self.positions.keys()):
            bar = current_bars.get(symbol)
            if bar is None:
                continue
            pos = self.positions[symbol]

            # Update stop from trailing rules before evaluating exits.
            self._maybe_update_trailing_stop(pos=pos, bar=bar)

            # Check take-profit first (highest priority)
            if pos.take_profit is not None:
                long_tp  = pos.side == "buy"  and bar.high >= pos.take_profit
                short_tp = pos.side == "sell" and bar.low  <= pos.take_profit
                
                if long_tp:
                    fill = FillResult(
                        symbol=symbol, side="sell",
                        filled_qty=pos.quantity, avg_price=pos.take_profit,
                    )
                    self.close_position(fill, exit_reason="TP", timestamp=clock.now())
                    continue
                elif short_tp:
                    fill = FillResult(
                        symbol=symbol, side="buy",
                        filled_qty=pos.quantity, avg_price=pos.take_profit,
                    )
                    self.close_position(fill, exit_reason="TP", timestamp=clock.now())
                    continue

            # Check stop-loss
            long_stop  = pos.side == "buy"  and bar.low  <= pos.stop_price
            short_stop = pos.side == "sell" and bar.high >= pos.stop_price

            if long_stop:
                fill = FillResult(
                    symbol=symbol, side="sell",
                    filled_qty=pos.quantity, avg_price=pos.stop_price,
                )
                self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
            elif short_stop:
                fill = FillResult(
                    symbol=symbol, side="buy",
                    filled_qty=pos.quantity, avg_price=pos.stop_price,
                )
                self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
            elif is_eod:
                close_side = "sell" if pos.side == "buy" else "buy"
                fill = FillResult(
                    symbol=symbol, side=close_side,
                    filled_qty=pos.quantity, avg_price=bar.close,
                )
                self.close_position(fill, exit_reason="EOD", timestamp=clock.now())

    def _maybe_update_trailing_stop(self, pos: Position, bar: Bar) -> None:
        """Update stop price based on configured trailing stop logic."""
        if not self.trailing_stop_config:
            return

        method = self.trailing_stop_config.get("method")
        if method not in {"breakeven_plus_ticks", "stepped_r_multiple"}:
            return

        risk = pos.initial_risk_per_share
        if risk <= 0:
            return

        if pos.side == "buy":
            favorable_move = max(0.0, bar.high - pos.entry_price)
            favorable_r = favorable_move / risk
        else:
            favorable_move = max(0.0, pos.entry_price - bar.low)
            favorable_r = favorable_move / risk

        if method == "breakeven_plus_ticks":
            trigger_r = float(self.trailing_stop_config.get("trigger_r", 1.0))
            plus_ticks = int(self.trailing_stop_config.get("plus_ticks", 0))
            tick_size = 0.01

            if favorable_r < trigger_r:
                return

            if pos.side == "buy":
                candidate = pos.entry_price + plus_ticks * tick_size
                if candidate > pos.stop_price:
                    pos.stop_price = candidate
            else:
                candidate = pos.entry_price - plus_ticks * tick_size
                if candidate < pos.stop_price:
                    pos.stop_price = candidate
            return

        # stepped_r_multiple
        steps = self.trailing_stop_config.get("steps", [])
        if not isinstance(steps, list):
            return

        for step in steps:
            if not isinstance(step, dict):
                continue

            at = float(step.get("at", 0.0))
            move_stop_to = float(step.get("move_stop_to", 0.0))
            if favorable_r < at:
                continue

            if pos.side == "buy":
                candidate = pos.entry_price + move_stop_to * risk
                if candidate > pos.stop_price:
                    pos.stop_price = candidate
            else:
                candidate = pos.entry_price - move_stop_to * risk
                if candidate < pos.stop_price:
                    pos.stop_price = candidate

    def update_equity(
        self, current_bars: Dict[str, Bar], timestamp: datetime
    ) -> None:
        position_value = sum(
            (current_bars[sym].close * pos.quantity
             if pos.side == "buy"
             else -current_bars[sym].close * pos.quantity)
            for sym, pos in self.positions.items()
            if sym in current_bars
        )
        self.equity_curve.append((timestamp, self.cash + position_value))
