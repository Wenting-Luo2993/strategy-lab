"""MAE/MFE capture on closed trades (research plan S2)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar
from vibe.common.models.trade import Trade
from vibe.research_pipeline.evidence import trade_ledger_digest

ET = ZoneInfo("America/New_York")
DAY = datetime(2024, 1, 15, tzinfo=ET)


def _at(hour: int, minute: int) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def _bar(ts: datetime, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(timestamp=ts, open=open_, high=high, low=low, close=close, volume=1_000)


def _step(pm: PortfolioManager, clock: SimulatedClock, ts: datetime, bar: Bar) -> None:
    clock.set_time(ts)
    pm.check_exits({"QQQ": bar}, clock)


def _open(pm, clock, side, price, stop, tp=None, qty=10):
    clock.set_time(_at(9, 35))
    pm.open_position(
        FillResult("QQQ", side, qty, price), stop_price=stop, timestamp=clock.now(),
        take_profit=tp,
    )


def _gross_r(trade: Trade, stop: float) -> float:
    sign = 1 if trade.side == "buy" else -1
    return sign * (trade.exit_price - trade.entry_price) / abs(trade.entry_price - stop)


def test_long_eod_exit_excursions() -> None:
    pm, clock = PortfolioManager(100_000.0), SimulatedClock()
    _open(pm, clock, "buy", 100.0, 99.0)
    _step(pm, clock, _at(10, 0), _bar(_at(10, 0), 100.0, 101.5, 99.6, 100.5))
    _step(pm, clock, _at(11, 0), _bar(_at(11, 0), 100.5, 100.8, 99.8, 100.0))
    _step(pm, clock, _at(15, 55), _bar(_at(15, 55), 100.0, 100.4, 99.9, 100.2))
    (trade,) = pm.trade_history
    assert trade.exit_reason == "EOD"
    assert trade.mfe_r == pytest.approx(1.5)
    assert trade.mae_r == pytest.approx(-0.4)


def test_long_stop_exit_uses_trigger_level_not_bar_low() -> None:
    pm, clock = PortfolioManager(100_000.0), SimulatedClock()
    _open(pm, clock, "buy", 100.0, 99.0)
    _step(pm, clock, _at(10, 0), _bar(_at(10, 0), 99.5, 99.7, 95.0, 96.0))
    (trade,) = pm.trade_history
    assert trade.exit_reason == "STOP"
    assert trade.mae_r == pytest.approx(-1.0)
    assert trade.mfe_r == pytest.approx(0.0)


def test_short_tp_exit_sign_convention() -> None:
    pm, clock = PortfolioManager(100_000.0), SimulatedClock()
    _open(pm, clock, "sell", 100.0, 101.0, tp=98.0)
    _step(pm, clock, _at(10, 0), _bar(_at(10, 0), 100.0, 100.5, 99.5, 99.8))
    _step(pm, clock, _at(10, 5), _bar(_at(10, 5), 99.8, 99.9, 97.0, 97.5))
    (trade,) = pm.trade_history
    assert trade.exit_reason == "TP"
    assert trade.mae_r == pytest.approx(-0.5)
    assert trade.mfe_r == pytest.approx(2.0)


def test_trailing_stop_keeps_initial_risk_denominator() -> None:
    pm = PortfolioManager(
        100_000.0,
        trailing_stop_config={"method": "breakeven_plus_ticks", "trigger_r": 1.0, "plus_ticks": 0},
    )
    clock = SimulatedClock()
    _open(pm, clock, "buy", 100.0, 98.0)
    _step(pm, clock, _at(10, 0), _bar(_at(10, 0), 100.5, 102.5, 100.4, 102.0))
    assert not pm.trade_history
    _step(pm, clock, _at(10, 5), _bar(_at(10, 5), 101.0, 101.2, 99.9, 100.0))
    (trade,) = pm.trade_history
    assert trade.exit_reason == "STOP"
    assert trade.mfe_r == pytest.approx(1.25)
    assert trade.mae_r == pytest.approx(0.0)


def test_excursions_none_when_risk_is_zero() -> None:
    pm, clock = PortfolioManager(100_000.0), SimulatedClock()
    _open(pm, clock, "buy", 100.0, 100.0)
    _step(pm, clock, _at(15, 55), _bar(_at(15, 55), 100.0, 100.5, 99.5, 100.0))
    (trade,) = pm.trade_history
    assert trade.mae_r is None and trade.mfe_r is None


def _decision_scenario() -> list[tuple]:
    """Fixed multi-trade scenario; returns the exit decisions only."""
    pm, clock = PortfolioManager(1_000_000.0), SimulatedClock()
    rng = random.Random(11)
    price = 100.0
    for day in range(40):
        base = DAY + timedelta(days=day)
        side = "buy" if rng.random() < 0.5 else "sell"
        stop = price - 1.0 if side == "buy" else price + 1.0
        tp = price + 1.5 if side == "buy" else price - 1.5
        clock.set_time(base.replace(hour=9, minute=35))
        pm.open_position(
            FillResult("QQQ", side, 10, price), stop_price=stop,
            timestamp=clock.now(), take_profit=tp,
        )
        minutes = 9 * 60 + 40
        while "QQQ" in pm.positions:
            ts = base.replace(hour=minutes // 60, minute=minutes % 60)
            move = rng.gauss(0, 0.5)
            open_ = price
            close = price + move
            high = max(open_, close) + abs(rng.gauss(0, 0.3))
            low = min(open_, close) - abs(rng.gauss(0, 0.3))
            clock.set_time(ts)
            pm.check_exits({"QQQ": _bar(ts, open_, high, low, close)}, clock)
            price = close
            minutes += 5
    return [
        (t.entry_time.isoformat(), t.exit_time.isoformat(), round(t.exit_price, 6), t.exit_reason)
        for t in pm.trade_history
    ]


# SHA-256 of the exit decisions of ``_decision_scenario``, captured from the
# portfolio code before MAE/MFE tracking was added.
_EXPECTED_DECISIONS_DIGEST = "3c1984167a9686021c5bfb297db1765dce3cc6b34dad8e5e83ec7b37be2d71e7"


def test_exit_decisions_unchanged_by_tracking() -> None:
    import hashlib
    import json

    digest = hashlib.sha256(json.dumps(_decision_scenario()).encode()).hexdigest()
    assert digest == _EXPECTED_DECISIONS_DIGEST


def test_excursion_invariant_over_random_run() -> None:
    pm, clock = PortfolioManager(1_000_000.0), SimulatedClock()
    rng = random.Random(5)
    price = 100.0
    stops: list[float] = []
    for day in range(120):
        base = DAY + timedelta(days=day)
        side = "buy" if day % 2 == 0 else "sell"
        stop = price - 0.8 if side == "buy" else price + 0.8
        stops.append(stop)
        clock.set_time(base.replace(hour=9, minute=35))
        pm.open_position(
            FillResult("QQQ", side, 10, price), stop_price=stop, timestamp=clock.now(),
            take_profit=price + 1.2 if side == "buy" else price - 1.2,
        )
        minutes = 9 * 60 + 40
        while "QQQ" in pm.positions:
            ts = base.replace(hour=minutes // 60, minute=minutes % 60)
            close = price + rng.gauss(0, 0.4)
            high = max(price, close) + abs(rng.gauss(0, 0.2))
            low = min(price, close) - abs(rng.gauss(0, 0.2))
            clock.set_time(ts)
            pm.check_exits({"QQQ": _bar(ts, price, high, low, close)}, clock)
            price = close
            minutes += 5
    assert len(pm.trade_history) == 120
    for trade, stop in zip(pm.trade_history, stops):
        gross = _gross_r(trade, stop)
        assert trade.mae_r <= 0.0 <= trade.mfe_r
        assert trade.mae_r - 1e-9 <= gross <= trade.mfe_r + 1e-9


def test_ledger_digest_ignores_excursions() -> None:
    def make(mae, mfe):
        return Trade(
            symbol="QQQ", side="buy", quantity=10, entry_price=100.0,
            exit_price=101.0, entry_time=_at(9, 35), exit_time=_at(10, 0),
            commission=1.0, initial_risk=10.0, exit_reason="EOD", mae_r=mae, mfe_r=mfe,
        )

    assert trade_ledger_digest([make(-0.3, 1.2)]) == trade_ledger_digest([make(None, None)])
