import pytest
from datetime import datetime, time
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
    # bar low touches stop level — intrabar detection, fills at stop_price
    bars = {"QQQ": _bar(close=474.0, low=473.0, high=480.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"
    assert pm.trade_history[0].exit_price == pytest.approx(475.0)  # fills at stop_price


def test_check_exits_intrabar_wick_stops():
    """Bar low wicks below stop even though close is above — intrabar detection fires."""
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=475.0, timestamp=clock.now())
    # low dips to 473 (below stop=475), close=476 is above stop
    bars = {"QQQ": _bar(close=476.0, low=473.0, high=481.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1  # stop fires on intrabar wick
    assert pm.trade_history[0].exit_price == pytest.approx(475.0)


def test_check_exits_short_stop_hit():
    """Short position stop fires when bar.high >= stop_price; fills at stop_price."""
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    short_fill = _fill(side="sell", price=480.0, qty=10)
    pm.open_position(short_fill, stop_price=485.0, timestamp=clock.now())
    # bar high rises above short stop
    bars = {"QQQ": _bar(close=484.0, low=479.0, high=487.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"
    assert pm.trade_history[0].exit_price == pytest.approx(485.0)


def test_short_cash_accounting():
    """Opening a short adds cash (receive proceeds); closing a short subtracts cash (buy back)."""
    pm = PortfolioManager(10_000.0)
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    short_fill = _fill(side="sell", price=480.0, qty=10)
    pm.open_position(short_fill, stop_price=485.0, timestamp=ts)
    assert pm.cash == pytest.approx(10_000.0 + 480.0 * 10)  # received proceeds

    pm.close_position(_fill(side="buy", price=470.0, qty=10), exit_reason="STOP", timestamp=ts)
    # bought back at 470, net cash = initial + proceeds_received - buyback_cost
    assert pm.cash == pytest.approx(10_000.0 + 480.0 * 10 - 470.0 * 10)

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


# ---------------------------------------------------------------------------
# EOD cutoff is a ruleset decision, not a simulator constant.
#
# These guard a real defect: the cutoff was a module-level constant, so
# ``exit.eod_time`` was never read. The sensitivity sweep's EOD_Time axis
# therefore reported "no sensitivity" for a knob nothing consumed, which is
# worse than reporting nothing at all.
# ---------------------------------------------------------------------------

def _open_at(pm, hour, minute):
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, hour, minute, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=400.0, timestamp=clock.now())
    return clock


def _quiet_bar():
    # Nowhere near the stop, so any exit observed must be the EOD exit.
    return {"QQQ": _bar(close=480.0, low=479.0, high=481.0)}


def test_eod_cutoff_defaults_to_ruleset_default():
    pm = PortfolioManager(10_000.0)
    assert pm.eod_cutoff == time(15, 55)
    assert pm.eod_enabled is True


def test_configured_eod_cutoff_closes_early():
    pm = PortfolioManager(10_000.0, eod_cutoff=time(12, 0))
    clock = _open_at(pm, 10, 0)
    clock.set_time(datetime(2024, 1, 15, 12, 0, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert pm.trade_history, "configured 12:00 cutoff did not close the position"
    assert pm.trade_history[0].exit_reason == "EOD"


def test_configured_eod_cutoff_holds_past_the_default():
    """The inverse direction: a later cutoff must *prevent* the 15:55 exit.

    Without this, a manager that ignored ``eod_cutoff`` and always closed at
    15:55 would still pass the early-close test above whenever the configured
    time happened to be earlier.
    """
    pm = PortfolioManager(10_000.0, eod_cutoff=time(15, 58))
    clock = _open_at(pm, 10, 0)
    clock.set_time(datetime(2024, 1, 15, 15, 55, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert not pm.trade_history, "closed at the hardcoded 15:55 despite a 15:58 cutoff"
    clock.set_time(datetime(2024, 1, 15, 15, 58, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert pm.trade_history[0].exit_reason == "EOD"


def test_eod_disabled_never_closes_on_time():
    pm = PortfolioManager(10_000.0, eod_enabled=False)
    clock = _open_at(pm, 10, 0)
    clock.set_time(datetime(2024, 1, 15, 15, 59, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert not pm.trade_history, "eod=false still produced an EOD exit"


def test_engine_threads_eod_time_from_the_ruleset():
    """The constructor seam above is useless if the engine never uses it."""
    import inspect
    from vibe.backtester.core import engine, portfolio_engine

    for module in (engine, portfolio_engine):
        source = inspect.getsource(module)
        assert "eod_cutoff=parse_hhmm(" in source, (
            f"{module.__name__} builds a PortfolioManager without passing the "
            "ruleset eod_time; the EOD_Time sweep axis is inert again"
        )
        assert "eod_enabled=" in source, f"{module.__name__} ignores exit.eod"


# ---------------------------------------------------------------------------
# Nothing is carried overnight.
#
# Making ``eod_time`` configurable removed the guarantee that an intraday
# position always closed: a ruleset setting it to the closing bell, or
# disabling ``exit.eod``, left the position open forever. It then blocked
# every later entry (the engines only enter when flat) and never reached the
# trade ledger, so the run reported *zero trades* while its equity curve kept
# moving with the unrealized position -- which the accounting validator
# correctly rejected as money created outside the trade ledger.
# ---------------------------------------------------------------------------

def test_session_end_flattens_a_position_the_eod_rule_never_closed():
    pm = PortfolioManager(10_000.0, eod_enabled=False)
    clock = _open_at(pm, 10, 0)
    clock.set_time(datetime(2024, 1, 15, 16, 0, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert not pm.trade_history, "precondition: the EOD rule must not have fired"

    pm.close_open_positions_at_session_end(_quiet_bar(), clock)
    assert pm.trade_history, "position survived the end of its session"
    assert pm.trade_history[0].exit_reason == "EOD"
    assert not pm.positions


def test_session_end_is_a_no_op_once_the_eod_rule_has_closed():
    """The inverse: the backstop must not invent a second exit.

    A backstop that closed unconditionally would double-count every trade,
    which the first test above cannot detect.
    """
    pm = PortfolioManager(10_000.0, eod_cutoff=time(15, 55))
    clock = _open_at(pm, 10, 0)
    clock.set_time(datetime(2024, 1, 15, 15, 55, tzinfo=ET))
    pm.check_exits(_quiet_bar(), clock)
    assert len(pm.trade_history) == 1

    pm.close_open_positions_at_session_end(_quiet_bar(), clock)
    assert len(pm.trade_history) == 1, "backstop re-closed an already-closed trade"


def test_session_end_skips_symbols_without_a_bar():
    pm = PortfolioManager(10_000.0, eod_enabled=False)
    clock = _open_at(pm, 10, 0)
    pm.close_open_positions_at_session_end({}, clock)
    assert pm.positions, "closed a position at a price no bar supplied"


def test_engine_calls_the_session_end_backstop():
    """A correct PortfolioManager is useless if the loop never calls it."""
    import inspect
    from vibe.backtester.core import engine

    source = inspect.getsource(engine)
    assert "close_open_positions_at_session_end(" in source, (
        "the engine relies on the configurable EOD rule alone, so a ruleset "
        "that disables it carries positions overnight again"
    )
