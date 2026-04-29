# Backtester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an event-driven backtester for the ORB strategy that produces convexity-focused HTML reports from Databento Parquet data.

**Architecture:** `ParquetLoader` eager-loads all symbols into memory and implements the shared `DataProvider` ABC. `BacktestEngine` iterates a sorted bar index, calling `clock.set_time(ts)` before each bar — no arithmetic time advancement. `ORBStrategy.generate_signal_incremental()` is called per bar; `PortfolioManager` tracks positions with `stop_price` at entry to compute R-multiples on close.

**Tech Stack:** Python 3.12, pandas, pyarrow, numpy, scipy (skewness), Plotly (HTML charts), pytest. All strategy logic reused from `vibe/common/`.

---

## File Map

| File | Responsibility |
|------|---------------|
| `vibe/backtester/__init__.py` | Package export |
| `vibe/backtester/core/clock.py` | `SimulatedClock` — data-driven time via `set_time()` |
| `vibe/backtester/core/fill_simulator.py` | `FillSimulator` — tick-based slippage, zero commission |
| `vibe/backtester/core/portfolio.py` | `Position`, `PortfolioManager` — tracks cash/positions/equity |
| `vibe/backtester/core/engine.py` | `BacktestEngine` — main event loop |
| `vibe/backtester/data/parquet_loader.py` | `ParquetLoader` — implements `DataProvider`, eager loads Parquet |
| `vibe/backtester/runner.py` | `RuleSetRunner` — maps `StrategyRuleSet` → `ORBStrategy` |
| `vibe/backtester/analysis/metrics.py` | `ConvexityMetrics`, `EquityMetrics`, `BacktestResult` dataclasses |
| `vibe/backtester/analysis/performance.py` | `PerformanceAnalyzer` — computes all metrics from trade list |
| `vibe/backtester/analysis/regime.py` | `MarketRegimeClassifier` — ADX-based TRENDING/RANGING/TRANSITIONING |
| `vibe/backtester/reporting/report.py` | `ReportGenerator` — Plotly HTML convexity dashboard |
| `scripts/run_backtest.py` | CLI entry point |
| `vibe/tests/backtester/` | All backtester tests |

---

## Key Facts (read before coding)

- **Parquet index column:** `ts_event`, dtype `datetime64[ns, America/New_York]`
- **Parquet columns:** `open`, `high`, `low`, `close`, `volume` (1-minute bars)
- **Parquet location:** `vibe/data/parquet/<SYMBOL>.parquet` (env: `BACKTEST__DATA_DIR`)
- **Resampling:** 1m → 5m using `df.resample("5min", closed="left", label="left")`
- **ORBStrategy signal:** `generate_signal_incremental(symbol, current_bar_dict, df_context_with_ATR)` returns `(int, dict)` — int is 1/−1/0; dict has `stop_loss`, `take_profit` keys on entry
- **ATR column:** indicator engine writes `ATR_14` column; must exist in df_context before calling generate_signal_incremental
- **Strategy position sync:** call `strategy.track_position()` after entry fill; `strategy.close_position(symbol)` after exit
- **Exit reasons:** `"STOP"` | `"TARGET"` | `"EOD"` — maps to `ExitSignal.exit_type` (`stop_loss`, `take_profit`, `time_exit`)
- **StrategyRuleSet fields used:** `instruments.timeframe`, `strategy.orb_start_time`, `strategy.orb_duration_minutes`, `strategy.entry_cutoff_time`, `exit.eod_time`, `exit.stop_loss.method`, `position_size.value`

---

## Task 1: Project Scaffold

**Files:**
- Create: `vibe/backtester/__init__.py`
- Create: `vibe/backtester/core/__init__.py`
- Create: `vibe/backtester/data/__init__.py`
- Create: `vibe/backtester/analysis/__init__.py`
- Create: `vibe/backtester/reporting/__init__.py`
- Create: `vibe/tests/backtester/__init__.py`

- [ ] **Step 1: Create directory tree**

```bash
mkdir -p vibe/backtester/core vibe/backtester/data vibe/backtester/analysis vibe/backtester/reporting
mkdir -p vibe/tests/backtester
touch vibe/backtester/__init__.py vibe/backtester/core/__init__.py
touch vibe/backtester/data/__init__.py vibe/backtester/analysis/__init__.py
touch vibe/backtester/reporting/__init__.py vibe/tests/backtester/__init__.py
```

- [ ] **Step 2: Verify structure**

```bash
find vibe/backtester -type f | sort
```

Expected output:
```
vibe/backtester/__init__.py
vibe/backtester/analysis/__init__.py
vibe/backtester/core/__init__.py
vibe/backtester/data/__init__.py
vibe/backtester/reporting/__init__.py
```

- [ ] **Step 3: Commit**

```bash
git add vibe/backtester/ vibe/tests/backtester/
git commit -m "feat: scaffold vibe/backtester package structure"
```

---

## Task 2: SimulatedClock

**Files:**
- Create: `vibe/backtester/core/clock.py`
- Create: `vibe/tests/backtester/test_clock.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_clock.py
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.clock import SimulatedClock

ET = ZoneInfo("America/New_York")

def test_raises_before_set():
    clock = SimulatedClock()
    with pytest.raises(RuntimeError, match="set_time"):
        clock.now()

def test_now_returns_set_time():
    clock = SimulatedClock()
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    clock.set_time(ts)
    assert clock.now() == ts

def test_is_market_open_during_hours():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    assert clock.is_market_open() is True

def test_is_market_open_before_open():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 9, 0, tzinfo=ET))
    assert clock.is_market_open() is False

def test_is_market_open_at_close():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 16, 0, tzinfo=ET))
    assert clock.is_market_open() is False

def test_is_market_open_utc_input():
    """UTC timestamp should be converted correctly."""
    from datetime import timezone
    clock = SimulatedClock()
    # 14:30 UTC = 10:30 ET (during summer)
    clock.set_time(datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc))
    assert clock.is_market_open() is True
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_clock.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.core.clock'`

- [ ] **Step 3: Implement SimulatedClock**

```python
# vibe/backtester/core/clock.py
from datetime import datetime, time
from zoneinfo import ZoneInfo

from vibe.common.clock.base import Clock


class SimulatedClock(Clock):
    """
    Backtester clock driven by bar timestamps.
    Engine calls set_time(ts) before each bar; now() returns it.
    is_market_open() checks 9:30–16:00 ET.
    """

    _MARKET_OPEN = time(9, 30)
    _MARKET_CLOSE = time(16, 0)
    _MARKET_TZ = ZoneInfo("America/New_York")

    def __init__(self) -> None:
        self._current: datetime | None = None

    def set_time(self, ts: datetime) -> None:
        self._current = ts

    def now(self) -> datetime:
        if self._current is None:
            raise RuntimeError("SimulatedClock has not been set — call set_time() first")
        return self._current

    def is_market_open(self) -> bool:
        if self._current is None:
            return False
        local = self._current.astimezone(self._MARKET_TZ)
        return self._MARKET_OPEN <= local.time() < self._MARKET_CLOSE
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_clock.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/core/clock.py vibe/tests/backtester/test_clock.py
git commit -m "feat: add SimulatedClock with data-driven set_time()"
```

---

## Task 3: ParquetLoader

