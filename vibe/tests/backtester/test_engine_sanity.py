"""
Phase 1 sanity tests — validates fundamental backtester invariants.

These must pass before any research results are trusted:
  - No-trade produces flat equity (no phantom P&L)
  - Buy-and-hold produces correct P&L
  - Cash balance invariant holds at every bar (equity = cash + MTM)
  - Random strategy loses money to costs (execution model is not giving free fills)
"""

import random
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillResult, FillSimulator
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, price: float, spread: float = 0.5) -> Bar:
    return Bar(
        timestamp=ts,
        open=price,
        high=price + spread,
        low=price - spread,
        close=price,
        volume=1_000_000,
    )


def _ts(hour: int, minute: int, day: int = 2) -> datetime:
    # Use timedelta so minute values >= 60 roll over naturally (e.g. 9:60 → 10:00)
    base = datetime(2024, 1, day, 0, 0, tzinfo=ET)
    return base + timedelta(hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# Phase 1.1 — No-trade flat equity
# ---------------------------------------------------------------------------

def test_no_trade_flat_equity():
    """With no positions opened, cash and equity must never change."""
    capital = 100_000.0
    pm = PortfolioManager(capital)
    clock = SimulatedClock()

    for i in range(20):
        ts = _ts(9, 30 + i * 5)
        clock.set_time(ts)
        bar = _bar(ts, 480.0 + i)  # price drifts up — should not affect us
        pm.check_exits({"QQQ": bar}, clock)
        pm.update_equity({"QQQ": bar}, ts)

    assert len(pm.trade_history) == 0
    assert pm.cash == pytest.approx(capital)
    for _, equity in pm.equity_curve:
        assert equity == pytest.approx(capital)


# ---------------------------------------------------------------------------
# Phase 1.2 — Buy-and-hold P&L
# ---------------------------------------------------------------------------

def test_buy_hold_long_pnl():
    """Buy 100 shares at 480, hold, sell at 500 → P&L = $2000."""
    pm = PortfolioManager(100_000.0)
    fill_sim = FillSimulator(slippage_ticks=0)

    ts_entry = _ts(9, 30)
    bar_entry = _bar(ts_entry, 480.0)
    fill = fill_sim.execute("QQQ", "buy", 100, bar_entry, price_override=480.0)
    pm.open_position(fill, stop_price=460.0, timestamp=ts_entry)

    ts_exit = _ts(15, 55, day=5)
    exit_fill = FillResult("QQQ", "sell", 100, 500.0)
    pm.close_position(exit_fill, exit_reason="EOD", timestamp=ts_exit)

    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].pnl == pytest.approx((500.0 - 480.0) * 100)
    assert pm.cash == pytest.approx(100_000.0 + 20.0 * 100)


def test_buy_hold_short_pnl():
    """Sell short 100 shares at 480, cover at 460 → P&L = $2000."""
    pm = PortfolioManager(100_000.0)
    fill_sim = FillSimulator(slippage_ticks=0)

    ts_entry = _ts(9, 30)
    bar_entry = _bar(ts_entry, 480.0)
    fill = fill_sim.execute("QQQ", "sell", 100, bar_entry, price_override=480.0)
    pm.open_position(fill, stop_price=500.0, timestamp=ts_entry)

    ts_exit = _ts(15, 55, day=5)
    exit_fill = FillResult("QQQ", "buy", 100, 460.0)
    pm.close_position(exit_fill, exit_reason="EOD", timestamp=ts_exit)

    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].pnl == pytest.approx((480.0 - 460.0) * 100)
    assert pm.cash == pytest.approx(100_000.0 + 20.0 * 100)


# ---------------------------------------------------------------------------
# Phase 1.3 — Cash balance invariant: equity = cash + mark-to-market
# ---------------------------------------------------------------------------

def test_cash_invariant_long():
    """
    After opening a long, at every bar:
      equity = cash + (bar.close * qty)
    """
    pm = PortfolioManager(100_000.0)
    fill_sim = FillSimulator(slippage_ticks=0)

    ts_entry = _ts(9, 30)
    bar_entry = _bar(ts_entry, 480.0)
    fill = fill_sim.execute("QQQ", "buy", 10, bar_entry, price_override=480.0)
    pm.open_position(fill, stop_price=460.0, timestamp=ts_entry)

    prices = [480.0, 485.0, 490.0, 488.0, 483.0, 492.0]
    for i, price in enumerate(prices):
        ts = _ts(9, 35 + i * 5)
        bar = _bar(ts, price)
        pm.update_equity({"QQQ": bar}, ts)

        _, equity = pm.equity_curve[-1]
        expected = pm.cash + price * 10  # cash (after deducting entry) + market value
        assert equity == pytest.approx(expected), f"Invariant violated at bar {i}: price={price}"


def test_cash_invariant_short():
    """
    After opening a short, at every bar:
      equity = cash + (-bar.close * qty)
    Cash was increased by proceeds; MTM is negative to offset.
    """
    pm = PortfolioManager(100_000.0)
    fill_sim = FillSimulator(slippage_ticks=0)

    ts_entry = _ts(9, 30)
    bar_entry = _bar(ts_entry, 480.0)
    fill = fill_sim.execute("QQQ", "sell", 10, bar_entry, price_override=480.0)
    pm.open_position(fill, stop_price=500.0, timestamp=ts_entry)

    prices = [480.0, 478.0, 475.0, 472.0, 470.0]
    for i, price in enumerate(prices):
        ts = _ts(9, 35 + i * 5)
        bar = _bar(ts, price)
        pm.update_equity({"QQQ": bar}, ts)

        _, equity = pm.equity_curve[-1]
        expected = pm.cash + (-price * 10)  # cash (including proceeds) - market buyback cost
        assert equity == pytest.approx(expected), f"Invariant violated at bar {i}: price={price}"


# ---------------------------------------------------------------------------
# Phase 1.4 — Random strategy loses to costs
# ---------------------------------------------------------------------------

def test_random_strategy_loses_to_costs():
    """
    Enter at price + 2 ticks (long) or price - 2 ticks (short), exit immediately
    at the same flat price. With neutral price data, slippage on entry alone
    ensures every trade is a loss. Total P&L must be negative.
    """
    rng = random.Random(42)
    pm = PortfolioManager(100_000.0)
    SLIPPAGE = 0.02  # 2 ticks
    PRICE = 480.0
    QTY = 10

    for i in range(50):
        ts = _ts(9, 30 + (i % 39) * 1, day=2 + i // 39)
        side = rng.choice(["buy", "sell"])

        # Entry with slippage applied in the direction of the trade
        entry_price = PRICE + SLIPPAGE if side == "buy" else PRICE - SLIPPAGE
        entry_fill = FillResult("QQQ", side, QTY, entry_price)
        pm.open_position(entry_fill, stop_price=PRICE - 5 if side == "buy" else PRICE + 5, timestamp=ts)

        # Immediate exit at flat price (no market move)
        exit_side = "sell" if side == "buy" else "buy"
        exit_fill = FillResult("QQQ", exit_side, QTY, PRICE)
        pm.close_position(exit_fill, exit_reason="TEST", timestamp=ts)

    total_pnl = sum(t.pnl for t in pm.trade_history)
    assert total_pnl < 0, f"Random strategy should lose to slippage costs, got P&L={total_pnl:.2f}"
    # Each trade loses exactly SLIPPAGE * QTY
    assert total_pnl == pytest.approx(-SLIPPAGE * QTY * 50, rel=1e-4)
