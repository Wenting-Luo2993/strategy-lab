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
        data_provider = HistoricalDataProvider(...)
        clock = SimulatedClock(start_date)
        fill_simulator = FillSimulator(asset_type=self.asset_type, ...)
        strategy = StrategyFactory.create(strategy_config)
        risk_manager = RiskManager(...)

        # 2. Load all data into memory (or stream if too large)
        historical_data = data_provider.load(symbols, start_date, end_date)

        # 3. Event loop
        while clock.current_time <= end_date:
            # Get current bars for all symbols
            current_bars = historical_data.get_bars_at(clock.current_time)

            for symbol, bar in current_bars.items():
                # Update indicators incrementally
                strategy.update_indicators(symbol, bar)

                # Generate signal
                signal = strategy.generate_signal(symbol, clock.current_time)

                if signal:
                    # Run risk checks
                    risk_ok = risk_manager.pre_trade_check(signal)

                    if risk_ok:
                        # Simulate order execution
                        order = create_order_from_signal(signal)
                        fill = fill_simulator.execute(order, bar)

                        # Update portfolio
                        portfolio.update(fill)

                        # Record trade
                        trade_store.insert(fill)

            # Check for stop-losses and take-profits
            portfolio.check_exits(current_bars)

            # Update portfolio value
            portfolio.update_equity(current_bars)

            # Advance time to next bar
            clock.advance()

        # 4. Calculate final metrics
        return PerformanceAnalyzer.analyze(trade_store, portfolio)
```

---

### 2. Simulated Clock

**Purpose:** Control time progression during backtest to enable deterministic, fast execution.

**Key Features:**
- Start at `start_date`, end at `end_date`
- Advance by bar interval (1m, 5m, 1h, 1d, etc.)
- Support for multiple timeframes (if 5m is primary, also track 15m and 1h bars)
- Implement `Clock` ABC from `vibe/common/clock/base.py`
- Market hours awareness (skip non-trading hours)

**Implementation:**
```python
class SimulatedClock(Clock):
    """
    Simulated clock for backtesting.
    Allows fast-forward through historical data.
    """

    def __init__(self, start_time: datetime, bar_interval: str, market_calendar: str = "NYSE"):
        self.current_time = start_time
        self.bar_interval = pd.Timedelta(bar_interval)
        self.market_calendar = mcal.get_calendar(market_calendar)

    def now(self) -> datetime:
        """Return current simulated time."""
        return self.current_time

    def advance(self):
        """Move to next bar."""
        self.current_time += self.bar_interval

        # Skip non-market hours if needed
        if not self.is_market_open():
            self.current_time = self.next_market_open()

    def is_market_open(self) -> bool:
        """Check if current time is during market hours."""
        # Use pandas_market_calendars
        return self.market_calendar.is_open_at_time(self.current_time)
```

---

### 3. Historical Data Provider

**Purpose:** Load historical OHLCV data for backtesting.

**Key Features:**
- Support multiple formats: Parquet, CSV, HDF5
- Support multiple sources: local files, Yahoo Finance, Alpaca, crypto APIs
- Efficient data loading (lazy loading for large datasets)
- Data validation (check for gaps, outliers)
- Timezone handling
- Implement `DataProvider` ABC from `vibe/common/data/base.py`

**Data Storage Strategy:**
```
data/
├── stocks/
│   ├── AAPL/
│   │   ├── 5m.parquet    # 5-minute bars
│   │   ├── 1h.parquet    # 1-hour bars
│   │   └── 1d.parquet    # Daily bars
│   ├── MSFT/
│   └── ...
├── forex/
│   ├── EURUSD/
│   │   └── 1m.parquet    # Forex trades 24/5
│   └── ...
└── crypto/
    ├── BTCUSD/
    │   └── 1m.parquet    # Crypto trades 24/7
    └── ...
