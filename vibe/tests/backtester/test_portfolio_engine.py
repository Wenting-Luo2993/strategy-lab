from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.backtester.core.commission import CommissionModel
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.core.exit_slippage import FixedTickExitSlippage
from vibe.backtester.core.portfolio_engine import (
    CausalSignalRanker,
    IncompleteSessionPolicy,
    PortfolioBacktestEngine,
    PortfolioSimulationConfig,
    RankedSignal,
    _compute_causal_adv,
    merge_symbol_events,
)
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")


class SimultaneousSignalRunner:
    def __init__(
        self,
        signals: dict[tuple[str, int, int], int] | None = None,
    ) -> None:
        self.open_symbols: set[str] = set()
        self.signals = signals

    def generate_signal(
        self,
        symbol: str,
        current_bar: dict[str, Any],
        df_context: pd.DataFrame,
    ) -> tuple[int, dict[str, Any]]:
        timestamp = current_bar["timestamp"]
        if self.signals is None:
            signal = 1 if (timestamp.hour, timestamp.minute) == (9, 30) else 0
        else:
            signal = self.signals.get(
                (symbol, timestamp.hour, timestamp.minute), 0
            )
        if signal in (1, -1):
            return signal, {
                "stop_loss": 99.0 if signal == 1 else 101.0,
                "take_profit": None,
            }
        return 0, {}

    def track_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        take_profit: float | None,
        stop_loss: float,
        timestamp: datetime,
    ) -> None:
        self.open_symbols.add(symbol)

    def entry_stop_trigger(
        self,
        signal: int,
        metadata: dict[str, Any],
    ) -> float | None:
        return None

    def close_position(self, symbol: str) -> None:
        self.open_symbols.discard(symbol)

    def reset_daily_state(self, symbol: str) -> None:
        return None


def _frame(
    *,
    final_close: float = 101.0,
    omit_middle: bool = False,
) -> pd.DataFrame:
    timestamps = [
        datetime(2024, 1, 2, 9, 30, tzinfo=ET),
        datetime(2024, 1, 2, 9, 35, tzinfo=ET),
        datetime(2024, 1, 2, 15, 55, tzinfo=ET),
    ]
    if omit_middle:
        timestamps.pop(1)
    closes = [100.0, 100.0, final_close]
    if omit_middle:
        closes.pop(1)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.25 for value in closes],
            "low": [value - 0.25 for value in closes],
            "close": closes,
            "volume": [10_000.0] * len(closes),
            "ATR_14": [1.0] * len(closes),
        },
        index=pd.DatetimeIndex(timestamps),
    )


def _four_bar_frame(*, stop_at_940: bool = False) -> pd.DataFrame:
    timestamps = [
        datetime(2024, 1, 2, 9, 30, tzinfo=ET),
        datetime(2024, 1, 2, 9, 35, tzinfo=ET),
        datetime(2024, 1, 2, 9, 40, tzinfo=ET),
        datetime(2024, 1, 2, 15, 55, tzinfo=ET),
    ]
    lows = [99.75, 99.75, 98.5 if stop_at_940 else 99.75, 100.75]
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 101.0],
            "high": [100.25, 100.25, 100.25, 101.25],
            "low": lows,
            "close": [100.0, 100.0, 100.0, 101.0],
            "volume": [10_000.0] * 4,
            "ATR_14": [1.0] * 4,
        },
        index=pd.DatetimeIndex(timestamps),
    )


def _custom_engine(
    signals: dict[tuple[str, int, int], int],
    *,
    max_open_positions: int | None = 1,
    max_symbol_exposure_pct: float = 1.0,
    incomplete_session_policy: IncompleteSessionPolicy = (
        IncompleteSessionPolicy.FAIL
    ),
) -> PortfolioBacktestEngine:
    ruleset = RuleSetLoader.from_name("orb_production")
    return PortfolioBacktestEngine(
        ruleset=ruleset,
        initial_capital=10_000.0,
        slippage_ticks=0,
        execution_realism=ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.zero(),
            exit_slippage=FixedTickExitSlippage.zero(),
        ),
        portfolio_config=PortfolioSimulationConfig(
            max_symbol_exposure_pct=max_symbol_exposure_pct,
            max_open_positions=max_open_positions,
            incomplete_session_policy=incomplete_session_policy,
        ),
        runner_factory=lambda _ruleset, _reject: SimultaneousSignalRunner(
            signals
        ),
    )


