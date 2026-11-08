"""Account-level exposure and sizing shared by live trading and backtests.

Both paths must size a trade identically, so the arithmetic lives here and the
callers only gather inputs (equity, open positions, pending entries).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class OpenExposure:
    """One open position as seen by the account-level rules."""

    symbol: str
    quantity: float
    entry_price: float

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.entry_price


@dataclass(frozen=True)
class PendingExposure:
    """One resting entry order that could still open a position.

    Orders that share ``group`` (an OCA pair) can fill at most one side, so the
    group reserves the largest notional among its members once.
    """

    symbol: str
    quantity: float
    price: float
    group: str

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.price


def gross_open_notional(positions: Iterable[OpenExposure]) -> float:
    """Sum of |qty| x entry price over open positions, long and short."""
    return sum(position.notional for position in positions)


def reserved_pending_notional(pending: Iterable[PendingExposure]) -> float:
    """Notional reserved by resting entries, counting each OCA group once."""
    by_group: dict[str, float] = {}
    for item in pending:
        by_group[item.group] = max(by_group.get(item.group, 0.0), item.notional)
    return sum(by_group.values())


def equity_headroom(
    *,
    current_equity: float,
    max_gross_leverage: float,
    positions: Iterable[OpenExposure] = (),
    pending: Iterable[PendingExposure] = (),
) -> float:
    """Notional still available before gross exposure exceeds the leverage cap."""
    return (
        max_gross_leverage * current_equity
        - gross_open_notional(positions)
        - reserved_pending_notional(pending)
    )


def open_and_pending_symbols(
    positions: Iterable[OpenExposure],
    pending: Iterable[PendingExposure],
) -> set[str]:
    """Symbols that hold a position or a resting entry (one slot each)."""
    return {item.symbol for item in positions} | {item.symbol for item in pending}


def net_liquidation(account: object) -> Optional[float]:
    """The broker's NetLiquidation, or None when it is missing or not positive.

    Only the unmodified broker value (``broker_equity``) is used:
    ``AccountState.equity`` is clamped to 0.0 when NetLiquidation is missing,
    and reading that as a real account value would look like a total loss.
    """
    value = getattr(account, "broker_equity", None)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def exposures_from_mapping(
    positions: Mapping[str, object],
) -> list[OpenExposure]:
    """Adapt ``{symbol: Position-like}`` (``quantity``, ``entry_price``)."""
    return [
        OpenExposure(
            symbol=symbol,
            quantity=float(getattr(position, "quantity")),
            entry_price=float(getattr(position, "entry_price")),
        )
        for symbol, position in positions.items()
        if float(getattr(position, "quantity", 0.0) or 0.0) != 0.0
    ]


CAPPED_BY_MAX_SHARES = "max_shares"
CAPPED_BY_SYMBOL_EXPOSURE = "max_symbol_exposure"
CAPPED_BY_EQUITY_HEADROOM = "equity_headroom"
CAPPED_BY_BROKER_FUNDS = "broker_available_funds"

# Float noise must not turn an exact 50.0 into 49 shares on one side of the
# live/backtest comparison and 50 on the other.
_FLOOR_EPSILON = 1e-9


def _floor(value: float) -> int:
    return int(math.floor(value + _FLOOR_EPSILON))


@dataclass(frozen=True)
class PortfolioSizingDecision:
    """Result of the shared sizing rule, with every intermediate quantity."""

    quantity: int
    risk_qty: int
    cap_qty: int
    room_qty: int
    broker_qty: Optional[int]
    max_shares_qty: Optional[int]
    capped_by: Optional[str]
    """Which limit reduced ``quantity`` below ``risk_qty``; ``None`` when risk bound."""
    sizing_equity: float
    current_equity: float
    headroom: float
    risk_amount: float

    @property
    def was_capped(self) -> bool:
        return self.capped_by is not None

    def describe(self) -> str:
        broker = "n/a" if self.broker_qty is None else str(self.broker_qty)
        return (
            f"qty={self.quantity} risk_qty={self.risk_qty} cap_qty={self.cap_qty} "
            f"room_qty={self.room_qty} broker_qty={broker} "
            f"capped_by={self.capped_by or 'none'} E={self.sizing_equity:,.2f} "
            f"NLV={self.current_equity:,.2f} headroom={self.headroom:,.2f}"
        )


def size_portfolio_entry(
    *,
    entry_price: float,
    stop_price: float,
    sizing_equity: float,
    risk_pct: float,
    max_symbol_exposure_pct: float,
    max_gross_leverage: float,
    current_equity: float,
    gross_open_notional: float = 0.0,
    reserved_pending_notional: float = 0.0,
    broker_available_funds: Optional[float] = None,
    max_shares: Optional[int] = None,
) -> PortfolioSizingDecision:
    """The account-level sizing rule, identical for live trading and backtests::

        risk_qty   = floor(risk_pct * E / |entry - stop|)
        cap_qty    = floor(max_symbol_exposure_pct * E / entry)
        headroom   = NLV_now - gross_open_notional - reserved_pending_notional
        room_qty   = floor(max_gross_leverage * headroom / entry)
        broker_qty = floor(AvailableFunds / entry)             # live-only
        qty        = min(risk_qty, cap_qty, room_qty, broker_qty)

    ``E`` is ``sizing_equity`` (the session-start snapshot or current NLV per
    ``sizing_equity_basis``). ``headroom`` is computed against the leverage
    cap, i.e. ``max_gross_leverage * NLV_now - gross - reserved``, so with the
    default leverage of 1.0 it is exactly the plan's formula. A quantity
    below one share means skip the entry.
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if stop_price <= 0:
        raise ValueError("stop_price must be positive")
    stop_distance = abs(entry_price - stop_price)
    if stop_distance == 0:
        raise ValueError("stop_price cannot equal entry_price")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct must be in (0, 1]")

    risk_amount = risk_pct * max(sizing_equity, 0.0)
    risk_qty = max(_floor(risk_amount / stop_distance), 0)
    cap_qty = max(_floor(max_symbol_exposure_pct * max(sizing_equity, 0.0) / entry_price), 0)
    headroom = (
        max_gross_leverage * current_equity
        - gross_open_notional
        - reserved_pending_notional
    )
    room_qty = max(_floor(headroom / entry_price), 0)
    broker_qty = (
        max(_floor(broker_available_funds / entry_price), 0)
        if broker_available_funds is not None
        else None
    )
    max_shares_qty = int(max_shares) if max_shares is not None else None

    quantity = risk_qty
    capped_by: Optional[str] = None
    # Evaluated in a fixed order so a tie always reports the same reason.
    for name, limit in (
        (CAPPED_BY_MAX_SHARES, max_shares_qty),
        (CAPPED_BY_SYMBOL_EXPOSURE, cap_qty),
        (CAPPED_BY_EQUITY_HEADROOM, room_qty),
        (CAPPED_BY_BROKER_FUNDS, broker_qty),
    ):
        if limit is not None and limit < quantity:
            quantity = limit
            capped_by = name

    return PortfolioSizingDecision(
        quantity=max(quantity, 0),
        risk_qty=risk_qty,
        cap_qty=cap_qty,
        room_qty=room_qty,
        broker_qty=broker_qty,
        max_shares_qty=max_shares_qty,
        capped_by=capped_by,
        sizing_equity=sizing_equity,
        current_equity=current_equity,
        headroom=headroom,
        risk_amount=risk_amount,
    )


__all__ = [
    "CAPPED_BY_BROKER_FUNDS",
    "CAPPED_BY_EQUITY_HEADROOM",
    "CAPPED_BY_MAX_SHARES",
    "CAPPED_BY_SYMBOL_EXPOSURE",
    "OpenExposure",
    "PendingExposure",
    "PortfolioSizingDecision",
    "equity_headroom",
    "exposures_from_mapping",
    "gross_open_notional",
    "net_liquidation",
    "open_and_pending_symbols",
    "reserved_pending_notional",
    "size_portfolio_entry",
]
