# Backtester Implementation Plan

## Project Scope Assessment

**Classification: Medium-Large (50+ tasks)**

This backtester implementation focuses on **event-driven backtesting for single-symbol day trading and swing trading strategies**. The implementation reuses all strategy logic from `vibe/common/` to ensure perfect consistency with live trading.

**Key Design Principles:**
1. **Strategy Reuse**: All strategies, indicators, risk management from `vibe/common/` work identically
2. **Single-Symbol Focus**: Optimize for testing one symbol at a time (no portfolio complexity)
3. **Hybrid Realism**: Instant fills + slippage + partial fills (no time delay)
4. **Extensible Optimization**: Start simple, design for scale
5. **Data Quality First**: Validate data before backtesting

**Timeline: ~7-10 days for MVP**

---

## Phase 0: Project Setup and Data Infrastructure

**Goal:** Set up backtester project structure and implement robust data loading with quality checks.

**Duration:** 2-3 days

---

### Task 0.1: Backtester Project Structure

**Description:** Create `vibe/backtester/` directory structure with all modules.

**Implementation Steps:**
1. Create `vibe/backtester/` with subdirectories per design.md
2. Create `__init__.py` files with proper `__all__` exports
3. Update `pyproject.toml` for backtester package
4. Add backtester dependencies to `requirements.txt`
5. Create `vibe/backtester/config/settings.py` with BacktestConfig

**Verification Criteria:**
- [ ] `python -c "import vibe.backtester"` succeeds
- [ ] All subdirectory imports work
- [ ] Can instantiate BacktestConfig from YAML

**Unit Tests:**
```python
def test_backtester_imports():
    """All backtester modules can be imported."""
    import vibe.backtester
    from vibe.backtester.core import BacktestEngine
    from vibe.backtester.data import HistoricalDataProvider
    from vibe.backtester.execution import FillSimulator

def test_config_loading():
    """BacktestConfig loads from YAML."""
    config = BacktestConfig.from_yaml("config/backtest.yaml")
    assert config.initial_capital == 10000
    assert "AAPL" in config.symbols
```

**Dependencies Added:**
```txt
# Backtesting specific
pandas-market-calendars>=4.0.0
scikit-learn>=1.3.0  # For optimization
plotly>=5.0.0        # For charts
kaleido>=0.2.1       # For static chart export
```

---

### Task 0.2: Historical Data Provider Base

**Description:** Implement HistoricalDataProvider that extends `vibe.common.data.DataProvider`.

**Implementation Steps:**
1. Create `vibe/backtester/data/historical_provider.py`
2. Implement `HistoricalDataProvider` class extending `DataProvider` ABC
3. Add Parquet file loading with caching
4. Add date range filtering
5. Implement `get_bars()` and `get_current_price()` methods for strategy interface

**Verification Criteria:**
- [ ] Extends `vibe.common.data.base.DataProvider`
- [ ] Loads Parquet files efficiently
- [ ] Filters by date range correctly
- [ ] Caches loaded data in memory
- [ ] Returns standardized OHLCV DataFrame

**Unit Tests:**
```python
def test_historical_provider_extends_base():
    """HistoricalDataProvider extends DataProvider ABC."""
    assert issubclass(HistoricalDataProvider, DataProvider)

def test_load_parquet(tmp_path):
    """Loads Parquet file and returns DataFrame."""
    # Create sample Parquet file
    df = create_sample_ohlcv(days=30)
    file_path = tmp_path / "AAPL" / "5m.parquet"
    file_path.parent.mkdir(parents=True)
    df.to_parquet(file_path)

    provider = HistoricalDataProvider(data_dir=tmp_path)
    data = provider.load("AAPL", "5m", start="2024-01-01", end="2024-01-31")

    assert len(data) > 0
    assert all(col in data.columns for col in ["open", "high", "low", "close", "volume"])

def test_date_range_filter():
    """Filters data by date range correctly."""
    provider = HistoricalDataProvider(data_dir=test_data_dir)
    data = provider.load("AAPL", "5m", start="2024-01-15", end="2024-01-20")

    assert data.index[0] >= pd.Timestamp("2024-01-15")
    assert data.index[-1] <= pd.Timestamp("2024-01-20")

def test_caching():
    """Subsequent loads use cache."""
    provider = HistoricalDataProvider(data_dir=test_data_dir)

    # First load
    start_time = time.time()
    data1 = provider.load("AAPL", "5m", start="2024-01-01", end="2024-12-31")
    load_time = time.time() - start_time

    # Second load (should be cached)
    start_time = time.time()
    data2 = provider.load("AAPL", "5m", start="2024-01-01", end="2024-12-31")
    cache_time = time.time() - start_time

    assert cache_time < load_time / 10  # Cache should be 10x faster
    pd.testing.assert_frame_equal(data1, data2)
```

**Functional Tests:**
- Load 3 years of AAPL 5m data (~150k bars), verify memory usage < 500MB

---

### Task 0.3: Data Quality Checker

**Description:** Implement comprehensive data quality validation to detect splits, gaps, outliers, and bad data.

**Implementation Steps:**
1. Create `vibe/backtester/data/quality_checker.py`
2. Implement `DataQualityChecker` class with check methods:
   - `check_gaps()` - Detect missing bars during market hours
   - `check_outliers()` - Detect extreme price movements (>10% intrabar)
   - `check_ohlc_consistency()` - Verify high >= low, high >= open/close
   - `check_volume()` - Detect zero or negative volume
   - `check_splits()` - Detect potential unadjusted splits (>20% overnight gap)
   - `check_negative_prices()` - Detect invalid negative prices
3. Create `DataQualityReport` and `DataIssue` models
4. Add severity levels: INFO, WARNING, CRITICAL

**Verification Criteria:**
- [ ] Detects stock splits (large overnight gaps)
- [ ] Detects data gaps (missing bars)
- [ ] Detects outliers (flash crashes, bad ticks)
- [ ] Detects OHLC inconsistencies
- [ ] Reports issues with severity levels
- [ ] All checks run in < 1 second for 1 year of data