def _engine(priority: tuple[str, ...]) -> PortfolioBacktestEngine:
    ruleset = RuleSetLoader.from_name("orb_production")
    return PortfolioBacktestEngine(
        ruleset=ruleset,
        initial_capital=10_000.0,
        slippage_ticks=0,
        execution_realism=ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.zero(),
            exit_slippage=FixedTickExitSlippage.zero(),
        ),
        portfolio_config=PortfolioSimulationConfig(
            max_symbol_exposure_pct=1.0,
            max_open_positions=1,
            symbol_priority=priority,
        ),
        runner_factory=lambda _ruleset, _reject: SimultaneousSignalRunner(),
    )


def _concentrated_engine() -> PortfolioBacktestEngine:
    ruleset = RuleSetLoader.from_name("orb_production")
    return PortfolioBacktestEngine(
        ruleset=ruleset,
        initial_capital=10_000.0,
        slippage_ticks=0,
        execution_realism=ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.zero(),
            exit_slippage=FixedTickExitSlippage.zero(),
        ),
        portfolio_config=PortfolioSimulationConfig(
            max_symbol_exposure_pct=0.34,
            max_open_positions=3,
        ),
        runner_factory=lambda _ruleset, _reject: SimultaneousSignalRunner(),
    )


def _run(
    engine: PortfolioBacktestEngine,
    frames: dict[str, pd.DataFrame],
):
    return engine.run_frames(
        frames,
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 2, 23, 59, tzinfo=ET),
    )


def test_merged_stream_is_sorted_and_preserves_missing_bar_events():
    events = list(
        merge_symbol_events(
            {"BBB": _frame(omit_middle=True), "AAA": _frame()}
        )
    )

    assert [timestamp for timestamp, _ in events] == sorted(
        timestamp for timestamp, _ in events
    )
    assert events[1][1] == ("AAA",)


def test_ranker_input_cannot_observe_prices_or_outcomes():
    fields = set(RankedSignal.__dataclass_fields__)
    signals = [
        RankedSignal(
            symbol="AAA",
            signal_time=datetime(2024, 1, 2, 9, 30, tzinfo=ET),
            side="buy",
        ),
        RankedSignal(
            symbol="BBB",
            signal_time=datetime(2024, 1, 2, 9, 30, tzinfo=ET),
            side="buy",
        ),
    ]

    assert fields == {"symbol", "signal_time", "side"}
    assert [item.symbol for item in CausalSignalRanker(("BBB",)).rank(signals)] == [
        "BBB",
        "AAA",
    ]


def test_contention_fixture_uses_priority_and_finite_shared_capital():
    frames = {symbol: _frame() for symbol in ("AAA", "BBB", "CCC")}
    result = _run(_engine(("CCC", "BBB", "AAA")), frames)

    assert [trade.symbol for trade in result.aggregate.trades] == ["CCC"]
    assert result.aggregate.execution_diagnostics[
        "signals_rejected_max_positions"
    ] == 2
    assert result.aggregate.execution_diagnostics["min_cash"] >= 0
    assert result.aggregate.execution_diagnostics[
        "max_funded_entry_leverage"
    ] <= 1.0 + 1e-9
    assert result.aggregate.execution_diagnostics[
        "max_single_symbol_concentration"
    ] <= 1.0 + 1e-9


def test_contention_is_deterministic_across_input_order_and_future_outcomes():
    first = _run(
        _engine(("BBB",)),
        {
            "AAA": _frame(final_close=150.0),
            "BBB": _frame(final_close=90.0),
            "CCC": _frame(final_close=120.0),
        },
    )
    second = _run(
        _engine(("BBB",)),
        {
            "CCC": _frame(final_close=80.0),
            "BBB": _frame(final_close=130.0),
            "AAA": _frame(final_close=70.0),
        },
    )

    assert [trade.symbol for trade in first.aggregate.trades] == ["BBB"]
    assert [trade.symbol for trade in second.aggregate.trades] == ["BBB"]
    assert first.validation_identity != second.validation_identity

    same_data_reordered = _run(
        _engine(("BBB",)),
        {
            "CCC": _frame(final_close=120.0),
            "BBB": _frame(final_close=90.0),
            "AAA": _frame(final_close=150.0),
        },
    )
    assert first.validation_identity == same_data_reordered.validation_identity


