# Backtester Architecture Design

## Overview

A high-performance, event-driven backtesting engine supporting multiple asset classes (stocks, options, forex, crypto), trading styles (day trading, swing trading, position trading), and advanced optimization capabilities. The backtester **reuses all strategy logic from `vibe/common/`** to ensure perfect consistency between live trading and backtesting.

**Design Principles:**
1. **Shared Strategy Logic**: All strategies, indicators, risk management, and validation rules are in `vibe/common/`
2. **Event-Driven Realism**: Process data bar-by-bar to match live trading execution flow
3. **Asset-Agnostic**: Support stocks, options, forex, crypto through unified abstraction
4. **Optimizable**: Built-in parameter sweeping with parallel execution
5. **Extensible**: Pluggable data providers, execution simulators, commission models

**Key Capabilities:**
- ✅ Multiple asset types with specific characteristics
- ✅ Realistic fill simulation (slippage, partial fills, liquidity constraints)
- ✅ Multi-timeframe strategy validation
- ✅ Portfolio backtesting (multiple symbols simultaneously)
- ✅ Parameter optimization with walk-forward analysis
- ✅ Comprehensive performance analytics
- ✅ Commission models for different asset types
- ✅ Margin trading and leverage simulation
- ✅ HTML/PDF report generation with charts

---

## High-Level Architecture

```
+====================================================================================+
|                         BACKTESTER ARCHITECTURE                                     |
+====================================================================================+
|                                                                                     |
|  +--------------------------+      +---------------------------+                   |
|  |   vibe/backtester/       |      |   vibe/common/            |                   |
|  |   (Backtest-Specific)    |<---->|   (Shared Logic)          |                   |
|  +--------------------------+      +---------------------------+                   |
|  | - BacktestEngine         |      | - Strategies (ORB, etc.)  |                   |
|  | - SimulatedClock         |      | - Indicators              |                   |
|  | - FillSimulator          |      | - Risk Management         |                   |
|  | - HistoricalDataProvider |      | - MTF Validation          |                   |
|  | - PerformanceAnalyzer    |      | - Models (Trade, Order)   |                   |
|  | - OptimizationEngine     |      +---------------------------+                   |
|  | - ReportGenerator        |                                                      |
|  +--------------------------+                                                      |
|                                                                                     |
+====================================================================================+
                                         |
        +--------------------------------+--------------------------------+
        |                                |                                |
        v                                v                                v
+------------------+         +----------------------+         +--------------------+
| Data Sources     |         | Execution Simulation |         | Analysis & Output  |
+------------------+         +----------------------+         +--------------------+
| - Parquet Files  |         | - FillSimulator      |         | - Metrics Engine   |
| - CSV Files      |         | - CommissionModel    |         | - Report Generator |
| - Yahoo Finance  |         | - SlippageModel      |         | - Visualization    |
| - Alpaca API     |         | - LiquidityModel     |         | - Comparison Tool  |
| - Crypto APIs    |         | - MarginSimulator    |         +--------------------+
| - Forex Feeds    |         +----------------------+
+------------------+
```

---

## Design Philosophy

### Shared vs Specific Components

The backtester follows the same architecture as the live trading bot, with a clear separation between shared logic and runtime-specific implementations:

```
+============================================================================+
|                    SHARED (vibe/common/)                                    |
|    IDENTICAL logic between live trading and backtesting                    |
+============================================================================+
|                                                                             |
|  ✓ Strategies (ORB, Mean Reversion, Momentum, etc.)                        |
|  ✓ Indicators (IncrementalIndicatorEngine, ORB levels, etc.)               |
|  ✓ Risk Management (RiskManager, PositionSizer, StopLossManager)           |
|  ✓ Multi-Timeframe Validation (MTFValidator, ValidationRules)              |
|  ✓ Data Models (Trade, Order, Position, Bar, Signal)                       |
|  ✓ Execution Interface (ExecutionEngine ABC)                               |
|  ✓ Data Interface (DataProvider ABC)                                       |
|  ✓ Clock Interface (Clock ABC)                                             |
|                                                                             |
+============================================================================+
|                    DIFFERENT (runtime-specific)                             |
+============================================================================+
|                                                                             |
|  LIVE (vibe/trading-bot/)          BACKTEST (vibe/backtester/)             |
|  -------------------------          ---------------------------             |
|  Clock:                             Clock:                                  |
|  - LiveClock (real time)            - SimulatedClock (controlled time)      |
|  - Market hours checks              - Fast-forward through data             |
|                                                                             |
|  Data:                              Data:                                   |
|  - Finnhub WebSocket                - HistoricalDataLoader                  |
|  - Yahoo Finance API                - Parquet/CSV files                     |
|  - Real-time streaming              - Pre-loaded datasets                   |
|                                                                             |
|  Execution:                         Execution:                              |
|  - MockExchange (realistic)         - FillSimulator (instant/realistic)     |
|  - Network latency                  - No network, configurable realism      |
|  - Order retries                    - Deterministic fills                   |
|                                                                             |
|  Orchestration:                     Orchestration:                          |
|  - 24/7 service                     - Batch processing                      |
|  - Graceful shutdown                - Run to completion                     |
|  - Health monitoring                - Progress tracking                     |
|  - Discord notifications            - Result aggregation                    |
|                                                                             |
+============================================================================+
```

**Key Insight:** A strategy that works in backtest will behave **identically** in live trading because they use the exact same `vibe/common/` code.

---

## Core Components

### 1. Backtest Engine

**Purpose:** Orchestrate the entire backtest process - data loading, event loop, strategy execution, result collection.

**Key Responsibilities:**
- Initialize all components (data, clock, execution simulator, strategy, risk manager)
- Run event loop: load bar → update indicators → generate signals → execute trades → track performance
- Handle multiple symbols for portfolio backtesting
- Manage time progression via SimulatedClock
- Collect metrics and trade history

**Event Loop Pseudocode:**
```python
class BacktestEngine:
    def run(self, start_date, end_date, symbols, strategy_config):
        # 1. Initialize
        clock = SimulatedClock()
        data_provider = ParquetLoader(data_dir, symbols)   # eager-loads all symbols
        fill_simulator = FillSimulator(...)
        strategy = StrategyRuleSet.from_yaml(strategy_config)
        risk_manager = RiskManager(...)

        # 2. Build aligned bar index across all symbols
        # get_bars returns the full in-memory slice; resample to strategy interval
        frames = {
            sym: _resample(await data_provider.get_bars(sym, start_time=start_date, end_time=end_date))
            for sym in symbols
        }
        timestamps = sorted(set().union(*[df.index for df in frames.values()]))

        # 3. Event loop — time is driven by data, not arithmetic
        for ts in timestamps:
            clock.set_time(ts)
            current_bars = {sym: frames[sym].loc[ts] for sym in symbols if ts in frames[sym].index}

            for symbol, bar in current_bars.items():
                signal = strategy.generate_signal(symbol, bar, clock)

                if signal:
                    risk_ok = risk_manager.pre_trade_check(signal)
                    if risk_ok:
                        fill = fill_simulator.execute(signal, bar)
                        portfolio.update(fill)
                        trade_store.append(fill.to_trade())

            portfolio.check_exits(current_bars, clock)
            portfolio.update_equity(current_bars)

        # 4. Calculate final metrics
        return PerformanceAnalyzer.analyze(trade_store, portfolio)
```