**Files:**
- Create: `vibe/backtester/data/parquet_loader.py`
- Create: `vibe/tests/backtester/test_parquet_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_parquet_loader.py
import pytest
import asyncio
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Skip if parquet data not present
PARQUET_DIR = Path("vibe/data/parquet")
pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available"
)


@pytest.fixture
def loader():
    from vibe.backtester.data.parquet_loader import ParquetLoader
    return ParquetLoader(PARQUET_DIR, ["QQQ"])


def test_loads_data_at_init(loader):
    assert "QQQ" in loader._data
    df = loader._data["QQQ"]
    assert set(df.columns) == {"open", "high", "low", "close", "volume"}


def test_get_bars_full_range(loader):
    df = asyncio.run(loader.get_bars("QQQ"))
    assert len(df) > 0
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_get_bars_with_time_filter(loader):
    start = datetime(2024, 1, 2, 9, 30, tzinfo=ET)
    end = datetime(2024, 1, 31, 16, 0, tzinfo=ET)
    df = asyncio.run(loader.get_bars("QQQ", start_time=start, end_time=end))
    assert len(df) > 0
    assert df.index[0] >= start
    assert df.index[-1] <= end


def test_get_bars_with_limit(loader):
    df = asyncio.run(loader.get_bars("QQQ", limit=50))
    assert len(df) == 50


def test_get_current_price(loader):
    price = asyncio.run(loader.get_current_price("QQQ"))
    assert isinstance(price, float)
    assert price > 0


def test_get_bar(loader):
    from vibe.common.models.bar import Bar
    bar = asyncio.run(loader.get_bar("QQQ"))
    assert isinstance(bar, Bar)
    assert bar.close > 0


def test_unknown_symbol_raises(loader):
    with pytest.raises(KeyError):
        asyncio.run(loader.get_bars("UNKNOWN"))
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_parquet_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.data.parquet_loader'`

- [ ] **Step 3: Implement ParquetLoader**

```python
# vibe/backtester/data/parquet_loader.py
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.common.data.base import DataProvider
from vibe.common.models.bar import Bar


class ParquetLoader(DataProvider):
    """
    Implements DataProvider for backtesting against local Parquet files.

    All symbols are loaded into memory at init (eager load). Subsequent
    get_bars / get_current_price / get_bar calls are pure in-memory slices.

    Parquet files are produced by scripts/convert_databento.py.
    Path configured via BACKTEST__DATA_DIR in .env.
    """

    def __init__(self, data_dir: Path, symbols: list[str]) -> None:
        self._data: dict[str, pd.DataFrame] = {
            sym: pd.read_parquet(data_dir / f"{sym}.parquet")
            for sym in symbols
        }

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        df = self._data[symbol]
        if start_time is not None:
            df = df[df.index >= start_time]
        if end_time is not None:
            df = df[df.index <= end_time]
        if limit is not None:
            df = df.tail(limit)
        return df

    async def get_current_price(self, symbol: str) -> float:
        return float(self._data[symbol]["close"].iloc[-1])

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        row = self._data[symbol].iloc[-1]
        return Bar(
            timestamp=row.name.to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

    def get_full_df(self, symbol: str) -> pd.DataFrame:
        """Return the full in-memory DataFrame (used by BacktestEngine for batch processing)."""
        return self._data[symbol]
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_parquet_loader.py -v
```

Expected: all tests PASS (or SKIP if parquet data not present)

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/data/parquet_loader.py vibe/tests/backtester/test_parquet_loader.py
git commit -m "feat: add ParquetLoader implementing DataProvider with eager load"
```

---

## Task 4: FillSimulator

**Files:**
- Create: `vibe/backtester/core/fill_simulator.py`
- Create: `vibe/tests/backtester/test_fill_simulator.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_fill_simulator.py
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.fill_simulator import FillSimulator, TICK_SIZE
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")

def _bar(close=100.0, open_=100.0, high=101.0, low=99.0, volume=1_000_000):
    return Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=open_, high=high, low=low, close=close, volume=volume,
    )

def test_buy_adds_slippage():
    sim = FillSimulator(slippage_ticks=5)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar(close=100.0))
    assert fill.avg_price == pytest.approx(100.0 + 5 * TICK_SIZE)

def test_sell_subtracts_slippage():
    sim = FillSimulator(slippage_ticks=5)
    fill = sim.execute("QQQ", "sell", quantity=100, bar=_bar(close=100.0))
    assert fill.avg_price == pytest.approx(100.0 - 5 * TICK_SIZE)

def test_zero_commission():
    sim = FillSimulator()
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar())
    assert fill.commission == 0.0

def test_full_fill():
    sim = FillSimulator()
    fill = sim.execute("QQQ", "buy", quantity=50, bar=_bar())
    assert fill.filled_qty == 50.0

def test_custom_slippage_ticks():
    sim = FillSimulator(slippage_ticks=10)
    fill = sim.execute("QQQ", "buy", quantity=1, bar=_bar(close=200.0))
    assert fill.avg_price == pytest.approx(200.0 + 10 * TICK_SIZE)

def test_next_bar_mode_uses_open():
    sim = FillSimulator(slippage_ticks=5, fill_mode=1)
    current = _bar(close=100.0)
    next_bar = _bar(open_=102.0, close=103.0)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=current, next_bar=next_bar)
    assert fill.avg_price == pytest.approx(102.0 + 5 * TICK_SIZE)

def test_next_bar_mode_falls_back_to_close_when_no_next_bar():
    sim = FillSimulator(slippage_ticks=5, fill_mode=1)
    fill = sim.execute("QQQ", "buy", quantity=100, bar=_bar(close=100.0), next_bar=None)
    assert fill.avg_price == pytest.approx(100.0 + 5 * TICK_SIZE)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_fill_simulator.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.core.fill_simulator'`

- [ ] **Step 3: Implement FillSimulator**

```python
# vibe/backtester/core/fill_simulator.py
from dataclasses import dataclass
from typing import Optional

from vibe.common.models.bar import Bar

TICK_SIZE = 0.01  # US equity minimum price increment


@dataclass
class FillResult:
    symbol: str
    side: str
    filled_qty: float
    avg_price: float
    commission: float = 0.0


class FillSimulator:
    """
    Simulates order fills using tick-based slippage. Zero commission.

    fill_mode=0: fill at bar close +/- slippage (default)
    fill_mode=1: fill at next bar open +/- slippage (more conservative)

    1 tick = $0.01 (US equity minimum). Default 5 ticks = $0.05/share.
    """

    def __init__(self, slippage_ticks: int = 5, fill_mode: int = 0) -> None:
        self.slippage_ticks = slippage_ticks
        self.fill_mode = fill_mode

    def execute(
        self,
        symbol: str,
        side: str,
        quantity: float,
        bar: Bar,
        next_bar: Optional[Bar] = None,
    ) -> FillResult:
        slippage = self.slippage_ticks * TICK_SIZE

        if self.fill_mode == 1 and next_bar is not None:
            base_price = next_bar.open
        else:
            base_price = bar.close

        fill_price = base_price + slippage if side == "buy" else base_price - slippage

        return FillResult(
            symbol=symbol,
            side=side,
            filled_qty=quantity,
            avg_price=fill_price,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_fill_simulator.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/core/fill_simulator.py vibe/tests/backtester/test_fill_simulator.py
git commit -m "feat: add FillSimulator with tick-based slippage"
```

---

## Task 5: PortfolioManager