def test_next_open_contention_cannot_use_later_intrabar_exit():
    result = _run(
        _custom_engine(
            {
                ("AAA", 9, 30): 1,
                ("BBB", 9, 35): 1,
            }
        ),
        {
            "AAA": _four_bar_frame(stop_at_940=True),
            "BBB": _four_bar_frame(),
        },
    )

    assert [trade.symbol for trade in result.aggregate.trades] == ["AAA"]
    assert result.aggregate.trades[0].exit_reason == "STOP"
    assert result.aggregate.execution_diagnostics[
        "signals_rejected_max_positions"
    ] == 1


def test_penultimate_signal_fills_at_final_bar_open_then_closes():
    result = _run(
        _custom_engine({("AAA", 9, 40): 1}),
        {"AAA": _four_bar_frame()},
    )

    assert len(result.aggregate.trades) == 1
    trade = result.aggregate.trades[0]
    assert trade.entry_time.hour == 15 and trade.entry_time.minute == 55
    assert trade.entry_price == 101.0
    assert trade.exit_reason == "EOD"


def test_short_proceeds_do_not_create_additional_buying_power():
    result = _run(
        _custom_engine(
            {
                ("AAA", 9, 30): -1,
                ("BBB", 9, 30): -1,
            },
            max_open_positions=None,
        ),
        {"AAA": _frame(), "BBB": _frame()},
    )

    assert len(result.aggregate.trades) == 1
    assert result.aggregate.execution_diagnostics[
        "signals_rejected_no_buying_power"
    ] == 1
    assert result.aggregate.execution_diagnostics[
        "max_funded_entry_leverage"
    ] <= 1.0 + 1e-9


def test_adv_uses_completed_sessions_and_ignores_future_volume():
    sessions = pd.bdate_range("2024-01-02", periods=22, tz=ET)
    frame = pd.DataFrame(
        {"volume": [1_000.0] * 22},
        index=sessions + pd.Timedelta(hours=9, minutes=30),
    )
    changed = frame.copy()
    changed.iloc[-1, 0] = 1_000_000_000.0

    baseline = _compute_causal_adv(frame, window=20)
    mutated = _compute_causal_adv(changed, window=20)

    assert baseline.iloc[-1] == 1_000.0
    assert baseline.iloc[-1] == mutated.iloc[-1]


def test_incomplete_exchange_session_fails_by_default():
    incomplete = _frame().iloc[:-1]

    with pytest.raises(ValueError, match="Incomplete exchange-session data"):
        _run(
            _custom_engine({("AAA", 9, 30): 1}),
            {"AAA": incomplete},
        )


def test_declared_incomplete_session_fallback_is_reported():
    incomplete = _frame().iloc[:-1]
    result = _run(
        _custom_engine(
            {("AAA", 9, 30): 1},
            incomplete_session_policy=(
                IncompleteSessionPolicy.LIQUIDATE_LAST_OBSERVED
            ),
        ),
        {"AAA": incomplete},
    )

    assert result.aggregate.execution_diagnostics[
        "incomplete_symbol_sessions"
    ] == 1


def test_concentration_cap_limits_each_symbol_and_reports_realized_exposure():
    result = _run(
        _concentrated_engine(),
        {symbol: _frame() for symbol in ("AAA", "BBB", "CCC")},
    )

    assert len(result.aggregate.trades) == 3
    assert {trade.quantity for trade in result.aggregate.trades} <= {
        32.0,
        34.0,
    }
    assert result.aggregate.execution_diagnostics[
        "orders_capped_by_concentration"
    ] >= 2
    assert result.aggregate.execution_diagnostics[
        "max_single_symbol_concentration"
    ] <= 0.34 + 1e-9
    assert result.aggregate.execution_diagnostics[
        "max_simultaneous_positions"
    ] == 3


def test_portfolio_result_separates_pooled_and_per_symbol_metrics():
    result = _run(
        _engine(("AAA",)),
        {"AAA": _frame(), "BBB": _frame()},
    )

    assert result.aggregate.symbol == "__PORTFOLIO__"
    assert result.aggregate.overall.n_trades == 1
    assert result.by_symbol["AAA"].n_trades == 1
    assert result.by_symbol["BBB"].n_trades == 0
    assert result.portfolio_identity["ranking_rule"] == (
        "declared_symbol_priority_then_lexical"
    )
    assert len(result.validation_identity) == 64


def test_portfolio_requires_buying_power_enforcement():
    ruleset = RuleSetLoader.from_name("orb_production")

    with pytest.raises(ValueError, match="buying-power enforcement"):
        PortfolioBacktestEngine(
            ruleset=ruleset,
            portfolio_config=PortfolioSimulationConfig(
                max_symbol_exposure_pct=1.0
            ),
        )
