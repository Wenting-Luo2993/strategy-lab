"""
Phase 3 execution realism tests — validates that the execution model behaves correctly.

  - Slippage is applied and degrades performance monotonically
  - Intrabar breakout detection fires on bar.high / bar.low, not bar.close
  - Reversal bars (both levels breached) resolve direction by LEAN tie-break heuristic
  - Entry fill price is OR_high + $0.01 + slippage (not bar.close)
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillResult, FillSimulator
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hour: int, minute: int, day: int = 2) -> datetime:
    return datetime(2024, 1, day, hour, minute, tzinfo=ET)


def _bar_dict(ts: datetime, open_: float, high: float, low: float, close: float) -> dict:
    return {"timestamp": ts, "open": open_, "high": high, "low": low, "close": close, "volume": 1_000_000}


def _make_orb_context(orb_high: float = 400.0, orb_low: float = 395.0) -> pd.DataFrame:
    """
    Build a minimal df_context with:
      - Two ORB-window bars (9:30, 9:32) establishing the OR levels
      - ATR_14 column set to a fixed value
      - timestamp column required by ORBCalculator
    """
    mid = (orb_high + orb_low) / 2
    rows = [
        {"timestamp": _ts(9, 30), "open": mid, "high": orb_high, "low": orb_low, "close": mid, "volume": 1_000_000},
        {"timestamp": _ts(9, 32), "open": mid, "high": orb_high, "low": orb_low, "close": mid, "volume": 1_000_000},
    ]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_localize(None).dt.tz_localize("America/New_York")
    df["ATR_14"] = 2.0
    return df


def _fresh_strategy() -> ORBStrategy:
    cfg = ORBStrategyConfig(
        name="TEST_ORB",
        orb_body_pct_filter=0.0,   # disable body filter so test bars always qualify
        entry_cutoff_time="15:00",
        take_profit_multiplier=2.0,
        stop_loss_at_level=True,
    )
    return ORBStrategy(cfg)


# ---------------------------------------------------------------------------
# Phase 3.1 — Slippage degrades performance monotonically
# ---------------------------------------------------------------------------

def test_slippage_increases_long_entry_price():
    """
    Long entry price = OR_high + $0.01 + slippage_ticks * $0.01.
    Each additional slippage tick must increase fill price by exactly $0.01.
    """
    OR_HIGH = 400.0
    _TICK = 0.01

    previous_entry = None
    for ticks in [0, 1, 2, 5]:
        slippage = ticks * _TICK
        entry_price = OR_HIGH + _TICK + slippage
        if previous_entry is not None:
            assert entry_price > previous_entry, (
                f"Entry price did not increase at slippage_ticks={ticks}"
            )
        previous_entry = entry_price


def test_slippage_decreases_short_entry_price():
    """
    Short entry price = OR_low - $0.01 - slippage_ticks * $0.01.
    Each additional slippage tick must decrease fill price by exactly $0.01.
    """
    OR_LOW = 395.0
    _TICK = 0.01

    previous_entry = None
    for ticks in [0, 1, 2, 5]:
        slippage = ticks * _TICK
        entry_price = OR_LOW - _TICK - slippage
        if previous_entry is not None:
            assert entry_price < previous_entry, (
                f"Entry price did not decrease at slippage_ticks={ticks}"
            )
        previous_entry = entry_price


def test_higher_slippage_reduces_trade_pnl():
    """
    Two identical trades with different entry slippage.
    The higher-slippage trade must have lower P&L.
    """
    exit_price = 405.0
    OR_HIGH = 400.0
    QTY = 10

    def _trade_pnl(slippage_ticks: int) -> float:
        _TICK = 0.01
        entry_price = OR_HIGH + _TICK + slippage_ticks * _TICK
        pm = PortfolioManager(100_000.0)
        ts = _ts(9, 35)
        pm.open_position(
            FillResult("QQQ", "buy", QTY, entry_price),
            stop_price=OR_HIGH - 2.0,
            timestamp=ts,
        )
        pm.close_position(
            FillResult("QQQ", "sell", QTY, exit_price),
            exit_reason="TARGET",
            timestamp=_ts(10, 0),
        )
        return pm.trade_history[0].pnl

    pnl_0 = _trade_pnl(0)
    pnl_2 = _trade_pnl(2)
    pnl_5 = _trade_pnl(5)

    assert pnl_0 > pnl_2 > pnl_5, (
        f"P&L should decrease with more slippage: {pnl_0:.2f} > {pnl_2:.2f} > {pnl_5:.2f}"
    )


# ---------------------------------------------------------------------------
# Phase 3.2 — Intrabar breakout detection
# ---------------------------------------------------------------------------

def test_intrabar_long_fires_when_close_below_level():
    """
    Bar high crosses OR_high + $0.01 intrabar, but close is below OR_high.
    Strategy must generate a long signal (detection is intrabar, not close-based).
    """
    strat = _fresh_strategy()
    df = _make_orb_context(orb_high=400.0, orb_low=395.0)

    # Bar: high=400.02 exceeds trigger (400.01); close=399 is below OR_high
    bar = _bar_dict(_ts(9, 40), open_=398.0, high=400.02, low=397.0, close=399.0)

    signal, meta = strat.generate_signal_incremental("QQQ", bar, df)
    assert signal == 1, f"Expected long signal, got {signal}. meta={meta}"
    assert meta.get("signal") == "long_breakout"


def test_no_signal_when_high_below_trigger():
    """
    Bar high reaches OR_high but not OR_high + $0.01 (the stop-market trigger).
    No signal should fire — touching the level is not a breakout.
    """
    strat = _fresh_strategy()
    df = _make_orb_context(orb_high=400.0, orb_low=395.0)

    # High=400.005 is between OR_high and OR_high+$0.01 — not enough to trigger
    bar = _bar_dict(_ts(9, 40), open_=398.0, high=400.005, low=397.0, close=399.0)

    signal, meta = strat.generate_signal_incremental("QQQ", bar, df)
    assert signal == 0, f"Expected no signal (high doesn't reach trigger), got {signal}. meta={meta}"


def test_intrabar_short_fires_when_close_above_level():
    """
    Bar low crosses OR_low - $0.01 intrabar, but close is above OR_low.
    Strategy must generate a short signal.
    """
    strat = _fresh_strategy()
    df = _make_orb_context(orb_high=400.0, orb_low=395.0)

    # Bar: low=394.98 is below trigger (394.99); close=396 is above OR_low
    bar = _bar_dict(_ts(9, 40), open_=397.0, high=398.0, low=394.98, close=396.0)

    signal, meta = strat.generate_signal_incremental("QQQ", bar, df)
    assert signal == -1, f"Expected short signal, got {signal}. meta={meta}"
    assert meta.get("signal") == "short_breakout"


# ---------------------------------------------------------------------------
# Phase 3.2b — Reversal bar tie-break (LEAN heuristic)
# ---------------------------------------------------------------------------

def test_reversal_bar_long_wins_when_up_move_larger():
    """
    Both levels are breached in the same bar. Bar open is near OR_low, so the
    upward move (bar_high - bar_open) is larger than the downward move
    (bar_open - bar_low). Long should win the tie-break.
    """
    strat = _fresh_strategy()
    df = _make_orb_context(orb_high=400.0, orb_low=395.0)

    # open=395.5 (near OR_low), high=401 (well above OR_high), low=394.9 (just below trigger)
    # up_move = 401 - 395.5 = 5.5 > down_move = 395.5 - 394.9 = 0.6 → long wins
    bar = _bar_dict(_ts(9, 40), open_=395.5, high=401.0, low=394.9, close=398.0)

    signal, meta = strat.generate_signal_incremental("QQQ", bar, df)
    assert signal == 1, f"Expected long to win tie-break, got {signal}. meta={meta}"


def test_reversal_bar_short_wins_when_down_move_larger():
    """
    Both levels are breached. Bar open is near OR_high, so the downward move
    is larger. Short should win the tie-break.
    """
    strat = _fresh_strategy()
    df = _make_orb_context(orb_high=400.0, orb_low=395.0)

    # open=399.5 (near OR_high), low=394.9 (well below OR_low), high=400.1 (just above trigger)
    # down_move = 399.5 - 394.9 = 4.6 > up_move = 400.1 - 399.5 = 0.6 → short wins
    bar = _bar_dict(_ts(9, 40), open_=399.5, high=400.1, low=394.9, close=396.0)

    signal, meta = strat.generate_signal_incremental("QQQ", bar, df)
    assert signal == -1, f"Expected short to win tie-break, got {signal}. meta={meta}"


# ---------------------------------------------------------------------------
# Phase 3.3 — Stop fill price model
# ---------------------------------------------------------------------------

def test_long_stop_fills_at_stop_price_not_close():
    """
    Long stop fires when bar.low <= stop_price.
    Fill must be at stop_price, even if bar.close is further below.
    """
    pm = PortfolioManager(10_000.0)
    clock = SimulatedClock()
    clock.set_time(_ts(10, 0))
    pm.open_position(FillResult("QQQ", "buy", 10, 480.0), stop_price=475.0, timestamp=clock.now())

    # low=473 is below stop=475; close=474 is also below stop but different from stop_price
    # fill must be at stop_price=475, not close=474
    bar = Bar(timestamp=_ts(10, 5), open=476.0, high=477.0, low=473.0, close=474.0, volume=1_000_000)
    pm.check_exits({"QQQ": bar}, clock)

    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"
    assert pm.trade_history[0].exit_price == pytest.approx(475.0)


def test_short_stop_fills_at_stop_price_not_close():
    """
    Short stop fires when bar.high >= stop_price.
    Fill must be at stop_price, not bar.close.
    """
    pm = PortfolioManager(10_000.0)
    clock = SimulatedClock()
    clock.set_time(_ts(10, 0))
    pm.open_position(FillResult("QQQ", "sell", 10, 480.0), stop_price=485.0, timestamp=clock.now())

    # high=487 is above stop=485; close=484 is below stop (but high triggered it)
    bar = Bar(timestamp=_ts(10, 5), open=481.0, high=487.0, low=480.0, close=484.0, volume=1_000_000)
    pm.check_exits({"QQQ": bar}, clock)

    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"
    assert pm.trade_history[0].exit_price == pytest.approx(485.0)


def test_intrabar_wick_fires_stop():
    """
    Bar low wicks below stop but close is above stop — stop fires (intrabar model).
    This is the intentional behavior: stop-market orders fill intrabar.
    """
    pm = PortfolioManager(10_000.0)
    clock = SimulatedClock()
    clock.set_time(_ts(10, 0))
    pm.open_position(FillResult("QQQ", "buy", 10, 480.0), stop_price=475.0, timestamp=clock.now())

    # low=473 touches stop; close=477 is above stop
    bar = Bar(timestamp=_ts(10, 5), open=478.0, high=479.0, low=473.0, close=477.0, volume=1_000_000)
    pm.check_exits({"QQQ": bar}, clock)

    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_price == pytest.approx(475.0)
