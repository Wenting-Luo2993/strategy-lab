"""Rulesets without a ``portfolio:`` block must backtest exactly as before.

The digests below were produced by the engines *before* the ``portfolio:``
block and the shared portfolio sizing rule existed (main at 3ed1f5c). Any
change to them means a research result published from an existing ruleset
would no longer reproduce, which the portfolio paper-trading plan forbids.

Synthetic, seeded data keeps this independent of the gitignored market data
that ``tests/integration/test_golden_orb.py`` needs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
import yaml

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.core.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioSimulationConfig,
)
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.research_pipeline.evidence import (
    equity_curve_digest,
    trade_ledger_digest,
)

ET = ZoneInfo("America/New_York")
_RULESETS = Path(__file__).resolve().parents[3] / "vibe" / "rulesets"
SESSIONS = [
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
    "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-16",
]

# Frozen on the pre-change engines; see the module docstring.
PINNED = {
    "single_legacy": "44f981fe401f0563f04828cae884beae60981219f2764a5d320f1def8b83d8ef",
    "single_realistic": "0fdd6277dfe937396f09ffb34164f00b0f0a0064b22ad6ad9101e8d35a43ecf4",
    "single_resting_stop": "f39dd169eb4730057915ea0fb36e58cca23a076ac885974b10d57ced94726a33",
    "portfolio_realistic": "306f75bbb4182525485e7bf0478a0627dad29f2190219de4d3b8b369f1795969",
}
# ``ruleset_content_sha256`` participates in published run identities, so the
# serialized form of an existing ruleset must not gain a ``portfolio`` key.
PINNED_RULESET_DUMP_SHA256 = (
    "cbc846644620817b0c71543bb36c2de23c89504e8f344f2ac5477756cb291778"
)


def synthetic_minutes(seed: int, start_price: float) -> pd.DataFrame:
    """Seeded 1-minute random walk over complete XNYS sessions."""
    rng = np.random.default_rng(seed)
    frames = []
    price = start_price
    for day in SESSIONS:
        idx = pd.date_range(
            pd.Timestamp(f"{day} 09:30", tz=ET),
            pd.Timestamp(f"{day} 15:59", tz=ET),
            freq="1min",
        )
        drift = rng.choice([-1.0, 1.0]) * 0.0004
        steps = rng.normal(drift, 0.0015, len(idx))
        closes = price * np.exp(np.cumsum(steps))
        opens = np.concatenate([[price], closes[:-1]])
        spread = np.abs(rng.normal(0, 0.0008, len(idx))) * closes
        highs = np.maximum(opens, closes) + spread
        lows = np.minimum(opens, closes) - spread
        frames.append(
            pd.DataFrame(
                {
                    "open": np.round(opens, 2),
                    "high": np.round(highs, 2),
                    "low": np.round(lows, 2),
                    "close": np.round(closes, 2),
                    "volume": rng.integers(5_000, 50_000, len(idx)).astype(float),
                },
                index=idx,
            )
        )
        price = float(closes[-1]) * (1 + rng.normal(0, 0.004))
    frame = pd.concat(frames)
    frame.index.name = "ts_event"
    return frame


def _ruleset(name: str, symbols: list[str]) -> StrategyRuleSet:
    cfg = yaml.safe_load((_RULESETS / f"{name}.yaml").read_text(encoding="utf-8"))
    cfg["instruments"]["symbols"] = symbols
    return StrategyRuleSet(**cfg)


def _bounds():
    return (
        datetime(2024, 1, 2, tzinfo=ET),
        datetime(2024, 1, 16, 23, 59, tzinfo=ET),
    )


def _digest(result) -> str:
    trades = result.trades if hasattr(result, "trades") else result.aggregate.trades
    equity = (
        result.equity.equity_curve
        if hasattr(result, "equity")
        else result.aggregate.equity.equity_curve
    )
    diagnostics = (
        result.execution_diagnostics
        if hasattr(result, "execution_diagnostics")
        else result.aggregate.execution_diagnostics
    )
    payload = {
        "n_trades": len(trades),
        "ledger": trade_ledger_digest(trades),
        "equity": equity_curve_digest(equity),
        "diagnostics": {k: round(float(v), 9) for k, v in sorted(diagnostics.items())},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def run_single(tmp_path: Path, realism: ExecutionRealismConfig, ruleset=None) -> str:
    frame = synthetic_minutes(7, 400.0)
    frame.to_parquet(tmp_path / "AAA.parquet")
    engine = BacktestEngine(
        ruleset or _ruleset("orb_production", ["AAA"]),
        data_dir=tmp_path,
        initial_capital=100_000.0,
        execution_realism=realism,
    )
    start, end = _bounds()
    return _digest(engine.run("AAA", start, end))


def run_portfolio(tmp_path: Path, ruleset=None) -> str:
    for symbol, seed, price in (("AAA", 7, 400.0), ("BBB", 11, 150.0), ("CCC", 13, 90.0)):
        synthetic_minutes(seed, price).to_parquet(tmp_path / f"{symbol}.parquet")
    engine = PortfolioBacktestEngine(
        ruleset or _ruleset("orb_production", ["AAA", "BBB", "CCC"]),
        PortfolioSimulationConfig(
            max_symbol_exposure_pct=0.5,
            max_open_positions=2,
            symbol_priority=("BBB", "AAA", "CCC"),
        ),
        data_dir=tmp_path,
        initial_capital=100_000.0,
        execution_realism=ExecutionRealismConfig.realistic(),
    )
    start, end = _bounds()
    return _digest(engine.run(["AAA", "BBB", "CCC"], start, end))


def ruleset_dump_sha256() -> str:
    dumps = {
        name: RuleSetLoader.from_name(name).model_dump(mode="python")
        for name in (
            "orb_production",
            "orb_production_no_tp",
            "orb_exp073_paper_burn_in",
        )
    }
    return hashlib.sha256(
        json.dumps(dumps, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


CASES = {
    "single_legacy": lambda p: run_single(p, ExecutionRealismConfig.legacy()),
    "single_realistic": lambda p: run_single(p, ExecutionRealismConfig.realistic()),
    "single_resting_stop": lambda p: run_single(
        p,
        ExecutionRealismConfig.realistic_resting_stop(),
        ruleset=_ruleset("orb_production_no_tp", ["AAA"]),
    ),
    "portfolio_realistic": run_portfolio,
}


def compute_all(tmp_root: Path) -> dict[str, str]:
    """Recompute every digest; use to re-freeze ``PINNED`` deliberately."""
    out = {}
    for name, case in CASES.items():
        path = tmp_root / name
        path.mkdir()
        out[name] = case(path)
    return out


@pytest.mark.parametrize("case", sorted(PINNED))
def test_backtest_without_portfolio_block_is_unchanged(tmp_path, case):
    assert CASES[case](tmp_path) == PINNED[case]

def test_existing_ruleset_serialization_is_unchanged():
    assert ruleset_dump_sha256() == PINNED_RULESET_DUMP_SHA256