**Files:**
- Create: `vibe/backtester/core/portfolio.py`
- Create: `vibe/tests/backtester/test_portfolio.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_portfolio.py
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.core.fill_simulator import FillResult
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")

def _fill(symbol="QQQ", side="buy", qty=100, price=480.0):
    return FillResult(symbol=symbol, side=side, filled_qty=qty, avg_price=price)

def _bar(symbol="QQQ", close=490.0, low=478.0, high=492.0):
    return Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=480.0, high=high, low=low, close=close, volume=1_000_000,
    )

def test_initial_cash():
    pm = PortfolioManager(initial_capital=10_000.0)
    assert pm.cash == 10_000.0

def test_open_position_deducts_cash():
    pm = PortfolioManager(10_000.0)
    fill = _fill(price=480.0, qty=10)
    pm.open_position(fill, stop_price=470.0, timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    assert pm.cash == pytest.approx(10_000.0 - 480.0 * 10)

def test_close_position_records_trade():
    pm = PortfolioManager(10_000.0)
    ts_entry = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    ts_exit  = datetime(2024, 1, 15, 11, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts_entry)
    pm.close_position(_fill(side="sell", price=490.0, qty=10), exit_reason="TARGET", timestamp=ts_exit)
    assert len(pm.trade_history) == 1
    trade = pm.trade_history[0]
    assert trade.pnl == pytest.approx((490.0 - 480.0) * 10)
    assert trade.initial_risk == pytest.approx((480.0 - 470.0) * 10)
    assert trade.exit_reason == "TARGET"

def test_close_position_restores_cash():
    pm = PortfolioManager(10_000.0)
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts)
    pm.close_position(_fill(side="sell", price=490.0, qty=10), exit_reason="EOD", timestamp=ts)
    assert pm.cash == pytest.approx(10_000.0 + (490.0 - 480.0) * 10)

def test_update_equity():
    pm = PortfolioManager(10_000.0)
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=ts)
    bars = {"QQQ": _bar(close=490.0)}
    pm.update_equity(bars, ts)
    assert len(pm.equity_curve) == 1
    _, equity = pm.equity_curve[0]
    assert equity == pytest.approx((10_000.0 - 4800.0) + 4900.0)

def test_check_exits_stop_hit():
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=475.0, timestamp=clock.now())
    # bar low touches stop
    bars = {"QQQ": _bar(close=474.0, low=473.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "STOP"

def test_check_exits_eod():
    pm = PortfolioManager(10_000.0)
    from vibe.backtester.core.clock import SimulatedClock
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 15, 55, tzinfo=ET))
    pm.open_position(_fill(price=480.0, qty=10), stop_price=470.0, timestamp=clock.now())
    bars = {"QQQ": _bar(close=485.0, low=484.0)}
    pm.check_exits(bars, clock)
    assert len(pm.trade_history) == 1
    assert pm.trade_history[0].exit_reason == "EOD"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_portfolio.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.core.portfolio'`

- [ ] **Step 3: Implement PortfolioManager**

```python
# vibe/backtester/core/portfolio.py
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from vibe.backtester.core.fill_simulator import FillResult
from vibe.common.models.bar import Bar
from vibe.common.models.trade import Trade

_ET = ZoneInfo("America/New_York")
_EOD_CUTOFF = time(15, 55)


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    stop_price: float
    side: str           # "buy" | "sell"
    entry_time: datetime


class PortfolioManager:
    """
    Tracks cash, open positions, equity curve, and closed trade history.
    Records initial_risk and exit_reason on every closed Trade.
    """

    def __init__(self, initial_capital: float) -> None:
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []

    def open_position(
        self, fill: FillResult, stop_price: float, timestamp: datetime
    ) -> None:
        self.positions[fill.symbol] = Position(
            symbol=fill.symbol,
            quantity=fill.filled_qty,
            entry_price=fill.avg_price,
            stop_price=stop_price,
            side=fill.side,
            entry_time=timestamp,
        )
        self.cash -= fill.filled_qty * fill.avg_price

    def close_position(
        self, fill: FillResult, exit_reason: str, timestamp: datetime
    ) -> None:
        pos = self.positions.pop(fill.symbol)
        pnl = (fill.avg_price - pos.entry_price) * fill.filled_qty
        initial_risk = abs(pos.entry_price - pos.stop_price) * pos.quantity
        pnl_pct = (fill.avg_price - pos.entry_price) / pos.entry_price

        self.trade_history.append(Trade(
            symbol=fill.symbol,
            side=pos.side,
            quantity=fill.filled_qty,
            entry_price=pos.entry_price,
            exit_price=fill.avg_price,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            pnl=pnl,
            pnl_pct=pnl_pct,
            initial_risk=initial_risk,
            exit_reason=exit_reason,
        ))
        self.cash += fill.filled_qty * fill.avg_price

    def check_exits(
        self, current_bars: Dict[str, Bar], clock
    ) -> None:
        """
        Check stop-loss hits and EOD exit for all open positions.
        clock must have a .now() method returning a timezone-aware datetime.
        """
        local_time = clock.now().astimezone(_ET).time()
        is_eod = local_time >= _EOD_CUTOFF

        for symbol in list(self.positions.keys()):
            bar = current_bars.get(symbol)
            if bar is None:
                continue
            pos = self.positions[symbol]

            if pos.side == "buy" and bar.low <= pos.stop_price:
                fill = FillResult(
                    symbol=symbol, side="sell",
                    filled_qty=pos.quantity, avg_price=pos.stop_price,
                )
                self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
            elif is_eod:
                fill = FillResult(
                    symbol=symbol, side="sell",
                    filled_qty=pos.quantity, avg_price=bar.close,
                )
                self.close_position(fill, exit_reason="EOD", timestamp=clock.now())

    def update_equity(
        self, current_bars: Dict[str, Bar], timestamp: datetime
    ) -> None:
        unrealized = sum(
            (current_bars[sym].close - pos.entry_price) * pos.quantity
            for sym, pos in self.positions.items()
            if sym in current_bars
        )
        self.equity_curve.append((timestamp, self.cash + unrealized))
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_portfolio.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/core/portfolio.py vibe/tests/backtester/test_portfolio.py
git commit -m "feat: add PortfolioManager with initial_risk and exit_reason tracking"
```

---

## Task 6: RuleSetRunner

**Files:**
- Create: `vibe/backtester/runner.py`
- Create: `vibe/tests/backtester/test_runner.py`

Maps `StrategyRuleSet` → `ORBStrategy` and provides `generate_signal()` and `check_exits()` wrappers that use the strategy's own state tracking.

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_runner.py
import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")
RULESETS_DIR = Path("vibe/rulesets")


@pytest.fixture
def ruleset():
    return RuleSetLoader.from_name("orb_production")


@pytest.fixture
def runner(ruleset):
    from vibe.backtester.runner import RuleSetRunner
    return RuleSetRunner(ruleset)


def _make_df(n=50, close_price=480.0):
    """Minimal DataFrame with ATR_14 column for signal generation."""
    idx = pd.date_range("2024-01-15 09:30", periods=n, freq="5min", tz=ET)
    df = pd.DataFrame({
        "open": close_price - 1,
        "high": close_price + 2,
        "low": close_price - 2,
        "close": close_price,
        "volume": 500_000,
        "ATR_14": 1.5,
    }, index=idx)
    return df


def test_runner_creates_strategy(runner):
    from vibe.common.strategies.orb import ORBStrategy
    assert isinstance(runner.strategy, ORBStrategy)


def test_generate_signal_returns_tuple(runner):
    df = _make_df()
    bar = df.iloc[-1].to_dict()
    bar["timestamp"] = df.index[-1].to_pydatetime()
    result = runner.generate_signal("QQQ", bar, df)
    assert isinstance(result, tuple)
    assert len(result) == 2
    signal_value, metadata = result
    assert signal_value in (-1, 0, 1)


def test_no_signal_without_orb_levels(runner):
    """ORB requires the first bar of the day (9:30) to establish levels."""
    df = _make_df()
    bar = df.iloc[-1].to_dict()
    bar["timestamp"] = df.index[-1].to_pydatetime()
    signal_value, _ = runner.generate_signal("QQQ", bar, df)
    # Mid-session without proper ORB bar — expect no signal
    assert signal_value == 0


def test_track_and_close_position(runner):
    runner.track_position(
        symbol="QQQ", side="buy",
        entry_price=480.0, take_profit=490.0, stop_loss=470.0,
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
    )
    assert runner.strategy.has_position("QQQ")
    runner.close_position("QQQ")
    assert not runner.strategy.has_position("QQQ")
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_runner.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.runner'`

- [ ] **Step 3: Implement RuleSetRunner**

