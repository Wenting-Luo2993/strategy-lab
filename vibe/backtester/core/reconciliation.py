"""Accounting reconciliation for a completed backtest.

The point of these checks is to catch an accounting error *by contradiction*
rather than by inspection. Every defect this module is designed to find was
originally found by a human noticing an odd number in a diff -- the commission
reserve bug (``min_cash`` at -$2.24) surfaced that way, and that was luck. An
identity that fails loudly does not depend on anyone looking.

Each identity is computed from facts recorded **independently** of the code
that produced them. That constraint is what makes them worth running. The plan
originally proposed ``equity == cash + sum(mark_to_market)`` and it was rejected
as tautological, because equity is *defined* that way -- the check could never
fail, so it asserted nothing. The same trap applies here: recording a cash
delta by evaluating the same expression the portfolio used would produce a test
that passes by construction. So the ledger records **observed** cash before and
after each fill, and reconciliation recomputes the expectation from the fill's
own quantity, price, and commission.

Three identities, matching section 7 of the implementation plan:

``flat_at_end_equity``
    ``equity_final - initial_capital == sum(trade.pnl) - total_costs``.
    Only meaningful when no positions are open, since an open position holds
    unrealized P&L that no closed trade accounts for. Reported as inapplicable
    rather than failed when positions remain.

``per_fill_cash_delta``
    Each fill moved cash by exactly ``+/- quantity * price`` less its
    commission. Catches a missing debit, a double charge, a sign error, or a
    commission billed to the wrong side.

``entry_exit_quantity_parity``
    Per symbol, ``entered - exited == still_open``. Stated with the open
    quantity on the right rather than asserting ``entered == exited``, so it
    holds mid-run and not only when flat. Catches a duplicated fill and a
    partial exit that fails to reduce the position.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

# Cash is order 1e5-1e6 and the flat-at-end identity accumulates over every
# trade in the run, so a pure relative tolerance is too tight at that scale
# while a pure absolute one is too loose per fill. Both are applied, and the
# looser of the two wins, which is what math.isclose already does.
_REL_TOL = 1e-9
_ABS_TOL = 1e-6


@dataclass(frozen=True)
class CashLedgerEntry:
    """One observed cash movement, recorded at the moment it happened.

    ``cash_before`` and ``cash_after`` are snapshots, not derivations. They are
    the whole reason this ledger exists: reconciliation compares them against a
    value recomputed from ``quantity``, ``price``, and ``commission``, and a
    disagreement means the portfolio moved cash by an amount its own fill does
    not justify.
    """

    timestamp: datetime
    symbol: str
    side: str
    """``buy`` or ``sell`` -- the direction of *this fill*, not of the position.

    Closing a long is a ``sell`` fill, so the sign convention below is keyed on
    the fill rather than on whether the position was opened or closed.
    """
    quantity: float
    price: float
    commission: float
    cash_before: float
    cash_after: float
    kind: str
    """``open``, ``add``, or ``close`` -- which portfolio operation ran."""

    @property
    def observed_delta(self) -> float:
        return self.cash_after - self.cash_before

    @property
    def expected_delta(self) -> float:
        """What the fill should have moved, from the fill's own facts.

        A buy pays out the notional; a sell takes it in. Commission is an
        outflow in both directions -- it is charged for the privilege of
        trading, not for the direction of the trade.
        """
        sign = 1.0 if self.side == "sell" else -1.0
        return sign * self.quantity * self.price - self.commission


@dataclass(frozen=True)
class ReconciliationFinding:
    """Outcome of one identity.

    ``applicable`` is distinct from ``passed`` on purpose. An identity that
    could not be evaluated -- flat-at-end equity while a position is open --
    is not a pass, and silently counting it as one would let a run look
    reconciled when the strongest check never ran.
    """

    identity: str
    passed: bool
    detail: str
    residual: Optional[float] = None
    applicable: bool = True

    @property
    def failed(self) -> bool:
        return self.applicable and not self.passed


@dataclass
class ReconciliationReport:
    findings: List[ReconciliationFinding] = field(default_factory=list)

    @property
    def failures(self) -> List[ReconciliationFinding]:
        return [f for f in self.findings if f.failed]

    @property
    def ok(self) -> bool:
        return not self.failures

    def raise_if_failed(self) -> None:
        if self.ok:
            return
        lines = "\n".join(f"  - {f.identity}: {f.detail}" for f in self.failures)
        raise AccountingError(
            f"{len(self.failures)} accounting identity failure(s):\n{lines}"
        )


class AccountingError(AssertionError):
    """The books do not balance.

    Deliberately an ``AssertionError``: a reconciliation failure means a
    computed result is arithmetically wrong, not that a caller passed bad
    input. There is no way to handle it other than to discard the run.
    """


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def check_flat_at_end_equity(
    *,
    equity_final: float,
    initial_capital: float,
    trade_pnls: List[float],
    total_costs: float,
    open_position_count: int,
) -> ReconciliationFinding:
    """``equity_final - initial_capital == sum(trade.pnl) - total_costs``.

    ``Trade.pnl`` is gross of costs -- it is recomputed from entry and exit
    prices alone -- so the commission has to be subtracted separately here.
    Exit slippage is *not* subtracted, because it is embedded in the exit price
    and has therefore already reduced every ``pnl`` in the sum. Subtracting it
    again would make this identity fail on a correct run.
    """
    if open_position_count:
        return ReconciliationFinding(
            identity="flat_at_end_equity",
            passed=False,
            applicable=False,
            detail=(
                f"{open_position_count} position(s) still open; realized trades "
                "cannot account for unrealized P&L"
            ),
        )

    observed = equity_final - initial_capital
    expected = sum(trade_pnls) - total_costs
    residual = observed - expected
    passed = _close(observed, expected)
    return ReconciliationFinding(
        identity="flat_at_end_equity",
        passed=passed,
        residual=residual,
        detail=(
            f"equity moved {observed:,.6f} while trades net of costs explain "
            f"{expected:,.6f} (residual {residual:,.6f})"
        ),
    )


def check_per_fill_cash_delta(
    entries: List[CashLedgerEntry],
) -> ReconciliationFinding:
    """Every fill moved cash by its own notional, less commission."""
    bad: List[tuple[CashLedgerEntry, float]] = []
    worst = 0.0
    for entry in entries:
        residual = entry.observed_delta - entry.expected_delta
        if abs(residual) > abs(worst):
            worst = residual
        if not _close(entry.observed_delta, entry.expected_delta):
            bad.append((entry, residual))

    if not bad:
        return ReconciliationFinding(
            identity="per_fill_cash_delta",
            passed=True,
            residual=worst,
            detail=(
                f"all {len(entries)} fill(s) reconcile "
                f"(largest residual {worst:,.9f})"
            ),
        )

    first, first_residual = bad[0]
    return ReconciliationFinding(
        identity="per_fill_cash_delta",
        passed=False,
        residual=first_residual,
        detail=(
            f"{len(bad)} of {len(entries)} fill(s) moved cash by an unexplained "
            f"amount; first at {first.timestamp} on {first.symbol} "
            f"({first.kind}/{first.side} {first.quantity} @ {first.price}): "
            f"moved {first.observed_delta:,.6f}, expected "
            f"{first.expected_delta:,.6f}"
        ),
    )


def check_entry_exit_quantity_parity(
    entries: List[CashLedgerEntry],
    open_quantities: Dict[str, float],
) -> ReconciliationFinding:
    """Per symbol, shares entered less shares exited equals shares still held.

    Stated against the open quantity rather than as ``entered == exited`` so it
    is checkable at any point in a run, not only at the end. A run that is flat
    reduces to the simpler form on its own, because the right-hand side is zero.
    """
    entered: Dict[str, float] = defaultdict(float)
    exited: Dict[str, float] = defaultdict(float)
    for entry in entries:
        if entry.kind in ("open", "add"):
            entered[entry.symbol] += entry.quantity
        else:
            exited[entry.symbol] += entry.quantity

    symbols = set(entered) | set(exited) | set(open_quantities)
    mismatches = []
    worst = 0.0
    for symbol in sorted(symbols):
        still_open = open_quantities.get(symbol, 0.0)
        residual = entered[symbol] - exited[symbol] - still_open
        if abs(residual) > abs(worst):
            worst = residual
        if not _close(entered[symbol] - exited[symbol], still_open):
            mismatches.append(
                f"{symbol}: entered {entered[symbol]:,.6f}, exited "
                f"{exited[symbol]:,.6f}, open {still_open:,.6f} "
                f"(residual {residual:,.6f})"
            )

    if not mismatches:
        return ReconciliationFinding(
            identity="entry_exit_quantity_parity",
            passed=True,
            residual=worst,
            detail=f"{len(symbols)} symbol(s) reconcile",
        )
    return ReconciliationFinding(
        identity="entry_exit_quantity_parity",
        passed=False,
        residual=worst,
        detail="; ".join(mismatches),
    )


def reconcile_portfolio(portfolio) -> ReconciliationReport:
    """Run all three identities against a finished ``PortfolioManager``.

    Takes the portfolio rather than a ``BacktestResult`` because two of the
    three identities need the cash ledger and the open-position map, neither of
    which survives into the result object.
    """
    equity_final = (
        portfolio.equity_curve[-1][1]
        if portfolio.equity_curve
        else portfolio.cash
    )
    open_quantities = {
        symbol: pos.quantity for symbol, pos in portfolio.positions.items()
    }
    entries = portfolio.cash_ledger

    return ReconciliationReport(
        findings=[
            check_flat_at_end_equity(
                equity_final=equity_final,
                initial_capital=portfolio.initial_capital,
                trade_pnls=[t.pnl or 0.0 for t in portfolio.trade_history],
                total_costs=portfolio.total_costs,
                open_position_count=len(portfolio.positions),
            ),
            check_per_fill_cash_delta(entries),
            check_entry_exit_quantity_parity(entries, open_quantities),
        ]
    )
