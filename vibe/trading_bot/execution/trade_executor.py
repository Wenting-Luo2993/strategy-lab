"""
Trade executor that coordinates signal-to-order conversion with risk management.
Integrates Position Sizer, Stop Loss Manager, Order Manager, and Exchange.
"""

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from vibe.common.execution.base import PartialPlacementError, RestingEntryLeg
from vibe.common.models import Order, OrderStatus, Position
from vibe.common.risk import PositionSizer
from vibe.common.risk.position_sizer import PositionSizeResult
from vibe.common.risk.portfolio_sizing import PendingExposure, net_liquidation
from vibe.trading_bot.execution.order_manager import (
    OrderManager,
    OrderRetryPolicy,
)
from vibe.trading_bot.exchange.mock_exchange import MockExchange


logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of pre-trade risk validation."""

    passed: bool
    """Whether risk check passed."""

    reason: str
    """Reason for pass/fail."""


@dataclass
class ExecutionResult:
    """Result from executing a trade signal."""

    success: bool
    """Whether execution succeeded."""

    order_id: Optional[str] = None
    """Order ID if successful."""

    reason: str = ""
    """Details about execution."""

    position_size: float = 0.0
    """Position size used."""

    avg_price: float = 0.0
    """Actual average fill price from exchange (includes slippage)."""

    remaining_position_size: float = 0.0
    """Quantity still open after this execution."""

    fully_closed: bool = False
    """Whether a close execution left the broker position flat."""

    pending: bool = False
    """Whether an accepted entry order is awaiting its first fill."""

    capped_by: Optional[str] = None
    """Limit that reduced the entry below its risk-implied size, if any."""

    broker_contacted: bool = False
    """Whether an order submission reached (or may have reached) the broker."""


def round_to_tick(price: float, tick_size: float, direction: str) -> float:
    """Round ``price`` to a multiple of ``tick_size`` (``up`` or ``down``)."""
    ticks = float(price) / tick_size
    whole = math.ceil(ticks - 1e-9) if direction == "up" else math.floor(ticks + 1e-9)
    return round(whole * tick_size, 10)


@dataclass(frozen=True)
class EntryOrderIntent:
    """Exact broker-neutral entry instructions produced by the caller."""

    order_type: Literal["market", "stop"]
    trigger_price: float
    cancel_after_seconds: Optional[float] = None
    expiry_bars: Optional[int] = None
    signal_bar_timestamp: Optional[datetime] = None
    bar_interval_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.trigger_price <= 0:
            raise ValueError("entry trigger price must be positive")
        if self.expiry_bars is not None and self.expiry_bars <= 0:
            raise ValueError("expiry_bars must be positive")


class SimpleRiskManager:
    """
    Simple risk manager for trade validation.

    Checks:
    - Position sizer constraints
    - Maximum positions limit
    - Drawdown limits
    """

    account_level = False
    """Whether ``pre_trade_check`` expects every account position and pending entry."""

    def __init__(
        self,
        position_sizer: PositionSizer,
        max_positions: int = 5,
        max_drawdown_pct: float = 10.0,
    ):
        """
        Initialize risk manager.

        Args:
            position_sizer: PositionSizer for sizing calculations
            max_positions: Maximum open positions
            max_drawdown_pct: Maximum daily drawdown percentage
        """
        self.position_sizer = position_sizer
        self.max_positions = max_positions
        self.max_drawdown_pct = max_drawdown_pct
        self._open_positions = 0

    def pre_trade_check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        current_account_value: float,
        existing_positions: Dict[str, Position],
    ) -> RiskCheckResult:
        """
        Validate trade before execution.

        Args:
            symbol: Trading symbol
            side: Order side
            quantity: Order quantity
            entry_price: Entry price
            current_account_value: Current account value
            existing_positions: Current open positions

        Returns:
            RiskCheckResult
        """
        # Check max positions
        num_open = len(existing_positions)
        if num_open >= self.max_positions:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"At maximum positions ({num_open}/"
                    f"{self.max_positions})"
                ),
            )

        # Check if symbol already has position
        if symbol in existing_positions:
            return RiskCheckResult(
                passed=False,
                reason=f"Position already exists in {symbol}",
            )

        # Check quantity is reasonable
        if quantity <= 0:
            return RiskCheckResult(
                passed=False,
                reason="Quantity must be positive",
            )

        # All checks passed
        return RiskCheckResult(
            passed=True,
            reason="Risk checks passed",
        )

    def register_position(self) -> None:
        """Register that a position was opened."""
        self._open_positions += 1

    def close_position(self) -> None:
        """Register that a position was closed."""
        if self._open_positions > 0:
            self._open_positions -= 1


class TradeExecutor:
    """
    Executes trades by converting signals to orders with risk management.

    Workflow:
    1. Receive signal (symbol, direction, price, stop)
    2. Validate risk constraints
    3. Calculate position size
    4. Submit entry order via OrderManager
    5. Submit stop-loss order
    6. Track position and P&L
    """

    def __init__(
        self,
        exchange: MockExchange,
        order_manager: OrderManager,
        position_sizer: PositionSizer,
        risk_manager: Optional[SimpleRiskManager] = None,
        on_execution: Optional[Callable[[ExecutionResult], None]] = None,
    ):
        """
        Initialize trade executor.

        Args:
            exchange: MockExchange for order submission
            order_manager: OrderManager for lifecycle management
            position_sizer: PositionSizer for sizing
            risk_manager: SimpleRiskManager (auto-created if None)
            on_execution: Callback for execution results
        """
        self.exchange = exchange
        self.order_manager = order_manager
        self.position_sizer = position_sizer
        self.risk_manager = (
            risk_manager
            or SimpleRiskManager(position_sizer)
        )
        self._on_execution = on_execution

        # Trade tracking
        self._open_trades: Dict[str, ExecutionResult] = {}
        self._pending_entries: Dict[str, Dict[str, Any]] = {}
        self._pending_closes: Dict[str, str] = {}
        # Broker-side protective stops: entry order id -> attached stop order
        # id, and symbol -> the stop protecting its open position.
        self._protective_by_entry: Dict[str, str] = {}
        self._active_protective: Dict[str, str] = {}
        # Protective stops this executor is cancelling itself (not broker-ended).
        self._cancelling: set[str] = set()
        # Symbols whose position is being closed by the client right now.
        self._closing: set[str] = set()
        # Serialises a symbol's close and stop re-protection, so a replacement
        # stop can never be placed behind a close that already flattened it.
        self._symbol_locks: Dict[str, asyncio.Lock] = {}
        # When each active protective stop became active (orphan-sweep grace).
        self._protective_since: Dict[str, float] = {}
        self.orphan_stop_grace_s = 30.0
        self.on_alert: Optional[Callable[[str, str], Awaitable[None]]] = None
        # How long a partial-stop close waits for the broker position to agree.
        self.position_agreement_timeout_s = 5.0

    async def place_resting_stop_entries(
        self,
        symbol: str,
        *,
        long_level: Optional[float],
        short_level: Optional[float],
        long_stop: Optional[float],
        short_stop: Optional[float],
        cancel_after_seconds: float,
        strategy_name: str = "Unknown",
        signal_timestamp: Optional[datetime] = None,
        oca_group: Optional[str] = None,
        placement_latency_s: Optional[float] = None,
        tick_size: float = 0.01,
        placement_metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Rest a buy stop and/or a sell stop in one OCA group with attached stops.

        Each side is sized on its own by the shared rule against the same
        account state (only one can fill, so the pair reserves the larger side
        once). Sides that size below one share are dropped. Prices are rounded
        to ``tick_size`` so the broker never rejects a sub-penny level: entry
        triggers away from the market (buy up, sell down), protective stops
        towards the entry (the conservative side). ``placement_metadata``
        (``or_end``/``clock`` for the latency, ``or_source``/``or_high``/``or_low``)
        is journalled with the orders.
        """
        legs_spec = []
        if long_level is not None and long_stop is not None:
            legs_spec.append((
                "buy",
                round_to_tick(long_level, tick_size, "up"),
                round_to_tick(long_stop, tick_size, "up"),
            ))
        if short_level is not None and short_stop is not None:
            legs_spec.append((
                "sell",
                round_to_tick(short_level, tick_size, "down"),
                round_to_tick(short_stop, tick_size, "down"),
            ))
        if not legs_spec:
            return ExecutionResult(success=False, reason="No resting entry side enabled")
        if any(context["symbol"] == symbol for context in self._pending_entries.values()):
            return ExecutionResult(
                success=False, reason=f"Entry order already pending for {symbol}"
            )
        submit = getattr(self.exchange, "submit_resting_entry_pair", None)
        if submit is None:
            return ExecutionResult(
                success=False,
                reason=f"{type(self.exchange).__name__} cannot place resting stop entries",
            )

        account = await self.exchange.get_account()
        if self.risk_manager.account_level:
            positions = await self.account_positions()
            pending = self.pending_exposures(positions)
            account_value = net_liquidation(account)
        else:
            existing = await self.exchange.get_position(symbol)
            positions = {symbol: existing} if existing is not None else {}
            pending = []
            account_value = account.equity

        legs: list[RestingEntryLeg] = []
        capped: Dict[str, Optional[str]] = {}
        reasons: list[str] = []
        for side, level, protective in legs_spec:
            if self.risk_manager.account_level:
                check = self.risk_manager.pre_trade_check(
                    symbol=symbol, side=side, quantity=1, entry_price=level,
                    current_account_value=account_value,
                    existing_positions=positions, pending_entries=pending,
                )
            else:
                check = self.risk_manager.pre_trade_check(
                    symbol=symbol, side=side, quantity=1, entry_price=level,
                    current_account_value=account_value,
                    existing_positions=positions,
                )
            if not check.passed:
                reasons.append(f"{side}: {check.reason}")
                continue
            try:
                if self.risk_manager.account_level:
                    decision = self.risk_manager.size_entry(
                        entry_price=level, stop_price=protective,
                        current_equity=account_value, positions=positions,
                        pending_entries=pending,
                        broker_available_funds=account.available_funds,
                    )
                    quantity, capped_by, detail = (
                        decision.quantity, decision.capped_by, decision.describe()
                    )
                else:
                    sized = self.position_sizer.calculate(
                        entry_price=level, stop_price=protective,
                        account_value=account.equity, buying_power=account.buying_power,
                    )
                    quantity, capped_by, detail = int(sized.size), sized.capped_by, sized.reasoning
            except Exception as exc:
                reasons.append(f"{side}: sizing error {exc}")
                continue
            logger.info(
                "[SIZING] %s resting %s @ %.2f stop=%.2f -> %s shares (capped_by=%s) | %s",
                symbol, side, level, protective, quantity, capped_by or "none", detail,
            )
            if quantity < 1:
                reasons.append(f"{side}: size below one share (capped_by={capped_by})")
                continue
            legs.append(RestingEntryLeg(side, int(quantity), level, protective, capped_by))
            capped[side] = capped_by

        if not legs:
            reason = "; ".join(reasons) or "No resting entry could be sized"
            logger.warning("Resting entries for %s not placed: %s", symbol, reason)
            return ExecutionResult(success=False, reason=reason)

        group = oca_group or f"ORB-{symbol}-{uuid.uuid4().hex[:10]}"
        partial: Optional[PartialPlacementError] = None
        try:
            placed = await submit(
                symbol,
                legs,
                group,
                lifecycle_metadata={
                    "strategy_name": strategy_name,
                    "entry_cancel_after_seconds": cancel_after_seconds,
                    "entry_signal_bar_timestamp": signal_timestamp,
                    "placement_latency_s": placement_latency_s,
                    **(placement_metadata or {}),
                },
            )
        except PartialPlacementError as exc:
            # Some brackets are live: monitor them like a normal placement.
            partial, placed = exc, list(exc.placed)
            await self._alert(
                "Resting entry placement incomplete",
                f"{symbol}: placement failed after {len(placed)} of {len(legs)} "
                f"bracket(s): {exc}. Placed brackets are being monitored.",
            )
        except Exception as exc:
            logger.error("Resting entry submission failed for %s: %s", symbol, exc, exc_info=True)
            # Orders may be live even though the call failed; the caller must
            # not try again this session.
            return ExecutionResult(
                success=False,
                reason=f"Order submission error: {exc}",
                broker_contacted=True,
            )
        if not placed:
            return ExecutionResult(
                success=False,
                reason=f"Order submission error: {partial}",
                broker_contacted=True,
            )

        entries = [entry for entry, _ in placed]
        stops = [stop for _, stop in placed]
        for entry, stop in placed:
            self._protective_by_entry[entry.order_id] = stop.order_id
            self._pending_entries[entry.order_id] = {
                "symbol": symbol,
                "side": entry.side,
                "stop_price": stop.stop_price,
                "take_profit": None,
                "strategy_name": strategy_name,
                "expiry_bars": None,
                "bars_elapsed": 0,
                "last_bar_timestamp": signal_timestamp,
                "quantity": float(entry.quantity),
                "trigger_price": float(entry.stop_price),
                "group": group,
                "capped_by": capped.get(entry.side),
                "protective_order_id": stop.order_id,
                "gap_fill": entry.gap_fill,
                "placement_latency_s": entry.placement_latency_s,
            }
        self.order_manager.track_orders(entries, cancel_after_seconds=cancel_after_seconds)
        self.order_manager.track_orders(stops, persistent=True)
        logger.info(
            "[RESTING ENTRY] %s oca=%s latency=%ss %s",
            symbol,
            group,
            None
            if entries[0].placement_latency_s is None
            else round(entries[0].placement_latency_s, 1),
            ", ".join(
                f"{e.side} {e.quantity:.0f} @ {e.stop_price:.2f} (stop {s.stop_price:.2f}, "
                f"gap_fill={e.gap_fill})"
                for e, s in placed
            ),
        )
        result = ExecutionResult(
            success=False,
            order_id=entries[0].order_id,
            reason=f"Resting entries placed in OCA group {group}",
            pending=True,
            capped_by=next((c for c in capped.values() if c), None),
            broker_contacted=True,
        )
        if self._on_execution:
            self._on_execution(result)
        return result

    def _symbol_lock(self, symbol: str) -> asyncio.Lock:
        lock = self._symbol_locks.get(symbol)
        if lock is None:
            lock = self._symbol_locks[symbol] = asyncio.Lock()
        return lock

    def _activate_protective(self, symbol: str, order_id: str) -> None:
        self._active_protective[symbol] = order_id
        self._protective_since[symbol] = time.monotonic()

    def mark_entry_filled(self, order_id: str, symbol: Optional[str] = None) -> None:
        """An entry filled completely: its attached stop is live, its OCA siblings are void.

        Call only once the parent is completely filled: IBKR activates an
        attached child only then, so until that point the position must stay
        under the client-side stop. Falls back to the bracket map when the
        pending context is already gone.
        """
        context = self._pending_entries.get(order_id)
        protective = (
            context.get("protective_order_id") if context is not None else None
        ) or self._protective_by_entry.get(order_id)
        symbol = context["symbol"] if context is not None else symbol
        if protective and symbol and symbol not in self._active_protective:
            # An existing active stop (e.g. a replacement for a lost child)
            # is never overridden by the bracket's original child.
            self._activate_protective(symbol, protective)
        if context is None:
            return
        group = context.get("group")
        if group and group != order_id:
            for other_id, other in list(self._pending_entries.items()):
                if other_id != order_id and other.get("group") == group:
                    # Cancelled by the broker's OCA group.
                    self._pending_entries.pop(other_id, None)

    async def _alert(self, title: str, message: str) -> None:
        logger.error("[ALERT] %s: %s", title, message)
        if self.on_alert is None:
            return
        try:
            await self.on_alert(title, message)
        except Exception as exc:
            logger.error("Alert callback failed: %s", exc, exc_info=True)

    async def _place_standalone_protective(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: Optional[float],
        strategy_name: Optional[str] = None,
    ) -> bool:
        """Place and monitor a standalone protective stop; True on success."""
        submit = getattr(self.exchange, "submit_protective_stop", None)
        if submit is None or not stop_price or int(quantity) < 1:
            return False
        try:
            stop = await submit(symbol, side, int(quantity), float(stop_price), strategy_name)
        except Exception as exc:
            logger.error(
                "Standalone protective stop for %s failed: %s", symbol, exc, exc_info=True
            )
            return False
        self._activate_protective(symbol, stop.order_id)
        self.order_manager.track_orders([stop], persistent=True)
        logger.info(
            "[PROTECTIVE STOP] %s %s %s @ %.2f (order %s)",
            symbol, side, int(quantity), float(stop_price), stop.order_id,
        )
        return True

    async def handle_entry_cancelled(self, order: Order) -> str:
        """An entry ended at its deadline or by the broker.

        Returns ``none`` (nothing filled), ``protected`` (the filled shares
        rest under a stop) or ``unprotected`` (the caller must flatten and
        alert). A partially filled bracket parent takes its attached stop with
        it when cancelled, so the filled shares get a standalone stop sized to
        exactly what filled -- never more than the open position.
        """
        context = self._pending_entries.pop(order.order_id, None) or {}
        self._protective_by_entry.pop(order.order_id, None)
        filled = float(order.filled_qty or 0.0)
        if filled <= 0 or order.order_role != "entry":
            return "none"
        if order.symbol in self._closing:
            return "closing"
        async with self._symbol_lock(order.symbol):
            if order.symbol in self._closing or self.has_pending_close(order.symbol):
                return "closing"
            if order.symbol in self._active_protective:
                return "protected"
            side = "sell" if order.side == "buy" else "buy"
            stop_price = context.get("stop_price") or order.strategy_stop_price
            if await self._place_standalone_protective(
                order.symbol, side, filled, stop_price, order.strategy_name
            ):
                return "protected"
        await self._alert(
            "Partial entry unprotected",
            f"{order.symbol}: {filled:.0f} shares filled before the entry was cancelled "
            "and no protective stop could be placed; flattening.",
        )
        return "unprotected"

    async def handle_protective_stop_ended(self, order: Order) -> str:
        """The broker cancelled or rejected the active protective stop.

        Returns ``ignored`` (not the active stop, or our own cancel),
        ``replaced`` or ``unprotected`` (client-side stop takes over; alerted).
        """
        symbol = order.symbol
        if (
            self._active_protective.get(symbol) != order.order_id
            or order.order_id in self._cancelling
            or symbol in self._closing
        ):
            return "ignored"
        async with self._symbol_lock(symbol):
            if self._active_protective.get(symbol) != order.order_id:
                return "ignored"
            self._active_protective.pop(symbol, None)
            self._protective_since.pop(symbol, None)
            if symbol in self._closing or self.has_pending_close(symbol):
                # A client close owns the position now; a new stop would be
                # left behind on a flat account.
                return "closing"
            remaining = float(order.quantity) - float(order.filled_qty or 0.0)
            position = await self.exchange.get_position(symbol)
            if position is not None and position.quantity:
                # Never protect more than is open.
                remaining = min(remaining, float(position.quantity))
            if remaining < 1 or position is None:
                logger.warning(
                    "Protective stop %s for %s ended; no open position to re-protect",
                    order.order_id, symbol,
                )
                return "ignored"
            if await self._place_standalone_protective(
                symbol, order.side, remaining, order.stop_price, order.strategy_name
            ):
                return "replaced"
        await self._alert(
            "Protective stop lost",
            f"{symbol}: the broker ended protective stop {order.order_id} and a "
            "replacement could not be placed; the client-side stop now manages the "
            "position.",
        )
        return "unprotected"

    def has_protective_stop(self, symbol: str) -> bool:
        return symbol in self._active_protective

    def protective_stop_order_id(self, symbol: str) -> Optional[str]:
        return self._active_protective.get(symbol)

    def clear_protective_stop(self, symbol: str) -> None:
        self._active_protective.pop(symbol, None)
        self._protective_since.pop(symbol, None)

    async def _fill_derived_quantity(self, symbol: str) -> Optional[float]:
        """Signed open quantity from today's fills, when the exchange can tell.

        Fills are authoritative ahead of a lagging broker position read.
        None means unknown (no resting entry today, or no fill history).
        """
        getter = getattr(self.exchange, "session_net_quantity", None)
        if getter is None:
            return None
        try:
            return await getter(symbol)
        except Exception as exc:
            logger.error("Fill-derived position for %s unavailable: %s", symbol, exc, exc_info=True)
            return None

    async def sweep_orphan_protective_stops(self) -> list[str]:
        """Cancel active protective stops left on a flat account.

        A stop is an orphan only when both the broker position and today's
        fills say the symbol is flat, and it has been active longer than a
        short grace period (a just-filled entry's position read can lag).
        """
        cancelled: list[str] = []
        now = time.monotonic()
        for symbol, stop_id in list(self._active_protective.items()):
            since = self._protective_since.get(symbol)
            if since is not None and now - since < self.orphan_stop_grace_s:
                continue
            if symbol in self._closing or self.has_pending_close(symbol):
                continue
            try:
                position = await self.exchange.get_position(symbol)
            except Exception as exc:
                logger.error("Orphan-stop check for %s failed: %s", symbol, exc, exc_info=True)
                continue
            if position is not None and float(position.quantity or 0.0):
                continue
            net = await self._fill_derived_quantity(symbol)
            if net is not None and abs(net) > 1e-9:
                continue
            async with self._symbol_lock(symbol):
                if self._active_protective.get(symbol) != stop_id:
                    continue
                outcome = await self._cancel_protective_stop(symbol)
            logger.warning(
                "[PROTECTIVE STOP] %s: stop %s was working on a flat position: %s",
                symbol, stop_id, outcome,
            )
            if outcome == "cancelled":
                cancelled.append(stop_id)
                await self._alert(
                    "Orphan protective stop cancelled",
                    f"{symbol}: protective stop {stop_id} was working while the account "
                    "was flat; cancelled.",
                )
        return cancelled

    async def _cancel_protective_stop(self, symbol: str) -> str:
        """Cancel the symbol's protective stop: ``cancelled`` | ``filled`` | ``error``."""
        order_id = self._active_protective.get(symbol)
        if order_id is None:
            return "cancelled"
        self._cancelling.add(order_id)
        try:
            return await self._cancel_protective_stop_inner(symbol, order_id)
        finally:
            self._cancelling.discard(order_id)

    async def _cancel_protective_stop_inner(self, symbol: str, order_id: str) -> str:
        try:
            current = await self.exchange.get_order(order_id)
            if current is not None and current.status == OrderStatus.FILLED:
                outcome = "filled"
            elif current is not None and current.status == OrderStatus.CANCELLED:
                outcome = "cancelled"
            elif self.order_manager.get_order(order_id) is not None:
                cancelled = await self.order_manager.cancel_order(order_id)
                managed = self.order_manager.get_order(order_id)
                outcome = (
                    "cancelled"
                    if cancelled
                    else "filled"
                    if managed is not None and managed.terminal_status == OrderStatus.FILLED
                    else None
                )
            else:
                outcome = None
            if outcome is None:
                order = await self.exchange.get_order(order_id)
                if order is not None and order.status == OrderStatus.FILLED:
                    outcome = "filled"
                elif order is not None and order.status == OrderStatus.CANCELLED:
                    outcome = "cancelled"
                else:
                    response = await self.exchange.cancel_order(order_id)
                    outcome = "filled" if response.status == OrderStatus.FILLED else "cancelled"
        except Exception as exc:
            # A cancel can lose a race with the fill; the broker state decides.
            try:
                order = await self.exchange.get_order(order_id)
            except Exception:
                order = None
            if order is not None and order.status == OrderStatus.FILLED:
                outcome = "filled"
            else:
                logger.error(
                    "Failed to cancel protective stop %s for %s: %s", order_id, symbol, exc,
                    exc_info=True,
                )
                return "error"
        self._active_protective.pop(symbol, None)
        self._protective_since.pop(symbol, None)
        logger.info("Protective stop %s for %s: %s", order_id, symbol, outcome)
        return outcome

    async def execute_signal(
        self,
        symbol: str,
        signal: int,
        entry_price: float,
        stop_price: float,
        take_profit: Optional[float] = None,
        strategy_name: str = "Unknown",
        entry_order: Optional[EntryOrderIntent] = None,
    ) -> ExecutionResult:
        """
        Execute a trade signal from the strategy.

        Args:
            symbol: Trading symbol
            signal: Signal direction (1=long, -1=short, 0=close)
            entry_price: Entry price
            stop_price: Stop-loss price
            take_profit: Take-profit price (optional)
            strategy_name: Name of strategy (for logging)

        Returns:
            ExecutionResult with execution details
        """
        logger.info(
            f"Executing signal: {symbol} {signal} "
            f"@{entry_price} stop={stop_price}"
        )

        # Handle close signal
        if signal == 0:
            return await self._close_position(symbol)

        pending_entry = next(
            (
                order_id
                for order_id, context in self._pending_entries.items()
                if context["symbol"] == symbol
            ),
            None,
        )
        if pending_entry is not None:
            return ExecutionResult(
                success=False,
                order_id=pending_entry,
                reason=f"Entry order already pending for {symbol}: {pending_entry}",
            )

        # Determine side
        if signal > 0:
            side = "buy"
        elif signal < 0:
            side = "sell"
        else:
            return ExecutionResult(
                success=False,
                reason="Invalid signal",
            )

        order_intent = entry_order or EntryOrderIntent(
            order_type="market",
            trigger_price=entry_price,
        )
        sizing_entry_price = order_intent.trigger_price

        # Get account state
        account = await self.exchange.get_account()

        if self.risk_manager.account_level:
            # Account-level rules need every position and resting entry, not
            # just this symbol's: the open-position cap and the equity
            # headroom are properties of the account.
            existing_positions = await self.account_positions()
            pending_entries = self.pending_exposures(existing_positions)
            account_value = net_liquidation(account)
            risk_check = self.risk_manager.pre_trade_check(
                symbol=symbol,
                side=side,
                quantity=1,  # Placeholder, will be sized
                entry_price=sizing_entry_price,
                current_account_value=account_value,
                existing_positions=existing_positions,
                pending_entries=pending_entries,
            )
        else:
            # Check if we already have a position in this symbol
            existing_position = await self.exchange.get_position(symbol)
            existing_positions = {}
            if existing_position is not None:
                existing_positions[symbol] = existing_position

            # Risk validation
            risk_check = self.risk_manager.pre_trade_check(
                symbol=symbol,
                side=side,
                quantity=1,  # Placeholder, will be sized
                entry_price=sizing_entry_price,
                current_account_value=account.equity,
                existing_positions=existing_positions,
            )

        if not risk_check.passed:
            result = ExecutionResult(
                success=False,
                reason=risk_check.reason,
            )
            if self._on_execution:
                self._on_execution(result)
            logger.warning(f"Risk check failed: {risk_check.reason}")
            return result

        # Calculate position size
        try:
            if self.risk_manager.account_level:
                decision = self.risk_manager.size_entry(
                    entry_price=sizing_entry_price,
                    stop_price=stop_price,
                    current_equity=account_value,
                    positions=existing_positions,
                    pending_entries=pending_entries,
                    broker_available_funds=account.available_funds,
                )
                size_result = PositionSizeResult(
                    size=decision.quantity,
                    risk_amount=decision.risk_amount,
                    requested_size=decision.risk_qty,
                    capped_by=decision.capped_by,
                    reasoning=decision.describe(),
                )
            else:
                size_result = self.position_sizer.calculate(
                    entry_price=sizing_entry_price,
                    stop_price=stop_price,
                    account_value=account.equity,
                    buying_power=account.buying_power,
                )
            logger.info(
                "[SIZING] %s %s entry=%.2f stop=%.2f -> %s shares (risk_qty=%s, capped_by=%s) | %s",
                symbol,
                side,
                sizing_entry_price,
                stop_price,
                int(size_result.size),
                int(size_result.requested_size),
                size_result.capped_by or "none",
                size_result.reasoning,
            )

            if size_result.size == 0:
                result = ExecutionResult(
                    success=False,
                    reason=(
                        "Insufficient capital for position"
                        if size_result.capped_by is None
                        else f"Position size below one share (capped_by={size_result.capped_by})"
                    ),
                    capped_by=size_result.capped_by,
                )
                if self._on_execution:
                    self._on_execution(result)
                return result

            if size_result.was_capped:
                # Worth a warning rather than a debug line: the trade is no
                # longer risking the configured percentage, so its R-multiple
                # will not match the strategy's assumptions.
                logger.warning(
                    f"Position for {symbol} reduced from "
                    f"{size_result.requested_size:.0f} to {size_result.size} shares "
                    f"by {size_result.capped_by}. {size_result.reasoning}"
                )

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Sizing error: {str(e)}",
            )
            if self._on_execution:
                self._on_execution(result)
            logger.error(f"Position sizing error: {e}")
            return result

        # Submit entry order
        try:
            response = await self.order_manager.submit_order(
                symbol=symbol,
                side=side,
                quantity=int(size_result.size),
                order_type=order_intent.order_type,
                price=order_intent.trigger_price,
                stop_price=(
                    order_intent.trigger_price
                    if order_intent.order_type == "stop"
                    else None
                ),
                cancel_after_seconds=order_intent.cancel_after_seconds,
                lifecycle_metadata={
                    "strategy_name": strategy_name,
                    "stop_price": stop_price,
                    "take_profit": take_profit,
                    "entry_cancel_after_seconds": order_intent.cancel_after_seconds,
                    "entry_expiry_bars": order_intent.expiry_bars,
                    "entry_signal_bar_timestamp": order_intent.signal_bar_timestamp,
                    "entry_bar_interval_seconds": order_intent.bar_interval_seconds,
                },
            )

            actual_filled = response.filled_qty or 0
            self._pending_entries[response.order_id] = {
                "symbol": symbol,
                "side": side,
                "stop_price": stop_price,
                "take_profit": take_profit,
                "strategy_name": strategy_name,
                "expiry_bars": order_intent.expiry_bars,
                "bars_elapsed": 0,
                "last_bar_timestamp": order_intent.signal_bar_timestamp,
                "quantity": int(size_result.size),
                "trigger_price": order_intent.trigger_price,
                "group": response.order_id,
                "capped_by": size_result.capped_by,
            }

            if actual_filled == 0:
                result = ExecutionResult(
                    success=False,
                    order_id=response.order_id,
                    reason=f"Entry order pending (status={response.status}): {response.order_id}",
                    pending=True,
                    capped_by=size_result.capped_by,
                )
                if self._on_execution:
                    self._on_execution(result)
                logger.warning(f"Order submitted but not filled: {response.order_id}")
                return result

            if actual_filled < int(size_result.size):
                logger.warning(
                    f"Partial fill: {symbol} {actual_filled}/{int(size_result.size)} shares "
                    f"@ {sizing_entry_price}. OrderManager will retry remaining "
                    f"{int(size_result.size) - actual_filled} shares."
                )

            result = ExecutionResult(
                success=True,
                order_id=response.order_id,
                reason=f"Order submitted: {response.order_id}",
                position_size=actual_filled,
                avg_price=response.avg_price,
                capped_by=size_result.capped_by,
            )

            was_open = symbol in self._open_trades
            self._open_trades[symbol] = result
            if not was_open:
                self.risk_manager.register_position()

            if self._on_execution:
                self._on_execution(result)

            logger.info(
                f"Trade executed: {symbol} {side} "
                f"{actual_filled} shares @ {sizing_entry_price}"
            )

            return result

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Order submission error: {str(e)}",
            )
            if self._on_execution:
                self._on_execution(result)
            logger.error(f"Order submission error: {e}")
            return result

    async def _broker_position_at_most(self, symbol: str, quantity: float) -> bool:
        """Poll briefly until the broker position is at most ``quantity``."""
        deadline = asyncio.get_running_loop().time() + self.position_agreement_timeout_s
        while True:
            position = await self.exchange.get_position(symbol)
            held = float(position.quantity) if position is not None else 0.0
            if held <= quantity + 1e-9:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _close_position(
        self,
        symbol: str,
        cancel_after_seconds: Optional[float] = None,
        exit_reason: Optional[str] = None,
    ) -> ExecutionResult:
        """Close an open position.

        Runs under the symbol's lock with the symbol marked as closing, so a
        stop re-protection can neither interleave with the close nor run
        after it on a now-flat position.
        """
        async with self._symbol_lock(symbol):
            self._closing.add(symbol)
            try:
                return await self._close_position_locked(
                    symbol, cancel_after_seconds=cancel_after_seconds, exit_reason=exit_reason
                )
            finally:
                self._closing.discard(symbol)

    async def _close_position_locked(
        self,
        symbol: str,
        cancel_after_seconds: Optional[float] = None,
        exit_reason: Optional[str] = None,
    ) -> ExecutionResult:
        # Check if position exists
        pending_close = self._pending_closes.get(symbol)
        if pending_close is not None:
            return ExecutionResult(
                success=False,
                order_id=pending_close,
                reason=f"Close order already pending for {symbol}: {pending_close}",
            )

        # A partially filled resting entry may still be working: cancel it
        # first, or it could reopen the position after the close. Its filled
        # shares are about to be closed, so no standalone stop is placed.
        working_entries = [
            order_id
            for order_id, context in self._pending_entries.items()
            if context["symbol"] == symbol and context.get("protective_order_id")
        ]
        for order_id in working_entries:
            current = await self.exchange.get_order(order_id)
            if current is not None and current.status == OrderStatus.FILLED:
                # The parent completed (perhaps during this close): its attached
                # stop is live at the broker and must be cancelled below,
                # or it would rest beside the market close.
                self.mark_entry_filled(order_id, symbol)
                self._pending_entries.pop(order_id, None)
                continue
            if current is not None and current.status == OrderStatus.CANCELLED:
                continue
            try:
                await self.order_manager.cancel_order(order_id)
            except Exception as exc:
                logger.error(
                    "Failed to cancel working entry %s before closing %s: %s",
                    order_id, symbol, exc, exc_info=True,
                )
                return ExecutionResult(
                    success=False,
                    reason=f"Working entry for {symbol} could not be cancelled; not closing",
                )
            self._pending_entries.pop(order_id, None)

        # A broker-side protective stop must be cancelled (and confirmed) before
        # a market close, or both could fill and flip the position. If the stop
        # filled, the broker closed the position: a broker position read can
        # lag that fill, so it must not be used to send a second exit.
        max_close_quantity: Optional[float] = None
        if symbol in self._active_protective:
            stop_id = self._active_protective[symbol]
            outcome = await self._cancel_protective_stop(symbol)
            if outcome == "error":
                return ExecutionResult(
                    success=False,
                    reason=f"Protective stop for {symbol} could not be cancelled; not closing",
                )
            if outcome == "filled":
                return ExecutionResult(
                    success=False,
                    reason=f"Position in {symbol} already closed by its protective stop",
                )
            stop_order = await self.exchange.get_order(stop_id)
            stop_filled = float(getattr(stop_order, "filled_qty", 0.0) or 0.0)
            if stop_order is not None and stop_filled > 0:
                # Partially filled stop: only its unfilled remainder is still
                # open. Size the close from fills, and send it only once the
                # broker position agrees, or a lagging read could flip it.
                max_close_quantity = max(float(stop_order.quantity) - stop_filled, 0.0)
                if max_close_quantity <= 0:
                    return ExecutionResult(
                        success=False,
                        reason=f"Position in {symbol} already closed by its protective stop",
                    )
                if not await self._broker_position_at_most(symbol, max_close_quantity):
                    await self._alert(
                        "Close aborted",
                        f"{symbol}: protective stop filled {stop_filled:.0f} shares but the "
                        f"broker position does not yet show at most {max_close_quantity:.0f} "
                        "open; not sending a market close.",
                    )
                    return ExecutionResult(
                        success=False,
                        reason=f"Broker position for {symbol} disagrees with stop fills; not closing",
                    )

        position = await self.exchange.get_position(symbol)
        if position is None or position.quantity == 0:
            return ExecutionResult(
                success=False,
                reason=f"No open position in {symbol}",
            )

        # Determine close side (opposite of position side)
        # NOTE: position.quantity is always positive — use position.side, not quantity
        close_side = "sell" if position.side == "long" else "buy"

        # Get current price
        close_price = getattr(position, "current_price", None)
        if not close_price and hasattr(self.exchange, "_prices"):
            close_price = self.exchange._prices.get(symbol)

        if not close_price:
            return ExecutionResult(
                success=False,
                reason="No price available for close",
            )

        # Size the close from today's fills too: the broker position can lag a
        # stop fill whose callback already cleared the protective stop.
        fill_quantity = await self._fill_derived_quantity(symbol)
        if fill_quantity is not None:
            fill_long = fill_quantity > 0
            if abs(fill_quantity) < 1:
                return ExecutionResult(
                    success=False,
                    reason=f"Position in {symbol} is flat by today's fills; not closing",
                )
            if fill_long != (position.side == "long"):
                await self._alert(
                    "Close aborted",
                    f"{symbol}: today's fills show {fill_quantity:+.0f} shares but the broker "
                    f"position is {position.side}; not sending a market close.",
                )
                return ExecutionResult(
                    success=False,
                    reason=f"Broker position for {symbol} disagrees with today's fills; not closing",
                )
            max_close_quantity = (
                abs(fill_quantity)
                if max_close_quantity is None
                else min(max_close_quantity, abs(fill_quantity))
            )

        # Submit close order
        try:
            quantity = abs(int(position.quantity))
            if max_close_quantity is not None:
                quantity = min(quantity, int(max_close_quantity))
            response = await self.order_manager.submit_order(
                symbol=symbol,
                side=close_side,
                quantity=quantity,
                order_type="market",
                price=close_price,
                cancel_after_seconds=cancel_after_seconds,
                lifecycle_metadata=(
                    {
                        "exit_reason": exit_reason,
                        "order_role": "eod" if exit_reason == "time_exit" else "exit",
                    }
                    if exit_reason
                    else None
                ),
            )

            actual_filled = response.filled_qty or 0
            self._pending_closes[symbol] = response.order_id

            if actual_filled == 0:
                return ExecutionResult(
                    success=False,
                    order_id=response.order_id,
                    reason=f"Close order not filled (status={response.status}): {response.order_id}",
                    position_size=0,
                    avg_price=response.avg_price,
                )

            if actual_filled < quantity:
                logger.warning(
                    f"Partial close fill: {symbol} {actual_filled}/{quantity} shares. "
                    f"OrderManager will continue monitoring {response.order_id}."
                )

            result = ExecutionResult(
                success=True,
                order_id=response.order_id,
                reason=f"Close fill received: {response.order_id}",
                position_size=actual_filled,
                avg_price=response.avg_price,
            )

            remaining_position = await self.exchange.get_position(symbol)
            remaining_quantity = (
                float(remaining_position.quantity)
                if remaining_position is not None
                else 0.0
            )
            result.remaining_position_size = remaining_quantity
            result.fully_closed = remaining_quantity <= 0

            if result.fully_closed:
                self._pending_closes.pop(symbol, None)
                self._open_trades.pop(symbol, None)
                self.risk_manager.close_position()
                result.reason = f"Position closed: {response.order_id}"
                logger.info(f"Position closed: {symbol}")
            else:
                self._open_trades[symbol] = ExecutionResult(
                    success=True,
                    order_id=response.order_id,
                    reason="Position remains open after partial close",
                    position_size=remaining_quantity,
                    avg_price=remaining_position.entry_price,
                    remaining_position_size=remaining_quantity,
                )
                result.reason = (
                    f"Position partially closed: {actual_filled} filled, "
                    f"{remaining_quantity} remaining"
                )
                logger.info(
                    "Position partially closed: %s filled=%s remaining=%s",
                    symbol,
                    actual_filled,
                    remaining_quantity,
                )

            return result

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Close order error: {str(e)}",
            )
            logger.error(f"Close order error: {e}")
            return result

    def get_open_trades(self) -> Dict[str, ExecutionResult]:
        """
        Get all open trades.

        Returns:
            Dictionary of symbol -> ExecutionResult
        """
        return self._open_trades.copy()

    def get_pending_entry(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Return entry context retained while a submitted order may still fill."""
        context = self._pending_entries.get(order_id)
        return dict(context) if context is not None else None

    def has_pending_entry(self, symbol: str) -> bool:
        """Return whether a non-terminal entry order exists for the symbol."""
        return any(
            context["symbol"] == symbol
            for context in self._pending_entries.values()
        )

    def pending_exposures(
        self,
        positions: Optional[Dict[str, Position]] = None,
    ) -> list[PendingExposure]:
        """Resting entries as account-level reservations.

        ``positions`` is the positions snapshot the caller will use alongside
        these reservations. A fill is subtracted only when that snapshot
        already holds its symbol; otherwise the fill may have landed after the
        snapshot was taken, and dropping it would count the shares nowhere.
        The full quantity stays reserved (and the symbol occupied) until the
        snapshot shows the position.
        """
        held = set(positions or {})
        exposures = []
        for order_id, context in self._pending_entries.items():
            quantity = float(context.get("quantity") or 0.0)
            managed = self.order_manager.get_order(order_id)
            if managed is not None and context["symbol"] in held:
                quantity -= float(managed.filled_qty or 0.0)
            price = float(context.get("trigger_price") or 0.0)
            if quantity <= 0 or price <= 0:
                continue
            exposures.append(
                PendingExposure(
                    symbol=context["symbol"],
                    quantity=quantity,
                    price=price,
                    group=str(context.get("group") or order_id),
                )
            )
        return exposures

    async def account_positions(self) -> Dict[str, Position]:
        """Every open broker position of the account, keyed by symbol."""
        list_positions = getattr(self.exchange, "list_positions", None)
        if list_positions is None:
            raise RuntimeError(
                f"{type(self.exchange).__name__} cannot list account positions; "
                "account-level risk checks require it"
            )
        return {
            position.symbol: position
            for position in await list_positions()
            if position.quantity != 0
        }

    async def cancel_all_pending_entries(self) -> list[str]:
        """Cancel every resting entry order; returns the cancelled order ids."""
        cancelled: list[str] = []
        for order_id in list(self._pending_entries):
            try:
                if await self.order_manager.cancel_order(order_id):
                    cancelled.append(order_id)
                    self._pending_entries.pop(order_id, None)
            except Exception as exc:
                logger.error(
                    "Failed to cancel pending entry %s: %s", order_id, exc, exc_info=True
                )
        return cancelled

    def restore_pending_entries(self, orders: list[Order]) -> int:
        """Restore entry context required to project fills after a restart.

        Resting-stop brackets are rebuilt from the broker's open orders: an
        open protective stop whose entry is still resting stays attached to
        that entry; one whose entry already filled protects the open position.
        Nothing is resubmitted.
        """
        restored = 0
        now = datetime.now().astimezone()
        open_entry_ids = {
            order.order_id for order in orders if order.order_role == "entry"
        }
        for order in orders:
            if order.order_role == "protective_stop":
                if order.parent_order_id and order.parent_order_id in open_entry_ids:
                    self._protective_by_entry[order.parent_order_id] = order.order_id
                else:
                    self._activate_protective(order.symbol, order.order_id)
        for order in orders:
            if order.order_role == "protective_stop":
                continue
            if order.order_type != "stop" or order.order_id in self._pending_entries:
                continue
            signal_bar_timestamp = order.entry_signal_bar_timestamp
            bars_elapsed = 0
            last_bar_timestamp = signal_bar_timestamp
            if (
                signal_bar_timestamp is not None
                and order.entry_bar_interval_seconds
                and order.entry_bar_interval_seconds > 0
            ):
                comparison_now = (
                    now.astimezone(signal_bar_timestamp.tzinfo)
                    if signal_bar_timestamp.tzinfo is not None
                    else now.replace(tzinfo=None)
                )
                bars_elapsed = max(
                    0,
                    int(
                        (comparison_now - signal_bar_timestamp).total_seconds()
                        // order.entry_bar_interval_seconds
                    ),
                )
                last_bar_timestamp = signal_bar_timestamp + (
                    bars_elapsed
                    * timedelta(seconds=order.entry_bar_interval_seconds)
                )
            self._pending_entries[order.order_id] = {
                "symbol": order.symbol,
                "side": order.side,
                "stop_price": order.strategy_stop_price,
                "take_profit": order.take_profit,
                "strategy_name": order.strategy_name,
                "expiry_bars": order.entry_expiry_bars,
                "bars_elapsed": bars_elapsed,
                "last_bar_timestamp": last_bar_timestamp,
                "quantity": float(order.quantity),
                "trigger_price": float(order.stop_price or order.price),
                "group": order.oca_group or order.order_id,
                "protective_order_id": self._protective_by_entry.get(order.order_id),
                "gap_fill": order.gap_fill,
            }
            restored += 1
        return restored

    async def advance_pending_entry_bar(
        self,
        symbol: str,
        bar_timestamp: datetime,
    ) -> list[str]:
        """Cancel bar-expiring entries after the configured completed-bar count."""
        cancelled: list[str] = []
        for order_id, context in list(self._pending_entries.items()):
            if context["symbol"] != symbol or context.get("expiry_bars") is None:
                continue
            last_bar_timestamp = context.get("last_bar_timestamp")
            if last_bar_timestamp is not None and bar_timestamp <= last_bar_timestamp:
                continue
            context["last_bar_timestamp"] = bar_timestamp
            context["bars_elapsed"] = int(context.get("bars_elapsed", 0)) + 1
            if context["bars_elapsed"] < int(context["expiry_bars"]):
                continue
            if await self.order_manager.cancel_order(order_id):
                cancelled.append(order_id)
                self._pending_entries.pop(order_id, None)
                logger.info(
                    "Pending entry expired after %s bars: %s %s",
                    context["expiry_bars"],
                    symbol,
                    order_id,
                )
        return cancelled

    async def cancel_restored_expired_entries(self) -> list[str]:
        """Cancel restored bar-expiring entries whose original window elapsed."""
        cancelled: list[str] = []
        for order_id, context in list(self._pending_entries.items()):
            expiry_bars = context.get("expiry_bars")
            if expiry_bars is None or context.get("bars_elapsed", 0) < expiry_bars:
                continue
            if await self.order_manager.cancel_order(order_id):
                cancelled.append(order_id)
                self._pending_entries.pop(order_id, None)
        return cancelled

    def clear_pending_entry(self, order_id: str) -> None:
        """Forget entry context after its first fill is projected or cancellation."""
        self._pending_entries.pop(order_id, None)

    def has_pending_close(self, symbol: str) -> bool:
        """Whether a close order for ``symbol`` is already in flight."""
        return symbol in self._pending_closes

    def clear_pending_close(
        self,
        *,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        """Clear a logical close after it finishes or its replacement cancels."""
        if symbol is not None:
            self._pending_closes.pop(symbol, None)
            return
        for pending_symbol, pending_order_id in list(self._pending_closes.items()):
            if pending_order_id == order_id:
                self._pending_closes.pop(pending_symbol, None)

    async def refresh_open_trade(self, symbol: str, order_id: Optional[str] = None) -> None:
        """Refresh cumulative quantity and weighted entry from broker position state."""
        position = await self.exchange.get_position(symbol)
        if position is None or position.quantity <= 0:
            if symbol in self._open_trades:
                self._open_trades.pop(symbol, None)
                self.risk_manager.close_position()
            return
        previous = self._open_trades.get(symbol)
        self._open_trades[symbol] = ExecutionResult(
            success=True,
            order_id=order_id or (previous.order_id if previous else None),
            reason="Open trade synchronized from broker fills",
            position_size=float(position.quantity),
            avg_price=float(position.entry_price),
            remaining_position_size=float(position.quantity),
        )
        if previous is None:
            self.risk_manager.register_position()

    async def get_realized_pnl(self) -> float:
        """
        Calculate realized P&L from closed trades.

        Returns:
            Total realized P&L
        """
        # This would need integration with trade store
        # For now, return 0
        return 0.0