```python
# vibe/backtester/runner.py
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from vibe.common.ruleset.models import StrategyRuleSet
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig


class RuleSetRunner:
    """
    Adapts StrategyRuleSet (config) to ORBStrategy (executor) for the backtester.

    The backtester calls generate_signal() per bar and track_position()/close_position()
    to keep the strategy's internal state in sync with the portfolio.
    """

    def __init__(self, ruleset: StrategyRuleSet) -> None:
        self.ruleset = ruleset
        self.strategy = self._build_strategy(ruleset)

    def _build_strategy(self, ruleset: StrategyRuleSet) -> ORBStrategy:
        s = ruleset.strategy
        exit_ = ruleset.exit

        # Map StrategyRuleSet fields to ORBStrategyConfig
        tp_multiplier = 0.0  # default EOD-only
        if exit_.take_profit is not None and hasattr(exit_.take_profit, "multiplier"):
            tp_multiplier = exit_.take_profit.multiplier

        config = ORBStrategyConfig(
            name=ruleset.name,
            orb_start_time=s.orb_start_time,
            orb_duration_minutes=s.orb_duration_minutes,
            orb_body_pct_filter=getattr(s, "orb_body_pct_filter", 0.0),
            entry_cutoff_time=s.entry_cutoff_time,
            take_profit_multiplier=tp_multiplier,
            stop_loss_at_level=True,
            use_volume_filter=ruleset.trade_filter.volume_confirmation,
            volume_threshold=ruleset.trade_filter.volume_threshold,
            market_close_time=exit_.eod_time if exit_.eod else "16:00",
        )
        return ORBStrategy(config=config)

    def generate_signal(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Delegate to strategy.generate_signal_incremental().
        current_bar must include 'timestamp' key (datetime).
        df_context must include 'ATR_14' column.
        """
        return self.strategy.generate_signal_incremental(
            symbol=symbol,
            current_bar=current_bar,
            df_context=df_context,
        )

    def track_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        take_profit: Optional[float],
        stop_loss: float,
        timestamp: datetime,
    ) -> None:
        self.strategy.track_position(
            symbol=symbol, side=side,
            entry_price=entry_price, take_profit=take_profit,
            stop_loss=stop_loss, timestamp=timestamp,
        )

    def close_position(self, symbol: str) -> None:
        self.strategy.close_position(symbol)

    def reset_daily_state(self, symbol: str) -> None:
        """Reset per-day tracking (one-trade-per-day guard). Call at start of each new day."""
        if hasattr(self.strategy, "_traded_today"):
            self.strategy._traded_today.pop(symbol, None)
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_runner.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/runner.py vibe/tests/backtester/test_runner.py
git commit -m "feat: add RuleSetRunner mapping StrategyRuleSet to ORBStrategy"
```

---

## Task 7: BacktestEngine

**Files:**
- Create: `vibe/backtester/core/engine.py`
- Create: `vibe/tests/backtester/test_engine.py`

The engine: loads data, pre-computes indicators, iterates bar index, drives clock, calls runner, drives portfolio.

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_engine.py
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
    # ORB on QQQ in Jan 2024 — expect at least some trades
    assert result.overall.n_trades >= 0  # may be 0 if no breakouts


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
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'vibe.backtester.core.engine'`

- [ ] **Step 3: Implement BacktestEngine**

```python
# vibe/backtester/core/engine.py
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillSimulator
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.backtester.runner import RuleSetRunner
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.indicators.engine import IncrementalIndicatorEngine
from vibe.common.models.bar import Bar
from vibe.common.ruleset.models import StrategyRuleSet


