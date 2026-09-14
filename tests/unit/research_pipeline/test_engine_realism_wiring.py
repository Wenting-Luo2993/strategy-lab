"""The realism toggle is only useful if it actually reaches the portfolio.

P2 added ExecutionRealismConfig and taught PortfolioManager to honour it, but
nothing constructed a portfolio with a non-legacy config from a normal engine
run, so the behaviour was unreachable in practice. These tests pin the wiring:
the engine default must stay bit-identical to the pre-P2 behaviour, an explicit
realistic config must change exit pricing, and the honesty counters must be
reported in every mode.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    BuyingPowerError,
    ExecutionRealismConfig,
    GapFillPolicy,
    IntrabarExitResolution,
)
from vibe.backtester.data.paths import MarketDataNotFoundError, resolve_market_data_dir
from vibe.common.ruleset.loader import RuleSetLoader


RULESET_NAME = "orb_production"


def _engine(**kwargs) -> BacktestEngine:
    ruleset = RuleSetLoader.from_name(RULESET_NAME)
    return BacktestEngine(ruleset=ruleset, initial_capital=100_000.0, **kwargs)


class TestDefaultIsLegacy:
    def test_engine_defaults_to_legacy_semantics(self):
        engine = _engine()
        assert engine.execution_realism == ExecutionRealismConfig.legacy()

    def test_legacy_does_not_enforce_buying_power(self):
        engine = _engine()
        assert engine.execution_realism.enforce_buying_power is False

    def test_legacy_resolves_ambiguity_optimistically(self):
        engine = _engine()
        assert (
            engine.execution_realism.intrabar_exit_resolution
            is IntrabarExitResolution.OPTIMISTIC
        )

    def test_legacy_fills_gaps_at_the_stated_level(self):
        engine = _engine()
        assert engine.execution_realism.gap_fill_policy is GapFillPolicy.AT_LEVEL


class TestExplicitConfigIsHonoured:
    def test_realistic_config_is_stored_verbatim(self):
        config = ExecutionRealismConfig.realistic()
        engine = _engine(execution_realism=config)
        assert engine.execution_realism is config

    def test_realistic_enables_every_guard(self):
        engine = _engine(execution_realism=ExecutionRealismConfig.realistic())
        realism = engine.execution_realism
        assert realism.enforce_buying_power is True
        assert realism.gap_fill_policy is GapFillPolicy.AT_OPEN
        assert realism.intrabar_exit_resolution is not IntrabarExitResolution.OPTIMISTIC

    def test_config_identity_differs_between_modes(self):
        """The fingerprint must distinguish the two, or cached runs collide."""
        legacy = ExecutionRealismConfig.legacy().identity()
        realistic = ExecutionRealismConfig.realistic().identity()
        assert legacy != realistic


def _data_available() -> bool:
    try:
        resolve_market_data_dir(None, require_exists=True)
    except MarketDataNotFoundError:
        return False
    return True


requires_data = pytest.mark.skipif(
    not _data_available(), reason="market data parquet not present"
)


@requires_data
class TestEndToEndReporting:
    """A real (small) backtest, because wiring bugs hide in the seams."""

    START = pd.Timestamp("2023-01-03", tz="America/New_York").to_pydatetime()
    END = pd.Timestamp("2023-02-28", tz="America/New_York").to_pydatetime()

    def _run(self, **kwargs):
        return _engine(**kwargs).run("QQQ", self.START, self.END)

    def test_legacy_run_reports_diagnostics(self):
        result = self._run()
        diagnostics = result.execution_diagnostics
        assert diagnostics, "counters must be reported even in legacy mode"
        for key in (
            "ambiguous_exit_bars",
            "gap_through_exits",
            "min_cash",
            "max_gross_exposure_ratio",
        ):
            assert key in diagnostics

    def test_diagnostics_record_the_execution_model_version(self):
        result = self._run()
        assert result.execution_diagnostics["execution_model_version"] == float(
            EXECUTION_MODEL_VERSION
        )

    def test_gap_counter_is_non_negative_and_bounded_by_trades(self):
        result = self._run()
        gaps = result.execution_diagnostics["gap_through_exits"]
        assert 0 <= gaps <= len(result.trades)

    def test_legacy_run_is_reproducible(self):
        first = self._run()
        second = self._run()
        assert [t.pnl for t in first.trades] == [t.pnl for t in second.trades]

    def test_realistic_run_rejects_orb_unfunded_leverage(self):
        """ORB's sizer ignores cash, and on real data it overshoots badly.

        With $100k capital this ruleset tries to open roughly $158k of stock -
        about 1.58x gross leverage that the legacy engine funded silently. The
        realistic config is supposed to make that impossible to miss, so the
        run must fail loudly rather than quietly report leveraged returns.
        """
        with pytest.raises(BuyingPowerError):
            self._run(execution_realism=ExecutionRealismConfig.realistic())

    def test_realistic_run_never_improves_on_legacy_pnl(self):
        """Honesty can only cost money: gaps fill at the open, never better.

        Uses a multi-year window because gapped exits are rare for ORB - only
        3 in 1256 trades over 2019-2023, since the strategy is flat overnight.
        Buying-power enforcement is disabled here on purpose, to isolate the
        gap-pricing change from the position-sizing defect it would otherwise
        mask. See test_realistic_run_rejects_orb_unfunded_leverage for sizing.
        """
        gaps_only = replace(
            ExecutionRealismConfig.realistic(), enforce_buying_power=False
        )
        start = pd.Timestamp("2019-01-02", tz="America/New_York").to_pydatetime()
        end = pd.Timestamp("2023-12-29", tz="America/New_York").to_pydatetime()
        legacy = _engine().run("QQQ", start, end)
        realistic = _engine(execution_realism=gaps_only).run("QQQ", start, end)
        assert legacy.execution_diagnostics["gap_through_exits"] > 0
        assert realistic.overall.total_pnl <= legacy.overall.total_pnl + 1e-6

    def test_legacy_run_records_the_leverage_it_took(self):
        """The counter must expose the overshoot even when it is permitted."""
        result = self._run()
        assert result.execution_diagnostics["max_gross_exposure_ratio"] > 1.0
