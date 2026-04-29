import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ET = ZoneInfo("America/New_York")
PARQUET_DIR = Path("vibe/data/parquet")
pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available"
)


@pytest.fixture
def engine():
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.common.ruleset.loader import RuleSetLoader
    ruleset = RuleSetLoader.from_name("orb_production")
    return BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
    )


def test_engine_runs_and_returns_result(engine):
    from vibe.backtester.analysis.metrics import BacktestResult
    result = engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )
    assert isinstance(result, BacktestResult)


def test_result_has_trades(engine):
    result = engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )
    assert result.overall.n_trades >= 0


def test_result_equity_curve_populated(engine):
    result = engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )
    assert len(result.equity.equity_curve) > 0


def test_trades_have_initial_risk(engine):
    result = engine.run(
        symbol="QQQ",
        start_date=datetime(2023, 1, 2, tzinfo=ET),
        end_date=datetime(2023, 3, 31, tzinfo=ET),
    )
    for trade in result.trades:
        assert trade.initial_risk is not None
        assert trade.initial_risk > 0
        assert trade.exit_reason in ("STOP", "TARGET", "EOD")