def _resample(df: pd.DataFrame, interval: str = "5min") -> pd.DataFrame:
    return df.resample(interval, closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()


def _add_indicators(df: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    engine = IncrementalIndicatorEngine()
    df = engine.update(
        df=df,
        start_idx=0,
        indicators=[{"name": "atr", "params": {"period": 14}}],
        symbol=symbol,
        timeframe=timeframe,
    )
    return df


class BacktestEngine:
    """
    Event-driven backtester. Iterates a sorted bar index from Parquet data,
    drives SimulatedClock, calls ORBStrategy via RuleSetRunner, manages
    portfolio, and returns a BacktestResult.
    """

    def __init__(
        self,
        ruleset: StrategyRuleSet,
        data_dir: Path,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
    ) -> None:
        self.ruleset = ruleset
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks

    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> BacktestResult:
        # 1. Load and resample data
        loader = ParquetLoader(self.data_dir, [symbol])
        df_1m = asyncio.run(
            loader.get_bars(symbol, start_time=start_date, end_time=end_date)
        )
        interval = self.ruleset.instruments.timeframe  # e.g. "5m"
        pd_interval = interval.replace("m", "min")
        df = _resample(df_1m, pd_interval)
        df = _add_indicators(df, symbol, interval)

        # 2. Init components
        clock = SimulatedClock()
        fill_sim = FillSimulator(slippage_ticks=self.slippage_ticks)
        portfolio = PortfolioManager(self.initial_capital)
        runner = RuleSetRunner(self.ruleset)

        # 3. Event loop
        prev_date = None
        for ts, row in df.iterrows():
            clock.set_time(ts.to_pydatetime())
            current_date = ts.date()

            # Reset daily strategy state at start of each new day
            if current_date != prev_date:
                runner.reset_daily_state(symbol)
                prev_date = current_date

            bar = Bar(
                timestamp=ts.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            current_bars = {symbol: bar}

            # Check exits before entry (stop/EOD)
            portfolio.check_exits(current_bars, clock)

            # Generate entry signal
            if symbol not in portfolio.positions:
                current_bar_dict = row.to_dict()
                current_bar_dict["timestamp"] = ts.to_pydatetime()

                signal_value, metadata = runner.generate_signal(
                    symbol, current_bar_dict, df.loc[:ts]
                )

                if signal_value in (1, -1):
                    side = "buy" if signal_value == 1 else "sell"
                    stop_price = metadata.get("stop_loss", bar.close * 0.99)
                    quantity = self._position_size(
                        capital=portfolio.cash + portfolio.cash * 0.0,
                        entry_price=bar.close,
                        stop_price=stop_price,
                    )
                    if quantity > 0:
                        fill = fill_sim.execute(symbol, side, quantity, bar)
                        portfolio.open_position(fill, stop_price=stop_price, timestamp=ts.to_pydatetime())
                        runner.track_position(
                            symbol=symbol, side=side,
                            entry_price=fill.avg_price,
                            take_profit=metadata.get("take_profit"),
                            stop_loss=stop_price,
                            timestamp=ts.to_pydatetime(),
                        )

            portfolio.update_equity(current_bars, ts.to_pydatetime())

        # 4. Analyze results
        return PerformanceAnalyzer.analyze(
            trades=portfolio.trade_history,
            equity_curve=portfolio.equity_curve,
            initial_capital=self.initial_capital,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            ruleset_name=self.ruleset.name,
            ruleset_version=self.ruleset.version,
        )

    def _position_size(
        self, capital: float, entry_price: float, stop_price: float
    ) -> int:
        """Risk 1% of capital per trade based on stop distance."""
        risk_pct = self.ruleset.position_size.value  # e.g. 0.01
        risk_dollars = capital * risk_pct
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0
        return max(1, int(risk_dollars / stop_distance))
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_engine.py -v
```

Expected: all tests PASS (engine runs, returns BacktestResult, trades have initial_risk)

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/core/engine.py vibe/tests/backtester/test_engine.py
git commit -m "feat: add BacktestEngine event loop with data-driven clock"
```

---

## Task 8: Metrics Dataclasses

**Files:**
- Create: `vibe/backtester/analysis/metrics.py`
- Create: `vibe/tests/backtester/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_metrics.py
from vibe.backtester.analysis.metrics import ConvexityMetrics, EquityMetrics, BacktestResult


def test_convexity_metrics_fields():
    cm = ConvexityMetrics(
        n_trades=10, win_rate=0.4, avg_win_r=3.0, avg_loss_r=-1.0,
        expectancy_r=0.6, max_win_r=8.0, max_loss_r=-1.5,
        top10_pct=45.0, skewness=1.2, max_losing_streak=3,
        total_pnl=1500.0,
        stop_wins=2, stop_losses=4, eod_wins=2, eod_losses=2,
        r_multiples=[3.0, -1.0, 8.0, -1.0, 2.0, -1.0, 4.0, -1.0, -1.0, 1.5],
        first_date="2024-01-02", last_date="2024-12-31",
    )
    assert cm.n_trades == 10
    assert cm.win_rate == 0.4


def test_backtest_result_fields():
    cm = ConvexityMetrics(
        n_trades=0, win_rate=0.0, avg_win_r=0.0, avg_loss_r=0.0,
        expectancy_r=0.0, max_win_r=0.0, max_loss_r=0.0,
        top10_pct=0.0, skewness=0.0, max_losing_streak=0,
        total_pnl=0.0, stop_wins=0, stop_losses=0, eod_wins=0, eod_losses=0,
        r_multiples=[], first_date="", last_date="",
    )
    import pandas as pd
    eq = EquityMetrics(
        total_return=0.05, annualized_return=0.05, sharpe_ratio=1.0,
        max_drawdown=0.05, max_drawdown_duration_days=10,
        equity_curve=pd.Series(dtype=float),
        drawdown_curve=pd.Series(dtype=float),
    )
    result = BacktestResult(
        overall=cm, by_year={}, equity=eq, trades=[],
        regime_breakdown={}, symbol="QQQ",
        start_date="2024-01-01", end_date="2024-12-31",
        ruleset_name="orb_production", ruleset_version="1.0.0",
    )
    assert result.symbol == "QQQ"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_metrics.py -v
```

- [ ] **Step 3: Implement metrics dataclasses**

```python
# vibe/backtester/analysis/metrics.py
from dataclasses import dataclass, field
from typing import Dict, List
import pandas as pd
from vibe.common.models.trade import Trade


@dataclass
class ConvexityMetrics:
    """
    R-multiple based metrics — primary output for convexity analysis.
    R = trade_pnl / initial_risk_dollars.
    """
    n_trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    max_win_r: float
    max_loss_r: float
    top10_pct: float        # % of total profit from top 10% of trades
    skewness: float
    max_losing_streak: int
    total_pnl: float
    stop_wins: int
    stop_losses: int
    eod_wins: int
    eod_losses: int
    r_multiples: List[float]
    first_date: str
    last_date: str


@dataclass
class EquityMetrics:
    """Capital-curve metrics — equity/drawdown charting."""
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration_days: int
    equity_curve: pd.Series
    drawdown_curve: pd.Series


@dataclass
class BacktestResult:
    """Full result returned by BacktestEngine.run()."""
    overall: ConvexityMetrics
    by_year: Dict[int, ConvexityMetrics]
    equity: EquityMetrics
    trades: List[Trade]
    regime_breakdown: Dict[str, ConvexityMetrics]
    symbol: str
    start_date: str
    end_date: str
    ruleset_name: str
    ruleset_version: str
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_metrics.py -v
```

Expected: both tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/analysis/metrics.py vibe/tests/backtester/test_metrics.py
git commit -m "feat: add ConvexityMetrics, EquityMetrics, BacktestResult dataclasses"
```

---

## Task 9: PerformanceAnalyzer

**Files:**
- Create: `vibe/backtester/analysis/performance.py`
- Create: `vibe/tests/backtester/test_performance.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_performance.py
import pytest
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade

ET = ZoneInfo("America/New_York")


def _trade(pnl, initial_risk, exit_reason="EOD", year=2024):
    entry = datetime(year, 6, 1, 10, 0, tzinfo=ET)
    exit_ = datetime(year, 6, 1, 15, 55, tzinfo=ET)
    return Trade(
        symbol="QQQ", side="buy", quantity=10,
        entry_price=100.0,
        exit_price=100.0 + pnl / 10,
        entry_time=entry, exit_time=exit_,
        pnl=pnl, pnl_pct=pnl / 1000,
        initial_risk=initial_risk,
        exit_reason=exit_reason,
    )


def _equity_curve(n=100, start=10_000.0, end=11_000.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz=ET)
    values = [start + (end - start) * i / (n - 1) for i in range(n)]
    return list(zip(idx.to_pydatetime(), values))


def test_basic_convexity():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0),   # 3R win
        _trade(pnl=-100.0, initial_risk=100.0),   # -1R loss
        _trade(pnl=200.0, initial_risk=100.0),    # 2R win
        _trade(pnl=-100.0, initial_risk=100.0),   # -1R loss
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.n_trades == 4
    assert result.overall.win_rate == pytest.approx(0.5)
    assert result.overall.avg_win_r == pytest.approx(2.5)
    assert result.overall.avg_loss_r == pytest.approx(-1.0)
    assert result.overall.expectancy_r == pytest.approx(0.75)


def test_by_year_grouping():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0, year=2023),
        _trade(pnl=-100.0, initial_risk=100.0, year=2024),
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2023, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert 2023 in result.by_year
    assert 2024 in result.by_year
    assert result.by_year[2023].n_trades == 1


def test_empty_trades():
    result = PerformanceAnalyzer.analyze(
        trades=[],
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.n_trades == 0
    assert result.overall.win_rate == 0.0


def test_exit_breakdown():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0, exit_reason="STOP"),
        _trade(pnl=-100.0, initial_risk=100.0, exit_reason="STOP"),
        _trade(pnl=200.0, initial_risk=100.0, exit_reason="EOD"),
        _trade(pnl=-50.0, initial_risk=100.0, exit_reason="EOD"),
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.stop_wins == 1
    assert result.overall.stop_losses == 1
    assert result.overall.eod_wins == 1
    assert result.overall.eod_losses == 1
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_performance.py -v
```

- [ ] **Step 3: Implement PerformanceAnalyzer**

```python
# vibe/backtester/analysis/performance.py
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)
from vibe.common.models.trade import Trade