---

### 2. Simulated Clock

**Purpose:** Control time progression during backtest to enable deterministic, fast execution.

**Key Features:**
- Implements `Clock` ABC (`vibe/common/clock/base.py`) — drop-in replacement for `LiveClock`
- Time is data-driven: engine calls `set_time(bar.timestamp)` before each bar
- No arithmetic advancement — weekends, holidays, and trading halts are handled automatically because they simply don't appear in the bar index
- `is_market_open()` checks the simulated timestamp against regular market hours (9:30–16:00 ET)

**Implementation:**

`SimulatedClock` extends `Clock` with a single extra method `set_time()`. The engine calls it before processing each bar, passing the bar's actual timestamp — no arithmetic advancement, no calendar skip logic. Time comes from the data, so gaps (weekends, holidays, halts) are handled automatically.

The base `Clock` ABC is unchanged; `LiveClock` is unaffected.

```python
# vibe/backtester/core/clock.py
class SimulatedClock(Clock):
    """
    Backtester clock. Time is driven by bar timestamps from the data,
    not by arithmetic addition. The engine calls set_time(bar.timestamp)
    before each bar; now() returns it. is_market_open() checks whether
    the current simulated time falls within regular market hours.
    """

    _MARKET_OPEN  = time(9, 30)
    _MARKET_CLOSE = time(16, 0)
    _MARKET_TZ    = ZoneInfo("America/New_York")

    def __init__(self) -> None:
        self._current: datetime | None = None

    def set_time(self, ts: datetime) -> None:
        """Called by BacktestEngine before processing each bar."""
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

The engine loop becomes:

```python
for ts, bars in bar_iterator:   # ts = bar timestamp, bars = {symbol: Bar}
    clock.set_time(ts)
    strategy.on_bar(bars, clock)
```

---

### 3. Historical Data Provider

**Purpose:** Load historical OHLCV data for backtesting.

**Plugin Pattern — swappable data sources:**

The `DataLoader` ABC decouples the backtester from any specific data source. Swapping sources requires only changing which loader is injected; everything else stays the same.

The backtester uses the same `DataProvider` ABC (`vibe/common/data/base.py`) as the live bot — `get_bars`, `get_current_price`, `get_bar`. `ParquetLoader` is the backtester's concrete implementation. No separate loader interface is needed; both applications share one contract.

**Databento CSV Loader (primary):**

Raw files: `data/databento/<date-range>.ohlcv-1m.<SYMBOL>.csv.zst`

CSV schema (per-row):
```
ts_event                      — nanosecond UTC timestamp (ISO 8601 + Z)
rtype, publisher_id, instrument_id — databento metadata (ignored)
open, high, low, close        — prices (float, already adjusted)
volume                        — bar volume
symbol                        — ticker (e.g. "QQQ")
```

Available symbols: `QQQ`, `MSFT`, `AMZN`, `GOOGL`, `TSLA` (2018-05-01 → 2026-04-28)

**One-time data preparation** — run `scripts/convert_databento.py` once before first backtest:

1. Decompresses each `.csv.zst` with zstandard
2. Converts UTC nanosecond timestamps → `America/New_York`
3. Filters to regular market hours only (09:30–15:59 ET)
4. Runs data quality validation (OHLC consistency, outliers, overnight gaps)
5. Writes one `<SYMBOL>.parquet` per symbol (snappy-compressed, ~10–20× faster reads)

The conversion script reads paths from `.env` (following the project's `SECTION__KEY` convention):

```ini
# .env
BACKTEST__DATABENTO_DIR=./data/databento   # raw source files
BACKTEST__DATA_DIR=./data/parquet          # output Parquet files (runtime read path)
```

After conversion `HistoricalDataProvider` reads only from `BACKTEST__DATA_DIR`. The raw `.csv.zst` files are preserved as source-of-truth but never read during backtesting.

**ParquetLoader — backtester's DataProvider implementation:**

```python
# vibe/backtester/data/parquet_loader.py
class ParquetLoader(DataProvider):
    """
    Implements DataProvider for backtesting against local Parquet files.

    All symbols are loaded into memory at init (eager load). Subsequent
    get_bars / get_current_price / get_bar calls are pure in-memory slices
    — no disk I/O after startup. ~5 symbols x ~100 MB uncompressed = ~500 MB
    total, well within normal RAM for a dev machine.

    Path configured via BACKTEST__DATA_DIR in .env.
    """

    def __init__(self, data_dir: Path, symbols: list[str]):
        self._data: dict[str, pd.DataFrame] = {
            sym: pd.read_parquet(data_dir / f"{sym}.parquet")
            for sym in symbols
        }

    async def get_bars(self, symbol: str, timeframe: str = "1m",
                       limit: int | None = None,
                       start_time: datetime | None = None,
                       end_time: datetime | None = None) -> pd.DataFrame:
        df = self._data[symbol]
        if start_time:
            df = df[df.index >= start_time]
        if end_time:
            df = df[df.index <= end_time]
        if limit:
            df = df.tail(limit)
        return df

    async def get_current_price(self, symbol: str) -> float:
        return float(self._data[symbol]["close"].iloc[-1])

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Bar | None:
        row = self._data[symbol].iloc[-1]
        return Bar(symbol=symbol, open=row.open, high=row.high,
                   low=row.low, close=row.close, volume=row.volume,
                   timestamp=row.name)
