"""Daily live-vs-backtest parity and entry-slippage report.

Paper trading measures execution, not edge, so every session is replayed
through the backtester's resting-stop path with the live ruleset and the
live session-start equity, and the two are diffed trade by trade:
taken/not taken, side, entry minute, entry price, quantity, exit reason and R.
Entry slippage is reported in ticks against the trigger level and compared
with the 2 ticks the backtest models.

Everything here is pure: callers supply executions, 1-minute bars and E0.
"""

from __future__ import annotations

import statistics
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from vibe.common.models import Trade
from vibe.common.ruleset.models import StrategyRuleSet

MARKET_TZ = ZoneInfo("America/New_York")
TICK = 0.01
MODELLED_ENTRY_SLIPPAGE_TICKS = 2

# Differences within these are execution noise, not a different trade. Entry
# price tolerance covers the measured slippage itself; a larger gap means the
# two paths filled at different prices for a reason worth explaining.
ENTRY_PRICE_TOLERANCE_TICKS = 10
R_TOLERANCE = 0.25

MISMATCH_KINDS = (
    "taken_live_only",
    "taken_backtest_only",
    "side",
    "entry_minute",
    "entry_price",
    "qty",
    "exit_reason",
    "r",
)

_EXIT_REASONS = {
    "protective_stop": "STOP",
    "stop_loss": "STOP",
    "stop": "STOP",
    "eod": "EOD",
    "time_exit": "EOD",
    "daily_loss_limit": "DAILY_LOSS_LIMIT",
}


def normalise_exit_reason(value: Optional[str]) -> Optional[str]:
    """Map live exit labels onto the backtester's vocabulary (STOP, EOD, ...)."""
    if value is None:
        return None
    return _EXIT_REASONS.get(str(value).lower(), str(value).upper())


def _r(side: str, entry: float, exit_: Optional[float], stop: Optional[float]) -> Optional[float]:
    if exit_ is None or stop is None or entry == stop:
        return None
    move = exit_ - entry if side == "buy" else entry - exit_
    return move / abs(entry - stop)


@dataclass(frozen=True)
class LiveTrade:
    """One live round trip, rebuilt from broker executions."""

    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    quantity: float
    stop_price: Optional[float]
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    trigger_price: Optional[float] = None
    gap_fill: Optional[bool] = None

    @property
    def entry_slippage_ticks(self) -> Optional[float]:
        """Signed ticks beyond the trigger (positive = worse than the trigger)."""
        if self.trigger_price is None:
            return None
        signed = (
            self.entry_price - self.trigger_price
            if self.side == "buy"
            else self.trigger_price - self.entry_price
        )
        return round(signed / TICK, 6)

    @property
    def r_multiple(self) -> Optional[float]:
        return _r(self.side, self.entry_price, self.exit_price, self.stop_price)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _vwap(items: list[Mapping[str, Any]]) -> tuple[float, float]:
    quantity = sum(float(item["quantity"]) for item in items)
    notional = sum(float(item["quantity"]) * float(item["price"]) for item in items)
    return quantity, (notional / quantity if quantity else 0.0)


def live_trades_from_executions(
    executions: Iterable[Mapping[str, Any]],
    session_date: date,
    tz: ZoneInfo = MARKET_TZ,
) -> list[LiveTrade]:
    """Rebuild the session's resting-stop round trips from durable executions.

    Uses the order metadata journalled at submission (``order_role``,
    ``benchmark_price`` = trigger, ``strategy_stop_price``, ``gap_fill``,
    ``exit_reason``). Symbols without an ``entry``-role execution are
    ignored, so unrelated orders do not create phantom trades.
    """
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for execution in executions:
        filled_at = _as_datetime(execution.get("filled_at"))
        if filled_at is None or filled_at.astimezone(tz).date() != session_date:
            continue
        by_symbol.setdefault(str(execution["symbol"]), []).append(execution)

    trades: list[LiveTrade] = []
    for symbol, items in sorted(by_symbol.items()):
        items = sorted(items, key=lambda item: _as_datetime(item["filled_at"]))
        entries = [
            item for item in items
            if (item.get("order_metadata") or {}).get("order_role") == "entry"
        ]
        if not entries:
            continue
        side = str(entries[0]["side"])
        entry_qty, entry_price = _vwap(entries)
        metadata = entries[0].get("order_metadata") or {}
        first_entry_at = _as_datetime(entries[0]["filled_at"])
        exits = [
            item for item in items
            if str(item["side"]) != side and _as_datetime(item["filled_at"]) >= first_entry_at
        ]
        exit_meta = (exits[-1].get("order_metadata") or {}) if exits else {}
        exit_role = exit_meta.get("order_role")
        exit_reason = normalise_exit_reason(
            "protective_stop"
            if exit_role == "protective_stop"
            else exit_meta.get("exit_reason") or exit_role
        ) if exits else None
        gap_fill = metadata.get("gap_fill")
        trades.append(
            LiveTrade(
                symbol=symbol,
                side=side,
                entry_time=first_entry_at,
                entry_price=entry_price,
                quantity=entry_qty,
                stop_price=metadata.get("strategy_stop_price"),
                exit_time=_as_datetime(exits[-1]["filled_at"]) if exits else None,
                exit_price=_vwap(exits)[1] if exits else None,
                exit_reason=exit_reason,
                trigger_price=metadata.get("benchmark_price"),
                gap_fill=None if gap_fill is None else bool(gap_fill),
            )
        )
    return trades