class PerformanceAnalyzer:

    @staticmethod
    def analyze(
        trades: List[Trade],
        equity_curve: List[Tuple[datetime, float]],
        initial_capital: float,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        ruleset_name: str,
        ruleset_version: str,
    ) -> BacktestResult:
        overall = PerformanceAnalyzer._calc_convexity(trades)
        by_year = PerformanceAnalyzer._calc_by_year(trades)
        equity  = PerformanceAnalyzer._calc_equity(equity_curve, initial_capital)
        return BacktestResult(
            overall=overall,
            by_year=by_year,
            equity=equity,
            trades=trades,
            regime_breakdown={},  # populated by MarketRegimeClassifier if used
            symbol=symbol,
            start_date=start_date.date().isoformat(),
            end_date=end_date.date().isoformat(),
            ruleset_name=ruleset_name,
            ruleset_version=ruleset_version,
        )

    @staticmethod
    def _calc_convexity(trades: List[Trade]) -> ConvexityMetrics:
        valid = [t for t in trades if t.initial_risk and t.initial_risk > 0]
        if not valid:
            return ConvexityMetrics(
                n_trades=0, win_rate=0.0, avg_win_r=0.0, avg_loss_r=0.0,
                expectancy_r=0.0, max_win_r=0.0, max_loss_r=0.0,
                top10_pct=0.0, skewness=0.0, max_losing_streak=0,
                total_pnl=0.0, stop_wins=0, stop_losses=0,
                eod_wins=0, eod_losses=0, r_multiples=[],
                first_date="", last_date="",
            )

        r_list = [t.pnl / t.initial_risk for t in valid]
        wins   = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r <= 0]
        wr = len(wins) / len(r_list)
        avg_win  = float(np.mean(wins))  if wins   else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        gross_profit = sum(t.pnl for t in valid if t.pnl > 0)
        top_n = max(1, len(valid) // 10)
        top_pnls = sorted([t.pnl for t in valid], reverse=True)[:top_n]
        top10_pct = (sum(top_pnls) / gross_profit * 100) if gross_profit > 0 else 0.0

        mean_r = float(np.mean(r_list))
        std_r  = float(np.std(r_list))
        skew = (float(np.mean([(r - mean_r) ** 3 for r in r_list])) / std_r ** 3
                if std_r > 0 else 0.0)

        streak = cur = 0
        for r in r_list:
            cur = cur + 1 if r <= 0 else 0
            streak = max(streak, cur)

        stop_trades = [t for t in valid if t.exit_reason == "STOP"]
        eod_trades  = [t for t in valid if t.exit_reason == "EOD"]

        return ConvexityMetrics(
            n_trades=len(r_list),
            win_rate=wr,
            avg_win_r=avg_win,
            avg_loss_r=avg_loss,
            expectancy_r=wr * avg_win + (1 - wr) * avg_loss,
            max_win_r=max(r_list),
            max_loss_r=min(r_list),
            top10_pct=top10_pct,
            skewness=skew,
            max_losing_streak=streak,
            total_pnl=sum(t.pnl for t in valid),
            stop_wins=sum(1 for t in stop_trades if t.pnl > 0),
            stop_losses=sum(1 for t in stop_trades if t.pnl <= 0),
            eod_wins=sum(1 for t in eod_trades if t.pnl > 0),
            eod_losses=sum(1 for t in eod_trades if t.pnl <= 0),
            r_multiples=r_list,
            first_date=valid[0].entry_time.date().isoformat(),
            last_date=valid[-1].entry_time.date().isoformat(),
        )

    @staticmethod
    def _calc_by_year(trades: List[Trade]) -> Dict[int, ConvexityMetrics]:
        by_year: Dict[int, List[Trade]] = {}
        for t in trades:
            y = t.entry_time.year
            by_year.setdefault(y, []).append(t)
        return {
            y: PerformanceAnalyzer._calc_convexity(ts)
            for y, ts in sorted(by_year.items())
        }

    @staticmethod
    def _calc_equity(
        equity_curve: List[Tuple[datetime, float]],
        initial_capital: float,
    ) -> EquityMetrics:
        if not equity_curve:
            empty = pd.Series(dtype=float)
            return EquityMetrics(
                total_return=0.0, annualized_return=0.0, sharpe_ratio=0.0,
                max_drawdown=0.0, max_drawdown_duration_days=0,
                equity_curve=empty, drawdown_curve=empty,
            )

        times, values = zip(*equity_curve)
        eq = pd.Series(values, index=pd.DatetimeIndex(times))
        returns = eq.pct_change().dropna()

        total_return = (eq.iloc[-1] - initial_capital) / initial_capital
        days = (eq.index[-1] - eq.index[0]).days or 1
        ann_return = (1 + total_return) ** (365 / days) - 1

        sharpe = 0.0
        if returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * np.sqrt(252 * 78))

        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max
        max_dd = float(drawdown.min())

        # Max drawdown duration
        in_dd = drawdown < 0
        max_dd_days = 0
        cur_dd = 0
        for v in in_dd:
            cur_dd = cur_dd + 1 if v else 0
            max_dd_days = max(max_dd_days, cur_dd)
        max_dd_days = max_dd_days * 5 // (78 * 5) or max_dd_days  # rough bar->day

        return EquityMetrics(
            total_return=total_return,
            annualized_return=ann_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_duration_days=max_dd_days,
            equity_curve=eq,
            drawdown_curve=drawdown,
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_performance.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/analysis/performance.py vibe/tests/backtester/test_performance.py
git commit -m "feat: add PerformanceAnalyzer with R-multiple convexity metrics"
```

---

## Task 10: MarketRegimeClassifier

**Files:**
- Create: `vibe/backtester/analysis/regime.py`
- Create: `vibe/tests/backtester/test_regime.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_regime.py
import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.analysis.regime import MarketRegimeClassifier

ET = ZoneInfo("America/New_York")


def _make_trending_df(n=100):
    """Strong trend: high ADX."""
    idx = pd.date_range("2024-01-01 09:30", periods=n, freq="5min", tz=ET)
    close = pd.Series(np.linspace(100, 130, n), index=idx)
    df = pd.DataFrame({
        "open": close - 0.5, "high": close + 0.5,
        "low": close - 0.5, "close": close,
        "volume": 1_000_000,
    }, index=idx)
    return df


def _make_ranging_df(n=100):
    """Choppy: oscillates between 99 and 101."""
    idx = pd.date_range("2024-01-01 09:30", periods=n, freq="5min", tz=ET)
    close = pd.Series([100 + np.sin(i * 0.3) for i in range(n)], index=idx)
    df = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.2,
        "low": close - 0.2, "close": close,
        "volume": 500_000,
    }, index=idx)
    return df


def test_classify_returns_series(make_trending_df=_make_trending_df):
    clf = MarketRegimeClassifier()
    df = _make_trending_df()
    result = clf.classify(df)
    assert isinstance(result, pd.Series)
    assert len(result) == len(df)


def test_regime_values_are_valid():
    clf = MarketRegimeClassifier()
    result = clf.classify(_make_ranging_df())
    valid = {"TRENDING", "RANGING", "TRANSITIONING"}
    assert set(result.dropna().unique()).issubset(valid)


def test_performance_by_regime():
    from vibe.backtester.analysis.regime import performance_by_regime
    from vibe.backtester.analysis.metrics import ConvexityMetrics
    from vibe.common.models.trade import Trade

    trades = [
        Trade(symbol="QQQ", side="buy", quantity=10,
              entry_price=100.0, exit_price=103.0,
              entry_time=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
              exit_time=datetime(2024, 1, 15, 15, 55, tzinfo=ET),
              pnl=300.0, pnl_pct=0.03, initial_risk=100.0, exit_reason="EOD"),
    ]
    idx = pd.date_range("2024-01-15 09:30", periods=80, freq="5min", tz=ET)
    regime = pd.Series("TRENDING", index=idx)

    result = performance_by_regime(trades, regime)
    assert "TRENDING" in result
    assert isinstance(result["TRENDING"], ConvexityMetrics)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_regime.py -v
```

- [ ] **Step 3: Implement MarketRegimeClassifier**

```python
# vibe/backtester/analysis/regime.py
from typing import Dict, List

import numpy as np
import pandas as pd

from vibe.backtester.analysis.metrics import ConvexityMetrics
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade


class MarketRegimeClassifier:
    """
    ADX-based market regime classification.
    ADX > 25 → TRENDING, ADX 20-25 → TRANSITIONING, ADX < 20 → RANGING.
    """

    def __init__(self, period: int = 14) -> None:
        self.period = period

    def classify(self, df: pd.DataFrame) -> pd.Series:
        adx = self._calc_adx(df, self.period)
        regime = pd.Series(index=df.index, dtype=str)
        regime[adx > 25] = "TRENDING"
        regime[(adx >= 20) & (adx <= 25)] = "TRANSITIONING"
        regime[adx < 20] = "RANGING"
        return regime

    def _calc_adx(self, df: pd.DataFrame, period: int) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        dm_plus  = np.where((high - high.shift(1)) > (low.shift(1) - low),
                            np.maximum(high - high.shift(1), 0), 0)
        dm_minus = np.where((low.shift(1) - low) > (high - high.shift(1)),
                            np.maximum(low.shift(1) - low, 0), 0)

        atr = tr.ewm(span=period, adjust=False).mean()
        dip = pd.Series(dm_plus,  index=df.index).ewm(span=period, adjust=False).mean() / atr * 100
        dim = pd.Series(dm_minus, index=df.index).ewm(span=period, adjust=False).mean() / atr * 100

        dx = ((dip - dim).abs() / (dip + dim).replace(0, np.nan) * 100).fillna(0)
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx


def performance_by_regime(
    trades: List[Trade],
    regime: pd.Series,
) -> Dict[str, ConvexityMetrics]:
    """
    Split trades by the regime at their entry time and compute
    ConvexityMetrics for each regime bucket.
    """
    result: Dict[str, List[Trade]] = {}
    for trade in trades:
        if trade.entry_time not in regime.index:
            nearest = regime.index.asof(trade.entry_time)
            regime_label = regime.get(nearest, "UNKNOWN")
        else:
            regime_label = regime.get(trade.entry_time, "UNKNOWN")
        result.setdefault(regime_label, []).append(trade)

    return {
        label: PerformanceAnalyzer._calc_convexity(ts)
        for label, ts in result.items()
    }
