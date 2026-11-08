"""Account-level risk management for live trading with a ``portfolio:`` ruleset.

The manager owns the per-session risk state:

- the session-start equity snapshot E0 (IBKR NetLiquidation at warm-up), which
  is the basis for risk and per-position caps for the whole session;
- the halt flag set by the daily loss limit or a failed snapshot.

Both are persisted per session date so a mid-session restart reuses the same
E0 and stays halted instead of re-basing on intraday equity.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from vibe.common.models import Position
from vibe.common.risk import PositionSizer
from vibe.common.risk.portfolio_sizing import (
    OpenExposure,
    PendingExposure,
    PortfolioSizingDecision,
    equity_headroom,
    gross_open_notional,
    open_and_pending_symbols,
    reserved_pending_notional,
    size_portfolio_entry,
)
from vibe.common.ruleset.models import PortfolioConfig
from vibe.trading_bot.execution.trade_executor import (
    RiskCheckResult,
    SimpleRiskManager,
)

logger = logging.getLogger(__name__)

SNAPSHOT_MISSING = "session_equity_snapshot_missing"
SNAPSHOT_FAILED = "session_equity_snapshot_failed"
DAILY_LOSS_LIMIT = "daily_loss_limit"


@dataclass(frozen=True)
class SessionEquitySnapshot:
    """Account equity captured once per session at warm-up."""

    session_date: date
    equity: float
    taken_at: datetime
    account_id: Optional[str] = None


@dataclass(frozen=True)
class DailyLossStatus:
    """Outcome of one daily-loss evaluation."""

    triggered: bool
    pnl: float
    limit: float
    session_equity: float
    current_equity: float


class PortfolioRiskManager(SimpleRiskManager):
    """Risk checks across every symbol of the account.

    Unlike ``SimpleRiskManager`` the caller must pass *all* open positions and
    resting entries, so the open-position cap and the equity headroom are
    evaluated for the account rather than for one symbol.
    """

    account_level = True

    def __init__(
        self,
        config: PortfolioConfig,
        position_sizer: PositionSizer,
        state_path: Optional[Path] = None,
    ) -> None:
        super().__init__(
            position_sizer=position_sizer,
            max_positions=config.max_open_positions or 10**9,
        )
        self.config = config
        self.state_path = Path(state_path) if state_path is not None else None
        self.session_date: Optional[date] = None
        self.snapshot: Optional[SessionEquitySnapshot] = None
        self.snapshot_error: Optional[str] = None
        self.halted_reason: Optional[str] = None
        self.halt_detail: Optional[str] = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def begin_session(self, session_date: date) -> Optional[SessionEquitySnapshot]:
        """Start (or resume) ``session_date``; returns a persisted snapshot if any.

        A new date clears the previous session's halt, which is how the daily
        loss limit resets at the next session.
        """
        if self.session_date != session_date:
            self.session_date = session_date
            self.snapshot = None
            self.snapshot_error = None
            self.halted_reason = None
            self.halt_detail = None
            self._load_state(session_date)
        return self.snapshot

    def record_snapshot(self, snapshot: SessionEquitySnapshot) -> None:
        if snapshot.equity <= 0:
            raise ValueError("session equity snapshot must be positive")
        self.begin_session(snapshot.session_date)
        self.snapshot = snapshot
        self.snapshot_error = None
        if self.halted_reason == SNAPSHOT_FAILED:
            self.halted_reason = None
            self.halt_detail = None
        self._save_state()

    def record_snapshot_failure(self, session_date: date, error: str) -> None:
        self.begin_session(session_date)
        self.snapshot_error = error
        if self.snapshot is None:
            self.halted_reason = SNAPSHOT_FAILED
            self.halt_detail = error
        self._save_state()

    def halt(self, reason: str, detail: Optional[str] = None) -> None:
        """Block new entries until the next session."""
        self.halted_reason = reason
        self.halt_detail = detail
        self._save_state()

    @property
    def session_equity(self) -> Optional[float]:
        return self.snapshot.equity if self.snapshot is not None else None

    def entries_blocked_reason(self) -> Optional[str]:
        if self.halted_reason is not None:
            return self.halted_reason
        if (
            self.config.sizing_equity_basis == "session_start"
            or self.config.daily_loss_limit_pct is not None
        ) and self.snapshot is None:
            return SNAPSHOT_MISSING
        return None

    def sizing_equity(self, current_equity: float) -> float:
        """Equity used for risk-per-trade and the per-position cap."""
        if self.config.sizing_equity_basis == "current":
            return current_equity
        if self.snapshot is None:
            raise RuntimeError("session-start equity snapshot is not available")
        return self.snapshot.equity

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def pre_trade_check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        current_account_value: float,
        existing_positions: Dict[str, Position],
        pending_entries: Iterable[PendingExposure] = (),
        exclude_pending_group: Optional[str] = None,
    ) -> RiskCheckResult:
        """Account-level checks before sizing a new entry.

        ``existing_positions`` must hold every open position of the account
        and ``pending_entries`` every resting entry order. The entry being
        placed may already be represented in ``pending_entries`` (the first
        side of an OCA pair); pass its group as ``exclude_pending_group``.
        """
        blocked = self.entries_blocked_reason()
        if blocked is not None:
            return RiskCheckResult(passed=False, reason=f"Entries blocked: {blocked}")
        if quantity <= 0:
            return RiskCheckResult(passed=False, reason="Quantity must be positive")

        positions = [
            OpenExposure(
                symbol=held_symbol,
                quantity=float(position.quantity),
                entry_price=float(position.entry_price),
            )
            for held_symbol, position in existing_positions.items()
            if float(position.quantity or 0.0) != 0.0
        ]
        pending = [
            item for item in pending_entries if item.group != exclude_pending_group
        ]
        if any(position.symbol == symbol for position in positions):
            return RiskCheckResult(
                passed=False, reason=f"Position already exists in {symbol}"
            )
        if any(item.symbol == symbol for item in pending):
            return RiskCheckResult(
                passed=False, reason=f"Entry order already pending for {symbol}"
            )

        occupied = open_and_pending_symbols(positions, pending)
        limit = self.config.max_open_positions
        if limit is not None and len(occupied) >= limit:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"At maximum positions ({len(occupied)}/{limit}, "
                    "open positions plus pending entries)"
                ),
            )

        headroom = equity_headroom(
            current_equity=current_account_value,
            max_gross_leverage=self.config.max_gross_leverage,
            positions=positions,
            pending=pending,
        )
        if headroom < entry_price:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"No equity headroom: {headroom:,.2f} available for a "
                    f"{entry_price:,.2f} share"
                ),
            )
        return RiskCheckResult(passed=True, reason="Risk checks passed")

    def size_entry(
        self,
        *,
        entry_price: float,
        stop_price: float,
        current_equity: float,
        positions: Dict[str, Position],
        pending_entries: Iterable[PendingExposure] = (),
        broker_available_funds: Optional[float] = None,
        exclude_pending_group: Optional[str] = None,
    ) -> PortfolioSizingDecision:
        """Size one entry with the shared rule the backtester also uses."""
        risk_pct = self.position_sizer.risk_pct
        if risk_pct is None:
            raise ValueError("portfolio sizing requires a percentage risk (max_loss_pct)")
        exposures = [
            OpenExposure(held, float(p.quantity), float(p.entry_price))
            for held, p in positions.items()
            if float(p.quantity or 0.0) != 0.0
        ]
        pending = [p for p in pending_entries if p.group != exclude_pending_group]
        return size_portfolio_entry(
            entry_price=entry_price,
            stop_price=stop_price,
            sizing_equity=self.sizing_equity(current_equity),
            risk_pct=risk_pct,
            max_symbol_exposure_pct=self.config.max_symbol_exposure_pct,
            max_gross_leverage=self.config.max_gross_leverage,
            current_equity=current_equity,
            gross_open_notional=gross_open_notional(exposures),
            reserved_pending_notional=reserved_pending_notional(pending),
            broker_available_funds=broker_available_funds,
            max_shares=self.position_sizer.max_position_size,
        )

    def check_daily_loss(self, current_equity: float) -> DailyLossStatus:
        """Evaluate the daily loss limit against session-start equity.

        P&L is ``NLV_now - E0``: realized plus unrealized, net of commissions,
        for the session. Returns ``triggered=False`` when no limit or no
        snapshot is configured.
        """
        e0 = self.session_equity
        limit_pct = self.config.daily_loss_limit_pct
        if e0 is None or limit_pct is None:
            return DailyLossStatus(False, 0.0, 0.0, e0 or 0.0, current_equity)
        pnl = current_equity - e0
        limit = -limit_pct * e0
        return DailyLossStatus(pnl <= limit, pnl, limit, e0, current_equity)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self, session_date: date) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("Unreadable session risk state %s: %s", self.state_path, exc, exc_info=True)
            return
        if data.get("session_date") != session_date.isoformat():
            return
        if data.get("equity") is not None:
            self.snapshot = SessionEquitySnapshot(
                session_date=session_date,
                equity=float(data["equity"]),
                taken_at=datetime.fromisoformat(data["taken_at"]),
                account_id=data.get("account_id"),
            )
        self.snapshot_error = data.get("snapshot_error")
        self.halted_reason = data.get("halted_reason")
        self.halt_detail = data.get("halt_detail")

    def _save_state(self) -> None:
        if self.state_path is None or self.session_date is None:
            return
        data = {
            "session_date": self.session_date.isoformat(),
            "equity": self.snapshot.equity if self.snapshot else None,
            "taken_at": self.snapshot.taken_at.isoformat() if self.snapshot else None,
            "account_id": self.snapshot.account_id if self.snapshot else None,
            "snapshot_error": self.snapshot_error,
            "halted_reason": self.halted_reason,
            "halt_detail": self.halt_detail,
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.state_path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            logger.error("Failed to persist session risk state: %s", exc, exc_info=True)
