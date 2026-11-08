"""Daily live-vs-backtest parity and entry-slippage report.

Paper trading measures execution, not edge, so every session is replayed
through the backtester's resting-stop path with the live ruleset and the
live session-start equity, and the two are diffed trade by trade:
taken/not taken, side, entry minute, entry price, quantity, exit reason and R.

Entry fills are split into three classes so the headline slippage measures
only what the backtest models (2 ticks beyond a resting trigger):

* ``normal``          -- measured against the live trigger; the headline.
* ``market_gap``      -- the backtest itself filled away from the level (the
  minute opened through it); measured against the backtest's base price.
* ``late_placement``  -- the replay's 1-minute bars show the level was
  already reached before the order reached the broker (``submitted_at``):
  the first touching minute ended before submission, or the touch was in
  the submission minute and the fill came within ``ALREADY_THROUGH_S``.

Everything here is pure: callers supply executions, 1-minute bars and E0.
"""

from __future__ import annotations

import statistics
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union
from zoneinfo import ZoneInfo

import pandas as pd

from vibe.common.models import Trade
from vibe.common.risk.portfolio_sizing import (
    CAPPED_BY_BROKER_FUNDS,
    CAPPED_BY_EQUITY_HEADROOM,
    CAPPED_BY_SYMBOL_EXPOSURE,
    size_portfolio_entry,
)
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.trading_bot.storage.ib_execution_store import dedupe_corrections

MARKET_TZ = ZoneInfo("America/New_York")
TICK = 0.01
MODELLED_ENTRY_SLIPPAGE_TICKS = 2

# Differences within these are execution noise, not a different trade. Entry
# price tolerance covers the measured slippage itself; a larger gap means the
# two paths filled at different prices for a reason worth explaining.
ENTRY_PRICE_TOLERANCE_TICKS = 10
R_TOLERANCE = 0.25

# A fill this close to submission, in the minute the level was first touched,
# means the market was already through the level when the order arrived.
ALREADY_THROUGH_S = 1.0
# Live trigger and stop must equal the replay's opening-range edges within this.
LEVEL_TOLERANCE_TICKS = 1.0
# Bars short of a full regular session by more than this are not trusted.
MAX_MISSING_RTH_MINUTES = 10

FILL_NORMAL = "normal"
FILL_MARKET_GAP = "market_gap"
FILL_LATE_PLACEMENT = "late_placement"

# Live caps that depend on the rest of the book (other symbols' positions or
# the broker's funds), which a per-symbol replay cannot reproduce.
_BOOK_DEPENDENT_CAPS = {CAPPED_BY_EQUITY_HEADROOM, CAPPED_BY_BROKER_FUNDS}