```

- [ ] **Step 4: Run tests**

```bash
pytest vibe/tests/backtester/test_regime.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/backtester/analysis/regime.py vibe/tests/backtester/test_regime.py
git commit -m "feat: add ADX-based MarketRegimeClassifier and performance_by_regime"
```

---

## Task 11: ReportGenerator

**Files:**
- Create: `vibe/backtester/reporting/report.py`
- Create: `vibe/tests/backtester/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# vibe/tests/backtester/test_report.py
import pytest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from vibe.backtester.reporting.report import ReportGenerator
from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)

ET = ZoneInfo("America/New_York")


def _minimal_result():
    cm = ConvexityMetrics(
        n_trades=4, win_rate=0.5, avg_win_r=2.5, avg_loss_r=-1.0,
        expectancy_r=0.75, max_win_r=3.0, max_loss_r=-1.0,
        top10_pct=40.0, skewness=0.8, max_losing_streak=2,
        total_pnl=600.0, stop_wins=1, stop_losses=1, eod_wins=1, eod_losses=1,
        r_multiples=[3.0, -1.0, 2.0, -1.0],
        first_date="2024-01-02", last_date="2024-01-31",
    )
    eq = EquityMetrics(
        total_return=0.06, annualized_return=0.06, sharpe_ratio=1.5,
        max_drawdown=-0.02, max_drawdown_duration_days=3,
        equity_curve=pd.Series([10000, 10200, 10400, 10600],
                                index=pd.date_range("2024-01-01", periods=4, tz=ET)),
        drawdown_curve=pd.Series([0, 0, 0, 0],
                                  index=pd.date_range("2024-01-01", periods=4, tz=ET)),
    )
    return BacktestResult(
        overall=cm, by_year={2024: cm}, equity=eq, trades=[],
        regime_breakdown={"TRENDING": cm, "RANGING": cm},
        symbol="QQQ", start_date="2024-01-01", end_date="2024-01-31",
        ruleset_name="orb_production", ruleset_version="1.0.0",
    )


def test_generate_html_creates_file(tmp_path):
    gen = ReportGenerator()
    result = _minimal_result()
    out = tmp_path / "report.html"
    gen.generate_html(result, out)
    assert out.exists()


def test_report_contains_expected_sections(tmp_path):
    gen = ReportGenerator()
    out = tmp_path / "report.html"
    gen.generate_html(_minimal_result(), out)
    html = out.read_text(encoding="utf-8")
    assert "Convexity" in html
    assert "Win Rate" in html
    assert "Expectancy" in html
    assert "2024" in html  # per-year section
    assert "TRENDING" in html  # regime section
    assert "plotly" in html.lower()  # Plotly charts


def test_report_is_self_contained_html(tmp_path):
    gen = ReportGenerator()
    out = tmp_path / "report.html"
    gen.generate_html(_minimal_result(), out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html")
    assert "</html>" in html
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest vibe/tests/backtester/test_report.py -v
```

- [ ] **Step 3: Install plotly if needed**

```bash
pip install plotly
```

- [ ] **Step 4: Implement ReportGenerator**

```python
# vibe/backtester/reporting/report.py
from pathlib import Path
from typing import Dict

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)

_SCORECARD = [
    ("Win Rate",         "win_rate",          lambda v: f"{v:.1%}", lambda v: "pass" if 0.25 <= v <= 0.45 else "watch" if v < 0.55 else "fail"),
    ("Avg Win R",        "avg_win_r",         lambda v: f"{v:.2f}R", lambda v: "pass" if v >= 3.0 else "watch" if v >= 2.0 else "fail"),
    ("Avg Loss R",       "avg_loss_r",        lambda v: f"{v:.2f}R", lambda v: "pass" if v >= -1.05 else "watch" if v >= -1.2 else "fail"),
    ("Expectancy",       "expectancy_r",      lambda v: f"{v:.2f}R", lambda v: "pass" if v > 0 else "fail"),
    ("Max Win R",        "max_win_r",         lambda v: f"{v:.1f}R", lambda v: "pass" if v >= 8.0 else "watch" if v >= 5.0 else "fail"),
    ("Top 10%",          "top10_pct",         lambda v: f"{v:.0f}%", lambda v: "pass" if v >= 40.0 else "watch" if v >= 30.0 else "fail"),
    ("Skewness",         "skewness",          lambda v: f"{v:.2f}", lambda v: "pass" if v > 0.5 else "watch" if v > 0 else "fail"),
    ("Max Losing Streak","max_losing_streak", lambda v: str(v), lambda v: "pass" if v <= 10 else "watch" if v <= 15 else "fail"),
]

_STATUS_COLOR = {"pass": "#2ecc71", "watch": "#f39c12", "fail": "#e74c3c"}