def backtest_session(
    ruleset: StrategyRuleSet,
    symbol: str,
    minutes: pd.DataFrame,
    session_equity: float,
    slippage_ticks: int = MODELLED_ENTRY_SLIPPAGE_TICKS,
) -> list[Trade]:
    """Replay one symbol-session through the backtester's resting-stop path.

    ``minutes`` are the session's 1-minute bars (tz-aware index, OHLCV). The
    engine starts from ``session_equity`` so its session-start equity, and
    therefore its sizing caps, are the live E0.
    """
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.backtester.core.execution_realism import ExecutionRealismConfig

    if minutes.empty:
        return []
    frame = minutes[["open", "high", "low", "close", "volume"]].astype(float).copy()
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(MARKET_TZ)
    frame = frame.tz_convert(MARKET_TZ).sort_index()
    frame.index.name = "ts_event"
    session = frame.index[0].date()
    with tempfile.TemporaryDirectory() as tmp:
        frame.to_parquet(Path(tmp) / f"{symbol}.parquet")
        engine = BacktestEngine(
            ruleset,
            data_dir=tmp,
            initial_capital=float(session_equity),
            slippage_ticks=slippage_ticks,
            execution_realism=ExecutionRealismConfig.realistic_resting_stop(),
        )
        result = engine.run(
            symbol,
            datetime(session.year, session.month, session.day, tzinfo=MARKET_TZ),
            datetime(session.year, session.month, session.day, 23, 59, tzinfo=MARKET_TZ),
        )
    return list(result.trades)


@dataclass
class SymbolParity:
    symbol: str
    matched: bool
    mismatches: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParityReport:
    session_date: date
    symbols: list[SymbolParity]
    entry_slippage_ticks: list[float]
    gap_fills: int
    modelled_slippage_ticks: int = MODELLED_ENTRY_SLIPPAGE_TICKS
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def traded(self) -> list[SymbolParity]:
        return [item for item in self.symbols if item.details.get("traded", True)]

    @property
    def matched(self) -> int:
        return sum(1 for item in self.traded if item.matched)

    @property
    def mismatched(self) -> list[SymbolParity]:
        return [item for item in self.symbols if not item.matched]

    @property
    def mean_slippage_ticks(self) -> Optional[float]:
        return statistics.fmean(self.entry_slippage_ticks) if self.entry_slippage_ticks else None

    @property
    def median_slippage_ticks(self) -> Optional[float]:
        return statistics.median(self.entry_slippage_ticks) if self.entry_slippage_ticks else None

    def mismatch_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.symbols:
            for kind in item.mismatches:
                counts[kind] = counts.get(kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "matched": self.matched,
            "mismatched": len(self.mismatched),
            "mismatch_counts": self.mismatch_counts(),
            "entry_slippage_ticks": self.entry_slippage_ticks,
            "mean_slippage_ticks": self.mean_slippage_ticks,
            "median_slippage_ticks": self.median_slippage_ticks,
            "modelled_slippage_ticks": self.modelled_slippage_ticks,
            "gap_fills": self.gap_fills,
            "errors": self.errors,
            "symbols": [asdict(item) for item in self.symbols],
        }


def _minute(value: datetime) -> datetime:
    return value.astimezone(MARKET_TZ).replace(second=0, microsecond=0)


