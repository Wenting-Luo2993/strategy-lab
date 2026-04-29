import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.fill_simulator import FillSimulator, TICK_SIZE
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")

def _bar(close=100.0, open_=None, high=None, low=None, volume=1_000_000):
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) + 1.0
    if low is None:
        low = min(open_, close) - 1.0
    return Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=open_, high=high, low=low, close=close, volume=volume,
    )

def test_buy_adds_slippage():
    sim = FillSimulator(slippage_ticks=5)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar(close=100.0))
    assert fill.avg_price == pytest.approx(100.0 + 5 * TICK_SIZE)

def test_sell_subtracts_slippage():
    sim = FillSimulator(slippage_ticks=5)
    fill = sim.execute("QQQ", "sell", quantity=100, bar=_bar(close=100.0))
    assert fill.avg_price == pytest.approx(100.0 - 5 * TICK_SIZE)

def test_zero_commission():
    sim = FillSimulator()
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar())
    assert fill.commission == 0.0

def test_full_fill():
    sim = FillSimulator()
    fill = sim.execute("QQQ", "buy", quantity=50, bar=_bar())
    assert fill.filled_qty == 50.0

def test_custom_slippage_ticks():
    sim = FillSimulator(slippage_ticks=10)
    fill = sim.execute("QQQ", "buy", quantity=1, bar=_bar(close=200.0))
    assert fill.avg_price == pytest.approx(200.0 + 10 * TICK_SIZE)

def test_next_bar_mode_uses_open():
    sim = FillSimulator(slippage_ticks=5, fill_mode=1)
    current = _bar(close=100.0)
    next_bar = _bar(open_=102.0, close=103.0)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=current, next_bar=next_bar)
    assert fill.avg_price == pytest.approx(102.0 + 5 * TICK_SIZE)

def test_next_bar_mode_falls_back_to_close_when_no_next_bar():
    sim = FillSimulator(slippage_ticks=5, fill_mode=1)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar(close=100.0), next_bar=None)
    assert fill.avg_price == pytest.approx(100.0 + 5 * TICK_SIZE)
