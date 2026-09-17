"""F13 - golden-file freeze of the QQQ ORB result.

Fixture F13 in section 12 of the backtest pipeline plan: *freeze the current
QQQ ORB result as a golden file before metric normalization, so that every
metric change is explicit rather than silent.*

The snapshot is deliberately split into two sections, and the split is the
whole point of the fixture:

``simulation``
    What the simulator did - trade count, ledger digest, equity digest, exit
    reason counts, execution honesty counters. Increment P1 redefines how
    metrics are *computed* and must leave this section byte-identical. If P1
    moves a digest, P1 changed the backtest, which is not what it claims to do.

``metrics``
    What was published from that simulation. P1 is expected to change these.
    Each change has to be re-frozen deliberately, which makes it reviewable.

Without the split, a golden file can only say "something changed" and every
metric edit forces a wholesale re-freeze that hides an accidental behavioural
change inside a pile of intended metric changes.

Regenerate after an intended change::

    $env:GOLDEN_UPDATE = "1"; python -m pytest tests/integration/test_golden_orb.py

Market data is gitignored, so these tests skip when it is unavailable rather
than failing in a checkout that never received a copy.
"""

from __future__ import annotations

import collections
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.data.paths import (
    MarketDataNotFoundError,
    resolve_market_data_dir,
)
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.research_pipeline.evidence import (
    equity_curve_digest,
    trade_ledger_digest,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"

SNAPSHOT_SCHEMA_VERSION = 1

SYMBOL = "QQQ"
RULESET = "orb_production"
INITIAL_CAPITAL = 100_000.0

# Rounded before storage so that platform-level float noise in the last bits
# cannot fail a comparison that is semantically equal. Nine places is far
# finer than any decision made from these numbers.
_PRECISION = 9

# Scalar metrics only. r_multiples is a 1200-element list that would bloat the
# file without adding signal the aggregate metrics do not already carry, and
# the ledger digest already pins the trades it derives from.
_CONVEXITY_FIELDS = (
    "n_trades", "win_rate", "avg_win_r", "avg_loss_r", "expectancy_r",
    "max_win_r", "max_loss_r", "top10_pct", "skewness", "max_losing_streak",
    "total_pnl", "stop_wins", "stop_losses", "eod_wins", "eod_losses",
    "first_date", "last_date",
)

_EQUITY_FIELDS = (
    "total_return", "annualized_return", "sharpe_ratio", "max_drawdown",
    "max_drawdown_duration_days",
)

WINDOWS = {
    "qqq_orb_2022": ("2022-01-01", "2022-12-31", False),
    "qqq_orb_2019_2023": ("2019-01-01", "2023-12-31", True),
}


def _market_data_available() -> bool:
    try:
        return (resolve_market_data_dir() / f"{SYMBOL}.parquet").is_file()
    except MarketDataNotFoundError:
        return False


requires_market_data = pytest.mark.skipif(
    not _market_data_available(),
    reason=(
        f"{SYMBOL}.parquet not present. Market data is gitignored; set "
        f"BACKTEST__DATA_DIR to a directory containing it."
    ),
)


def _round(value: Any) -> Any:
    """Round floats for storage, passing everything else through."""
    if isinstance(value, bool) or not isinstance(value, float):
        return value
    if math.isnan(value) or math.isinf(value):
        # Preserved as a string rather than rejected: a non-finite metric is
        # exactly the kind of defect this fixture exists to make visible, and
        # crashing the snapshot would hide it.
        return f"non-finite:{value}"
    return round(value, _PRECISION)


def _ny(value: str) -> Any:
    """Parse a date into the market timezone the parquet index uses."""
    return pd.Timestamp(value, tz="America/New_York").to_pydatetime()


def build_snapshot(start: str, end: str) -> dict[str, Any]:
    """Run the backtest and reduce it to a comparable snapshot."""
    ruleset = RuleSetLoader.from_name(RULESET)
    engine = BacktestEngine(
        ruleset=ruleset,
        initial_capital=INITIAL_CAPITAL,
        execution_realism=ExecutionRealismConfig.realistic(),
    )
    result = engine.run(SYMBOL, _ny(start), _ny(end))

    equity_curve = result.equity.equity_curve

    simulation = {
        "n_trades": len(result.trades),
        "trade_ledger_sha256": trade_ledger_digest(result.trades),
        "equity_curve_sha256": equity_curve_digest(equity_curve),
        "n_equity_points": int(len(equity_curve)),
        "exit_reason_counts": dict(
            sorted(
                collections.Counter(
                    t.exit_reason for t in result.trades
                ).items(),
                key=lambda kv: (kv[0] is None, kv[0]),
            )
        ),
        "execution_diagnostics": {
            k: _round(v)
            for k, v in sorted(result.execution_diagnostics.items())
        },
    }

    metrics = {
        "overall": {
            f: _round(getattr(result.overall, f)) for f in _CONVEXITY_FIELDS
        },
        "equity": {
            f: _round(getattr(result.equity, f)) for f in _EQUITY_FIELDS
        },
        "by_year": {
            str(year): {
                f: _round(getattr(m, f)) for f in _CONVEXITY_FIELDS
            }
            for year, m in sorted(result.by_year.items())
        },
    }

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "run": {
            "symbol": SYMBOL,
            "start": start,
            "end": end,
            "ruleset_name": result.ruleset_name,
            "ruleset_version": result.ruleset_version,
            "initial_capital": INITIAL_CAPITAL,
            "execution_realism": "realistic",
        },
        "simulation": simulation,
        "metrics": metrics,
    }


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def _write(name: str, snapshot: dict[str, Any]) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    _golden_path(name).write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _diff(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Return human-readable differences between two snapshot subtrees."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            where = f"{path}.{key}" if path else key
            if key not in expected:
                out.append(f"{where}: added, now {actual[key]!r}")
            elif key not in actual:
                out.append(f"{where}: removed, was {expected[key]!r}")
            else:
                out.extend(_diff(expected[key], actual[key], where))
        return out
    if isinstance(expected, float) and isinstance(actual, float):
        if expected == pytest.approx(actual, rel=1e-9, abs=1e-9):
            return []
        return [f"{path}: {expected!r} -> {actual!r}"]
    return [] if expected == actual else [f"{path}: {expected!r} -> {actual!r}"]


@requires_market_data
@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n, marks=pytest.mark.slow if slow else ()
        )
        for n, (_s, _e, slow) in WINDOWS.items()
    ],
)
def test_orb_golden_snapshot(name: str) -> None:
    start, end, _slow = WINDOWS[name]
    snapshot = build_snapshot(start, end)
    path = _golden_path(name)

    if os.environ.get("GOLDEN_UPDATE") == "1" or not path.is_file():
        _write(name, snapshot)
        if os.environ.get("GOLDEN_UPDATE") != "1":
            pytest.skip(f"Created missing golden file {path.name}; re-run to compare.")
        return

    expected = json.loads(path.read_text(encoding="utf-8"))

    sim_diff = _diff(expected["simulation"], snapshot["simulation"], "simulation")
    assert not sim_diff, (
        "The simulation changed, not just the metrics.\n"
        + "\n".join(f"  {d}" for d in sim_diff)
        + "\n\nA metric-normalization change (P1) must leave this section "
        "identical. If the behavioural change was intended, say so explicitly "
        "and re-freeze with GOLDEN_UPDATE=1."
    )

    metric_diff = _diff(expected["metrics"], snapshot["metrics"], "metrics")
    assert not metric_diff, (
        "Published metrics changed against the frozen baseline.\n"
        + "\n".join(f"  {d}" for d in metric_diff)
        + "\n\nIf intended, re-freeze with GOLDEN_UPDATE=1 and record the "
        "reason in the plan's execution status section."
    )
