"""Stage 2: one sizing rule for live trading and backtests.

The live path (``PortfolioRiskManager.size_entry``, used by ``TradeExecutor``)
and the backtest path (``BacktestEngine._portfolio_position_size``) are fed
the same equity, exposure and prices and must agree on quantity and
``capped_by``. The backtester's ``portfolio:`` behaviour (exposure cap, daily
loss limit, portfolio-engine derivation) is checked on synthetic sessions.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.core.portfolio import PortfolioManager, Position as SimPosition
from vibe.backtester.core.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioSimulationConfig,
)
from vibe.common.models import Position
from vibe.common.risk import PositionSizer
from vibe.common.risk.portfolio_sizing import (
    CAPPED_BY_BROKER_FUNDS,
    CAPPED_BY_EQUITY_HEADROOM,
    CAPPED_BY_SYMBOL_EXPOSURE,
    PendingExposure,
    size_portfolio_entry,
)
from vibe.common.ruleset.models import PortfolioConfig, StrategyRuleSet
from vibe.common.ruleset.support import unsupported_settings
from vibe.trading_bot.execution.portfolio_risk import (
    PortfolioRiskManager,
    SessionEquitySnapshot,
)

ET = ZoneInfo("America/New_York")
_RULESETS = Path(__file__).resolve().parents[3] / "vibe" / "rulesets"
SESSION = date(2024, 1, 2)

PORTFOLIO = {
    "sizing_equity_basis": "session_start",
    "max_symbol_exposure_pct": 0.20,
    "max_gross_leverage": 1.0,
    "max_open_positions": 5,
    "daily_loss_limit_pct": 0.03,
}


def _ruleset(name="orb_production_no_tp", symbols=("TEST",), **portfolio) -> StrategyRuleSet:
    cfg = yaml.safe_load((_RULESETS / f"{name}.yaml").read_text(encoding="utf-8"))
    cfg["instruments"]["symbols"] = list(symbols)
    if portfolio is not None:
        cfg["portfolio"] = {**PORTFOLIO, **portfolio}
    return StrategyRuleSet(**cfg)


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------

def test_rule_takes_the_minimum_and_names_the_binding_cap():
    common = dict(
        sizing_equity=100_000.0,
        risk_pct=0.01,
        max_symbol_exposure_pct=0.20,
        max_gross_leverage=1.0,
        current_equity=100_000.0,
    )
    risk_bound = size_portfolio_entry(entry_price=100.0, stop_price=90.0, **common)
    assert (risk_bound.quantity, risk_bound.capped_by) == (100, None)

    exposure = size_portfolio_entry(entry_price=100.0, stop_price=99.0, **common)
    assert (exposure.risk_qty, exposure.quantity, exposure.capped_by) == (
        1_000, 200, CAPPED_BY_SYMBOL_EXPOSURE,
    )

    headroom = size_portfolio_entry(
        entry_price=100.0, stop_price=95.0, gross_open_notional=80_000.0,
        reserved_pending_notional=10_000.0, **common,
    )
    assert (headroom.quantity, headroom.capped_by) == (100, CAPPED_BY_EQUITY_HEADROOM)

    broker = size_portfolio_entry(
        entry_price=100.0, stop_price=95.0, broker_available_funds=5_000.0, **common
    )
    assert (broker.quantity, broker.capped_by) == (50, CAPPED_BY_BROKER_FUNDS)

    exhausted = size_portfolio_entry(
        entry_price=100.0, stop_price=95.0, gross_open_notional=100_050.0, **common
    )
    assert exhausted.quantity == 0 and exhausted.room_qty == 0


def test_floor_is_stable_against_float_noise():
    decision = size_portfolio_entry(
        entry_price=100.0, stop_price=99.98, sizing_equity=1_000.0, risk_pct=0.01,
        max_symbol_exposure_pct=1.0, max_gross_leverage=1.0, current_equity=1_000_000.0,
    )
    # 10 / 0.02 is 499.999...96 in binary floating point; the rule says 500.
    assert decision.risk_qty == 500


# ---------------------------------------------------------------------------
# Live vs backtest parity table
# ---------------------------------------------------------------------------

PARITY_CASES = [
    # (name, E0, NLV_now, open [(qty, entry)], entry, stop, basis)
    ("risk_bound", 100_000.0, 100_000.0, [], 100.0, 90.0, "session_start"),
    ("exposure_cap", 100_000.0, 100_000.0, [], 100.0, 99.0, "session_start"),
    ("headroom_long_short", 100_000.0, 100_000.0, [(400, 100.0), (-300, 150.0)], 50.0, 49.5, "session_start"),
    ("no_room", 50_000.0, 50_000.0, [(250, 100.0), (-250, 100.0)], 25.0, 24.0, "session_start"),
    ("e0_not_current", 100_000.0, 80_000.0, [], 200.0, 190.0, "session_start"),
    ("current_basis", 100_000.0, 80_000.0, [], 200.0, 190.0, "current"),
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=[c[0] for c in PARITY_CASES])
def test_live_and_backtest_size_identically(case, tmp_path):
    _name, e0, nlv, open_positions, entry, stop, basis = case
    config = PortfolioConfig(**{**PORTFOLIO, "sizing_equity_basis": basis})

    live = PortfolioRiskManager(config, PositionSizer(risk_pct=0.01))
    live.record_snapshot(
        SessionEquitySnapshot(SESSION, e0, datetime(2024, 1, 2, 9, 25, tzinfo=ET))
    )
    live_positions = {
        f"S{i}": Position(
            symbol=f"S{i}", side="long" if qty > 0 else "short",
            quantity=abs(qty), entry_price=price, current_price=price,
        )
        for i, (qty, price) in enumerate(open_positions)
    }
    live_decision = live.size_entry(
        entry_price=entry, stop_price=stop, current_equity=nlv,
        positions=live_positions,
    )

    engine = BacktestEngine(
        _ruleset(sizing_equity_basis=basis), data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic(),
    )
    portfolio = PortfolioManager(nlv)
    for i, (qty, price) in enumerate(open_positions):
        portfolio.positions[f"S{i}"] = SimPosition(
            symbol=f"S{i}", quantity=abs(qty), entry_price=price, stop_price=price,
            take_profit=None, side="buy" if qty > 0 else "sell",
            entry_time=datetime(2024, 1, 2, 10, tzinfo=ET),
            initial_stop_price=price, initial_risk_per_share=1.0,
        )
    backtest_qty = engine._portfolio_position_size(
        portfolio=portfolio, entry_price=entry, stop_price=stop,
        session_start_equity=e0, current_equity=nlv,
    )

    assert backtest_qty == live_decision.quantity
    assert engine._last_sizing_cap == live_decision.capped_by


def test_live_reserves_pending_oca_pair_once():
    config = PortfolioConfig(**PORTFOLIO)
    live = PortfolioRiskManager(config, PositionSizer(risk_pct=0.01))
    live.record_snapshot(
        SessionEquitySnapshot(SESSION, 10_000.0, datetime(2024, 1, 2, 9, 25, tzinfo=ET))
    )
    pending = [
        PendingExposure("AAA", 20, 100.0, group="oca"),  # 2,000
        PendingExposure("AAA", 25, 96.0, group="oca"),   # 2,400 reserved
    ]
    decision = live.size_entry(
        entry_price=50.0, stop_price=49.0, current_equity=10_000.0, positions={},
        pending_entries=pending,
    )
    assert decision.headroom == pytest.approx(7_600.0)


# ---------------------------------------------------------------------------
# Backtester behaviour with a portfolio block
# ---------------------------------------------------------------------------

def _ts(hhmm: str, day=(2024, 1, 2)) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime(*day, h, m, tzinfo=ET))


def _session(after_price: float, day=(2024, 1, 2)) -> pd.DataFrame:
    """OR 09:30-09:34 at 99/101, long breakout at 09:40, then parked."""
    idx = pd.date_range(_ts("09:30", day), _ts("15:59", day), freq="1min")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 10_000.0},
        index=idx,
    )
    df.loc[_ts("09:31", day), "high"] = 101.0
    df.loc[_ts("09:33", day), "low"] = 99.0
    df.loc[_ts("09:40", day), ["open", "high", "low", "close"]] = [100.9, 101.5, 100.9, 101.4]
    mask = df.index > _ts("09:40", day)
    df.loc[mask, ["open", "close"]] = after_price
    df.loc[mask, "high"] = after_price + 0.05
    df.loc[mask, "low"] = after_price - 0.05
    df.index.name = "ts_event"
    return df


def _run_single(tmp_path, frame, ruleset):
    frame.to_parquet(tmp_path / "TEST.parquet")
    engine = BacktestEngine(
        ruleset, data_dir=tmp_path, initial_capital=100_000.0,
        execution_realism=ExecutionRealismConfig.realistic_resting_stop(),
    )
    first = frame.index[0]
    result = engine.run(
        "TEST",
        datetime(first.year, first.month, first.day, tzinfo=ET),
        datetime(first.year, first.month, first.day, 23, 59, tzinfo=ET),
    )
    return engine, result


def test_backtest_caps_position_at_symbol_exposure_of_session_equity(tmp_path):
    engine, result = _run_single(tmp_path, _session(102.0), _ruleset())

    (trade,) = result.trades
    expected = size_portfolio_entry(
        entry_price=101.02, stop_price=99.0, sizing_equity=100_000.0, risk_pct=0.01,
        max_symbol_exposure_pct=0.20, max_gross_leverage=1.0, current_equity=100_000.0,
    )
    assert expected.capped_by == CAPPED_BY_SYMBOL_EXPOSURE
    assert trade.quantity == expected.quantity
    assert result.execution_diagnostics["orders_capped_by_symbol_exposure"] == 1.0
    assert result.execution_diagnostics["daily_loss_limit_halts"] == 0.0


def test_backtest_daily_loss_limit_flattens_and_halts(tmp_path):
    # 197 shares from 101.02 to ~100.0 loses ~$200 = 0.2% of E0.
    engine, result = _run_single(
        tmp_path, _session(100.0), _ruleset(daily_loss_limit_pct=0.0015)
    )

    (trade,) = result.trades
    assert trade.exit_reason == "DAILY_LOSS_LIMIT"
    assert result.execution_diagnostics["daily_loss_limit_halts"] == 1.0


def test_backtest_without_block_has_no_portfolio_diagnostics(tmp_path):
    _engine, result = _run_single(tmp_path, _session(102.0), _plain())
    assert "daily_loss_limit_halts" not in result.execution_diagnostics


def _plain() -> StrategyRuleSet:
    cfg = yaml.safe_load((_RULESETS / "orb_production_no_tp.yaml").read_text(encoding="utf-8"))
    cfg["instruments"]["symbols"] = ["TEST"]
    return StrategyRuleSet(**cfg)


def test_portfolio_engine_derives_policy_from_the_ruleset(tmp_path):
    ruleset = _ruleset(symbols=("AAA", "BBB"), symbol_priority=["BBB", "AAA"])
    engine = PortfolioBacktestEngine(
        ruleset, data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic(),
    )
    assert engine.portfolio_config == PortfolioSimulationConfig(
        max_symbol_exposure_pct=0.20, max_open_positions=5, symbol_priority=("BBB", "AAA"),
    )
    with pytest.raises(ValueError, match="disagrees"):
        PortfolioBacktestEngine(
            ruleset,
            PortfolioSimulationConfig(max_symbol_exposure_pct=0.5),
            data_dir=tmp_path,
            execution_realism=ExecutionRealismConfig.realistic(),
        )


def test_portfolio_engine_with_block_reports_rule_diagnostics(tmp_path):
    for symbol in ("AAA", "BBB"):
        _session(102.0).to_parquet(tmp_path / f"{symbol}.parquet")
    engine = PortfolioBacktestEngine(
        _ruleset("orb_production", symbols=("AAA", "BBB")),
        data_dir=tmp_path, initial_capital=100_000.0,
        execution_realism=ExecutionRealismConfig.realistic(),
    )
    result = engine.run(
        ["AAA", "BBB"],
        datetime(2024, 1, 2, tzinfo=ET),
        datetime(2024, 1, 2, 23, 59, tzinfo=ET),
    )
    diagnostics = result.aggregate.execution_diagnostics
    assert "orders_capped_by_symbol_exposure" in diagnostics
    for trade in result.aggregate.trades:
        assert trade.quantity * trade.entry_price <= 0.20 * 100_000.0 + 1e-6


def test_max_position_pct_is_reported_as_superseded():
    cfg = yaml.safe_load((_RULESETS / "orb_production.yaml").read_text(encoding="utf-8"))
    cfg["position_size"]["max_position_pct"] = 0.5
    cfg["portfolio"] = PORTFOLIO
    problems = unsupported_settings(StrategyRuleSet(**cfg), "live")
    assert any("superseded" in problem for problem in problems)
