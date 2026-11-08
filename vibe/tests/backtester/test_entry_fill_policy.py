"""E6 entry fill policy — AT_NEXT_BAR_OPEN.

Live evaluates a completed bar, then submits a market order. The earliest
price that order can obtain is the *next* bar's open, so that is what the
backtest must pay. These tests pin both halves of that claim:

  - the mechanical one, that the fill really lands on the next bar's open
    (and not on the signal bar's close), exercised end to end on real data;
  - the boundary one, that a signal on a session's last bar is dropped rather
    than filled against the next morning's gapped open.
"""

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.backtester.core.engine import BacktestEngine, _next_session_bar
from vibe.backtester.core.execution_realism import (
    EntryFillPolicy,
    ExecutionRealismConfig,
)
from vibe.backtester.data.paths import resolve_market_data_dir
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")
PARQUET_DIR = resolve_market_data_dir(require_exists=False)

_SLIPPAGE_TICKS = 2
_SLIPPAGE = _SLIPPAGE_TICKS * 0.01


# ---------------------------------------------------------------------------
# The session-boundary guard, unit tested
# ---------------------------------------------------------------------------

def _frame(timestamps: list[datetime]) -> pd.DataFrame:
    """Bars whose open differs from close, so an open-priced fill is visible."""
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(len(timestamps))],
            "high": [200.0 + i for i in range(len(timestamps))],
            "low": [50.0 + i for i in range(len(timestamps))],
            "close": [150.0 + i for i in range(len(timestamps))],
            "volume": [1_000_000.0] * len(timestamps),
        },
        index=pd.DatetimeIndex(timestamps),
    )


def test_next_session_bar_prices_off_the_following_open():
    df = _frame([datetime(2024, 1, 2, 9, 30, tzinfo=ET), datetime(2024, 1, 2, 9, 35, tzinfo=ET)])

    bar = _next_session_bar(df, 0, date(2024, 1, 2))

    assert bar is not None
    # close is deliberately the next bar's open: that is what makes every
    # fill path price the order at the open without knowing about the policy.
    assert bar.close == pytest.approx(101.0)
    assert bar.open == pytest.approx(101.0)
    # ...while the rest of the bar stays real, so slippage and volume caps
    # still see the true range.
    assert bar.high == pytest.approx(201.0)
    assert bar.low == pytest.approx(51.0)
    assert bar.volume == pytest.approx(1_000_000.0)
    assert bar.timestamp == datetime(2024, 1, 2, 9, 35, tzinfo=ET)


def test_next_session_bar_refuses_to_cross_a_session_boundary():
    """The row after a session's last bar is the next morning's open.

    Filling there would hand the entry an overnight gap it could never have
    traded, which is the very bug this policy exists to remove.
    """
    df = _frame([datetime(2024, 1, 2, 15, 55, tzinfo=ET), datetime(2024, 1, 3, 9, 30, tzinfo=ET)])

    assert _next_session_bar(df, 0, date(2024, 1, 2)) is None


def test_next_session_bar_returns_none_at_end_of_data():
    df = _frame([datetime(2024, 1, 2, 9, 30, tzinfo=ET)])

    assert _next_session_bar(df, 0, date(2024, 1, 2)) is None


# ---------------------------------------------------------------------------
# The fill price itself, end to end
# ---------------------------------------------------------------------------

pytestmark_data = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available",
)


def _five_minute_bars() -> pd.DataFrame:
    raw = pd.read_parquet(PARQUET_DIR / "QQQ.parquet")
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("America/New_York")
    return raw.resample("5min", closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()


def _run(policy: EntryFillPolicy):
    engine = BacktestEngine(
        ruleset=RuleSetLoader.from_name("orb_production"),
        data_dir=PARQUET_DIR,
        initial_capital=100_000.0,
        slippage_ticks=_SLIPPAGE_TICKS,
        execution_realism=replace(
            ExecutionRealismConfig.realistic(), entry_fill_policy=policy
        ),
    )
    return engine.run(
        symbol="QQQ",
        start_date=pd.Timestamp(2022, 1, 1, tz=ET),
        end_date=pd.Timestamp(2022, 6, 30, tz=ET),
    )


def _classify(trades, bars):
    """Count entries priced off the next bar's open vs the signal bar's close."""
    at_open = at_close = checked = moved = 0
    for trade in trades:
        pos = bars.index.get_indexer([pd.Timestamp(trade.entry_time)])[0]
        if pos < 0 or pos + 1 >= len(bars):
            continue
        if bars.index[pos + 1].date() != bars.index[pos].date():
            continue
        sign = 1 if trade.side in ("buy", "long") else -1
        next_open = float(bars.iloc[pos + 1]["open"])
        signal_close = float(bars.iloc[pos]["close"])
        checked += 1
        if trade.entry_price == pytest.approx(next_open + sign * _SLIPPAGE, abs=1e-6):
            at_open += 1
        if trade.entry_price == pytest.approx(signal_close + sign * _SLIPPAGE, abs=1e-6):
            at_close += 1
        if abs(next_open - signal_close) > 1e-9:
            moved += 1
    return at_open, at_close, checked, moved


@pytestmark_data
def test_next_bar_open_policy_fills_at_the_next_bar_open():
    bars = _five_minute_bars()
    trades = _run(EntryFillPolicy.AT_NEXT_BAR_OPEN).trades
    at_open, at_close, checked, moved = _classify(trades, bars)

    assert checked > 50, "too few entries to draw a conclusion"
    # Non-vacuity: if open and close always agreed, this test could not fail.
    assert moved > checked * 0.5, (
        f"only {moved}/{checked} bars had open != prior close; the assertion "
        "below would not discriminate between the two policies"
    )
    assert at_open == checked
    assert at_close < checked, "did not discriminate against the close model"


@pytestmark_data
def test_signal_bar_close_policy_still_fills_at_the_close():
    """The mirror image, so the test above is pinning a real difference."""
    bars = _five_minute_bars()
    trades = _run(EntryFillPolicy.AT_SIGNAL_BAR_CLOSE).trades
    at_open, at_close, checked, _ = _classify(trades, bars)

    assert checked > 50
    assert at_close == checked
    assert at_open < checked


@pytestmark_data
def test_next_bar_open_is_not_a_strict_penalty():
    """Unlike E1-E4, this policy is not uniformly worse.

    A breakout that keeps running fills worse at the next open; one that fades
    fills better. Asserting a uniform direction would be wrong, so pin the
    mixture instead.
    """
    bars = _five_minute_bars()
    close_trades = {t.entry_time: t for t in _run(EntryFillPolicy.AT_SIGNAL_BAR_CLOSE).trades}
    open_trades = {t.entry_time: t for t in _run(EntryFillPolicy.AT_NEXT_BAR_OPEN).trades}

    worse = better = 0
    for ts in set(close_trades) & set(open_trades):
        trade = open_trades[ts]
        sign = 1 if trade.side in ("buy", "long") else -1
        delta = (trade.entry_price - close_trades[ts].entry_price) * sign
        if delta > 1e-9:
            worse += 1
        elif delta < -1e-9:
            better += 1

    assert worse > 0 and better > 0, (
        f"expected a mixture of better and worse entries, got {worse} worse / {better} better"
    )


def test_realistic_config_defaults_to_next_bar_open():
    assert ExecutionRealismConfig.realistic().entry_fill_policy is EntryFillPolicy.AT_NEXT_BAR_OPEN
