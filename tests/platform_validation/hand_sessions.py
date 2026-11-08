"""Helpers that run the real engine on tiny hand-built single-session data."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

NY = ZoneInfo("America/New_York")
RULESET = Path(__file__).resolve().parents[2] / "vibe" / "rulesets" / "orb_production.yaml"
DAY = date(2024, 1, 3)


def build_session(overrides: dict[str, tuple[float, float, float, float]], first_close: float):
    """78 five-minute bars; unspecified bars are flat at the previous close."""
    rows, index = [], []
    prev = first_close
    start = datetime.combine(DAY, time(9, 30), tzinfo=NY)
    for i in range(78):
        ts = start + timedelta(minutes=5 * i)
        key = ts.strftime("%H:%M")
        o, h, l, c = overrides.get(key, (prev, prev, prev, prev))
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        index.append(ts)
        prev = c
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def run_session(bars: pd.DataFrame, tmp_path: Path):
    from vibe.backtester.core.commission import CommissionModel
    from vibe.backtester.core.engine import BacktestEngine
    from vibe.backtester.core.exit_slippage import FixedTickExitSlippage
    from vibe.backtester.core.execution_realism import ExecutionRealismConfig
    from vibe.common.ruleset.models import StrategyRuleSet

    cfg = json.loads(json.dumps(yaml.safe_load(RULESET.read_text(encoding="utf-8")), default=str))
    cfg["instruments"]["symbols"] = ["HND"]
    bars.to_parquet(tmp_path / "HND.parquet")
    engine = BacktestEngine(
        ruleset=StrategyRuleSet(**cfg),
        data_dir=str(tmp_path),
        initial_capital=100_000.0,
        slippage_ticks=0,
        execution_realism=ExecutionRealismConfig.realistic(
            commission_model=CommissionModel.zero(),
            exit_slippage=FixedTickExitSlippage.zero(),
        ),
    )
    result = engine.run(
        "HND",
        datetime.combine(DAY, time(0, 0), tzinfo=NY),
        datetime.combine(DAY, time(23, 59, 59), tzinfo=NY),
    )
    return list(result.trades)
