"""V3: five hand-verified single-session datasets run through the real engine.

Arithmetic (equity 100,000, 1% risk = 1,000; stop at the opposite OR level):
risk/share 2.0 -> 500 shares -> initial_risk 1,000.
"""

import pytest
from hand_sessions import build_session, run_session

OR_LONG = (100, 101, 100, 101)  # OR high 101, low 100, full-body bar


def _run(overrides, tmp_path, first_close=100):
    return run_session(build_session(overrides, first_close), tmp_path)


def test_long_breakout_exits_at_eod(tmp_path):
    trades = _run(
        {"09:30": OR_LONG, "09:35": (101, 102.5, 101, 102), "15:50": (102, 103, 102, 103)},
        tmp_path,
    )
    assert len(trades) == 1
    t = trades[0]
    assert (t.entry_price, t.exit_price, t.exit_reason) == (102.0, 103.0, "EOD")
    assert t.initial_risk == pytest.approx(1000.0)
    assert t.pnl / t.initial_risk == pytest.approx(0.5)
    assert t.mae_r == pytest.approx(0.0)
    assert t.mfe_r == pytest.approx(0.5)


def test_short_breakdown_hits_stop(tmp_path):
    trades = _run(
        {"09:30": (100, 100, 99, 99), "09:35": (99, 99, 97.5, 98), "09:40": (98, 100.5, 98, 100)},
        tmp_path,
    )
    assert len(trades) == 1
    t = trades[0]
    assert (t.entry_price, t.exit_price, t.exit_reason) == (98.0, 100.0, "STOP")
    assert t.pnl / t.initial_risk == pytest.approx(-1.0)
    assert t.mae_r == pytest.approx(-1.0)
    assert t.mfe_r == pytest.approx(0.0)


def test_no_breakout_no_trade(tmp_path):
    assert _run({"09:30": OR_LONG, "09:35": (101, 101, 100.5, 100.8)}, tmp_path) == []


def test_ambiguous_bar_resolves_to_stop_first(tmp_path):
    # Bar makes a new high (104) and also trades through the stop (99.5);
    # conservative resolution assumes the stop came first.
    trades = _run(
        {"09:30": OR_LONG, "09:35": (101, 102.5, 101, 102), "09:40": (102, 104, 99.5, 103)},
        tmp_path,
    )
    assert len(trades) == 1
    t = trades[0]
    assert (t.exit_price, t.exit_reason) == (100.0, "STOP")
    assert t.pnl / t.initial_risk == pytest.approx(-1.0)
    assert t.mfe_r == pytest.approx(0.0)


def test_breakout_after_entry_cutoff_no_trade(tmp_path):
    assert _run({"09:30": OR_LONG, "15:05": (101, 103, 101, 102.5)}, tmp_path) == []