```

**Resampling to 5-minute bars:**

`ParquetLoader` stores 1-minute bars. The `BacktestEngine` resamples to the strategy's required bar interval on startup using pandas `resample`:

```python
def _resample(df: pd.DataFrame, interval: str = "5min") -> pd.DataFrame:
    return df.resample(interval, closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()

    def get_bars(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Get last N completed bars at current simulated time."""
        pass

    def get_current_price(self, symbol: str) -> float:
        """Return last close at current simulated time."""
        pass
```

**Adding a new data source** requires only implementing `DataLoader.load()` — no changes to `HistoricalDataProvider`, `BacktestEngine`, or strategies.

---

### 4. Asset Type Abstraction

**Purpose:** Support different asset classes with specific characteristics.

**Asset Types:**
```python
from enum import Enum

class AssetType(Enum):
    STOCK = "stock"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"
    FUTURE = "future"

class AssetSpec:
    """Asset-specific configuration."""

    @dataclass
    class StockSpec:
        min_tick: float = 0.01      # Minimum price movement
        commission_per_share: float = 0.0  # $0 for most brokers now
        commission_percent: float = 0.0
        borrowing_rate: float = 0.0  # For short selling
        requires_locate: bool = False  # Hard-to-borrow stocks

    @dataclass
    class ForexSpec:
        pip_size: float = 0.0001    # 4th decimal for most pairs
        spread_pips: float = 2.0    # Typical bid-ask spread
        leverage: int = 50          # 50:1 common for forex
        swap_rate: float = 0.0      # Overnight financing
        market_hours: str = "24/5"  # 24 hours, 5 days

    @dataclass
    class CryptoSpec:
        min_tick: float = 0.01
        maker_fee: float = 0.001    # 0.1%
        taker_fee: float = 0.002    # 0.2%
        withdrawal_fee: float = 0.0
        market_hours: str = "24/7"

    @dataclass
    class OptionSpec:
        contract_multiplier: int = 100
        commission_per_contract: float = 0.65
        underlying_asset: str = ""
        option_type: str = "call"  # call or put
        strike: float = 0.0
        expiration: datetime = None
        # Greeks would be calculated or provided
```

---

### 5. Fill Simulator

**Purpose:** Simulate realistic order execution using tick-based slippage.

**Slippage model:** Tick-based. 1 tick = $0.01 (US equity minimum increment). Default 5 ticks = $0.05/share. Applied directionally: buy fills slightly above bar close, sell fills slightly below.

For our five liquid large-caps (QQQ, MSFT, AMZN, GOOGL, TSLA), slippage is dominated by the bid-ask spread rather than market impact. Tick-based slippage is more realistic than flat percentage and requires no rolling volatility window. At current prices:
- QQQ ~$480 → 5 ticks = 0.010% per trade
- TSLA ~$250 → 5 ticks = 0.020% per trade

Configurable via `slippage_ticks` in `backtest.yaml`. Commission is zero (standard for US retail brokers today).

**Fill modes:**
- **Level 0 — Instant**: fill at bar close + slippage (default, used for MVP)
- **Level 1 — Next-bar**: signal on bar N, fill at open of bar N+1 + slippage (more conservative)

**Implementation:**
```python
TICK_SIZE = 0.01  # US equity minimum price increment

class FillSimulator:
    """
    Simulates order fills for backtesting.
    Slippage is tick-based: buy fills above close, sell fills below.
    Commission is zero (standard US retail broker).
    """

    def __init__(self, slippage_ticks: int = 5, fill_mode: int = 0):
        self.slippage_ticks = slippage_ticks
        self.fill_mode = fill_mode

    def execute(self, signal: Signal, bar: Bar, next_bar: Bar | None = None) -> OrderResponse:
        slippage = self.slippage_ticks * TICK_SIZE

        if self.fill_mode == 0:
            base_price = bar.close
        else:  # level 1 — next bar open
            base_price = next_bar.open if next_bar else bar.close

        if signal.side == "buy":
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage

        return OrderResponse(
            symbol=signal.symbol,
            side=signal.side,
            filled_qty=signal.quantity,
            avg_price=fill_price,
            commission=0.0,
            status=OrderStatus.FILLED,
        )
```

---

### 6. Commission Models

**Purpose:** Calculate transaction costs for different asset types and brokers.

**Implementation:**
```python
class CommissionModel(ABC):
    @abstractmethod
    def calculate(self, quantity: float, price: float) -> float:
        pass

class StockCommissionModel(CommissionModel):
    """Zero commission for most US brokers."""
    def __init__(self, per_share: float = 0.0, min_commission: float = 0.0):
        self.per_share = per_share
        self.min_commission = min_commission

    def calculate(self, quantity: float, price: float) -> float:
        commission = quantity * self.per_share
        return max(commission, self.min_commission)

class ForexCommissionModel(CommissionModel):
    """Spread-based commission."""
    def __init__(self, spread_pips: float, pip_value: float):
        self.spread_pips = spread_pips
        self.pip_value = pip_value

    def calculate(self, quantity: float, price: float) -> float:
        # Commission = spread * quantity * pip_value
        return self.spread_pips * quantity * self.pip_value

class CryptoCommissionModel(CommissionModel):
    """Maker/taker fees."""
    def __init__(self, maker_fee: float = 0.001, taker_fee: float = 0.002):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def calculate(self, quantity: float, price: float, is_maker: bool = False) -> float:
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        return quantity * price * fee_rate
```

---

### 7. Portfolio Manager

**Purpose:** Track positions, cash, equity, and P&L across multiple assets.

**Key Features:**
- Support multiple positions simultaneously
- Track realized and unrealized P&L
- Handle margin and leverage
- Calculate portfolio-level metrics
- Support different capital allocation strategies

**Implementation:**

`Position` stores `stop_price` at entry so `initial_risk` can be computed on close. `exit_reason` is passed into `close_position()` by whichever caller triggered the close (stop hit, EOD sweep, or signal reversal).

```python
@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    stop_price: float       # set at entry; used to compute initial_risk on close
    side: str               # "long" | "short"
    entry_time: datetime


class PortfolioManager:
    """Manages portfolio state during backtest."""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []

    def open_position(self, fill: OrderResponse, stop_price: float, timestamp: datetime) -> None:
        """Open a new position. stop_price is the hard stop set by the strategy at entry."""
        self.positions[fill.symbol] = Position(
            symbol=fill.symbol,
            quantity=fill.filled_qty,
            entry_price=fill.avg_price,
            stop_price=stop_price,
            side="long",
            entry_time=timestamp,
        )
        self.cash -= fill.filled_qty * fill.avg_price + fill.commission

    def close_position(self, fill: OrderResponse, exit_reason: str, timestamp: datetime) -> None:
        """
        Close an open position and record the completed Trade.
        exit_reason: 'STOP' | 'EOD' | 'SIGNAL'
        """
        pos = self.positions.pop(fill.symbol)
        realized_pnl = (fill.avg_price - pos.entry_price) * fill.filled_qty - fill.commission
        initial_risk = abs(pos.entry_price - pos.stop_price) * pos.quantity

        self.trade_history.append(Trade(
            symbol=fill.symbol,
            side="buy",
            quantity=fill.filled_qty,
            entry_price=pos.entry_price,
            exit_price=fill.avg_price,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            pnl=realized_pnl,
            pnl_pct=(fill.avg_price - pos.entry_price) / pos.entry_price,
            commission=fill.commission,
            initial_risk=initial_risk,
            exit_reason=exit_reason,
        ))
        self.cash += fill.filled_qty * fill.avg_price - fill.commission

    def check_exits(self, current_bars: Dict[str, Bar], clock: SimulatedClock) -> None:
        """Check stop hits and EOD exit; calls close_position() with the right exit_reason."""
        local_time = clock.now().astimezone(ZoneInfo("America/New_York")).time()
        eod_cutoff = time(15, 55)

        for symbol, pos in list(self.positions.items()):
            bar = current_bars.get(symbol)
            if bar is None:
                continue
            if bar.low <= pos.stop_price:
                fill = self._create_stop_fill(pos, bar)
                self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
            elif local_time >= eod_cutoff:
                fill = self._create_market_fill(pos, bar)
                self.close_position(fill, exit_reason="EOD", timestamp=clock.now())

    def update_equity(self, current_bars: Dict[str, Bar], timestamp: datetime) -> None:
        unrealized = sum(
            (current_bars[sym].close - pos.entry_price) * pos.quantity
            for sym, pos in self.positions.items()
            if sym in current_bars
        )
        self.equity_curve.append((timestamp, self.cash + unrealized))
```

---

### 8. Performance Analyzer

**Purpose:** Calculate comprehensive performance metrics from backtest results.

**Metrics to Calculate:**

Two metric types are computed: `ConvexityMetrics` (R-multiple-based, primary for ORB analysis) and `EquityMetrics` (capital-based, secondary).

```python
@dataclass
class ConvexityMetrics:
    """
    R-multiple based metrics — primary output for convexity analysis.
    R = trade_pnl / initial_risk_dollars (how many R multiples were gained/lost).
    Matches the orb_convexity_dashboard.html scorecard.
    """
    n_trades: int
    win_rate: float           # winning / total; target 25–45% for convex systems
    avg_win_r: float          # avg R of winning trades; target ≥ 3R
    avg_loss_r: float         # avg R of losing trades; should ≈ −1R
    expectancy_r: float       # (wr * avg_win_r) + (1-wr) * avg_loss_r; must be > 0
    max_win_r: float          # largest single-trade R; looking for 8R+ fat tails
    max_loss_r: float         # worst single-trade R
    top10_pct: float          # % of total profit from top 10% of trades; target ≥ 40%
    skewness: float           # positive = right tail (convex); target > 0.5
    max_losing_streak: int    # max consecutive losses; acceptable ≤ 15
    total_pnl: float          # sum of dollar P&L
    # Exit breakdown
    stop_wins: int
    stop_losses: int
    eod_wins: int
    eod_losses: int
    # Raw series (for charting)
    r_multiples: List[float]  # trade-by-trade R sequence
    first_date: str
    last_date: str

@dataclass
class EquityMetrics:
    """Capital-curve metrics — used for equity/drawdown charting."""
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
    by_year: Dict[int, ConvexityMetrics]   # e.g. {2022: ..., 2023: ..., 2024: ...}
    equity: EquityMetrics
    trades: List[Trade]
    regime_breakdown: Dict[str, ConvexityMetrics]  # TRENDING / RANGING / TRANSITIONING
    symbol: str
    start_date: str
    end_date: str
    ruleset_name: str
    ruleset_version: str
```

**Implementation:**
```python
class PerformanceAnalyzer:

    @staticmethod
    def analyze(trades: List[Trade], equity_curve: pd.Series,
                initial_capital: float) -> BacktestResult:
        overall = PerformanceAnalyzer._calc_convexity(trades)
        by_year = PerformanceAnalyzer._calc_by_year(trades)
        equity = PerformanceAnalyzer._calc_equity(equity_curve, initial_capital)
        return BacktestResult(overall=overall, by_year=by_year, equity=equity, trades=trades)

    @staticmethod
    def _calc_convexity(trades: List[Trade]) -> ConvexityMetrics:
        if not trades:
            return ConvexityMetrics(n_trades=0, ...)
        r_list = [t.pnl / t.initial_risk for t in trades if t.initial_risk > 0]
        wins = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r <= 0]
        wr = len(wins) / len(r_list)
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        top_n = max(1, int(len(r_list) * 0.1))
        top10_pct = (
            sorted([t.pnl for t in trades], reverse=True)[:top_n]
        )
        top10_pct = sum(top10_pct) / gross_profit * 100 if gross_profit > 0 else 0.0
        mean_r = np.mean(r_list)
        std_r = np.std(r_list)
        skew = np.mean([(r - mean_r) ** 3 for r in r_list]) / std_r ** 3 if std_r > 0 else 0.0
        streak, cur = 0, 0
        for r in r_list:
            if r <= 0:
                cur += 1; streak = max(streak, cur)
            else:
                cur = 0
        stop_trades = [t for t in trades if t.exit_reason == "STOP"]
        eod_trades  = [t for t in trades if t.exit_reason == "EOD"]
        return ConvexityMetrics(
            n_trades=len(r_list), win_rate=wr,
            avg_win_r=avg_win, avg_loss_r=avg_loss,
            expectancy_r=wr * avg_win + (1 - wr) * avg_loss,
            max_win_r=max(r_list), max_loss_r=min(r_list),
            top10_pct=top10_pct, skewness=skew,
            max_losing_streak=streak,
            total_pnl=sum(t.pnl for t in trades),
            stop_wins=sum(1 for t in stop_trades if t.pnl > 0),
            stop_losses=sum(1 for t in stop_trades if t.pnl <= 0),
            eod_wins=sum(1 for t in eod_trades if t.pnl > 0),
            eod_losses=sum(1 for t in eod_trades if t.pnl <= 0),
            r_multiples=r_list,
            first_date=trades[0].entry_time.date().isoformat(),
            last_date=trades[-1].entry_time.date().isoformat(),
        )

    @staticmethod
    def _calc_by_year(trades: List[Trade]) -> Dict[int, ConvexityMetrics]:
        by_year: Dict[int, List[Trade]] = {}
        for t in trades:
            y = t.entry_time.year
            by_year.setdefault(y, []).append(t)
        return {y: PerformanceAnalyzer._calc_convexity(ts) for y, ts in sorted(by_year.items())}
```

---

### 9. Optimization Engine

**Purpose:** Sweep strategy parameters to find optimal configurations.

**Key Features:**
- Grid search over parameter ranges
- Walk-forward optimization (train/test split)
- Parallel execution (use all CPU cores)
- Overfitting detection
- Results ranking and filtering

**Optimization Methods:**
```python
class OptimizationMethod(Enum):
    GRID_SEARCH = "grid"           # Test all combinations
    RANDOM_SEARCH = "random"       # Random sampling
    GENETIC_ALGORITHM = "genetic"  # Evolutionary optimization
    BAYESIAN = "bayesian"          # Bayesian optimization
```

**Walk-Forward Analysis:**
```
Historical Data: 2020-01-01 to 2024-12-31 (5 years)

Walk-Forward Split:
  Period 1: Train 2020-2021 → Test 2022
  Period 2: Train 2021-2022 → Test 2023
  Period 3: Train 2022-2023 → Test 2024

This prevents overfitting by always testing on unseen data.
```

**Implementation:**
```python
class OptimizationEngine:
    """
    Parameter optimization with walk-forward analysis.
    """

    def __init__(self, backtest_engine: BacktestEngine,
                 method: OptimizationMethod = OptimizationMethod.GRID_SEARCH):
        self.backtest_engine = backtest_engine
        self.method = method

    def optimize(self, strategy_name: str, param_grid: Dict[str, List],
                 data_range: Tuple[datetime, datetime],
                 optimization_metric: str = "sharpe_ratio",
                 walk_forward: bool = True,
                 n_splits: int = 3) -> OptimizationResult:
        """
        Run parameter optimization.

        Args:
            strategy_name: Strategy to optimize
            param_grid: {"param_name": [value1, value2, ...]}
            data_range: (start_date, end_date)
            optimization_metric: Metric to maximize
            walk_forward: Use walk-forward validation
            n_splits: Number of walk-forward periods

        Returns:
            OptimizationResult with best parameters and performance
        """
        # Generate parameter combinations
        param_combinations = self._generate_combinations(param_grid)

        if walk_forward:
            # Split data into train/test periods
            periods = self._create_walk_forward_splits(data_range, n_splits)
        else:
            # Single train/test split
            periods = [self._create_train_test_split(data_range, test_ratio=0.2)]

        # Run backtests in parallel
        results = []
        with concurrent.futures.ProcessPoolExecutor() as executor:
            futures = []
            for params in param_combinations:
                for period in periods:
                    future = executor.submit(
                        self._run_single_backtest,
                        strategy_name,
                        params,
                        period["train"],
                        period["test"]
                    )
                    futures.append((future, params, period))

            for future, params, period in futures:
                result = future.result()
                results.append({
                    "params": params,
                    "period": period,
                    "train_metrics": result["train"],
                    "test_metrics": result["test"]
                })

        # Rank by test performance
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values(
            by=f"test_metrics.{optimization_metric}",
            ascending=False
        )

        # Detect overfitting
        results_df["overfitting_ratio"] = (
            results_df[f"train_metrics.{optimization_metric}"] /
            results_df[f"test_metrics.{optimization_metric}"]
        )

        return OptimizationResult(
            best_params=results_df.iloc[0]["params"],
            best_test_performance=results_df.iloc[0]["test_metrics"],
            all_results=results_df,
            optimization_metric=optimization_metric
        )

    def _generate_combinations(self, param_grid: Dict) -> List[Dict]:
        """Generate all parameter combinations for grid search."""
        from itertools import product

        keys = param_grid.keys()
        values = param_grid.values()
        combinations = [dict(zip(keys, v)) for v in product(*values)]
        return combinations
```

---

### 10. Report Generator

**Purpose:** Generate comprehensive HTML/PDF reports with visualizations.

**Report style: convexity dashboard** (matches `data/orb_convexity_dashboard.html`)

The report is trade-centric, not capital-centric. The primary question is: *is this strategy convex?* — does it lose small often and win big rarely?

**Report Sections:**

1. **Overall — all years combined**
   - 8-metric convexity grid: Win rate / Avg win R / Avg loss R / Expectancy / Largest win / Top 10% / Skewness / Max losing streak
   - Annual PnL bar chart + Annual expectancy bar chart
   - Year-by-year summary table (Trades / Win% / Avg win / Avg loss / Expectancy / Max R / Top 10% / Max streak / PnL)
   - R-multiple histogram (bucket by R range, green/red bars)
   - Cumulative R curve (running sum of R-multiples)
   - Trade R waterfall (trade-by-trade bars)
   - **Convexity scorecard**: each of the 8 metrics rated pass/watch/fail with target thresholds:
     - Win rate 25–45% → convex signal
     - Avg win ≥ 3R
     - Avg loss ≈ −1R (within 5%)
     - Expectancy > 0
     - Max win ≥ 8R (fat tail)
     - Top 10% ≥ 40% of profit
     - Skewness > 0.5
     - Stop wins = 0 (edge comes from EOD holds, not stop reversals)

2. **Per-year sections** (one section per calendar year)
   - Same 8-metric grid for that year
   - Breakdown bars: Win% / Loss% / Stop% / EOD%
   - R-distribution histogram
   - Cumulative R curve
   - Exit outcome chart (Stop wins / Stop losses / EOD wins / EOD losses)
   - Trade R waterfall

3. **Regime breakdown** (P1 — requires MarketRegimeClassifier)
   - Metrics table split by TRENDING / RANGING / TRANSITIONING
   - Shows which regime the strategy profits in

**Implementation:**

The report is built from `BacktestResult` fields — `result.overall` and `result.by_year` for convexity sections, `result.equity` for the equity/drawdown curve. Plotly is used for all charts (self-contained HTML, no server required).

```python
class ReportGenerator:
    """Generates the convexity dashboard HTML report."""

    def generate_html(self, result: BacktestResult, output_path: Path) -> Path:
        overall_section = self._render_overall_section(result.overall, result.equity)
        year_sections   = "".join(
            self._render_year_section(year, cm)
            for year, cm in sorted(result.by_year.items())
        )
        regime_section  = self._render_regime_section(result.regime_breakdown)

        html = self._wrap_page(
            title=f"{result.ruleset_name} — Convexity Report",
            body=overall_section + year_sections + regime_section,
        )
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _render_overall_section(self, cm: ConvexityMetrics, eq: EquityMetrics) -> str:
        """
        Renders:
        - 8-metric convexity grid (win rate, avg win R, avg loss R, expectancy,
          largest win, top 10%, skewness, max losing streak)
        - Convexity scorecard (pass/watch/fail per metric with target thresholds)
        - Annual PnL bar chart + annual expectancy bar chart
        - Year-by-year summary table
        - R-multiple histogram (green/red bars by R bucket)
        - Cumulative R curve
        - Trade R waterfall
        - Equity curve + drawdown curve
        """
        ...

    def _render_year_section(self, year: int, cm: ConvexityMetrics) -> str:
        """
        Renders per-year section:
        - Same 8-metric grid for that year
        - Breakdown bars: Win% / Loss% / Stop% / EOD%
        - R-distribution histogram
        - Cumulative R curve
        - Exit outcome chart (Stop wins / Stop losses / EOD wins / EOD losses)
        - Trade R waterfall
        """
        ...

    def _render_regime_section(self, breakdown: Dict[str, ConvexityMetrics]) -> str:
        """Metrics table split by TRENDING / RANGING / TRANSITIONING."""
        ...
```

---

## Strategy Reuse Pattern

All strategy logic lives in `vibe/common/strategies/` via `StrategyRuleSet`. Both live bot and backtester load the exact same YAML ruleset; only the injected `DataProvider` and `Clock` differ.

```python
# vibe/trading_bot/main.py (Live Trading)
async def run_live_trading():
    data_provider = FinnhubWebSocketClient(...)   # real-time WebSocket
    clock         = LiveClock()                    # wall clock
    execution     = MockExchange(...)              # paper trading
    ruleset       = StrategyRuleSet.from_yaml("config/orb_ruleset.yaml")

    async for bar in data_provider.stream():
        signal = ruleset.generate_signal(bar, clock)
        if signal:
            await execution.submit_order(signal.to_order())


# vibe/backtester/main.py (Backtesting)
async def run_backtest():
    clock         = SimulatedClock()               # data-driven time
    data_provider = ParquetLoader(data_dir, symbols)   # eager-loaded Parquet
    fill_sim      = FillSimulator(...)
    portfolio     = PortfolioManager(initial_capital)
    ruleset       = StrategyRuleSet.from_yaml("config/orb_ruleset.yaml")  # SAME file

    frames = {sym: _resample(await data_provider.get_bars(sym, start_time=start, end_time=end))
              for sym in symbols}
    timestamps = sorted(set().union(*[df.index for df in frames.values()]))

    for ts in timestamps:
        clock.set_time(ts)                         # advance simulated time
        current_bars = {sym: frames[sym].loc[ts] for sym in symbols if ts in frames[sym].index}

        for symbol, bar in current_bars.items():
            signal = ruleset.generate_signal(bar, clock)   # SAME ruleset
            if signal:
                fill = fill_sim.execute(signal, bar)
                portfolio.open_position(fill, stop_price=signal.stop_price, timestamp=ts)

        portfolio.check_exits(current_bars, clock)
        portfolio.update_equity(current_bars, ts)
```

**Key insight:** `StrategyRuleSet` doesn't know whether it's live or backtest — it just receives a `Bar` and a `Clock`. Swap `ParquetLoader` ↔ `FinnhubWebSocketClient` and `SimulatedClock` ↔ `LiveClock` to move between modes.

---

## Directory Structure

```
vibe/
├── __init__.py
│
├── common/                         # SHARED between live & backtest
│   ├── strategies/                 # (Already exists from trading-bot)
│   ├── indicators/
│   ├── risk/
│   ├── validation/
│   ├── models/
│   ├── execution/
│   ├── data/
│   └── clock/
│
├── trading_bot/                    # Live trading (already implemented)
│   └── ...
│
├── backtester/                     # NEW: Backtesting specific
│   ├── __init__.py
│   ├── main.py                     # Entry point
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── engine.py               # BacktestEngine
│   │   ├── portfolio.py            # PortfolioManager
│   │   ├── clock.py                # SimulatedClock
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── historical_provider.py  # HistoricalDataProvider
│   │   ├── loader.py               # Parquet/CSV loaders
│   │   ├── downloader.py           # Download historical data
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── fill_simulator.py       # FillSimulator
│   │   ├── slippage.py             # SlippageModel (reuse from trading-bot?)
│   │   ├── commission.py           # CommissionModel
│   │   ├── liquidity.py            # LiquidityModel
│   │
│   ├── assets/                     # P1: stock only
│   │   ├── __init__.py
│   │   ├── spec.py                 # AssetSpec, AssetType
│   │   ├── stock.py                # Stock-specific logic
│   │   ├── forex.py                # P2 — not in MVP
│   │   ├── crypto.py               # P2 — not in MVP
│   │   ├── option.py               # P2 — not in MVP
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── performance.py          # PerformanceAnalyzer
│   │   ├── metrics.py              # Metrics calculation
│   │   ├── statistics.py           # Trade statistics
│   │
│   ├── optimization/               # P2 — not in MVP
│   │   ├── __init__.py
│   │   ├── engine.py               # OptimizationEngine
│   │   ├── grid_search.py
│   │   ├── walk_forward.py
│   │   ├── genetic.py
│   │
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── generator.py            # ReportGenerator
│   │   ├── templates/              # HTML templates
│   │   └── charts.py               # Chart generation
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── data_helpers.py
│   │   └── validation.py
│   │
│   └── config/
│       ├── __init__.py
│       └── settings.py             # Backtest configuration
│
├── tests/
│   ├── backtester/                 # NEW: Backtest tests
│   │   ├── test_engine.py
│   │   ├── test_fill_simulator.py
│   │   ├── test_performance.py
│   │   ├── test_optimization.py
│   │   └── integration/
│   │       └── test_full_backtest.py
│
└── scripts/                        # NEW: Utility scripts
    ├── download_data.py            # Download historical data
    ├── run_backtest.py             # CLI for running backtests
    ├── run_optimization.py         # CLI for optimization
    └── compare_results.py          # Compare multiple backtests
```

---

## Configuration

```yaml
# config/backtest.yaml
backtest:
  # Data
  data_source: "parquet"  # parquet, csv, yahoo, alpaca
  data_dir: "data/historical"
  symbols: ["AAPL", "MSFT", "AMZN", "TSLA", "GOOGL"]
  start_date: "2020-01-01"
  end_date: "2024-12-31"
  timeframe: "5m"

  # Asset
  asset_type: "stock"  # stock, forex, crypto, option

  # Execution
  fill_mode: 0            # 0=instant (bar close), 1=next-bar (bar N+1 open)
  slippage_ticks: 5       # 1 tick = $0.01; default 5 ticks = $0.05/share
  commission: 0.0         # zero (standard US retail broker)

  # Capital
  initial_capital: 10000
  margin_enabled: false
  leverage: 1.0

  # Strategy
  strategy_name: "orb"
  strategy_params:
    opening_range_minutes: 5
    take_profit_atr_multiple: 2.0
    stop_loss_atr_multiple: 1.0

  # Multi-Timeframe Validation
  mtf_validation_enabled: true
  validation_timeframes: ["15m", "1h"]

  # Risk
  max_position_size: 1000  # shares
  max_positions: 5
  risk_per_trade_pct: 0.01  # 1% of capital

  # Analysis
  # benchmark_symbol: "SPY"  # P2 — not used in MVP (ORB vs SPY buy-and-hold is not meaningful)

# Optimization (P2 — not in MVP)
# optimization:
#   method: "grid"
#   metric: "expectancy_r"
#   walk_forward: true
#   n_splits: 3
#   param_grid:
#     opening_range_minutes: [5, 10, 15, 30]
#     take_profit_atr_multiple: [1.5, 2.0, 2.5, 3.0]
#     stop_loss_atr_multiple: [0.5, 1.0, 1.5]
```

---

## Usage Examples

### Simple Backtest

```python
from vibe.backtester import BacktestEngine
from vibe.backtester.config import BacktestConfig

# Load configuration
config = BacktestConfig.from_yaml("config/backtest.yaml")

# Create backtest engine
engine = BacktestEngine(config)

# Run backtest
result = engine.run(
    strategy_name="orb",
    symbols=["AAPL"],
    start_date="2023-01-01",
    end_date="2023-12-31"
)

# Display results
print(f"Total Return: {result.total_return:.2%}")
print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
print(f"Max Drawdown: {result.max_drawdown:.2%}")
print(f"Total Trades: {result.total_trades}")

# Generate report
from vibe.backtester.reporting import ReportGenerator
report = ReportGenerator()
report.generate_html(result, "reports/backtest_result.html")
```

### Parameter Optimization *(P2 — not in MVP)*

```python
from vibe.backtester.optimization import OptimizationEngine

# Define parameter grid
param_grid = {
    "opening_range_minutes": [5, 10, 15, 30],
    "take_profit_atr_multiple": [1.5, 2.0, 2.5, 3.0],
    "stop_loss_atr_multiple": [0.5, 1.0, 1.5]
}

# Run optimization
optimizer = OptimizationEngine(engine)
result = optimizer.optimize(
    strategy_name="orb",
    param_grid=param_grid,
    data_range=("2020-01-01", "2024-12-31"),
    optimization_metric="sharpe_ratio",
    walk_forward=True,
    n_splits=3
)

# Best parameters
print(f"Best params: {result.best_params}")
print(f"Train Sharpe: {result.best_train_sharpe:.2f}")
print(f"Test Sharpe: {result.best_test_sharpe:.2f}")
print(f"Overfitting ratio: {result.overfitting_ratio:.2f}")
```

### Multi-Asset Backtest *(P2 — not in MVP; MVP runs one symbol at a time)*

```python
# Test strategy on multiple assets
results = {}

for symbol in ["AAPL", "MSFT", "AMZN", "TSLA", "GOOGL"]:
    result = engine.run(
        strategy_name="orb",
        symbols=[symbol],
        start_date="2023-01-01",
        end_date="2023-12-31"
    )
    results[symbol] = result

# Compare
comparison = pd.DataFrame({
    symbol: {
        "Total Return": r.total_return,
        "Sharpe Ratio": r.sharpe_ratio,
        "Max Drawdown": r.max_drawdown,
        "Win Rate": r.win_rate
    }
    for symbol, r in results.items()
}).T

print(comparison)
```

---

## Design Decisions (Finalized)

Based on requirements discussion, here are the finalized design decisions:

### 1. **Data Strategy** ✅
- **Primary Source**: Databento XNAS ITCH ohlcv-1m (downloaded, stored locally as `.csv.zst`)
- **Data loader pattern**: `ParquetLoader` implements the shared `DataProvider` ABC — swap sources by injecting a different `DataProvider`; no other code changes required. See component #3.
- **Storage**:
  - Raw: `data/databento/*.csv.zst` (compressed, read directly — no conversion step needed)
  - Cache: in-memory `Dict[symbol, pd.DataFrame]` after first load per session
- **Scope**: 5 symbols, 2018-05-01 → 2026-04-28 (~8 years per symbol)
- **Resolution**: Raw data is 1-minute bars; backtester resamples to 5m on load

**Available symbol list** (Databento download):
```python
SYMBOLS = [
    "QQQ",    # Nasdaq-100 ETF — primary ORB target, high volume
    "MSFT",   # Microsoft — large cap, clean trends
    "AMZN",   # Amazon — large cap, higher volatility
    "GOOGL",  # Google — large cap
    "TSLA",   # Tesla — high volatility
]
```

### 2. **Backtest Speed vs Realism** ✅
- **Approach**: Hybrid (Instant fill + realistic factors)
  - **NO time delay simulation** (not critical for strategy validation)
  - **YES slippage model** (price impact matters)
  - **YES partial fills** (liquidity constraints matter)
- **Rationale**: Time delay (100-500ms) is negligible for 5-minute bar strategies
- **Performance**: Run backtest in seconds, not minutes

### 3. **Optimization Scope** *(P2 — not in MVP)*
- P1 ships no optimization; the engine runs a single fixed ruleset
- P2 will add grid search (50–100 combinations) with local CPU parallelization
- Walk-forward and genetic/Bayesian methods are future scope beyond P2

### 4. **MVP Scope** ✅

**Phase 1 - MVP:**
- ✅ Event-driven backtest engine
- ✅ Single-symbol backtesting (no portfolio)
- ✅ Stock trading (US market)
- ✅ ORB strategy loaded via `StrategyRuleSet` (same object as live bot)
- ✅ Hybrid fill simulation (instant + slippage + partial fills)
- ✅ Data quality checks (splits, gaps, outliers)
- ✅ Convexity metrics (R-multiple based) — overall + per-year breakdown
- ✅ Market regime classification (ADX-based)
- ✅ HTML convexity report (matching `orb_convexity_dashboard.html` layout)

**Phase 2 - Enhancement (future):**
- 🔲 In-sample / out-of-sample parameter optimization
- 🔲 Walk-forward optimization
- 🔲 Multiple optimization methods (genetic, Bayesian)
- 🔲 Interactive Streamlit dashboard
- 🔲 Forex/Crypto support

**Not Needed (Rationale):**
- ❌ Portfolio backtesting - Not relevant for day trading single setups
- ❌ Options trading - Different strategy type, future scope
- ❌ Time delay simulation - Negligible impact on 5m bar strategies
- ❌ Benchmark comparison (buy-and-hold SPY) - ORB is a day-trade strategy; SPY buy-and-hold is not a meaningful comparison

### 5. **Data Storage** ✅
- **Raw source**: `data/databento/*.ohlcv-1m.<SYMBOL>.csv.zst` — preserved as source-of-truth, never read at backtest runtime
- **One-time conversion**: `scripts/convert_databento.py` decompresses, adjusts splits, validates, and writes one `<SYMBOL>.parquet` per symbol to `BACKTEST__DATA_DIR`
- **Runtime**: `ParquetLoader` reads all symbols from Parquet at init (eager load into `Dict[str, pd.DataFrame]`); all subsequent `get_bars` / `get_bar` calls are in-memory slices — no disk I/O after startup

---

## Critical Missing Pieces (Now Added)

### 1. Data Quality Checks

**Why needed even for specific symbols:**
- Stock splits (AAPL 4:1 in 2020, TSLA 5:1 in 2020, GOOGL 20:1 in 2022)
- Data gaps (holidays, halts, provider outages)
- Bad ticks (flash crashes, after-hours data contamination)
- Corporate actions (dividends, mergers)

**Quality Check Pipeline:**
```python
class DataQualityChecker:
    """Validate historical data before backtesting."""

    def check_all(self, df: pd.DataFrame, symbol: str) -> DataQualityReport:
        """Run all quality checks."""
        issues = []

        # 1. Check for gaps
        issues.extend(self.check_gaps(df))

        # 2. Check for outliers (>10% intrabar move)
        issues.extend(self.check_outliers(df))

        # 3. Check for negative prices
        issues.extend(self.check_negative_prices(df))

        # 4. Check OHLC consistency (high >= low, high >= open/close, etc.)
        issues.extend(self.check_ohlc_consistency(df))

        # 5. Check for zero volume
        issues.extend(self.check_volume(df))

        # 6. Check for splits (large overnight gap with volume spike)
        issues.extend(self.check_splits(df))

        return DataQualityReport(symbol=symbol, issues=issues)

    def check_splits(self, df: pd.DataFrame) -> List[DataIssue]:
        """Detect potential unadjusted splits."""
        # Overnight gaps > 20% with high volume = likely split
        overnight_returns = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
        potential_splits = df[abs(overnight_returns) > 0.20]

        if len(potential_splits) > 0:
            return [DataIssue(
                severity="CRITICAL",
                message=f"Potential unadjusted split detected at {date}",
                date=date
            ) for date in potential_splits.index]
        return []
```

### 2. In-Sample / Out-of-Sample Testing *(P2 — not needed for MVP)*

**Prevent overfitting by always validating on unseen data:**

```python
class InSampleOutSampleSplitter:
    """
    Split data into training (in-sample) and testing (out-of-sample).
    """

    def split(self, data: pd.DataFrame,
              in_sample_ratio: float = 0.7) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Split data chronologically.

        Example:
            Data: 2020-01-01 to 2024-12-31 (5 years)
            In-sample: 2020-01-01 to 2023-06-30 (70%)
            Out-of-sample: 2023-07-01 to 2024-12-31 (30%)
        """
        split_point = int(len(data) * in_sample_ratio)
        in_sample = data.iloc[:split_point]
        out_of_sample = data.iloc[split_point:]

        return in_sample, out_of_sample


class OptimizationEngine:
    def optimize_with_validation(self, param_grid: Dict, data: pd.DataFrame):
        """
        Optimize on in-sample, validate on out-of-sample.
        """
        # Split data
        in_sample, out_of_sample = self.splitter.split(data, in_sample_ratio=0.7)

        # Optimize on in-sample
        best_params = self.grid_search(param_grid, in_sample)

        # Validate on out-of-sample
        is_result = self.backtest(best_params, in_sample)
        oos_result = self.backtest(best_params, out_of_sample)

        # Check for overfitting
        overfitting_ratio = is_result.sharpe / oos_result.sharpe

        if overfitting_ratio > 2.0:
            logger.warning(f"Possible overfitting detected! IS Sharpe: {is_result.sharpe:.2f}, OOS Sharpe: {oos_result.sharpe:.2f}")

        return OptimizationResult(
            best_params=best_params,
            in_sample_metrics=is_result,
            out_of_sample_metrics=oos_result,
            overfitting_ratio=overfitting_ratio
        )
```

### 3. Market Regime Classification

**ADX-Based Regime Detection (MVP Approach):**

```python
class MarketRegimeClassifier:
    """
    Classify market regime for strategy performance analysis.
    """

    def __init__(self, method: str = "adx"):
        self.method = method

    def classify_adx(self, df: pd.DataFrame) -> pd.Series:
        """
        Classify regime based on ADX (Average Directional Index).

        ADX > 25: Trending market
        ADX < 20: Ranging/choppy market
        ADX 20-25: Transitioning
        """
        adx = self.calculate_adx(df, period=14)

        regime = pd.Series(index=df.index, dtype=str)
        regime[adx > 25] = "TRENDING"
        regime[(adx >= 20) & (adx <= 25)] = "TRANSITIONING"
        regime[adx < 20] = "RANGING"

        return regime

    def classify_price_sma(self, df: pd.DataFrame) -> pd.Series:
        """
        Classify regime based on price vs SMA200.

        Price > SMA200 + 5%: BULL
        Price < SMA200 - 5%: BEAR
        Otherwise: NEUTRAL
        """
        sma200 = df['close'].rolling(200).mean()
        deviation = (df['close'] - sma200) / sma200

        regime = pd.Series(index=df.index, dtype=str)
        regime[deviation > 0.05] = "BULL"
        regime[deviation < -0.05] = "BEAR"
        regime[abs(deviation) <= 0.05] = "NEUTRAL"

        return regime

    def classify_multifactor(self, df: pd.DataFrame) -> pd.Series:
        """
        Classify regime using multiple factors (future enhancement).
        """
        # Combine ADX, SMA, RSI, volatility
        # Return composite regime classification
        pass


class PerformanceByRegime:
    """Analyze strategy performance by market regime."""

    def analyze(self, trades: List[Trade],
                regime_data: pd.Series) -> Dict[str, PerformanceMetrics]:
        """
        Calculate metrics separately for each regime.

        Returns:
            {
                "TRENDING": PerformanceMetrics(...),
                "RANGING": PerformanceMetrics(...),
                ...
            }
        """
        results = {}

        for regime in regime_data.unique():
            regime_trades = [
                t for t in trades
                if regime_data[t.entry_time] == regime
            ]

            if regime_trades:
                results[regime] = PerformanceAnalyzer.analyze(regime_trades)

        return results
```

**Why this matters:**
- Your ORB strategy likely performs well in **trending** markets
- May lose money in **ranging/choppy** markets
- Knowing this helps you:
  - Add market regime filter to strategy
  - Only trade when regime is favorable
  - Adjust parameters per regime

### 4. Benchmark Comparison

**Always compare against buy-and-hold:**

```python
class BenchmarkComparison:
    """Compare strategy against buy-and-hold benchmark."""

    def compare(self, strategy_result: BacktestResult,
                symbol: str, benchmark: str = "SPY") -> BenchmarkReport:
        """
        Compare strategy vs benchmark.

        Args:
            strategy_result: Backtest result
            symbol: Trading symbol
            benchmark: Benchmark symbol (default: SPY)
        """
        # Get benchmark data
        benchmark_data = self.data_provider.get_historical(
            benchmark,
            start=strategy_result.start_date,
            end=strategy_result.end_date
        )

        # Calculate buy-and-hold return
        buy_hold_return = (
            (benchmark_data['close'].iloc[-1] - benchmark_data['close'].iloc[0]) /
            benchmark_data['close'].iloc[0]
        )

        # Compare
        alpha = strategy_result.total_return - buy_hold_return

        # Calculate beta (strategy vs benchmark correlation)
        strategy_returns = strategy_result.equity_curve.pct_change()
        benchmark_returns = benchmark_data['close'].pct_change()
        beta = np.cov(strategy_returns, benchmark_returns)[0, 1] / np.var(benchmark_returns)

        return BenchmarkReport(
            strategy_return=strategy_result.total_return,
            benchmark_return=buy_hold_return,
            alpha=alpha,
            beta=beta,
            outperformed=alpha > 0,
            risk_adjusted_alpha=alpha / strategy_result.volatility
        )
```

### 5. Transaction Cost Analysis

**Open item - to be detailed in implementation:**
- Beyond commission (mostly $0 now)
- Need to model:
  - Bid-ask spread (especially for less liquid stocks)
  - Market impact (large orders move price)
  - Slippage (difference between expected and actual fill)

**Placeholder for future design:**
```python
class TransactionCostModel:
    """
    Model all transaction costs.
    """

    def calculate_total_cost(self, order: Order, bar: Bar) -> TransactionCost:
        """
        Calculate all costs for an order.

        Returns:
            TransactionCost(
                commission=0.0,        # Most US brokers are $0
                spread=0.05,           # Bid-ask spread
                market_impact=0.02,    # Price movement from order
                slippage=0.03,         # Execution vs expected
                total=0.10             # Sum of all costs
            )
        """
        # TO BE IMPLEMENTED
        pass
```

---