import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.core.fill_simulator import FillResult
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")

def _fill(symbol="QQQ", side="buy", qty=100, price=480.0):
    return FillResult(symbol=symbol, side=side, filled_qty=qty, avg_price=price)

def _bar(symbol="QQQ", close=490.0, low=None, high=None):
    open_ = close
    if high is None:
        high = close + 2.0
    if low is None:
        low = close - 2.0
    return Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=open_, high=high, low=low, close=close, volume=1_000_000,
    )

def test_initial_cash():
    pm = PortfolioManager(initial_capital=10_000.0)
    assert pm.cash == 10_000.0

def test_open_position_deducts_cash():
    pm = PortfolioManager(10_000.0)
    fill = _fill(price=480.0, qty=10)
    pm.open_position(fill, stop_price=470.0, timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    assert pm.cash == pytest.approx(10_000.0 - 480.0 * 10)

def test_close_position_records_trade():
    pm = PortfolioManager(10_000.0)
    ts_entry = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    ts_exit  = datetime(2024, 1, 15, 11, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts_entry)
    pm.close_position(_fill(side="sell", price=490.0, qty=10), exit_reason="TARGET", timestamp=ts_exit)
    assert len(pm.trade_history) == 1
    trade = pm.trade_history[0]
    assert trade.pnl == pytest.approx((490.0 - 480.0) * 10)
    assert trade.initial_risk == pytest.approx((480.0 - 470.0) * 10)
    assert trade.exit_reason == "TARGET"

def test_close_position_restores_cash():
    pm = PortfolioManager(10_000.0)
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts)
    pm.close_position(_fill(side="sell", price=490.0, qty=10), exit_reason="EOD", timestamp=ts)
    assert pm.cash == pytest.approx(10_000.0 + (490.0 - 480.0) * 10)

def test_update_equity():
    pm = PortfolioManager(10_000.0)
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts)
    bars = {"QQQ": _bar(close=490.0)}
    pm.update_equity(bars, ts)
    assert len(pm.equity_curve) == 1
    _, equity = pm.equity_curve[0]
    assert equity == pytest.approx((10_000.0 - 4800.0) + 4900.0)

def test_check_exits_stop_hit():
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=475.0, timestamp=clock.now())
    # bar low touches stop
    bars = {"QQQ": _bar(close=474.0, low=473.0, high=480.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"

def test_check_exits_eod():
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 15, 55, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=clock.now())
    bars = {"QQQ": _bar(close=485.0, low=484.0, high=487.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "EOD"
