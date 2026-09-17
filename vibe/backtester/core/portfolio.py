from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from vibe.backtester.core.execution_realism import (
    BuyingPowerError,
    ExecutionRealismConfig,
    GapFillPolicy,
    IntrabarExitResolution,
    clamp_to_bar,
)
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
    entry_commission: float = 0.0
    """Commission paid to open, carried so the closed Trade reports the full
    round trip rather than the exit leg alone."""


class PortfolioManager:
    """
    Tracks cash, open positions, equity curve, and closed trade history.
    Records initial_risk and exit_reason on every closed Trade.
    """

    def __init__(
        self,
        initial_capital: float,
        trailing_stop_config: Optional[Dict[str, Any]] = None,
        execution_realism: Optional[ExecutionRealismConfig] = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []
        self.trailing_stop_config = trailing_stop_config
        # Defaults to legacy semantics per ADR-015, so existing results are
        # reproduced exactly unless a caller opts in.
        self.execution_realism = execution_realism or ExecutionRealismConfig.legacy()

        # Always measured, in every mode. A legacy run still reports how much
        # of its result rests on optimistic assumptions.
        self.ambiguous_exit_bars = 0
        self.gap_through_exits = 0
        self.min_cash = initial_capital
        self.max_gross_exposure_ratio = 0.0
        self.total_costs = 0.0

    @property
    def commission_model(self):
        """Cost schedule for this run, taken from the realism config."""
        return self.execution_realism.commission_model

    def _charge_commission(self, quantity: float, price: float) -> float:
        """Debit commission for one fill and return the amount charged.

        Cash is reduced on both sides of a round trip: commission is an
        outflow whether the fill opened or closed a position.
        """
        cost = self.commission_model.cost(quantity, price)
        if cost:
            self.cash -= cost
            self.total_costs += cost
        return cost

    def _record_cash(self) -> None:
        self.min_cash = min(self.min_cash, self.cash)

    def gross_exposure(self, prices: Dict[str, float]) -> float:
        """Absolute market value of open positions."""
        return sum(
            abs(pos.quantity) * prices[sym]
            for sym, pos in self.positions.items()
            if sym in prices
        )

    def equity_basis(self) -> float:
        """Equity used for funding decisions: cash plus long positions at cost.

        Entry cost is used rather than mark-to-market so the figure does not
        move intrabar, which keeps a sizing decision and the assertion that
        validates it from disagreeing within the same bar.
        """
        return self.cash + sum(
            pos.quantity * pos.entry_price
            for pos in self.positions.values()
            if pos.side == "buy"
        )

    def committed_notional(self) -> float:
        """Absolute notional already committed to open positions, at cost."""
        return sum(
            abs(pos.quantity) * pos.entry_price for pos in self.positions.values()
        )

    def available_buying_power(
        self, entry_price: Optional[float] = None
    ) -> Optional[float]:
        """Funds available for a new position, or None when unenforced.

        ``None`` means "do not gate" rather than "zero available", so callers
        must handle it explicitly instead of silently treating an unenforced
        account as broke.

        When ``entry_price`` is supplied, the result is net of the round-trip
        commission the position will incur. Without that reserve the gate
        funds the notional exactly and the account is then debited for costs
        it never set aside, so a maximum-size order overdraws by the
        commission -- a real effect measured at -$2.24 on the QQQ ORB
        baseline. Reserving both legs is deliberate: the exit is not optional,
        so treating it as free would let the gate approve a position the
        account cannot actually close.
        """
        if not self.execution_realism.enforce_buying_power:
            return None
        limit = self.equity_basis() * self.execution_realism.max_gross_leverage
        available = max(0.0, limit - self.committed_notional())

        if entry_price is None or entry_price <= 0 or available <= 0:
            return available

        # Sized on the pre-reserve share count, so the reserve covers slightly
        # more shares than will actually be bought. One pass, erring wide.
        model = self.commission_model
        if model.is_zero:
            return available
        round_trip = 2.0 * model.cost(available / entry_price, entry_price)
        return max(0.0, available - round_trip)

    def assert_buying_power(self, notional: float) -> None:
        """Fail loudly on a position the account could not fund.

        This is a backstop, not the gate. Sizing is expected to have already
        clamped the order via ``available_buying_power``; reaching this error
        means something bypassed sizing, so it must raise rather than clamp.
        """
        available = self.available_buying_power()
        if available is None:
            return
        if notional > available + 1e-6:
            raise BuyingPowerError(
                f"Order notional {notional:,.2f} exceeds available buying power "
                f"{available:,.2f} (equity {self.equity_basis():,.2f} x max "
                f"leverage {self.execution_realism.max_gross_leverage}, less "
                f"{self.committed_notional():,.2f} already committed). Position "
                f"sizing should have clamped this order."
            )

    def open_position(
        self, fill: FillResult, stop_price: float, timestamp: datetime, take_profit: Optional[float] = None
    ) -> None:
        self.assert_buying_power(abs(fill.filled_qty) * fill.avg_price)
        entry_commission = self._charge_commission(
            fill.filled_qty, fill.avg_price
        )
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
            entry_commission=entry_commission,
        )
        if fill.side == "buy":
            self.cash -= fill.filled_qty * fill.avg_price
        else:  # short (sell)
            self.cash += fill.filled_qty * fill.avg_price
        self._record_cash()

    def add_to_position(
        self, fill: FillResult, timestamp: datetime
    ) -> None:
        """
        Scale into an existing position with a partial fill.
        
        Calculates weighted average entry price, accumulates quantity,
        and preserves stop/TP from the original signal.
        
        Args:
            fill: Partial fill to add
            timestamp: Execution time
            
        Raises:
            KeyError: If position doesn't exist (must open_position first)
            ValueError: If fill side doesn't match position side
        """
        if fill.symbol not in self.positions:
            raise KeyError(f"Position {fill.symbol} does not exist. Use open_position() first.")
        
        pos = self.positions[fill.symbol]
        
        # Verify same side (can't buy into short or vice versa)
        if fill.side != pos.side:
            raise ValueError(
                f"Cannot add {fill.side} fill to existing {pos.side} position for {fill.symbol}"
            )
        
        # Calculate weighted average entry price
        old_value = pos.quantity * pos.entry_price
        new_value = fill.filled_qty * fill.avg_price
        total_quantity = pos.quantity + fill.filled_qty
        weighted_avg_price = (old_value + new_value) / total_quantity
        
        # Update position (quantity and entry_price only; stop/TP unchanged)
        pos.quantity = total_quantity
        pos.entry_price = weighted_avg_price
        # Each scale-in is a separate order and is billed as one.
        pos.entry_commission += self._charge_commission(
            fill.filled_qty, fill.avg_price
        )
        
        # Update cash (same logic as open_position)
        if fill.side == "buy":
            self.cash -= fill.filled_qty * fill.avg_price
        else:  # short (sell)
            self.cash += fill.filled_qty * fill.avg_price
        self._record_cash()

    def close_position(
        self, fill: FillResult, exit_reason: str, timestamp: datetime
    ) -> None:
        pos = self.positions.pop(fill.symbol)
        # R-multiple denominator must remain anchored to entry-time risk.
        # Do not use the moved trailing stop at exit time.
        initial_risk = abs(pos.entry_price - pos.initial_stop_price) * pos.quantity

        exit_commission = self._charge_commission(
            fill.filled_qty, fill.avg_price
        )

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
            commission=pos.entry_commission + exit_commission,
        ))
        if fill.side == "sell":  # closing long
            self.cash += fill.filled_qty * fill.avg_price
        else:  # closing short (buy back)
            self.cash -= fill.filled_qty * fill.avg_price
        self._record_cash()

    def check_exits(
        self, current_bars: Dict[str, Bar], clock
    ) -> None:
        """Check take-profit, stop-loss, and EOD exit for all open positions.

        Triggers are **intrabar**: they use ``bar.high``/``bar.low``, not
        ``bar.close``. A resting stop or limit order would have been hit by the
        wick, so this is correct, but note that an earlier version of this
        docstring claimed otherwise.

        When a single bar touches both the stop and the target, the true order
        of events is unknowable from OHLC. Resolution follows
        ``execution_realism.intrabar_exit_resolution``; every such bar is
        counted in ``ambiguous_exit_bars`` regardless of mode.

        Exit priority when unambiguous: TP/Stop (whichever triggered) > EOD.
        ``clock`` must have a ``.now()`` returning a timezone-aware datetime.
        """
        local_time = clock.now().astimezone(_ET).time()
        is_eod = local_time >= _EOD_CUTOFF
        policy = self.execution_realism

        for symbol in list(self.positions.keys()):
            bar = current_bars.get(symbol)
            if bar is None:
                continue
            pos = self.positions[symbol]

            # Update stop from trailing rules before evaluating exits.
            self._maybe_update_trailing_stop(pos=pos, bar=bar)

            is_long = pos.side == "buy"
            if is_long:
                tp_hit = pos.take_profit is not None and bar.high >= pos.take_profit
                stop_hit = bar.low <= pos.stop_price
            else:
                tp_hit = pos.take_profit is not None and bar.low <= pos.take_profit
                stop_hit = bar.high >= pos.stop_price

            if tp_hit and stop_hit:
                self.ambiguous_exit_bars += 1
                if policy.intrabar_exit_resolution is IntrabarExitResolution.OPTIMISTIC:
                    stop_hit = False
                else:
                    tp_hit = False

            if tp_hit:
                self._close_at_level(
                    pos=pos, bar=bar, level=pos.take_profit,
                    reason="TP", clock=clock,
                )
                continue
            if stop_hit:
                self._close_at_level(
                    pos=pos, bar=bar, level=pos.stop_price,
                    reason="STOP", clock=clock,
                )
                continue
            if is_eod:
                close_side = "sell" if is_long else "buy"
                self.close_position(
                    FillResult(
                        symbol=symbol, side=close_side,
                        filled_qty=pos.quantity, avg_price=bar.close,
                    ),
                    exit_reason="EOD",
                    timestamp=clock.now(),
                )

    def _close_at_level(
        self, pos: Position, bar: Bar, level: float, reason: str, clock
    ) -> None:
        """Close ``pos`` at ``level``, adjusted for gaps and clamped to the bar.

        Filling exactly at the trigger price assumes the market paused there to
        accommodate us. When the bar *opened* beyond the level, a resting order
        would have filled at the open instead. For a stop that is materially
        worse, and is the entire cost of gap risk.

        The gap is counted in every mode, but only *repriced* under
        ``GapFillPolicy.AT_OPEN``, so legacy runs stay bit-comparable while
        still reporting how often the assumption mattered.
        """
        is_long = pos.side == "buy"
        # For a long, a stop is below and a target above; inverted for a short.
        level_is_below = (is_long and reason == "STOP") or (
            not is_long and reason == "TP"
        )
        gapped_through = bar.open < level if level_is_below else bar.open > level

        price = level
        if gapped_through:
            self.gap_through_exits += 1
            if self.execution_realism.gap_fill_policy is GapFillPolicy.AT_OPEN:
                # A fill outside the traded range is a price that never existed.
                price = clamp_to_bar(bar.open, bar.low, bar.high)

        self.close_position(
            FillResult(
                symbol=pos.symbol,
                side="sell" if is_long else "buy",
                filled_qty=pos.quantity,
                avg_price=price,
            ),
            exit_reason=reason,
            timestamp=clock.now(),
        )

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
        equity = self.cash + position_value
        self.equity_curve.append((timestamp, equity))

        # Peak leverage is evidence, not a setting. Recorded even when
        # enforcement is off, so an unfunded strategy is visible after the fact.
        if equity > 0:
            gross = self.gross_exposure(
                {sym: bar.close for sym, bar in current_bars.items()}
            )
            self.max_gross_exposure_ratio = max(
                self.max_gross_exposure_ratio, gross / equity
            )
