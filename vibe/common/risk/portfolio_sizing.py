"""Account-level exposure and sizing shared by live trading and backtests.

Both paths must size a trade identically, so the arithmetic lives here and the
callers only gather inputs (equity, open positions, pending entries).
"""

from __future__ import annotations

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


__all__ = [
    "OpenExposure",
    "PendingExposure",
    "equity_headroom",
    "exposures_from_mapping",
    "gross_open_notional",
    "open_and_pending_symbols",
    "reserved_pending_notional",
]