MISMATCH_KINDS = (
    "taken_live_only",
    "taken_backtest_only",
    "side",
    "entry_minute",
    "entry_price",
    "qty",
    "exit_reason",
    "r",
    "oca_overfill",
    "level",
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


def _signed_ticks(side: str, price: float, reference: Optional[float]) -> Optional[float]:
    """Ticks ``price`` is beyond ``reference`` (positive = worse for ``side``)."""
    if reference is None:
        return None
    signed = price - reference if side == "buy" else reference - price
    return round(signed / TICK, 6)


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
    placement_latency_s: Optional[float] = None
    capped_by: Optional[str] = None
    oca_overfill_qty: float = 0.0
    submitted_at: Optional[datetime] = None
    """When the entry order was sent to the broker (journalled)."""
    """Shares filled on the opposite OCA leg (both legs filled)."""

    @property
    def entry_slippage_ticks(self) -> Optional[float]:
        """Signed ticks beyond the trigger (positive = worse than the trigger)."""
        return _signed_ticks(self.side, self.entry_price, self.trigger_price)

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


def dedupe_executions(executions: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Collapse IBKR execution corrections onto their latest revision.

    Same rule as the live path (``dedupe_corrections``): each fill appears
    once, under the id of its first revision, with its latest data.
    """
    return dedupe_corrections(dict(item) for item in executions)


def _role(item: Mapping[str, Any]) -> Optional[str]:
    return (item.get("order_metadata") or {}).get("order_role")


def live_trades_from_executions(
    executions: Iterable[Mapping[str, Any]],
    session_date: date,
    tz: ZoneInfo = MARKET_TZ,
) -> list[LiveTrade]:
    """Rebuild the session's resting-stop round trips from durable executions.

    Uses the order metadata journalled at submission (``order_role``,
    ``benchmark_price`` = trigger, ``strategy_stop_price``, ``gap_fill``,
    ``placement_latency_s``, ``capped_by``, ``exit_reason``). Symbols without
    an ``entry``-role execution are ignored, so unrelated orders do not
    create phantom trades. The trade's side is its first entry fill's side;
    entry fills on the other OCA leg are reported as an overfill, never
    merged into the entry or mistaken for the exit.
    """
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for execution in dedupe_executions(executions):
        filled_at = _as_datetime(execution.get("filled_at"))
        if filled_at is None or filled_at.astimezone(tz).date() != session_date:
            continue
        by_symbol.setdefault(str(execution["symbol"]), []).append(execution)

    trades: list[LiveTrade] = []
    for symbol, items in sorted(by_symbol.items()):
        items = sorted(items, key=lambda item: _as_datetime(item["filled_at"]))
        all_entries = [item for item in items if _role(item) == "entry"]
        if not all_entries:
            continue
        side = str(all_entries[0]["side"])
        entries = [item for item in all_entries if str(item["side"]) == side]
        overfill = [item for item in all_entries if str(item["side"]) != side]
        entry_qty, entry_price = _vwap(entries)
        metadata = entries[0].get("order_metadata") or {}
        first_entry_at = _as_datetime(entries[0]["filled_at"])
        exits = [
            item for item in items
            if str(item["side"]) != side
            and _role(item) != "entry"
            and _as_datetime(item["filled_at"]) >= first_entry_at
        ]
        exit_meta = (exits[-1].get("order_metadata") or {}) if exits else {}
        exit_role = exit_meta.get("order_role")
        exit_reason = normalise_exit_reason(
            "protective_stop"
            if exit_role == "protective_stop"
            else exit_meta.get("exit_reason") or exit_role
        ) if exits else None
        gap_fill = metadata.get("gap_fill")
        latency = metadata.get("placement_latency_s")
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
                placement_latency_s=None if latency is None else float(latency),
                capped_by=metadata.get("capped_by"),
                oca_overfill_qty=_vwap(overfill)[0] if overfill else 0.0,
                submitted_at=_as_datetime(metadata.get("submitted_at")),
            )
        )
    return trades


@dataclass(frozen=True)
class BacktestReplay:
    """One symbol-session replayed through the backtester."""

    trades: list
    sizing_caps: dict = field(default_factory=dict)
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    first_touch: dict = field(default_factory=dict)
    """side -> start of the first minute at/after OR end reaching that level."""

    def level(self, side: str) -> Optional[float]:
        return self.or_high if side == "buy" else self.or_low

    def opposite_edge(self, side: str) -> Optional[float]:
        return self.or_low if side == "buy" else self.or_high


def _first_touch(frame: pd.DataFrame, or_end: pd.Timestamp, high: float, low: float) -> dict:
    after = frame[frame.index >= or_end]
    touches = {}
    long_hits = after.index[after["high"] >= high]
    short_hits = after.index[after["low"] <= low]
    if len(long_hits):
        touches["buy"] = long_hits[0].to_pydatetime()
    if len(short_hits):
        touches["sell"] = short_hits[0].to_pydatetime()
    return touches


def _market_time(session_date: date, hhmm: str) -> pd.Timestamp:
    hours, minutes = map(int, hhmm.split(":"))
    return pd.Timestamp(
        datetime(session_date.year, session_date.month, session_date.day, hours, minutes),
        tz=MARKET_TZ,
    )


def validate_session_bars(
    ruleset: StrategyRuleSet,
    minutes: Optional[pd.DataFrame],
    session_date: Optional[date] = None,
) -> pd.DataFrame:
    """The session's bars as a sorted market-time OHLCV frame, or raise.

    Historical-data errors come back from ib_insync as an empty list, so
    missing, wrong-day or incomplete bars must fail loudly rather than
    replay as "no trade". Raises when bars are empty, dated another day,
    cover fewer than the session's RTH minutes (XNYS calendar, so half days
    work) less ``MAX_MISSING_RTH_MINUTES``, or miss any opening-range minute.
    """
    if minutes is None or minutes.empty:
        raise ValueError(f"no bars for {session_date or 'the session'}")
    frame = minutes[["open", "high", "low", "close", "volume"]].astype(float).copy()
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(MARKET_TZ)
    frame = frame.tz_convert(MARKET_TZ).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    days = sorted(set(frame.index.date))
    session_date = session_date or days[0]
    if days != [session_date]:
        raise ValueError(
            f"bars dated {', '.join(d.isoformat() for d in days)}; expected {session_date}"
        )

    import pandas_market_calendars as mcal

    schedule = mcal.get_calendar("XNYS").schedule(
        start_date=session_date.isoformat(), end_date=session_date.isoformat()
    )
    if schedule.empty:
        raise ValueError(f"{session_date} is not an XNYS session")
    market_open = schedule.iloc[0]["market_open"].tz_convert(MARKET_TZ)
    market_close = schedule.iloc[0]["market_close"].tz_convert(MARKET_TZ)
    expected = int((market_close - market_open) / pd.Timedelta(minutes=1))
    present = int(((frame.index >= market_open) & (frame.index < market_close)).sum())
    if present < expected - MAX_MISSING_RTH_MINUTES:
        raise ValueError(f"only {present} of {expected} RTH minutes for {session_date}")

    strategy = ruleset.strategy
    window = pd.date_range(
        _market_time(session_date, strategy.orb_start_time),
        periods=int(strategy.orb_duration_minutes),
        freq="1min",
    )
    missing = window.difference(frame.index)
    if len(missing):
        raise ValueError(
            "opening-range minutes missing: "
            + ", ".join(stamp.strftime("%H:%M") for stamp in missing)
        )
    frame.index.name = "ts_event"
    return frame


def backtest_session(
    ruleset: StrategyRuleSet,
    symbol: str,
    minutes: pd.DataFrame,
    session_equity: float,
    session_date: Optional[date] = None,
    slippage_ticks: int = MODELLED_ENTRY_SLIPPAGE_TICKS,
) -> BacktestReplay:
    """Replay one symbol-session through the backtester's resting-stop path.

    ``minutes`` are the session's 1-minute bars (tz-aware index, OHLCV),
    validated by ``validate_session_bars``. The engine starts from
    ``session_equity`` so its session-start equity, and therefore its
    sizing caps, are the live E0.
    """
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.backtester.core.execution_realism import ExecutionRealismConfig

    frame = validate_session_bars(ruleset, minutes, session_date)
    session = frame.index[0].date()
    strategy = ruleset.strategy
    window = frame.loc[
        _market_time(session, strategy.orb_start_time):
        _market_time(session, strategy.orb_start_time)
        + pd.Timedelta(minutes=int(strategy.orb_duration_minutes) - 1)
    ]
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
    or_high, or_low = float(window["high"].max()), float(window["low"].min())
    or_end = _market_time(session, strategy.orb_start_time) + pd.Timedelta(
        minutes=int(strategy.orb_duration_minutes)
    )
    return BacktestReplay(
        trades=list(result.trades),
        sizing_caps=dict(getattr(engine, "sizing_caps", {}) or {}),
        or_high=or_high,
        or_low=or_low,
        first_touch=_first_touch(frame, or_end, or_high, or_low),
    )


def _as_replay(value: Union[BacktestReplay, list, None]) -> BacktestReplay:
    if isinstance(value, BacktestReplay):
        return value
    return BacktestReplay(trades=list(value or []))


def backtest_base_price(trade: Trade, slippage_ticks: int = MODELLED_ENTRY_SLIPPAGE_TICKS) -> float:
    """The price the backtest filled at before its modelled slippage."""
    offset = slippage_ticks * TICK
    base = trade.entry_price - offset if trade.side == "buy" else trade.entry_price + offset
    return round(base, 6)


def placed_after_touch(live: LiveTrade, first_touch: Optional[datetime]) -> bool:
    """Whether the market reached the level before the order reached the broker."""
    if first_touch is None or live.submitted_at is None:
        return False
    touch_start = pd.Timestamp(first_touch).tz_convert("UTC")
    submitted = pd.Timestamp(live.submitted_at).tz_convert("UTC")
    if touch_start + pd.Timedelta(minutes=1) <= submitted:
        return True  # the whole touching minute was over before submission
    if touch_start <= submitted:
        filled = pd.Timestamp(live.entry_time).tz_convert("UTC")
        return (filled - submitted) <= pd.Timedelta(seconds=ALREADY_THROUGH_S)
    return False


def classify_fill(
    live: LiveTrade,
    backtest: Optional[Trade],
    replay: Optional["BacktestReplay"],
) -> tuple[str, Optional[float], str]:
    """``(fill_class, slippage_ticks, reference)`` for one live entry fill.

    A late placement is classed first: once the order was not resting when
    the level traded, its fill says nothing about resting-stop slippage even
    if the market also gapped.
    """
    if replay is not None and placed_after_touch(live, replay.first_touch.get(live.side)):
        return FILL_LATE_PLACEMENT, live.entry_slippage_ticks, "trigger"
    level = replay.level(live.side) if replay is not None else None
    if backtest is not None and backtest.side == live.side and level is not None:
        base = backtest_base_price(backtest)
        gapped = base > level + 1e-9 if live.side == "buy" else base < level - 1e-9
        if gapped:
            return FILL_MARKET_GAP, _signed_ticks(live.side, live.entry_price, base), "backtest_base"
    return FILL_NORMAL, live.entry_slippage_ticks, "trigger"


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
    """Normal fills only: the headline the 2-tick model is judged against."""
    gap_fills: int
    """Market-gap fills (the backtest also filled away from the level)."""
    modelled_slippage_ticks: int = MODELLED_ENTRY_SLIPPAGE_TICKS
    errors: dict[str, str] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)
    """Every live entry fill with its class, slippage and reference price."""

    @property
    def traded(self) -> list[SymbolParity]:
        return [item for item in self.symbols if item.details.get("traded", True)]

    @property
    def matched(self) -> int:
        return sum(1 for item in self.traded if item.matched)

    @property
    def mismatched(self) -> list[SymbolParity]:
        return [item for item in self.symbols if not item.matched]

    def _class_ticks(self, fill_class: str) -> list[float]:
        return [
            fill["slippage_ticks"]
            for fill in self.fills
            if fill["fill_class"] == fill_class and fill["slippage_ticks"] is not None
        ]

    @property
    def market_gap_slippage_ticks(self) -> list[float]:
        return self._class_ticks(FILL_MARKET_GAP)

    @property
    def late_placement_slippage_ticks(self) -> list[float]:
        return self._class_ticks(FILL_LATE_PLACEMENT)

    @property
    def late_placements(self) -> int:
        return sum(1 for fill in self.fills if fill["fill_class"] == FILL_LATE_PLACEMENT)

    @property
    def mean_slippage_ticks(self) -> Optional[float]:
        return statistics.fmean(self.entry_slippage_ticks) if self.entry_slippage_ticks else None

    @property
    def median_slippage_ticks(self) -> Optional[float]:
        return statistics.median(self.entry_slippage_ticks) if self.entry_slippage_ticks else None

    @property
    def mean_gap_slippage_ticks(self) -> Optional[float]:
        ticks = self.market_gap_slippage_ticks
        return statistics.fmean(ticks) if ticks else None

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
            "market_gap_slippage_ticks": self.market_gap_slippage_ticks,
            "mean_gap_slippage_ticks": self.mean_gap_slippage_ticks,
            "late_placements": self.late_placements,
            "late_placement_slippage_ticks": self.late_placement_slippage_ticks,
            "already_through_s": ALREADY_THROUGH_S,
            "fills": self.fills,
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


def _sized(
    ruleset: Optional[StrategyRuleSet],
    session_equity: Optional[float],
    entry: Optional[float],
    stop: Optional[float],
) -> Optional[int]:
    """The shared sizing rule on a flat book at E0, or None if not computable."""
    portfolio = getattr(ruleset, "portfolio", None)
    if portfolio is None or not session_equity or not entry or not stop or entry == stop:
        return None
    return size_portfolio_entry(
        entry_price=float(entry),
        stop_price=float(stop),
        sizing_equity=float(session_equity),
        risk_pct=float(ruleset.position_size.value),
        max_symbol_exposure_pct=float(portfolio.max_symbol_exposure_pct),
        max_gross_leverage=float(portfolio.max_gross_leverage),
        current_equity=float(session_equity),
        max_shares=ruleset.position_size.max_shares,
    ).quantity


def compare_symbol(
    symbol: str,
    live: Optional[LiveTrade],
    backtest: Optional[Trade],
    *,
    replay: Optional[BacktestReplay] = None,
    ruleset: Optional[StrategyRuleSet] = None,
    session_equity: Optional[float] = None,
) -> SymbolParity:
    """Diff one symbol's live trade against its backtest replay.

    Levels: the live trigger and protective stop must be the replay's
    opening-range edges within ``LEVEL_TOLERANCE_TICKS`` (both come from the
    same IBKR 1-minute bars). Quantity: live sizes at its trigger, the
    backtest at its fill, so the live quantity is checked against the shared
    sizing rule evaluated at the live trigger and E0 -- with matching levels
    that pins it completely. It is not checked when the live entry was
    capped by equity headroom or broker funds, which depend on the rest of
    the book. Without a ruleset/E0 the raw quantities are compared (one
    share of tolerance under the symbol-exposure cap).
    """
    caps = dict(replay.sizing_caps) if replay is not None else {}
    if live is None and backtest is None:
        return SymbolParity(symbol, True, details={"traded": False})
    if live is None:
        return SymbolParity(
            symbol, False, ["taken_backtest_only"],
            {
                "backtest_side": backtest.side,
                "backtest_entry_time": backtest.entry_time.isoformat(),
                "backtest_entry_price": backtest.entry_price,
                "backtest_qty": backtest.quantity,
                "backtest_sizing_caps": caps,
            },
        )
    fill_class, class_ticks, reference = classify_fill(live, backtest, replay)
    live_details = {
        "live_side": live.side,
        "live_entry_time": live.entry_time.isoformat(),
        "live_entry_price": live.entry_price,
        "live_qty": live.quantity,
        "live_capped_by": live.capped_by,
        "gap_fill": live.gap_fill,
        "placement_latency_s": live.placement_latency_s,
        "submitted_at": live.submitted_at.isoformat() if live.submitted_at else None,
        "first_touch": (
            replay.first_touch[live.side].isoformat()
            if replay is not None and live.side in replay.first_touch
            else None
        ),
        "fill_class": fill_class,
        "fill_slippage_ticks": class_ticks,
        "fill_reference": reference,
        "oca_overfill_qty": live.oca_overfill_qty,
    }
    level_mismatch = False
    if replay is not None and replay.or_high is not None:
        level, edge = replay.level(live.side), replay.opposite_edge(live.side)
        trigger_ticks = (
            abs(live.trigger_price - level) / TICK if live.trigger_price is not None else None
        )
        stop_ticks = abs(live.stop_price - edge) / TICK if live.stop_price is not None else None
        live_details.update({
            "replay_level": level,
            "replay_opposite_edge": edge,
            "trigger_vs_replay_ticks": None if trigger_ticks is None else round(trigger_ticks, 6),
            "stop_vs_replay_ticks": None if stop_ticks is None else round(stop_ticks, 6),
        })
        level_mismatch = any(
            ticks is None or ticks > LEVEL_TOLERANCE_TICKS + 1e-6
            for ticks in (trigger_ticks, stop_ticks)
        )
    if backtest is None:
        mismatches = ["taken_live_only"]
        if live.oca_overfill_qty:
            mismatches.append("oca_overfill")
        if level_mismatch:
            mismatches.append("level")
        return SymbolParity(symbol, False, mismatches, live_details)

    backtest_r = _r(backtest.side, backtest.entry_price, backtest.exit_price, _backtest_stop(backtest))
    live_r = live.r_multiple
    entry_diff_ticks = round((live.entry_price - backtest.entry_price) / TICK, 6)
    expected_live_qty = _sized(ruleset, session_equity, live.trigger_price, live.stop_price)
    exposure_capped = (
        CAPPED_BY_SYMBOL_EXPOSURE in caps or live.capped_by == CAPPED_BY_SYMBOL_EXPOSURE
    )
    details = {
        **live_details,
        "backtest_side": backtest.side,
        "live_entry_minute": _minute(live.entry_time).isoformat(),
        "backtest_entry_minute": _minute(backtest.entry_time).isoformat(),
        "backtest_entry_price": backtest.entry_price,
        "backtest_base_price": backtest_base_price(backtest),
        "entry_price_diff_ticks": entry_diff_ticks,
        "backtest_qty": backtest.quantity,
        "expected_live_qty": expected_live_qty,
        "backtest_sizing_caps": caps,
        "live_exit_reason": live.exit_reason,
        "backtest_exit_reason": normalise_exit_reason(backtest.exit_reason),
        "live_r": live_r,
        "backtest_r": backtest_r,
        "entry_slippage_ticks": live.entry_slippage_ticks,
    }
    mismatches: list[str] = []
    if live.side != backtest.side:
        mismatches.append("side")
    if _minute(live.entry_time) != _minute(backtest.entry_time):
        mismatches.append("entry_minute")
    if abs(entry_diff_ticks) > ENTRY_PRICE_TOLERANCE_TICKS:
        mismatches.append("entry_price")
    live_qty = int(round(live.quantity))
    if live.capped_by not in _BOOK_DEPENDENT_CAPS:
        if expected_live_qty is not None:
            qty_wrong = live_qty != expected_live_qty
        else:
            qty_wrong = abs(live_qty - int(round(backtest.quantity))) > (
                1 if exposure_capped else 0
            )
        if qty_wrong:
            mismatches.append("qty")
    if live.exit_reason != normalise_exit_reason(backtest.exit_reason):
        mismatches.append("exit_reason")
    if live_r is None or backtest_r is None or abs(live_r - backtest_r) > R_TOLERANCE:
        mismatches.append("r")
    if live.oca_overfill_qty:
        mismatches.append("oca_overfill")
    if level_mismatch:
        mismatches.append("level")
    return SymbolParity(symbol, not mismatches, mismatches, details)


def build_report(
    session_date: date,
    symbols: Iterable[str],
    live_trades: Iterable[LiveTrade],
    backtest_trades: Mapping[str, Union[BacktestReplay, list]],
    errors: Optional[Mapping[str, str]] = None,
    *,
    ruleset: Optional[StrategyRuleSet] = None,
    session_equity: Optional[float] = None,
) -> ParityReport:
    """Compare every symbol (traded live or not) and summarise the session.

    Symbols listed in ``errors`` (for example, bars unavailable) are reported
    as errors rather than compared; their live fills are listed as
    ``unclassified`` and kept out of every slippage figure.
    """
    live_trades = list(live_trades)
    live_by_symbol = {trade.symbol: trade for trade in live_trades}
    errors = dict(errors or {})
    results: list[SymbolParity] = []
    fills: list[dict[str, Any]] = []
    for symbol in sorted(set(symbols) | set(live_by_symbol)):
        live = live_by_symbol.get(symbol)
        if symbol in errors:
            if live is not None:
                fills.append({
                    "symbol": symbol, "fill_class": "unclassified",
                    "slippage_ticks": live.entry_slippage_ticks, "reference": "trigger",
                })
            continue
        replay = _as_replay(backtest_trades.get(symbol))
        backtest = replay.trades[0] if replay.trades else None
        result = compare_symbol(
            symbol,
            live,
            backtest,
            replay=replay,
            ruleset=ruleset,
            session_equity=session_equity,
        )
        results.append(result)
        if live is not None:
            fills.append({
                "symbol": symbol,
                "fill_class": result.details["fill_class"],
                "slippage_ticks": result.details["fill_slippage_ticks"],
                "reference": result.details["fill_reference"],
            })
    return ParityReport(
        session_date=session_date,
        symbols=results,
        entry_slippage_ticks=[
            fill["slippage_ticks"]
            for fill in fills
            if fill["fill_class"] == FILL_NORMAL and fill["slippage_ticks"] is not None
        ],
        gap_fills=sum(1 for fill in fills if fill["fill_class"] == FILL_MARKET_GAP),
        errors=errors,
        fills=fills,
    )


__all__ = [
    "BacktestReplay",
    "FILL_LATE_PLACEMENT",
    "FILL_MARKET_GAP",
    "FILL_NORMAL",
    "ALREADY_THROUGH_S",
    "LiveTrade",
    "MISMATCH_KINDS",
    "ParityReport",
    "SymbolParity",
    "backtest_base_price",
    "backtest_session",
    "build_report",
    "classify_fill",
    "placed_after_touch",
    "compare_symbol",
    "dedupe_executions",
    "live_trades_from_executions",
    "normalise_exit_reason",
    "validate_session_bars",
]