```

**Implementation:**
```python
class HistoricalDataProvider(DataProvider):
    """
    Loads historical data for backtesting.
    """

    def __init__(self, data_dir: Path, asset_type: AssetType):
        self.data_dir = data_dir
        self.asset_type = asset_type
        self.cache = {}  # In-memory cache

    def load(self, symbols: List[str], start: datetime, end: datetime,
             interval: str) -> Dict[str, pd.DataFrame]:
        """
        Load historical data for multiple symbols.
        Returns dict of {symbol: DataFrame with OHLCV}.
        """
        result = {}
        for symbol in symbols:
            # Check cache first
            cache_key = (symbol, interval, start, end)
            if cache_key in self.cache:
                result[symbol] = self.cache[cache_key]
                continue

            # Load from disk
            file_path = self.data_dir / self.asset_type.value / symbol / f"{interval}.parquet"
            df = pd.read_parquet(file_path)

            # Filter by date range
            df = df[(df.index >= start) & (df.index <= end)]

            # Validate
            self._validate_data(df, symbol)

            # Cache
            self.cache[cache_key] = df
            result[symbol] = df

        return result

    def get_bars(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Get last N bars (implements DataProvider ABC)."""
        # Implementation for incremental bar access
        pass

    def get_current_price(self, symbol: str) -> float:
        """Get current price (implements DataProvider ABC)."""
        # Return last close price at current simulated time
        pass
```

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

**Purpose:** Simulate realistic order execution with configurable realism levels.

**Realism Levels:**
1. **Instant Fill (Level 0)**: Orders fill immediately at bar close price (fastest, least realistic)
2. **Next-Bar Fill (Level 1)**: Signal on bar N, fill at open of bar N+1 (simple, reasonable)
3. **Realistic Fill (Level 2)**: Slippage, partial fills, fill probability based on volume
4. **Full Book Simulation (Level 3)**: Bid-ask spread, market depth, liquidity (most realistic, slowest)

**Implementation:**
```python
class FillSimulator(ExecutionEngine):
    """
    Simulates order execution for backtesting.
    Implements ExecutionEngine ABC from vibe/common/.
    """

    def __init__(self, config: FillSimulatorConfig, asset_spec: AssetSpec):
        self.realism_level = config.realism_level
        self.slippage_model = SlippageModel(asset_spec)
        self.commission_model = CommissionModel(asset_spec)
        self.liquidity_model = LiquidityModel()

    async def submit_order(self, order: Order, bar: Bar) -> OrderResponse:
        """
        Simulate order execution.
        """
        if self.realism_level == 0:
            # Instant fill at bar close
            fill_price = bar.close
            filled_qty = order.quantity

        elif self.realism_level == 1:
            # Fill at next bar open (to be called with next bar)
            fill_price = bar.open
            filled_qty = order.quantity

        elif self.realism_level >= 2:
            # Realistic fill with slippage
            fill_price = self.slippage_model.apply(
                price=bar.close,
                side=order.side,
                volatility=self._calculate_atr(bar),
                order_size=order.quantity,
                avg_volume=bar.volume
            )

            # Check liquidity constraints
            filled_qty = self.liquidity_model.get_filled_quantity(
                order_quantity=order.quantity,
                bar_volume=bar.volume,
                volatility=self._calculate_atr(bar)
            )

        # Calculate commission
        commission = self.commission_model.calculate(
            quantity=filled_qty,
            price=fill_price
        )

        return OrderResponse(
            order_id=order.id,
            status=OrderStatus.FILLED if filled_qty == order.quantity else OrderStatus.PARTIAL,
            filled_qty=filled_qty,
            avg_price=fill_price,
            remaining_qty=order.quantity - filled_qty,
            commission=commission
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
```python
class PortfolioManager:
    """
    Manages portfolio state during backtest.
    """

    def __init__(self, initial_capital: float, margin_enabled: bool = False):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trade_history: List[Trade] = []
        self.margin_enabled = margin_enabled
        self.buying_power = initial_capital * (2 if margin_enabled else 1)

    def update(self, fill: OrderResponse, timestamp: datetime):
        """Update portfolio after order fill."""
        symbol = fill.symbol

        if fill.side == "buy":
            # Open or add to long position
            if symbol not in self.positions:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    quantity=fill.filled_qty,
                    entry_price=fill.avg_price,
                    side="long",
                    entry_time=timestamp
                )
            else:
                # Average up
                pos = self.positions[symbol]
                total_cost = (pos.quantity * pos.entry_price) + (fill.filled_qty * fill.avg_price)
                pos.quantity += fill.filled_qty
                pos.entry_price = total_cost / pos.quantity

            # Deduct cash
            self.cash -= (fill.filled_qty * fill.avg_price + fill.commission)

        elif fill.side == "sell":
            # Close or reduce long position
            if symbol in self.positions:
                pos = self.positions[symbol]

                # Calculate realized P&L
                realized_pnl = (fill.avg_price - pos.entry_price) * fill.filled_qty - fill.commission

                pos.quantity -= fill.filled_qty

                if pos.quantity == 0:
                    # Position closed
                    del self.positions[symbol]

                    # Record trade
                    self.trade_history.append(Trade(
                        symbol=symbol,
                        side="long",
                        entry_price=pos.entry_price,
                        exit_price=fill.avg_price,
                        quantity=fill.filled_qty,
                        entry_time=pos.entry_time,
                        exit_time=timestamp,
                        pnl=realized_pnl,
                        pnl_pct=(fill.avg_price - pos.entry_price) / pos.entry_price
                    ))

                # Add cash
                self.cash += (fill.filled_qty * fill.avg_price - fill.commission)

    def update_equity(self, current_prices: Dict[str, float], timestamp: datetime):
        """Update equity curve with current market prices."""
        # Calculate unrealized P&L
        unrealized_pnl = sum(
            (current_prices[symbol] - pos.entry_price) * pos.quantity
            for symbol, pos in self.positions.items()
            if symbol in current_prices
        )

        equity = self.cash + unrealized_pnl
        self.equity_curve.append((timestamp, equity))

    def get_account(self) -> AccountState:
        """Implements ExecutionEngine.get_account()."""
        return AccountState(
            cash=self.cash,
            equity=self.calculate_equity(),
            buying_power=self.buying_power,
            positions=list(self.positions.values())
        )
```

---

### 8. Performance Analyzer

**Purpose:** Calculate comprehensive performance metrics from backtest results.

**Metrics to Calculate:**
```python
class PerformanceMetrics:
    # Returns
    total_return: float           # (final_equity - initial_capital) / initial_capital
    annualized_return: float      # CAGR

    # Risk-Adjusted Returns
    sharpe_ratio: float           # (mean_return - risk_free_rate) / std_return
    sortino_ratio: float          # Downside risk only
    calmar_ratio: float           # CAGR / max_drawdown

    # Risk
    max_drawdown: float           # Largest peak-to-trough decline
    max_drawdown_duration: int    # Days in max drawdown
    volatility: float             # Annualized standard deviation

    # Trade Statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float               # winning_trades / total_trades
    avg_win: float
    avg_loss: float
    profit_factor: float          # gross_profit / gross_loss
    expectancy: float             # (win_rate * avg_win) - (loss_rate * avg_loss)

    # Position Statistics
    avg_trade_duration: timedelta
    avg_position_size: float
    max_position_size: float

    # Equity Curve
    equity_curve: pd.Series
    drawdown_curve: pd.Series

    # Benchmarks
    benchmark_return: float       # Buy-and-hold SPY
    alpha: float                  # Excess return vs benchmark
    beta: float                   # Correlation with benchmark
```

**Implementation:**
```python
class PerformanceAnalyzer:
    """
    Calculates comprehensive performance metrics.
    """

    @staticmethod
    def analyze(trade_history: List[Trade], equity_curve: pd.Series,
                initial_capital: float, benchmark: pd.Series = None) -> PerformanceMetrics:
        """
        Analyze backtest results.
        """
        # Calculate returns
        returns = equity_curve.pct_change().dropna()
        total_return = (equity_curve.iloc[-1] - initial_capital) / initial_capital

        # Annualized return (CAGR)
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25
        annualized_return = (1 + total_return) ** (1 / years) - 1

        # Sharpe ratio (assuming 252 trading days, 0% risk-free rate)
        sharpe_ratio = np.sqrt(252) * returns.mean() / returns.std()

        # Max drawdown
        rolling_max = equity_curve.expanding().max()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown = drawdown.min()

        # Trade statistics
        winning_trades = [t for t in trade_history if t.pnl > 0]
        losing_trades = [t for t in trade_history if t.pnl <= 0]

        win_rate = len(winning_trades) / len(trade_history) if trade_history else 0
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.pnl for t in losing_trades]) if losing_trades else 0

        gross_profit = sum(t.pnl for t in winning_trades)
        gross_loss = abs(sum(t.pnl for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        return PerformanceMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            total_trades=len(trade_history),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            equity_curve=equity_curve,
            drawdown_curve=drawdown,
            # ... additional metrics
        )
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

**Report Sections:**
1. **Summary**
   - Key metrics table
   - Equity curve chart
   - Drawdown chart

2. **Trade Analysis**
   - Trade list table
   - Win/loss distribution histogram
   - Trade duration distribution
   - P&L by symbol

3. **Risk Analysis**
   - Drawdown periods
   - Volatility over time
   - Risk-adjusted return metrics

4. **Strategy Behavior**
   - Entry/exit reasons distribution
   - Position size over time
   - Holding period analysis

5. **Market Conditions**
   - Performance by market regime
   - Correlation with benchmark
   - Performance by day of week, time of day

**Implementation:**
```python
class ReportGenerator:
    """
    Generate HTML/PDF backtest reports.
    """

    def generate_html(self, backtest_result: BacktestResult,
                     output_path: Path) -> Path:
        """
        Generate interactive HTML report with Plotly charts.
        """
        report_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Backtest Report - {backtest_result.strategy_name}</title>
            <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .metric {{ display: inline-block; margin: 10px; padding: 15px;
                          background: #f0f0f0; border-radius: 5px; }}
                .metric-value {{ font-size: 24px; font-weight: bold; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
            </style>
        </head>
        <body>
            <h1>Backtest Report: {backtest_result.strategy_name}</h1>

            <!-- Key Metrics -->
            <h2>Performance Summary</h2>
            <div class="metrics-grid">
                <div class="metric">
                    <div>Total Return</div>
                    <div class="metric-value">{backtest_result.total_return:.2%}</div>
                </div>
                <div class="metric">
                    <div>Sharpe Ratio</div>
                    <div class="metric-value">{backtest_result.sharpe_ratio:.2f}</div>
                </div>
                <div class="metric">
                    <div>Max Drawdown</div>
                    <div class="metric-value">{backtest_result.max_drawdown:.2%}</div>
                </div>
                <!-- More metrics -->
            </div>

            <!-- Equity Curve -->
            <h2>Equity Curve</h2>
            <div id="equity-curve"></div>
            <script>
                var data = [{equity_curve_json}];
                Plotly.newPlot('equity-curve', data);
            </script>

            <!-- Trade List -->
            <h2>Trade History</h2>
            <table>
                <tr>
                    <th>Symbol</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>P&L</th>
                    <th>Duration</th>
                </tr>
                {trade_rows_html}
            </table>
        </body>
        </html>
        """

        output_path.write_text(report_html)
        return output_path
```

---

## Strategy Reuse Pattern

All strategy logic lives in `vibe/common/strategies/`. Here's how both live trading and backtesting use the same strategy:

```python
# vibe/common/strategies/orb.py
class ORBStrategy(StrategyBase):
    """
    Opening Range Breakout strategy.
    SHARED between live trading and backtesting.
    """

    def generate_signal(self, symbol: str, timestamp: datetime) -> Optional[Signal]:
        """
        Generate trading signal.
        This method is called by BOTH live bot and backtester.
        """
        # Get current bar
        current_bar = self.data_provider.get_current_bar(symbol)

        # Calculate ORB levels
        orb_high, orb_low = self.orb_calculator.calculate(symbol, self.clock.now())

        # Check for breakout
        if current_bar.close > orb_high:
            return Signal(
                symbol=symbol,
                side="buy",
                signal_strength=1.0,
                reason=f"Breakout above ORB high {orb_high}"
            )
        elif current_bar.close < orb_low:
            return Signal(
                symbol=symbol,
                side="sell",
                signal_strength=1.0,
                reason=f"Breakdown below ORB low {orb_low}"
            )

        return None


# vibe/trading_bot/main.py (Live Trading)
def run_live_trading():
    data_provider = FinnhubWebSocketClient(...)  # Real-time data
    clock = LiveClock()                           # Real time
    execution = MockExchange(...)                 # Paper trading

    strategy = ORBStrategy(                       # SAME strategy class
        data_provider=data_provider,
        clock=clock,
        config=strategy_config
    )

    # Run 24/7
    while True:
        signal = strategy.generate_signal("AAPL", clock.now())
        if signal:
            execution.submit_order(signal_to_order(signal))


# vibe/backtester/main.py (Backtesting)
def run_backtest():
    data_provider = HistoricalDataProvider(...)  # Historical data
    clock = SimulatedClock(start_date)           # Simulated time
    execution = FillSimulator(...)               # Instant fills

    strategy = ORBStrategy(                      # SAME strategy class
        data_provider=data_provider,
        clock=clock,
        config=strategy_config
    )

    # Run through historical data
    while clock.now() < end_date:
        signal = strategy.generate_signal("AAPL", clock.now())
        if signal:
            execution.submit_order(signal_to_order(signal))
        clock.advance()
```

**Key Insight:** The strategy doesn't know whether it's running live or in backtest. It just calls `self.data_provider.get_current_bar()` and `self.clock.now()`, and the implementation is swapped.

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
│   ├── assets/
│   │   ├── __init__.py
│   │   ├── spec.py                 # AssetSpec, AssetType
│   │   ├── stock.py                # Stock-specific logic
│   │   ├── forex.py                # Forex-specific logic
│   │   ├── crypto.py               # Crypto-specific logic
│   │   ├── option.py               # Option-specific logic (future)
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── performance.py          # PerformanceAnalyzer
│   │   ├── metrics.py              # Metrics calculation
│   │   ├── statistics.py           # Trade statistics
│   │
│   ├── optimization/
│   │   ├── __init__.py
│   │   ├── engine.py               # OptimizationEngine
│   │   ├── grid_search.py
│   │   ├── walk_forward.py
│   │   ├── genetic.py              # (future)
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
  fill_realism_level: 2  # 0=instant, 1=next-bar, 2=realistic, 3=full-book
  slippage_pct: 0.0005   # 0.05%
  commission_model: "zero"  # zero, per_share, percentage

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
  benchmark_symbol: "SPY"

# Optimization
optimization:
  method: "grid"  # grid, random, genetic, bayesian
  metric: "sharpe_ratio"  # sharpe_ratio, total_return, calmar_ratio
  walk_forward: true
  n_splits: 3

  param_grid:
    opening_range_minutes: [5, 10, 15, 30]
    take_profit_atr_multiple: [1.5, 2.0, 2.5, 3.0]
    stop_loss_atr_multiple: [0.5, 1.0, 1.5]
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

### Parameter Optimization

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

### Multi-Asset Backtest

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
- **Primary Source**: Yahoo Finance (yfinance) - Already implemented in Phase 2
- **Secondary Source**: Alpaca API (future enhancement)
- **Storage**:
  - Cloud storage for long-term historical data (10+ years)
  - Local Parquet files for active backtesting (3 years, 10 symbols)
- **Scope**: 10 symbols (mix of large cap and medium cap), 3 years of data (~1GB)
- **Resolution**: 5-minute bars (primary), 15m and 1h for MTF validation

**Recommended Symbol List** (diversified across sectors):
```python
SYMBOLS = [
    # Large Cap Tech
    "AAPL",   # Apple - High liquidity, clean trends
    "MSFT",   # Microsoft - Stable, lower volatility
    "GOOGL",  # Google - Tech giant

    # Large Cap Other
    "JPM",    # JPMorgan - Financial sector
    "JNJ",    # Johnson & Johnson - Healthcare, defensive

    # Medium Cap Growth
    "SQ",     # Block (Square) - Fintech, higher volatility
    "ROKU",   # Roku - Streaming, growth stock

    # Medium Cap Value
    "F",      # Ford - Traditional auto, value
    "AAL",    # American Airlines - Cyclical

    # High Volatility
    "TSLA",   # Tesla - High volatility, meme stock characteristics
]
```

### 2. **Backtest Speed vs Realism** ✅
- **Approach**: Hybrid (Instant fill + realistic factors)
  - **NO time delay simulation** (not critical for strategy validation)
  - **YES slippage model** (price impact matters)
  - **YES partial fills** (liquidity constraints matter)
- **Rationale**: Time delay (100-500ms) is negligible for 5-minute bar strategies
- **Performance**: Run backtest in seconds, not minutes

### 3. **Optimization Scope** ✅
- **MVP**: Small scale (50-100 parameter combinations)
- **Design for extensibility**: Easy to scale to medium (1000+) and large (10,000+)
- **Execution**: Local parallelization (all CPU cores)
- **Future**: Add distributed computing support if needed

### 4. **MVP Scope** ✅

**Phase 1 - MVP (7-10 days):**
- ✅ Event-driven backtest engine
- ✅ Single-symbol backtesting (no portfolio)
- ✅ Stock trading (US market)
- ✅ ORB strategy from `vibe/common/`
- ✅ Hybrid fill simulation (instant + slippage + partial fills)
- ✅ Data quality checks (splits, gaps, outliers)
- ✅ Basic performance metrics
- ✅ In-sample/out-of-sample optimization
- ✅ Market regime classification (ADX-based)
- ✅ Benchmark comparison (buy-and-hold)
- ✅ Simple HTML reports

**Phase 2 - Enhancement (future):**
- 🔲 Walk-forward optimization
- 🔲 Multiple optimization methods (genetic, Bayesian)
- 🔲 Interactive Streamlit dashboard
- 🔲 Forex/Crypto support

**Not Needed (Rationale):**
- ❌ Portfolio backtesting - Not relevant for day trading single setups
- ❌ Options trading - Different strategy type, future scope
- ❌ Time delay simulation - Negligible impact on 5m bar strategies

### 5. **Data Storage** ✅
- **Local Active Set**: 10 symbols × 3 years × 5m bars ≈ 1GB
- **Cloud Archive**: 10 symbols × 10+ years × multiple timeframes ≈ 5GB
- **Format**: Parquet (efficient, compressed, fast loading)

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

### 2. In-Sample / Out-of-Sample Testing

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