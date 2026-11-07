"""Execution model v7: minimum-risk floor, take-profit trade-through, and an
exactly-once buying-power cap counter.

Engine-level cases drive ``BacktestEngine`` over a synthetic parquet with a
stub signal runner, so each assertion isolates one execution rule rather than
depending on what ORB happens to do on real data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.backtester.core import engine as engine_module
from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    ExecutionRealismConfig,
    GapFillPolicy,
    IntrabarExitResolution,
)
from vibe.backtester.core.fill_simulator import FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.common.models.bar import Bar
from vibe.common.ruleset.loader import RuleSetLoader

NY = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------
# Config surface
# --------------------------------------------------------------------------


class TestConfig:
    def test_version_is_seven(self):
        assert EXECUTION_MODEL_VERSION == 7

    def test_legacy_disables_both_guards(self):
        legacy = ExecutionRealismConfig.legacy()
        assert legacy.min_risk_bps == 0.0
        assert legacy.take_profit_trade_through_ticks == 0
        assert legacy == ExecutionRealismConfig()

    def test_realistic_enables_both_guards(self):
        realistic = ExecutionRealismConfig.realistic()
        assert realistic.min_risk_bps == 5.0
        assert realistic.take_profit_trade_through_ticks == 1

    def test_identity_carries_the_new_settings(self):
        identity = ExecutionRealismConfig.realistic().identity()
        assert identity["min_risk_bps"] == 5.0
        assert identity["take_profit_trade_through_ticks"] == 1
        assert identity["execution_model_version"] == 7
        assert (
            ExecutionRealismConfig.realistic(min_risk_bps=10.0).identity()
            != identity
        )

    @pytest.mark.parametrize(
        "kwargs",
        [{"min_risk_bps": -1.0}, {"take_profit_trade_through_ticks": -1}],
    )
    def test_rejects_negative_settings(self, kwargs):
        with pytest.raises(ValueError):
            ExecutionRealismConfig(**kwargs)


class TestEntryRiskFloor:
    cfg = ExecutionRealismConfig(min_risk_bps=5.0)

    def test_disabled_floor_never_skips(self):
        legacy = ExecutionRealismConfig.legacy()
        assert not legacy.entry_risk_too_small(
            side="buy", fill_price=100.0, stop_price=100.5
        )

    @pytest.mark.parametrize(
        "side,fill,stop,expected",
        [
            ("buy", 100.0, 99.96, True),   # 4 bps
            ("buy", 100.0, 99.95, False),  # 5 bps, at the floor
            ("buy", 100.0, 100.0, True),   # fill at the stop
            ("buy", 100.0, 100.5, True),   # fill below a long's stop
            ("sell", 100.0, 100.04, True),
            ("sell", 100.0, 100.5, False),
            ("sell", 100.0, 99.5, True),   # fill above a short's stop
        ],
    )
    def test_threshold_and_wrong_side(self, side, fill, stop, expected):
        assert (
            self.cfg.entry_risk_too_small(
                side=side, fill_price=fill, stop_price=stop
            )
            is expected
        )


# --------------------------------------------------------------------------
# Take-profit trade-through
# --------------------------------------------------------------------------


class _Clock:
    def now(self) -> datetime:
        return datetime(2024, 3, 5, 11, 0, tzinfo=NY)


def _bar(o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        symbol="TEST", timestamp=_Clock().now(),
        open=o, high=h, low=l, close=c, volume=100_000,
    )


def _pm(**realism) -> PortfolioManager:
    return PortfolioManager(
        100_000, execution_realism=ExecutionRealismConfig(**realism)
    )


def _open(pm: PortfolioManager, side: str, entry: float, stop: float, tp: float):
    pm.open_position(
        FillResult(symbol="TEST", side=side, filled_qty=10, avg_price=entry),
        stop_price=stop, timestamp=_Clock().now(), take_profit=tp,
    )


class TestTakeProfitTradeThrough:
    def test_legacy_touch_fills(self):
        pm = _pm()
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(100.0, 102.0, 99.5, 101.0)}, _Clock())
        assert pm.trade_history[0].exit_reason == "TP"

    def test_touch_without_trade_through_does_not_fill_long(self):
        pm = _pm(take_profit_trade_through_ticks=1)
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(100.0, 102.0, 99.5, 101.0)}, _Clock())
        assert not pm.trade_history
        assert "TEST" in pm.positions

    def test_trade_through_fills_long_at_the_limit(self):
        pm = _pm(take_profit_trade_through_ticks=1)
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(100.0, 102.01, 99.5, 101.0)}, _Clock())
        trade = pm.trade_history[0]
        assert trade.exit_reason == "TP"
        assert trade.exit_price == pytest.approx(102.0)

    def test_short_requires_trade_through(self):
        pm = _pm(take_profit_trade_through_ticks=1)
        _open(pm, "sell", 100.0, 101.0, 98.0)
        pm.check_exits({"TEST": _bar(100.0, 100.5, 98.0, 99.0)}, _Clock())
        assert not pm.trade_history
        pm.check_exits({"TEST": _bar(99.0, 99.5, 97.99, 98.5)}, _Clock())
        assert pm.trade_history[0].exit_reason == "TP"
        assert pm.trade_history[0].exit_price == pytest.approx(98.0)

    def test_gap_open_beyond_target_still_fills_at_open(self):
        pm = _pm(
            take_profit_trade_through_ticks=1,
            gap_fill_policy=GapFillPolicy.AT_OPEN,
        )
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(102.5, 103.0, 102.4, 102.8)}, _Clock())
        assert pm.trade_history[0].exit_reason == "TP"
        assert pm.trade_history[0].exit_price == pytest.approx(102.5)

    def test_touch_plus_stop_is_a_stop_not_ambiguous(self):
        """Ambiguity uses the trade-through condition, not the touch."""
        pm = _pm(
            take_profit_trade_through_ticks=1,
            intrabar_exit_resolution=IntrabarExitResolution.OPTIMISTIC,
        )
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(100.0, 102.0, 98.5, 100.0)}, _Clock())
        assert pm.trade_history[0].exit_reason == "STOP"
        assert pm.ambiguous_exit_bars == 0

    def test_trade_through_plus_stop_is_ambiguous(self):
        pm = _pm(
            take_profit_trade_through_ticks=1,
            intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE,
        )
        _open(pm, "buy", 100.0, 99.0, 102.0)
        pm.check_exits({"TEST": _bar(100.0, 102.5, 98.5, 100.0)}, _Clock())
        assert pm.trade_history[0].exit_reason == "STOP"
        assert pm.ambiguous_exit_bars == 1


# --------------------------------------------------------------------------
# Engine: min-risk skip and buying-power counting
# --------------------------------------------------------------------------


class _StubRunner:
    """Emits one long signal per session at 10:00 with a fixed stop offset."""

    stop_offset = 0.5

    def __init__(self, ruleset, reject_retraced_wick: bool = False) -> None:
        self._fired: set = set()

    def reset_daily_state(self, symbol: str) -> None:
        pass

    def close_position(self, symbol: str) -> None:
        pass

    def track_position(self, **kwargs) -> None:
        pass

    def generate_signal(self, symbol, bar, history):
        ts = pd.Timestamp(bar["timestamp"])
        if ts.hour == 10 and ts.minute == 0 and ts.date() not in self._fired:
            self._fired.add(ts.date())
            return 1, {"stop_loss": float(bar["close"]) - self.stop_offset}
        return 0, {}


@pytest.fixture
def synthetic_data(tmp_path):
    idx = pd.date_range("2024-01-02 09:30", "2024-01-05 15:55", freq="5min", tz=NY)
    idx = idx[
        (idx.time >= pd.Timestamp("09:30").time())
        & (idx.time <= pd.Timestamp("15:55").time())
    ]
    n = len(idx)
    pd.DataFrame(
        {
            "open": [100.0] * n, "high": [100.2] * n, "low": [99.9] * n,
            "close": [100.0] * n, "volume": [1_000_000.0] * n,
        },
        index=idx,
    ).to_parquet(tmp_path / "ZZZ.parquet")
    return tmp_path


def _run(data_dir, monkeypatch, realism, stop_offset):
    stub = type("Stub", (_StubRunner,), {"stop_offset": stop_offset})
    monkeypatch.setattr(engine_module, "RuleSetRunner", stub)
    engine = BacktestEngine(
        ruleset=RuleSetLoader.from_name("orb_production"),
        data_dir=data_dir,
        initial_capital=100_000.0,
        execution_realism=realism,
    )
    result = engine.run(
        "ZZZ",
        datetime(2024, 1, 2, tzinfo=NY),
        datetime(2024, 1, 5, 23, 59, tzinfo=NY),
    )
    return engine, result


class TestEngineMinRiskSkip:
    def test_tiny_risk_entries_are_skipped_and_counted(
        self, synthetic_data, monkeypatch
    ):
        _, result = _run(
            synthetic_data, monkeypatch,
            ExecutionRealismConfig.realistic(), stop_offset=0.02,
        )
        assert result.trades == []
        assert result.execution_diagnostics["entries_skipped_min_risk"] == 4.0

    def test_fill_beyond_stop_is_skipped(self, synthetic_data, monkeypatch):
        _, result = _run(
            synthetic_data, monkeypatch,
            ExecutionRealismConfig.realistic(), stop_offset=-0.5,
        )
        assert result.trades == []
        assert result.execution_diagnostics["entries_skipped_min_risk"] == 4.0

    def test_legacy_takes_the_same_entries(self, synthetic_data, monkeypatch):
        _, result = _run(
            synthetic_data, monkeypatch,
            ExecutionRealismConfig.legacy(), stop_offset=0.02,
        )
        assert len(result.trades) == 4
        assert result.execution_diagnostics["entries_skipped_min_risk"] == 0.0

    def test_adequate_risk_entries_are_taken(self, synthetic_data, monkeypatch):
        _, result = _run(
            synthetic_data, monkeypatch,
            ExecutionRealismConfig.realistic(), stop_offset=0.5,
        )
        assert len(result.trades) == 4
        assert result.execution_diagnostics["entries_skipped_min_risk"] == 0.0


class TestBuyingPowerCapCountedOnce:
    def test_entry_capped_at_sizing_and_fill_counts_once(
        self, synthetic_data, monkeypatch
    ):
        """Every entry here is capped twice: sizing wants ~2x the account,
        and the wrapped fill clamp then trims it again, as dynamic slippage
        can. Before v7 that reported two caps per trade."""
        original = BacktestEngine._clamp_fill_to_buying_power

        def clamp_twice(self, fill, portfolio):
            clamped, _ = original(self, fill, portfolio)
            self.sizing_caps["buying_power_fill"] = (
                self.sizing_caps.get("buying_power_fill", 0) + 1
            )
            return replace(clamped, filled_qty=clamped.filled_qty - 1), True

        monkeypatch.setattr(
            BacktestEngine, "_clamp_fill_to_buying_power", clamp_twice
        )
        engine, result = _run(
            synthetic_data, monkeypatch,
            ExecutionRealismConfig.realistic(), stop_offset=0.5,
        )
        assert len(result.trades) == 4
        assert engine.sizing_caps["buying_power"] == 4
        assert engine.sizing_caps["buying_power_fill"] == 4
        assert result.execution_diagnostics["orders_capped_by_buying_power"] == 4.0