**Unit Tests:**
```python
def test_detect_split():
    """Detects potential unadjusted stock split."""
    # Create data with 4:1 split
    df = create_ohlcv(days=10)
    # Insert split: close $500 -> open $125 next day
    df.loc["2024-01-15 16:00", "close"] = 500
    df.loc["2024-01-16 09:30", "open"] = 125

    checker = DataQualityChecker()
    report = checker.check_all(df, "AAPL")

    split_issues = [i for i in report.issues if "split" in i.message.lower()]
    assert len(split_issues) > 0
    assert split_issues[0].severity == "CRITICAL"

def test_detect_gap():
    """Detects missing data during market hours."""
    df = create_ohlcv(days=5)
    # Remove 2 hours of data
    df = df.drop(df.between_time("10:00", "12:00").index)

    checker = DataQualityChecker()
    report = checker.check_all(df, "AAPL")

    gap_issues = [i for i in report.issues if "gap" in i.message.lower()]
    assert len(gap_issues) > 0

def test_detect_outlier():
    """Detects extreme price movements."""
    df = create_ohlcv(days=5)
    # Insert outlier: close $150, high $500 (flash crash)
    df.loc["2024-01-15 10:30", "high"] = 500
    df.loc["2024-01-15 10:30", "close"] = 150

    checker = DataQualityChecker()
    report = checker.check_all(df, "AAPL")

    outlier_issues = [i for i in report.issues if "outlier" in i.message.lower()]
    assert len(outlier_issues) > 0

def test_ohlc_consistency():
    """Detects OHLC inconsistencies."""
    df = create_ohlcv(days=5)
    # Insert inconsistency: high < low
    df.loc["2024-01-15 10:30", "high"] = 100
    df.loc["2024-01-15 10:30", "low"] = 110

    checker = DataQualityChecker()
    report = checker.check_all(df, "AAPL")

    assert any("high" in i.message.lower() and "low" in i.message.lower()
               for i in report.issues)

def test_clean_data_passes():
    """Clean data passes all checks."""
    df = create_clean_ohlcv(days=30)

    checker = DataQualityChecker()
    report = checker.check_all(df, "AAPL")

    critical_issues = [i for i in report.issues if i.severity == "CRITICAL"]
    assert len(critical_issues) == 0
```

**Functional Tests:**
- Test with real AAPL data including known split (Aug 31, 2020)
- Test with TSLA data including known split (Aug 31, 2020)

---

### Task 0.4: Data Downloader

**Description:** Implement data downloader for Yahoo Finance with automatic split/dividend adjustment.

**Implementation Steps:**
1. Create `vibe/backtester/data/downloader.py`
2. Implement `YahooDataDownloader` class (reuse from trading-bot if possible)
3. Support multiple timeframes (5m, 15m, 1h, 1d)
4. Automatic split and dividend adjustment via yfinance
5. Save to Parquet with compression
6. Add rate limiting (5 req/sec)
7. Add progress tracking for batch downloads

**Verification Criteria:**
- [ ] Downloads data from yfinance successfully
- [ ] Handles rate limiting
- [ ] Saves to Parquet format
- [ ] Data is split-adjusted
- [ ] Progress bar shows download status

**Unit Tests:**
```python
@patch('yfinance.download')
def test_download_historical(mock_download):
    """Downloads historical data via yfinance."""
    mock_download.return_value = create_sample_ohlcv()

    downloader = YahooDataDownloader(data_dir=tmp_path)
    downloader.download("AAPL", start="2020-01-01", end="2023-12-31", interval="5m")

    # Verify file created
    assert (tmp_path / "AAPL" / "5m.parquet").exists()

def test_split_adjustment():
    """Data is automatically split-adjusted."""
    downloader = YahooDataDownloader(data_dir=tmp_path)

    # Download AAPL around split date (Aug 31, 2020)
    df = downloader.download("AAPL", start="2020-08-01", end="2020-09-30", interval="1d")

    # Pre-split close and post-split close should be adjusted
    pre_split = df.loc["2020-08-28", "close"]
    post_split = df.loc["2020-09-01", "close"]

    # Ratio should be close to 1 (not 4) if adjusted
    ratio = pre_split / post_split
    assert 0.8 < ratio < 1.2  # Should be near 1, not 4

def test_batch_download():
    """Can download multiple symbols."""
    downloader = YahooDataDownloader(data_dir=tmp_path)

    symbols = ["AAPL", "MSFT", "GOOGL"]
    downloader.batch_download(
        symbols=symbols,
        start="2023-01-01",
        end="2023-12-31",
        interval="5m"
    )

    for symbol in symbols:
        assert (tmp_path / symbol / "5m.parquet").exists()
```

**Functional Tests:**
- Download 3 years of data for 10 symbols
- Verify file sizes are reasonable (~100MB per symbol for 5m bars)

---

### Task 0.5: Simulated Clock

**Description:** Implement simulated clock that controls time progression during backtest.

**Implementation Steps:**
1. Create `vibe/backtester/core/clock.py`
2. Implement `SimulatedClock` class extending `vibe.common.clock.Clock` ABC
3. Initialize with start_date and bar_interval
4. Implement `now()` returns current simulated time
5. Implement `advance()` moves to next bar
6. Implement `is_market_open()` using pandas_market_calendars
7. Skip non-market hours automatically on advance

**Verification Criteria:**
- [ ] Extends `vibe.common.clock.Clock` ABC
- [ ] Time advances by bar_interval
- [ ] Skips weekends and holidays
- [ ] Skips non-market hours (before 9:30 AM, after 4:00 PM ET)
- [ ] Handles DST transitions correctly