def _backtest_stop(trade: Trade) -> Optional[float]:
    if not trade.initial_risk or not trade.quantity:
        return None
    per_share = trade.initial_risk / trade.quantity
    return trade.entry_price - per_share if trade.side == "buy" else trade.entry_price + per_share


def compare_symbol(
    symbol: str,
    live: Optional[LiveTrade],
    backtest: Optional[Trade],
) -> SymbolParity:
    """Diff one symbol's live trade against its backtest replay."""
    if live is None and backtest is None:
        return SymbolParity(symbol, True, details={"traded": False})
    if live is None:
        return SymbolParity(
            symbol, False, ["taken_backtest_only"],
            {
                "backtest_side": backtest.side,
                "backtest_entry_time": backtest.entry_time.isoformat(),
                "backtest_entry_price": backtest.entry_price,
            },
        )
    if backtest is None:
        return SymbolParity(
            symbol, False, ["taken_live_only"],
            {
                "live_side": live.side,
                "live_entry_time": live.entry_time.isoformat(),
                "live_entry_price": live.entry_price,
                "gap_fill": live.gap_fill,
            },
        )

    backtest_r = _r(backtest.side, backtest.entry_price, backtest.exit_price, _backtest_stop(backtest))
    live_r = live.r_multiple
    entry_diff_ticks = round((live.entry_price - backtest.entry_price) / TICK, 6)
    details = {
        "live_side": live.side,
        "backtest_side": backtest.side,
        "live_entry_minute": _minute(live.entry_time).isoformat(),
        "backtest_entry_minute": _minute(backtest.entry_time).isoformat(),
        "live_entry_price": live.entry_price,
        "backtest_entry_price": backtest.entry_price,
        "entry_price_diff_ticks": entry_diff_ticks,
        "live_qty": live.quantity,
        "backtest_qty": backtest.quantity,
        "live_exit_reason": live.exit_reason,
        "backtest_exit_reason": normalise_exit_reason(backtest.exit_reason),
        "live_r": live_r,
        "backtest_r": backtest_r,
        "entry_slippage_ticks": live.entry_slippage_ticks,
        "gap_fill": live.gap_fill,
    }
    mismatches: list[str] = []
    if live.side != backtest.side:
        mismatches.append("side")
    if _minute(live.entry_time) != _minute(backtest.entry_time):
        mismatches.append("entry_minute")
    if abs(entry_diff_ticks) > ENTRY_PRICE_TOLERANCE_TICKS:
        mismatches.append("entry_price")
    if int(round(live.quantity)) != int(round(backtest.quantity)):
        mismatches.append("qty")
    if live.exit_reason != normalise_exit_reason(backtest.exit_reason):
        mismatches.append("exit_reason")
    if live_r is None or backtest_r is None or abs(live_r - backtest_r) > R_TOLERANCE:
        mismatches.append("r")
    return SymbolParity(symbol, not mismatches, mismatches, details)


def build_report(
    session_date: date,
    symbols: Iterable[str],
    live_trades: Iterable[LiveTrade],
    backtest_trades: Mapping[str, list[Trade]],
    errors: Optional[Mapping[str, str]] = None,
) -> ParityReport:
    """Compare every symbol (traded live or not) and summarise the session.

    Symbols listed in ``errors`` (for example, bars unavailable) are reported
    as errors rather than compared.
    """
    live_trades = list(live_trades)
    live_by_symbol = {trade.symbol: trade for trade in live_trades}
    results = []
    for symbol in sorted(set(symbols) | set(live_by_symbol)):
        if errors and symbol in errors:
            continue
        backtest = (backtest_trades.get(symbol) or [None])[0]
        results.append(compare_symbol(symbol, live_by_symbol.get(symbol), backtest))
    return ParityReport(
        session_date=session_date,
        symbols=results,
        entry_slippage_ticks=[
            trade.entry_slippage_ticks
            for trade in live_trades
            if trade.entry_slippage_ticks is not None
        ],
        gap_fills=sum(1 for trade in live_trades if trade.gap_fill),
        errors=dict(errors or {}),
    )


__all__ = [
    "LiveTrade",
    "MISMATCH_KINDS",
    "ParityReport",
    "SymbolParity",
    "backtest_session",
    "build_report",
    "compare_symbol",
    "live_trades_from_executions",
    "normalise_exit_reason",
]