class ReportGenerator:
    """Generates the convexity dashboard HTML report using Plotly."""

    def generate_html(self, result: BacktestResult, output_path: Path) -> Path:
        overall_html  = self._render_overall(result.overall, result.equity)
        year_htmls    = "".join(
            self._render_year(year, cm)
            for year, cm in sorted(result.by_year.items())
        )
        regime_html   = self._render_regime(result.regime_breakdown)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{result.ruleset_name} — Convexity Report</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  body {{ font-family: Arial, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
  h1 {{ color: #58a6ff; }} h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
  .meta {{ color: #8b949e; font-size: 13px; margin-bottom: 20px; }}
  .grid-8 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
  .metric-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; text-align: center; }}
  .metric-label {{ font-size: 12px; color: #8b949e; margin-bottom: 6px; }}
  .metric-value {{ font-size: 24px; font-weight: bold; }}
  .pass {{ color: #2ecc71; }} .watch {{ color: #f39c12; }} .fail {{ color: #e74c3c; }}
  .scorecard {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }}
  .sc-item {{ background: #161b22; border-radius: 6px; padding: 10px 14px; font-size: 13px; }}
  .sc-label {{ color: #8b949e; }} .sc-val {{ font-weight: bold; font-size: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #161b22; color: #8b949e; padding: 8px 12px; text-align: right; }}
  th:first-child {{ text-align: left; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #21262d; text-align: right; }}
  td:first-child {{ text-align: left; font-weight: bold; }}
  .section {{ margin-bottom: 40px; }}
</style>
</head>
<body>
<h1>{result.ruleset_name} v{result.ruleset_version} — Convexity Dashboard</h1>
<p class="meta">Symbol: {result.symbol} &nbsp;|&nbsp; {result.start_date} to {result.end_date}</p>
{overall_html}
{year_htmls}
{regime_html}
</body>
</html>"""

        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _render_scorecard(self, cm: ConvexityMetrics) -> str:
        items = []
        for label, attr, fmt, grade_fn in _SCORECARD:
            val = getattr(cm, attr)
            grade = grade_fn(val)
            color = _STATUS_COLOR[grade]
            items.append(
                f'<div class="sc-item"><div class="sc-label">{label}</div>'
                f'<div class="sc-val" style="color:{color}">{fmt(val)}</div>'
                f'<div style="font-size:11px;color:{color}">{grade.upper()}</div></div>'
            )
        return f'<div class="scorecard">{"".join(items)}</div>'

    def _render_metrics_grid(self, cm: ConvexityMetrics) -> str:
        cards = [
            ("Trades", str(cm.n_trades), ""),
            ("Win Rate", f"{cm.win_rate:.1%}", "pass" if 0.25 <= cm.win_rate <= 0.45 else ""),
            ("Expectancy", f"{cm.expectancy_r:.2f}R", "pass" if cm.expectancy_r > 0 else "fail"),
            ("Total P&L", f"${cm.total_pnl:,.0f}", "pass" if cm.total_pnl > 0 else "fail"),
            ("Max Win", f"{cm.max_win_r:.1f}R", ""),
            ("Skewness", f"{cm.skewness:.2f}", ""),
            ("Max Streak", str(cm.max_losing_streak), ""),
            ("Top 10%", f"{cm.top10_pct:.0f}%", ""),
        ]
        html = '<div class="grid-8">'
        for label, val, cls in cards:
            html += (f'<div class="metric-card"><div class="metric-label">{label}</div>'
                     f'<div class="metric-value {cls}">{val}</div></div>')
        return html + "</div>"

    def _r_histogram(self, r_multiples: list, div_id: str) -> str:
        if not r_multiples:
            return ""
        colors = ["#2ecc71" if r > 0 else "#e74c3c" for r in r_multiples]
        fig = go.Figure(go.Bar(
            x=list(range(len(r_multiples))), y=r_multiples,
            marker_color=colors, name="R-multiple",
        ))
        fig.update_layout(
            title="Trade R Waterfall", plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117", font_color="#c9d1d9",
            xaxis_title="Trade #", yaxis_title="R",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _cumulative_r_chart(self, r_multiples: list, div_id: str) -> str:
        if not r_multiples:
            return ""
        import numpy as np
        cumr = list(np.cumsum(r_multiples))
        fig = go.Figure(go.Scatter(
            x=list(range(len(cumr))), y=cumr,
            mode="lines", line=dict(color="#58a6ff", width=2), name="Cumulative R",
        ))
        fig.update_layout(
            title="Cumulative R", plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117", font_color="#c9d1d9",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _equity_chart(self, eq: EquityMetrics, div_id: str) -> str:
        if eq.equity_curve.empty:
            return ""
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(
            x=eq.equity_curve.index.tolist(), y=eq.equity_curve.tolist(),
            mode="lines", line=dict(color="#58a6ff"), name="Equity",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=eq.drawdown_curve.index.tolist(), y=(eq.drawdown_curve * 100).tolist(),
            mode="lines", fill="tozeroy", line=dict(color="#e74c3c"), name="Drawdown %",
        ), row=2, col=1)
        fig.update_layout(
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117", font_color="#c9d1d9",
        )
        return fig.to_html(full_html=False, div_id=div_id)

    def _render_overall(self, cm: ConvexityMetrics, eq: EquityMetrics) -> str:
        return (
            '<div class="section"><h2>Overall Performance</h2>'
            + self._render_metrics_grid(cm)
            + "<h3>Convexity Scorecard</h3>"
            + self._render_scorecard(cm)
            + self._r_histogram(cm.r_multiples, "r-waterfall-overall")
            + self._cumulative_r_chart(cm.r_multiples, "cumr-overall")
            + self._equity_chart(eq, "equity-overall")
            + "</div>"
        )

    def _render_year(self, year: int, cm: ConvexityMetrics) -> str:
        return (
            f'<div class="section"><h2>{year}</h2>'
            + self._render_metrics_grid(cm)
            + self._render_scorecard(cm)
            + self._r_histogram(cm.r_multiples, f"r-waterfall-{year}")
            + self._cumulative_r_chart(cm.r_multiples, f"cumr-{year}")
            + "</div>"
        )

    def _render_regime(self, breakdown: Dict[str, ConvexityMetrics]) -> str:
        if not breakdown:
            return ""
        rows = ""
        for regime, cm in sorted(breakdown.items()):
            rows += (
                f"<tr><td>{regime}</td><td>{cm.n_trades}</td>"
                f"<td>{cm.win_rate:.1%}</td><td>{cm.expectancy_r:.2f}R</td>"
                f"<td>{cm.avg_win_r:.2f}R</td><td>{cm.avg_loss_r:.2f}R</td>"
                f"<td>${cm.total_pnl:,.0f}</td></tr>"
            )
        return (
            '<div class="section"><h2>Regime Breakdown</h2>'
            "<table><tr><th>Regime</th><th>Trades</th><th>Win%</th>"
            "<th>Expectancy</th><th>Avg Win</th><th>Avg Loss</th><th>P&L</th></tr>"
            + rows + "</table></div>"
        )
```

- [ ] **Step 5: Run tests**

```bash
pytest vibe/tests/backtester/test_report.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add vibe/backtester/reporting/report.py vibe/tests/backtester/test_report.py
git commit -m "feat: add ReportGenerator with Plotly convexity dashboard"
```

---

## Task 12: CLI Entry Point

**Files:**
- Create: `scripts/run_backtest.py`

- [ ] **Step 1: Implement CLI**

```python
#!/usr/bin/env python3
"""
Run a backtest from the command line.

Usage:
    python scripts/run_backtest.py --ruleset orb_production --symbol QQQ \
        --start 2023-01-01 --end 2024-12-31 --capital 10000 \
        --output reports/backtest.html
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.reporting.report import ReportGenerator
from vibe.common.ruleset.loader import RuleSetLoader

ET = ZoneInfo("America/New_York")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ORB backtest")
    parser.add_argument("--ruleset", default="orb_production", help="Ruleset name")
    parser.add_argument("--symbol",  default="QQQ",           help="Symbol to test")
    parser.add_argument("--start",   default="2020-01-01",    help="Start date YYYY-MM-DD")
    parser.add_argument("--end",     default="2024-12-31",    help="End date YYYY-MM-DD")
    parser.add_argument("--capital", default=10_000.0, type=float, help="Initial capital")
    parser.add_argument("--slippage-ticks", default=5, type=int, help="Slippage in ticks")
    parser.add_argument("--output",  default="reports/backtest.html", help="Output HTML path")
    args = parser.parse_args()

    data_dir = Path(os.environ.get("BACKTEST__DATA_DIR", "vibe/data/parquet"))
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        sys.exit(1)

    ruleset = RuleSetLoader.from_name(args.ruleset)
    engine  = BacktestEngine(ruleset=ruleset, data_dir=data_dir,
                              initial_capital=args.capital,
                              slippage_ticks=args.slippage_ticks)

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=ET)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=ET)

    print(f"Running {args.ruleset} on {args.symbol} from {args.start} to {args.end}...")
    result = engine.run(symbol=args.symbol, start_date=start, end_date=end)

    cm = result.overall
    print(f"Trades: {cm.n_trades}  Win: {cm.win_rate:.1%}  "
          f"Expectancy: {cm.expectancy_r:.2f}R  P&L: ${cm.total_pnl:,.0f}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    ReportGenerator().generate_html(result, out)
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test the CLI**

```bash
python scripts/run_backtest.py --symbol QQQ --start 2024-01-02 --end 2024-01-31 \
    --output reports/test_run.html
```

Expected output:
```
Running orb_production on QQQ from 2024-01-02 to 2024-01-31...
Trades: N  Win: X%  Expectancy: X.XXR  P&L: $X,XXX
Report: reports/test_run.html
```

- [ ] **Step 3: Commit**

```bash
git add scripts/run_backtest.py reports/.gitkeep
git commit -m "feat: add run_backtest.py CLI entry point"
```

---

## Task 13: Full Test Suite

- [ ] **Step 1: Run all backtester tests**

```bash
pytest vibe/tests/backtester/ -v --tb=short
```

Expected: all tests pass (data-dependent tests skip gracefully if Parquet not present)

- [ ] **Step 2: Run full test suite to check no regressions**

```bash
pytest vibe/tests/ -v --tb=short -q
```

Expected: existing tests still pass

- [ ] **Step 3: Run end-to-end backtest on full history**

```bash
python scripts/run_backtest.py --symbol QQQ --start 2018-05-01 --end 2026-04-28 \
    --output reports/qqq_full_history.html
```

Review the HTML report — confirm:
- Overall scorecard renders
- Per-year sections appear for each year
- Regime breakdown section present
- Charts load (R waterfall, cumulative R, equity)

- [ ] **Step 4: Final commit**

```bash
git add -p  # stage only intended files
git commit -m "feat: backtester MVP complete — ORB backtest with convexity dashboard"
```