**Unit Tests:**
```python
def test_simulated_clock_extends_base():
    """SimulatedClock extends Clock ABC."""
    assert issubclass(SimulatedClock, Clock)

def test_clock_advance():
    """Clock advances by bar interval."""
    clock = SimulatedClock(
        start_time=datetime(2024, 1, 2, 9, 30),  # Tuesday 9:30 AM
        bar_interval="5m"
    )

    assert clock.now() == datetime(2024, 1, 2, 9, 30)

    clock.advance()
    assert clock.now() == datetime(2024, 1, 2, 9, 35)

    clock.advance()
    assert clock.now() == datetime(2024, 1, 2, 9, 40)

def test_skip_weekend():
    """Clock skips weekends."""
    clock = SimulatedClock(
        start_time=datetime(2024, 1, 5, 15, 55),  # Friday 3:55 PM
        bar_interval="5m"
    )

    # Advance to 4:00 PM Friday (market close)
    clock.advance()
    assert clock.now() == datetime(2024, 1, 5, 16, 0)

    # Next advance should jump to Monday 9:30 AM
    clock.advance()
    assert clock.now() == datetime(2024, 1, 8, 9, 30)  # Monday

def test_skip_holiday():
    """Clock skips market holidays."""
    # New Year's Day 2024 is Monday, Jan 1
    clock = SimulatedClock(
        start_time=datetime(2023, 12, 29, 15, 55),  # Friday before New Year
        bar_interval="5m"
    )

    # Advance to market close Friday
    clock.advance()  # 4:00 PM

    # Next advance should skip Jan 1 (holiday) and go to Jan 2
    clock.advance()
    assert clock.now() == datetime(2024, 1, 2, 9, 30)  # Tuesday

def test_is_market_open():
    """Correctly identifies market hours."""
    clock = SimulatedClock(
        start_time=datetime(2024, 1, 2, 10, 30),
        bar_interval="5m"
    )

    assert clock.is_market_open() == True

    # Set to after hours
    clock.current_time = datetime(2024, 1, 2, 17, 0)
    assert clock.is_market_open() == False

    # Set to weekend
    clock.current_time = datetime(2024, 1, 6, 10, 30)  # Saturday
    assert clock.is_market_open() == False
```

**Functional Tests:**
- Advance through 1 year of data, verify correct number of market days

---

## Phase 1: Fill Simulation and Execution

**Goal:** Implement realistic order execution simulation with slippage, partial fills, and commission models.

**Duration:** 2-3 days

---

### Task 1.1: Slippage Model

**Description:** Implement slippage calculation based on volatility, order size, and market conditions.

**Implementation Steps:**
1. Create `vibe/backtester/execution/slippage.py`
2. Implement `SlippageModel` class with configurable parameters:
   - `base_slippage_pct`: Base slippage (default 0.05%)
   - `volatility_factor`: Increase slippage in volatile conditions
   - `size_impact_factor`: Increase slippage for large orders
3. Calculate ATR for volatility measure
4. Apply direction: buy orders slip up, sell orders slip down

**Verification Criteria:**
- [ ] Base slippage applied correctly
- [ ] Higher volatility increases slippage
- [ ] Larger orders have more slippage
- [ ] Buy orders slip up (worse price)
- [ ] Sell orders slip down (worse price)

**Unit Tests:**
```python
def test_slippage_direction_buy():
    """Buy orders slip up (worse price)."""
    model = SlippageModel(base_slippage_pct=0.001)  # 0.1%

    slipped = model.apply(price=100.00, side="buy")

    assert slipped > 100.00
    assert slipped == pytest.approx(100.10, rel=0.01)

def test_slippage_direction_sell():
    """Sell orders slip down (worse price)."""
    model = SlippageModel(base_slippage_pct=0.001)

    slipped = model.apply(price=100.00, side="sell")

    assert slipped < 100.00
    assert slipped == pytest.approx(99.90, rel=0.01)

def test_slippage_volatility():
    """Higher volatility increases slippage."""
    model = SlippageModel(base_slippage_pct=0.001, volatility_factor=0.5)

    low_vol = model.apply(price=100.00, side="buy", volatility=0.01)
    high_vol = model.apply(price=100.00, side="buy", volatility=0.05)

    assert high_vol > low_vol

def test_slippage_order_size():
    """Larger orders have more slippage."""
    model = SlippageModel(base_slippage_pct=0.001, size_impact_factor=0.0001)

    small_order = model.apply(price=100.00, side="buy", order_size=100, avg_volume=100000)
    large_order = model.apply(price=100.00, side="buy", order_size=10000, avg_volume=100000)

    assert large_order > small_order
```

**Functional Tests:**
- Compare slippage model output to realistic market data

---

### Task 1.2: Commission Model

**Description:** Implement commission calculation for different brokers and asset types.

**Implementation Steps:**
1. Create `vibe/backtester/execution/commission.py`
2. Implement `CommissionModel` ABC
3. Implement `ZeroCommissionModel` (most US brokers)
4. Implement `PerShareCommissionModel` (legacy brokers)
5. Implement `PercentageCommissionModel` (some international brokers)
6. Add factory method `CommissionModel.create(broker_type)`

**Verification Criteria:**
- [ ] ZeroCommissionModel returns 0
- [ ] PerShareCommissionModel calculates correctly
- [ ] PercentageCommissionModel calculates correctly
- [ ] Factory creates correct model type

**Unit Tests:**
```python
def test_zero_commission():
    """Zero commission model returns 0."""
    model = ZeroCommissionModel()
    commission = model.calculate(quantity=100, price=150.00)
    assert commission == 0.0

def test_per_share_commission():
    """Per-share commission calculated correctly."""
    model = PerShareCommissionModel(per_share=0.01, min_commission=1.0)

    # 100 shares @ $0.01 = $1.00
    assert model.calculate(quantity=100, price=150.00) == 1.00

    # 10 shares @ $0.01 = $0.10, but min is $1.00
    assert model.calculate(quantity=10, price=150.00) == 1.00

def test_percentage_commission():
    """Percentage commission calculated correctly."""
    model = PercentageCommissionModel(rate=0.001)  # 0.1%

    # 100 shares @ $150 = $15,000 * 0.1% = $15
    assert model.calculate(quantity=100, price=150.00) == 15.00
```

---

### Task 1.3: Liquidity Model

**Description:** Simulate partial fills based on order size relative to bar volume.

**Implementation Steps:**
1. Create `vibe/backtester/execution/liquidity.py`
2. Implement `LiquidityModel` class
3. Calculate fillable quantity based on:
   - Order size vs bar volume ratio
   - Max fill percentage (e.g., can't fill more than 10% of bar volume)
   - Volatility factor (harder to fill in volatile conditions)
4. Return filled quantity and remaining quantity

**Verification Criteria:**
- [ ] Large orders relative to volume are partially filled
- [ ] Small orders relative to volume are fully filled
- [ ] High volatility reduces fill rate
- [ ] Zero volume bars result in no fill

**Unit Tests:**
```python
def test_full_fill_small_order():
    """Small orders relative to volume are fully filled."""
    model = LiquidityModel(max_fill_pct=0.10)  # Max 10% of bar volume

    filled = model.get_filled_quantity(
        order_quantity=100,
        bar_volume=100000  # 100 shares vs 100k volume = 0.1%
    )

    assert filled == 100  # Fully filled

def test_partial_fill_large_order():
    """Large orders are partially filled."""
    model = LiquidityModel(max_fill_pct=0.10)

    filled = model.get_filled_quantity(
        order_quantity=50000,   # 50k shares
        bar_volume=100000       # 100k volume = 50% of volume
    )

    # Should fill max 10% of bar volume = 10,000 shares
    assert filled == 10000

def test_no_fill_zero_volume():
    """Zero volume bars result in no fill."""
    model = LiquidityModel()

    filled = model.get_filled_quantity(
        order_quantity=100,
        bar_volume=0
    )

    assert filled == 0
```

---

### Task 1.4: Fill Simulator

**Description:** Coordinate slippage, commission, and liquidity models to simulate realistic order fills.

**Implementation Steps:**
1. Create `vibe/backtester/execution/fill_simulator.py`
2. Implement `FillSimulator` class extending `ExecutionEngine` ABC
3. Integrate slippage, commission, and liquidity models
4. Implement instant fill (no time delay) with realistic factors
5. Support market orders (fill at current bar)
6. Support limit orders (check if limit price reached)
7. Support stop-loss orders (trigger and fill)
8. Track order state transitions

**Verification Criteria:**
- [ ] Extends `vibe.common.execution.ExecutionEngine`
- [ ] Market orders fill at bar close + slippage
- [ ] Limit orders only fill when price reaches limit
- [ ] Stop-loss orders trigger at stop price
- [ ] Commission deducted from account
- [ ] Partial fills tracked correctly

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_fill_simulator_extends_base():
    """FillSimulator extends ExecutionEngine."""
    assert issubclass(FillSimulator, ExecutionEngine)

@pytest.mark.asyncio
async def test_market_order_instant_fill():
    """Market order fills instantly at bar close."""
    simulator = FillSimulator(
        slippage_model=SlippageModel(base_slippage_pct=0.0005),
        commission_model=ZeroCommissionModel(),
        liquidity_model=LiquidityModel()
    )

    bar = Bar(open=150.00, high=151.00, low=149.00, close=150.50, volume=100000)
    order = Order(symbol="AAPL", side="buy", quantity=100, order_type="market")

    response = await simulator.submit_order(order, bar)

    # Should fill at close + slippage
    assert response.status == OrderStatus.FILLED
    assert response.filled_qty == 100
    assert response.avg_price > 150.50  # Buy slips up

@pytest.mark.asyncio
async def test_limit_order_not_reached():
    """Limit order doesn't fill if price not reached."""
    simulator = FillSimulator(...)

    bar = Bar(open=150.00, high=151.00, low=149.00, close=150.50, volume=100000)
    order = Order(symbol="AAPL", side="buy", quantity=100, order_type="limit", limit_price=148.00)

    response = await simulator.submit_order(order, bar)

    assert response.status == OrderStatus.PENDING  # Not filled
    assert response.filled_qty == 0

@pytest.mark.asyncio
async def test_limit_order_filled():
    """Limit order fills when price reaches limit."""
    simulator = FillSimulator(...)

    bar = Bar(open=150.00, high=151.00, low=148.50, close=150.50, volume=100000)
    order = Order(symbol="AAPL", side="buy", quantity=100, order_type="limit", limit_price=149.00)

    response = await simulator.submit_order(order, bar)

    # Low = 148.50, limit = 149.00 -> filled at limit (no slippage for limit orders)
    assert response.status == OrderStatus.FILLED
    assert response.avg_price == 149.00

@pytest.mark.asyncio
async def test_partial_fill():
    """Large order results in partial fill."""
    simulator = FillSimulator(
        liquidity_model=LiquidityModel(max_fill_pct=0.10)
    )

    bar = Bar(open=150.00, high=151.00, low=149.00, close=150.50, volume=1000)
    order = Order(symbol="AAPL", side="buy", quantity=500, order_type="market")  # 50% of volume

    response = await simulator.submit_order(order, bar)

    assert response.status == OrderStatus.PARTIAL
    assert response.filled_qty == 100  # 10% of 1000 volume
    assert response.remaining_qty == 400
```

**Functional Tests:**
- Simulate 1000 orders with various market conditions, verify all behave correctly

---

## Phase 2: Backtest Engine and Portfolio Management

**Goal:** Implement the core backtest engine and portfolio tracking.

**Duration:** 2-3 days

---

### Task 2.1: Portfolio Manager

**Description:** Track positions, cash, equity, and P&L during backtest.

**Implementation Steps:**
1. Create `vibe/backtester/core/portfolio.py`
2. Implement `PortfolioManager` class
3. Track cash, positions, equity curve
4. Calculate realized and unrealized P&L
5. Handle position opens and closes
6. Support account state queries (implements ExecutionEngine.get_account())

**Verification Criteria:**
- [ ] Tracks cash correctly after fills
- [ ] Calculates P&L correctly on position close
- [ ] Equity curve updates with current prices
- [ ] Can retrieve current positions
- [ ] Commission deducted from cash

**Unit Tests:**
```python
def test_portfolio_initial_state():
    """Portfolio initializes with correct cash."""
    portfolio = PortfolioManager(initial_capital=10000)

    assert portfolio.cash == 10000
    assert len(portfolio.positions) == 0
    assert portfolio.equity == 10000

def test_open_position():
    """Opening position updates cash and positions."""
    portfolio = PortfolioManager(initial_capital=10000)

    fill = OrderResponse(
        symbol="AAPL",
        side="buy",
        filled_qty=10,
        avg_price=150.00,
        commission=0.0
    )

    portfolio.update(fill, timestamp=datetime(2024, 1, 2, 10, 0))

    assert portfolio.cash == 10000 - (10 * 150.00)  # $8500
    assert "AAPL" in portfolio.positions
    assert portfolio.positions["AAPL"].quantity == 10
    assert portfolio.positions["AAPL"].entry_price == 150.00

def test_close_position():
    """Closing position calculates P&L correctly."""
    portfolio = PortfolioManager(initial_capital=10000)

    # Open
    portfolio.update(OrderResponse(
        symbol="AAPL", side="buy", filled_qty=10, avg_price=150.00, commission=0.0
    ), timestamp=datetime(2024, 1, 2, 10, 0))

    # Close
    portfolio.update(OrderResponse(
        symbol="AAPL", side="sell", filled_qty=10, avg_price=155.00, commission=0.0
    ), timestamp=datetime(2024, 1, 2, 15, 0))

    # P&L = (155 - 150) * 10 = $50
    assert portfolio.cash == 10000 + 50  # $10,050
    assert "AAPL" not in portfolio.positions
    assert len(portfolio.trade_history) == 1
    assert portfolio.trade_history[0].pnl == 50.00

def test_equity_curve():
    """Equity curve tracks portfolio value over time."""
    portfolio = PortfolioManager(initial_capital=10000)

    # Open position
    portfolio.update(OrderResponse(
        symbol="AAPL", side="buy", filled_qty=10, avg_price=150.00, commission=0.0
    ), timestamp=datetime(2024, 1, 2, 10, 0))

    # Update equity with current prices
    portfolio.update_equity({"AAPL": 155.00}, timestamp=datetime(2024, 1, 2, 11, 0))

    # Equity = cash + unrealized P&L
    # Cash = $8500, unrealized = (155 - 150) * 10 = $50
    assert portfolio.equity == 10050.00
    assert len(portfolio.equity_curve) > 0
```

**Functional Tests:**
- Simulate 100 trades, verify final equity matches expected

---

### Task 2.2: Backtest Engine

**Description:** Core orchestrator that runs the event loop and coordinates all components.

**Implementation Steps:**
1. Create `vibe/backtester/core/engine.py`
2. Implement `BacktestEngine` class
3. Initialize all components (data provider, clock, fill simulator, strategy, risk manager, portfolio)
4. Implement event loop:
   - Load bar → Update indicators → Generate signal → Risk check → Execute → Update portfolio
5. Track progress with tqdm progress bar
6. Return `BacktestResult` with all metrics and trade history

**Verification Criteria:**
- [ ] Initializes all components correctly
- [ ] Event loop processes all bars
- [ ] Strategy generates signals
- [ ] Risk checks are applied
- [ ] Orders are executed via fill simulator
- [ ] Portfolio updates correctly
- [ ] Returns comprehensive result object

**Unit Tests:**
```python
def test_engine_initialization():
    """Engine initializes all components."""
    config = BacktestConfig(
        symbols=["AAPL"],
        start_date="2024-01-01",
        end_date="2024-01-31",
        initial_capital=10000,
        strategy_name="orb"
    )

    engine = BacktestEngine(config)

    assert engine.data_provider is not None
    assert engine.clock is not None
    assert engine.fill_simulator is not None
    assert engine.portfolio is not None

def test_engine_runs_complete():
    """Engine runs to completion."""
    config = BacktestConfig(
        symbols=["AAPL"],
        start_date="2024-01-01",
        end_date="2024-01-05",  # Just 5 days
        initial_capital=10000,
        strategy_name="orb"
    )

    engine = BacktestEngine(config)
    result = engine.run()

    assert result is not None
    assert result.start_date == config.start_date
    assert result.end_date == config.end_date

@patch('vibe.common.strategies.ORBStrategy')
def test_engine_calls_strategy(mock_strategy):
    """Engine calls strategy for each bar."""
    mock_strategy.return_value.generate_signal.return_value = None

    config = BacktestConfig(...)
    engine = BacktestEngine(config)
    result = engine.run()

    # Should have called strategy for each bar
    assert mock_strategy.return_value.generate_signal.call_count > 0
```

**Functional Tests:**
- Run full 3-year backtest on AAPL with ORB strategy
- Verify results are consistent across multiple runs (deterministic)

---

### Task 2.3: Backtest Result Model

**Description:** Comprehensive result object containing all backtest data and metrics.

**Implementation Steps:**
1. Create `vibe/backtester/core/result.py`
2. Implement `BacktestResult` dataclass with all fields:
   - Configuration (symbols, dates, params)
   - Trade history
   - Equity curve
   - Performance metrics
   - Execution statistics
3. Add serialization to JSON for saving results

**Verification Criteria:**
- [ ] Contains all backtest metadata
- [ ] Trade history accessible
- [ ] Equity curve accessible
- [ ] Can serialize to JSON

**Unit Tests:**
```python
def test_result_creation():
    """BacktestResult can be created with all fields."""
    result = BacktestResult(
        strategy_name="orb",
        symbol="AAPL",
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=10000,
        final_equity=11500,
        total_return=0.15,
        trade_history=[...],
        equity_curve=pd.Series([...]),
        metrics=PerformanceMetrics(...)
    )

    assert result.total_return == 0.15
    assert len(result.trade_history) > 0

def test_result_serialization():
    """BacktestResult can be serialized to JSON."""
    result = BacktestResult(...)

    json_str = result.to_json()
    loaded = BacktestResult.from_json(json_str)

    assert loaded.strategy_name == result.strategy_name
    assert loaded.total_return == result.total_return
```

---

## Phase 3: Performance Analysis and Metrics

**Goal:** Calculate comprehensive performance metrics and regime-based analysis.

**Duration:** 1-2 days

---

### Task 3.1: Performance Analyzer

**Description:** Calculate all performance metrics from backtest results.

**Implementation Steps:**
1. Create `vibe/backtester/analysis/performance.py`
2. Implement `PerformanceAnalyzer` class
3. Calculate metrics:
   - Returns: total return, annualized return, CAGR
   - Risk: volatility, max drawdown, max drawdown duration
   - Risk-adjusted: Sharpe ratio, Sortino ratio, Calmar ratio
   - Trade stats: win rate, avg win, avg loss, profit factor, expectancy
   - Duration: avg trade duration, longest winning/losing streak
4. Return `PerformanceMetrics` dataclass

**Verification Criteria:**
- [ ] All metrics calculated correctly
- [ ] Handles edge cases (no trades, all wins, all losses)
- [ ] Annualization factor correct (252 trading days)
- [ ] Sharpe ratio uses correct risk-free rate

**Unit Tests:**
```python
def test_total_return():
    """Total return calculated correctly."""
    equity = pd.Series([10000, 10500, 11000, 10800, 11500])

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(
        equity_curve=equity,
        trade_history=[],
        initial_capital=10000
    )

    assert metrics.total_return == 0.15  # (11500 - 10000) / 10000

def test_sharpe_ratio():
    """Sharpe ratio calculated correctly."""
    # Create equity curve with known returns
    equity = pd.Series([...])  # Returns = 1% per day avg, std = 2%

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(equity_curve=equity, ...)

    # Sharpe = (mean_return - rf_rate) / std * sqrt(252)
    # Assuming rf_rate = 0
    expected_sharpe = 0.01 / 0.02 * np.sqrt(252)
    assert abs(metrics.sharpe_ratio - expected_sharpe) < 0.1

def test_max_drawdown():
    """Max drawdown calculated correctly."""
    equity = pd.Series([10000, 11000, 10500, 9500, 10000, 11500])
    #                           ^     ^     ^
    #                         peak  trough  recovery
    # Max DD = (11000 - 9500) / 11000 = 13.6%

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(equity_curve=equity, ...)

    assert abs(metrics.max_drawdown - 0.136) < 0.001

def test_win_rate():
    """Win rate calculated correctly."""
    trades = [
        Trade(pnl=100),   # Win
        Trade(pnl=-50),   # Loss
        Trade(pnl=75),    # Win
        Trade(pnl=125),   # Win
    ]

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(trade_history=trades, ...)

    assert metrics.win_rate == 0.75  # 3/4
    assert metrics.avg_win == 100.0  # (100 + 75 + 125) / 3
    assert metrics.avg_loss == -50.0

def test_no_trades():
    """Handles backtest with no trades."""
    analyzer = PerformanceAnalyzer()
    metrics = analyzer.analyze(
        equity_curve=pd.Series([10000] * 100),
        trade_history=[],
        initial_capital=10000
    )

    assert metrics.total_return == 0.0
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
```

**Functional Tests:**
- Calculate metrics for real AAPL ORB backtest, manually verify key metrics

---

### Task 3.2: Market Regime Classifier

**Description:** Classify market conditions into regimes (trending, ranging, bull, bear).

**Implementation Steps:**
1. Create `vibe/backtester/analysis/regime.py`
2. Implement `MarketRegimeClassifier` class
3. Implement ADX-based classification (MVP):
   - ADX > 25: TRENDING
   - ADX < 20: RANGING
   - ADX 20-25: TRANSITIONING
4. Add price-vs-SMA classification:
   - Price > SMA200 + 5%: BULL
   - Price < SMA200 - 5%: BEAR
   - Otherwise: NEUTRAL
5. Return regime labels for each bar

**Verification Criteria:**
- [ ] Classifies trending markets correctly (high ADX)
- [ ] Classifies ranging markets correctly (low ADX)
- [ ] Classifies bull markets correctly (price > SMA)
- [ ] Classifies bear markets correctly (price < SMA)

**Unit Tests:**
```python
def test_adx_trending():
    """High ADX classified as TRENDING."""
    df = create_trending_data()  # Strong uptrend, ADX = 30

    classifier = MarketRegimeClassifier(method="adx")
    regimes = classifier.classify(df)

    assert regimes.iloc[-1] == "TRENDING"

def test_adx_ranging():
    """Low ADX classified as RANGING."""
    df = create_ranging_data()  # Sideways, ADX = 15

    classifier = MarketRegimeClassifier(method="adx")
    regimes = classifier.classify(df)

    assert regimes.iloc[-1] == "RANGING"

def test_price_sma_bull():
    """Price above SMA classified as BULL."""
    df = create_bull_market_data()  # Price 10% above SMA200

    classifier = MarketRegimeClassifier(method="price_sma")
    regimes = classifier.classify(df)

    assert regimes.iloc[-1] == "BULL"
```

---

### Task 3.3: Performance by Regime Analyzer

**Description:** Break down strategy performance by market regime.

**Implementation Steps:**
1. Create `vibe/backtester/analysis/regime_performance.py`
2. Implement `PerformanceByRegime` class
3. For each regime, calculate:
   - Number of trades
   - Win rate
   - Avg profit per trade
   - Total return
   - Sharpe ratio
4. Identify which regimes strategy performs best/worst in

**Verification Criteria:**
- [ ] Separates trades by regime correctly
- [ ] Calculates metrics per regime
- [ ] Identifies best/worst regimes

**Unit Tests:**
```python
def test_performance_by_regime():
    """Calculates performance separately for each regime."""
    trades = [
        Trade(pnl=100, entry_time="2024-01-02 10:00"),  # Trending
        Trade(pnl=-50, entry_time="2024-01-03 10:00"),  # Ranging
        Trade(pnl=75, entry_time="2024-01-04 10:00"),   # Trending
    ]

    regime_data = pd.Series({
        "2024-01-02 10:00": "TRENDING",
        "2024-01-03 10:00": "RANGING",
        "2024-01-04 10:00": "TRENDING",
    })

    analyzer = PerformanceByRegime()
    results = analyzer.analyze(trades, regime_data)

    assert results["TRENDING"].total_trades == 2
    assert results["TRENDING"].total_pnl == 175
    assert results["RANGING"].total_trades == 1
    assert results["RANGING"].total_pnl == -50
```

---

### Task 3.4: Benchmark Comparison

**Description:** Compare strategy performance against buy-and-hold benchmark.

**Implementation Steps:**
1. Create `vibe/backtester/analysis/benchmark.py`
2. Implement `BenchmarkComparison` class
3. Load benchmark data (SPY) for same date range
4. Calculate buy-and-hold return
5. Calculate alpha (excess return)
6. Calculate beta (correlation with benchmark)
7. Generate comparison chart

**Verification Criteria:**
- [ ] Loads benchmark data correctly
- [ ] Calculates buy-and-hold return
- [ ] Calculates alpha correctly
- [ ] Calculates beta correctly
- [ ] Identifies if strategy outperformed

**Unit Tests:**
```python
def test_benchmark_comparison():
    """Compares strategy vs buy-and-hold."""
    strategy_result = BacktestResult(
        total_return=0.25,  # 25%
        equity_curve=pd.Series([...])
    )

    benchmark_data = pd.Series([100, 105, 110, 115, 120])  # 20% return

    comparison = BenchmarkComparison()
    result = comparison.compare(strategy_result, benchmark_data)

    assert result.strategy_return == 0.25
    assert result.benchmark_return == 0.20
    assert result.alpha == 0.05  # 5% excess return
    assert result.outperformed == True
```

---

## Phase 4: Optimization and Reporting

**Goal:** Implement parameter optimization with in-sample/out-of-sample validation and HTML report generation.

**Duration:** 2-3 days

---

### Task 4.1: In-Sample / Out-of-Sample Splitter

**Description:** Split data chronologically into training and testing sets.

**Implementation Steps:**
1. Create `vibe/backtester/optimization/splitter.py`
2. Implement `InSampleOutSampleSplitter` class
3. Split data chronologically (e.g., 70% train, 30% test)
4. Support custom split ratios
5. Support multiple splits for walk-forward (future)

**Verification Criteria:**
- [ ] Splits data chronologically (no future data in training)
- [ ] Split ratio honored
- [ ] No overlap between sets

**Unit Tests:**
```python
def test_split_70_30():
    """Splits data 70/30."""
    df = create_ohlcv(days=100)

    splitter = InSampleOutSampleSplitter()
    train, test = splitter.split(df, train_ratio=0.7)

    assert len(train) == 70
    assert len(test) == 30
    assert train.index[-1] < test.index[0]  # No overlap

def test_split_chronological():
    """Train data comes before test data."""
    df = create_ohlcv(days=100)

    splitter = InSampleOutSampleSplitter()
    train, test = splitter.split(df, train_ratio=0.7)

    # Train should be first 70 days
    assert train.index[0] == df.index[0]
    assert train.index[-1] == df.index[69]

    # Test should be last 30 days
    assert test.index[0] == df.index[70]
    assert test.index[-1] == df.index[99]
```

---

### Task 4.2: Grid Search Optimizer

**Description:** Run backtests for all parameter combinations in a grid.

**Implementation Steps:**
1. Create `vibe/backtester/optimization/grid_search.py`
2. Implement `GridSearchOptimizer` class
3. Generate all parameter combinations from grid
4. Run backtests in parallel (multiprocessing)
5. Rank results by specified metric
6. Detect overfitting (train vs test performance)
7. Return best parameters and full results

**Verification Criteria:**
- [ ] Generates all parameter combinations
- [ ] Runs backtests in parallel
- [ ] Ranks by specified metric
- [ ] Detects overfitting
- [ ] Returns comprehensive results

**Unit Tests:**
```python
def test_grid_generation():
    """Generates all parameter combinations."""
    param_grid = {
        "param1": [1, 2, 3],
        "param2": ["a", "b"]
    }

    optimizer = GridSearchOptimizer()
    combinations = optimizer._generate_combinations(param_grid)

    assert len(combinations) == 6  # 3 * 2
    assert {"param1": 1, "param2": "a"} in combinations
    assert {"param1": 3, "param2": "b"} in combinations

@patch('vibe.backtester.core.BacktestEngine')
def test_optimize_runs_all_combinations(mock_engine):
    """Optimizer runs backtest for each combination."""
    param_grid = {
        "opening_range_minutes": [5, 10],
        "take_profit_atr": [2.0, 3.0]
    }  # 4 combinations

    optimizer = GridSearchOptimizer(mock_engine)
    result = optimizer.optimize(param_grid, metric="sharpe_ratio")

    # Should have run 4 backtests (IS + OOS)
    assert mock_engine.run.call_count >= 4

def test_overfitting_detection():
    """Detects overfitting when train >> test performance."""
    # Mock results where train Sharpe = 3.0, test Sharpe = 0.5
    optimizer = GridSearchOptimizer()

    result = OptimizationResult(
        in_sample_sharpe=3.0,
        out_of_sample_sharpe=0.5
    )

    assert optimizer._check_overfitting(result) == True
    assert result.overfitting_ratio == 6.0  # 3.0 / 0.5
```

**Functional Tests:**
- Optimize ORB strategy with 4×3×2 = 24 parameter combinations
- Verify best parameters make sense

---

### Task 4.3: Report Generator

**Description:** Generate comprehensive HTML reports with charts.

**Implementation Steps:**
1. Create `vibe/backtester/reporting/generator.py`
2. Implement `ReportGenerator` class
3. Create HTML template with sections:
   - Summary metrics table
   - Equity curve chart (Plotly)
   - Drawdown chart
   - Trade distribution histogram
   - Monthly returns heatmap
   - Performance by regime table
   - Trade list table
4. Support PDF export (optional)

**Verification Criteria:**
- [ ] Generates valid HTML
- [ ] Charts render correctly
- [ ] All metrics displayed
- [ ] Trade list included
- [ ] File saved successfully

**Unit Tests:**
```python
def test_generate_html(tmp_path):
    """Generates HTML report."""
    result = BacktestResult(...)

    generator = ReportGenerator()
    report_path = generator.generate_html(result, output_path=tmp_path / "report.html")

    assert report_path.exists()
    html = report_path.read_text()
    assert "Backtest Report" in html
    assert str(result.total_return) in html

def test_charts_included():
    """Charts are included in report."""
    result = BacktestResult(...)

    generator = ReportGenerator()
    report_path = generator.generate_html(result, ...)

    html = report_path.read_text()
    assert "plotly" in html.lower()  # Plotly charts
    assert "equity-curve" in html.lower()
```

**Functional Tests:**
- Generate report for real backtest, open in browser, verify looks good

---

## Phase 5: Integration and Testing

**Goal:** End-to-end integration testing and CLI interface.

**Duration:** 1-2 days

---

### Task 5.1: CLI Interface

**Description:** Create command-line interface for running backtests.

**Implementation Steps:**
1. Create `vibe/backtester/main.py`
2. Implement CLI with argparse:
   - `run`: Run single backtest
   - `optimize`: Run parameter optimization
   - `download`: Download historical data
   - `report`: Generate report from saved result
3. Support config file loading
4. Add progress bars for long operations

**Verification Criteria:**
- [ ] CLI commands work
- [ ] Config file loading works
- [ ] Progress displayed
- [ ] Results saved to file

**Unit Tests:**
```python
def test_cli_run_command(tmp_path):
    """CLI run command executes backtest."""
    result = subprocess.run([
        "python", "-m", "vibe.backtester.main",
        "run",
        "--config", "config/backtest.yaml",
        "--output", str(tmp_path / "result.json")
    ], capture_output=True)

    assert result.returncode == 0
    assert (tmp_path / "result.json").exists()
```

---

### Task 5.2: End-to-End Integration Test

**Description:** Test complete backtest workflow from data loading to report generation.

**Implementation Steps:**
1. Create `vibe/tests/backtester/integration/test_full_backtest.py`
2. Test complete workflow:
   - Load historical data
   - Run backtest with ORB strategy
   - Calculate metrics
   - Generate report
3. Verify results are reasonable

**Verification Criteria:**
- [ ] Complete workflow runs without errors
- [ ] Results are deterministic (same input = same output)
- [ ] Trade count is reasonable
- [ ] Metrics are within expected ranges

**Integration Test:**
```python
def test_full_backtest_workflow():
    """Complete backtest workflow end-to-end."""
    # 1. Setup
    config = BacktestConfig(
        symbols=["AAPL"],
        start_date="2023-01-01",
        end_date="2023-12-31",
        initial_capital=10000,
        strategy_name="orb",
        strategy_params={
            "opening_range_minutes": 5,
            "take_profit_atr_multiple": 2.0,
            "stop_loss_atr_multiple": 1.0
        }
    )

    # 2. Run backtest
    engine = BacktestEngine(config)
    result = engine.run()

    # 3. Verify results
    assert result is not None
    assert result.total_trades > 0
    assert -0.5 < result.total_return < 2.0  # Reasonable return range
    assert result.sharpe_ratio > -2.0  # Not completely terrible

    # 4. Generate report
    generator = ReportGenerator()
    report_path = generator.generate_html(result, "reports/test_backtest.html")
    assert report_path.exists()

    # 5. Verify deterministic
    result2 = engine.run()
    assert result.total_return == result2.total_return
    assert len(result.trade_history) == len(result2.trade_history)
```

---

### Task 5.3: Documentation and Examples

**Description:** Create comprehensive documentation and example scripts.

**Implementation Steps:**
1. Create `vibe/backtester/README.md` with usage guide
2. Create example scripts in `examples/`:
   - `examples/simple_backtest.py`
   - `examples/parameter_optimization.py`
   - `examples/regime_analysis.py`
   - `examples/benchmark_comparison.py`
3. Document all public APIs with docstrings

**Verification Criteria:**
- [ ] README explains how to use backtester
- [ ] Example scripts run successfully
- [ ] All public classes/methods have docstrings

---

## Summary and Timeline

**Total Duration: 7-10 days**

| Phase | Tasks | Duration | Dependencies |
|-------|-------|----------|--------------|
| **Phase 0: Data Infrastructure** | 5 tasks | 2-3 days | None |
| **Phase 1: Fill Simulation** | 4 tasks | 2-3 days | Phase 0 |
| **Phase 2: Backtest Engine** | 3 tasks | 2-3 days | Phase 0, Phase 1 |
| **Phase 3: Performance Analysis** | 4 tasks | 1-2 days | Phase 2 |
| **Phase 4: Optimization & Reporting** | 3 tasks | 2-3 days | Phase 3 |
| **Phase 5: Integration** | 3 tasks | 1-2 days | All phases |

**Total Tasks: 22 main tasks + integration**

---

## Success Criteria

**MVP is complete when:**
- ✅ Can backtest ORB strategy from `vibe/common/` on AAPL
- ✅ Data quality checks pass
- ✅ Realistic fills with slippage and partial fills (no time delay)
- ✅ Performance metrics calculated accurately
- ✅ Market regime analysis working
- ✅ Benchmark comparison (vs buy-and-hold)
- ✅ In-sample/out-of-sample optimization
- ✅ HTML report with charts generated
- ✅ Results are deterministic (reproducible)
- ✅ Full backtest (3 years) runs in < 5 minutes

---

## Future Enhancements (Post-MVP)

**Phase 2 Additions:**
1. Walk-forward optimization (rolling train/test periods)
2. Genetic algorithm optimization
3. Bayesian optimization
4. Monte Carlo simulation for robustness testing
5. Interactive Streamlit dashboard
6. Forex and crypto asset support
7. Multi-symbol portfolio backtesting
8. Transaction cost analysis (TCA)
9. Options strategy support
10. Live vs backtest comparison tool

**Priority for Phase 2:**
1. Walk-forward optimization (most impactful for avoiding overfitting)
2. Genetic algorithm (better than grid search for large parameter spaces)
3. Interactive dashboard (easier result exploration)
