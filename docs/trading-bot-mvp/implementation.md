# Trading Bot Implementation Plan

## Project Scope Assessment

**Classification: Large (70+ tasks)**

This is a comprehensive trading bot with multiple subsystems requiring careful orchestration. The implementation is divided into 6 phases to ensure each component is independently verifiable before integration.

**Key Updates:**
- Restructured to `vibe/` directory with `vibe/common/` for shared components
- Added Phase 0 for shared components infrastructure
- Added runtime model with graceful shutdown and health checks
- Added dashboard infrastructure (FastAPI + Streamlit)
- Multi-timeframe validation system (Phase 3)
- Enhanced mock exchange with slippage, partial fills, and retry logic (Phase 4)
- Enhanced Discord notifications with payload schema and rate limiting (Phase 5)
- Initial capital: $10,000
- Database: SQLite (prioritizing FREE + SIMPLE)

---

## Phase 0: Shared Components Foundation (vibe/common/)

**Goal:** Establish shared components that will be identical between live trading and backtesting.

**Duration:** 3-4 days

### Task 0.1: Shared Project Structure Setup

**Description:** Create the `vibe/common/` directory structure with all shared modules.

**Implementation Steps:**
1. Create `vibe/` root directory with `__init__.py`
2. Create `vibe/common/` with subdirectories: strategies, indicators, risk, validation, models, execution, data, clock, utils
3. Create `__init__.py` files with proper `__all__` exports
4. Setup relative imports to work from both `vibe/trading-bot` and `vibe/backtester`

**Verification Criteria:**
- [ ] `python -c "import vibe.common"` succeeds
- [ ] All subdirectory imports work without circular dependencies
- [ ] Can import from `vibe.common.models` in both `vibe.trading_bot` and `vibe.backtester`

**Unit Tests:**
- `test_common_imports.py`: Verify all common module imports work
- `test_no_circular_deps.py`: Verify no circular dependencies

**Functional Tests:**
- Import test from multiple entry points

---

### Task 0.2: Shared Data Models

**Description:** Implement core data models (Trade, Order, Position, Bar, Signal) in `vibe/common/models/`.

**Implementation Steps:**
1. Create `vibe/common/models/bar.py` with OHLCV Bar model (Pydantic)
2. Create `vibe/common/models/order.py` with Order model and OrderStatus enum
3. Create `vibe/common/models/position.py` with Position model
4. Create `vibe/common/models/trade.py` with Trade model including P&L fields
5. Create `vibe/common/models/signal.py` with Signal model
6. Create `vibe/common/models/account.py` with AccountState model

**Verification Criteria:**
- [ ] All models validate correctly with Pydantic
- [ ] Models serialize to/from JSON
- [ ] OrderStatus enum has: CREATED, PENDING, SUBMITTED, PARTIAL, FILLED, CANCELLED, REJECTED
- [ ] Trade model includes: entry_price, exit_price, pnl, pnl_pct fields

**Unit Tests:**
```python
def test_bar_model():
    """Bar validates OHLCV data."""
    bar = Bar(timestamp=datetime.now(), open=100, high=105, low=99, close=102, volume=1000)
    assert bar.high >= bar.low

def test_order_status_transitions():
    """OrderStatus enum has all required states."""
    assert OrderStatus.PENDING.value < OrderStatus.FILLED.value

def test_trade_pnl_calculation():
    """Trade calculates P&L correctly."""
    trade = Trade(entry_price=100, exit_price=110, quantity=10, side="buy")
    assert trade.pnl == 100  # (110-100) * 10
```

**Functional Tests:**
- Create trade, serialize to JSON, deserialize, verify equality

---

### Task 0.3: Abstract Execution Interface

**Description:** Define the ExecutionEngine abstract base class that both live and backtest will implement.

**Implementation Steps:**
1. Create `vibe/common/execution/base.py` with `ExecutionEngine` ABC
2. Define abstract methods: `submit_order()`, `cancel_order()`, `get_position()`, `get_account()`
3. Define `OrderResponse` dataclass for standardized responses
4. Add type hints for all methods

**Verification Criteria:**
- [ ] ExecutionEngine cannot be instantiated directly
- [ ] All abstract methods have clear signatures
- [ ] OrderResponse contains: order_id, status, filled_qty, avg_price, remaining_qty

**Unit Tests:**
```python
def test_execution_engine_abstract():
    """ExecutionEngine cannot be instantiated."""
    with pytest.raises(TypeError):
        ExecutionEngine()

def test_order_response_fields():
    """OrderResponse contains all required fields."""
    response = OrderResponse(
        order_id="123",
        status=OrderStatus.FILLED,
        filled_qty=100,
        avg_price=150.0,
        remaining_qty=0
    )
    assert response.order_id == "123"
```

---

### Task 0.4: Abstract Data Provider Interface

**Description:** Define the DataProvider abstract base class for data access.

**Implementation Steps:**
1. Create `vibe/common/data/base.py` with `DataProvider` ABC
2. Define abstract methods: `get_bars()`, `get_current_price()`
3. Define standard column names for OHLCV DataFrames

**Verification Criteria:**
- [ ] DataProvider cannot be instantiated directly
- [ ] Column constants defined: OPEN, HIGH, LOW, CLOSE, VOLUME
- [ ] `get_bars()` returns DataFrame with standard columns

**Unit Tests:**
```python
def test_data_provider_abstract():
    """DataProvider cannot be instantiated."""
    with pytest.raises(TypeError):
        DataProvider()
```

---

### Task 0.5: Abstract Clock Interface

**Description:** Define the Clock abstract base class for time management (enables backtest to control time).

**Implementation Steps:**
1. Create `vibe/common/clock/base.py` with `Clock` ABC
2. Define abstract methods: `now()`, `is_market_open()`
3. Create `vibe/common/clock/live_clock.py` with `LiveClock` implementation
4. Create `vibe/common/clock/market_hours.py` with market hours logic

**Verification Criteria:**
- [ ] Clock cannot be instantiated directly
- [ ] LiveClock returns real datetime
- [ ] market_hours correctly identifies NYSE trading hours

**Unit Tests:**
```python
def test_clock_abstract():
    """Clock cannot be instantiated."""
    with pytest.raises(TypeError):
        Clock()

def test_live_clock_now():
    """LiveClock returns current time."""
    clock = LiveClock()
    now = clock.now()
    assert abs((datetime.now() - now).total_seconds()) < 1

def test_market_hours_open():
    """Market hours correctly identifies trading time."""
    # 10:30 AM ET on Monday
    assert is_market_open(datetime(2026, 2, 2, 10, 30), tz="US/Eastern") == True
```

**Functional Tests:**
- Test market hours across DST transitions

---

## Phase 1: Foundation and Core Infrastructure

**Goal:** Establish project structure, configuration, and basic infrastructure for `vibe/trading-bot/`.

**Duration:** 3-5 days

### Task 1.1: Trading Bot Project Structure Setup

**Description:** Create the `vibe/trading-bot/` directory structure and initialize the Python package.

**Implementation Steps:**
1. Create `vibe/trading-bot/` directory with all subdirectories per design.md
2. Create `__init__.py` files with proper `__all__` exports
3. Create `pyproject.toml` for package installation
4. Create base `requirements.txt` with core dependencies
5. Ensure imports from `vibe.common` work

**Verification Criteria:**
- [ ] `python -c "import vibe.trading_bot"` succeeds
- [ ] `python -c "from vibe.trading_bot.core import orchestrator"` succeeds
- [ ] All subdirectory imports work without circular dependencies
- [ ] Package can be installed in editable mode: `pip install -e .`

**Unit Tests:**
- `test_imports.py`: Verify all module imports work
- `test_package_structure.py`: Verify expected modules exist

**Functional Tests:**
- Run `python -m vibe.trading_bot --help` shows usage (stub)

---

### Task 1.2: Configuration System

**Description:** Implement Pydantic-based settings with environment variable support and YAML config loading.

**Implementation Steps:**
1. Create `vibe/trading-bot/config/settings.py` with Pydantic `BaseSettings` classes
2. Define settings groups: TradingSettings, DataSettings, CloudSettings, NotificationSettings
3. Create `config/constants.py` for static values (market hours, symbols)
4. Create `config/logging_config.py` for structured JSON logging setup
5. Support `.env` file loading with `python-dotenv`

**Verification Criteria:**
- [ ] Settings load from environment variables
- [ ] Settings load from `.env` file
- [ ] Settings validate types (invalid values raise ValidationError)
- [ ] Default values work when env vars not set
- [ ] Logging outputs structured JSON format

**Unit Tests:**
```python
def test_settings_from_env():
    """Settings load from environment variables."""
    os.environ["FINNHUB_API_KEY"] = "test_key"
    settings = TradingSettings()
    assert settings.finnhub_api_key == "test_key"

def test_settings_validation_error():
    """Invalid settings raise ValidationError."""
    os.environ["YAHOO_RATE_LIMIT"] = "not_a_number"
    with pytest.raises(ValidationError):
        DataSettings()

def test_default_symbols():
    """Default trading symbols are set."""
    settings = TradingSettings()
    assert "AAPL" in settings.symbols
```

**Functional Tests:**
- Set env vars, run bot with `--show-config` flag, verify output matches expected values

---

### Task 1.3: Logging Infrastructure

**Description:** Implement structured JSON logging with rotation and multiple handlers (console, file, database).

**Implementation Steps:**
1. Create `vibe/trading-bot/utils/logger.py` with `get_logger()` factory
2. Implement JSON formatter for structured logs
3. Add rotating file handler (10MB, 5 backups)
4. Create database log handler for `service_logs` table
5. Add log level filtering by environment (DEBUG for dev, INFO for prod)

**Verification Criteria:**
- [ ] Logs output to console in human-readable format
- [ ] Logs output to file in JSON format
- [ ] Log rotation works at 10MB
- [ ] Logger name appears in output
- [ ] Extra metadata is included in JSON output

**Unit Tests:**
```python
def test_logger_json_format(caplog):
    """Logger outputs valid JSON."""
    logger = get_logger("test")
    logger.info("test message", extra={"meta": {"key": "value"}})
    # Parse log output as JSON
    log_json = json.loads(caplog.text)
    assert log_json["message"] == "test message"
    assert log_json["meta"]["key"] == "value"

def test_log_rotation(tmp_path):
    """Logs rotate at size limit."""
    # Write > 10MB of logs, verify backup files created
```

**Functional Tests:**
- Run bot for 1 minute with DEBUG logging, verify log file created with expected content

---

### Task 1.4: SQLite Trade Store

**Description:** Implement SQLite database for trades, metrics, and service logs with schema from design.md.

**Implementation Steps:**
1. Create `vibe/trading-bot/storage/trade_store.py` with TradeStore class
2. Implement schema creation with migrations support
3. Add CRUD operations for trades table
4. Implement `insert_trade()`, `update_trade()`, `get_trades()`, `get_trade_by_id()`
5. Add connection pooling for thread safety

**Verification Criteria:**
- [ ] Database file created on first access
- [ ] Schema matches design.md specification
- [ ] Trades can be inserted and retrieved
- [ ] Concurrent access works without corruption
- [ ] Index on symbol, strategy, status columns

**Unit Tests:**
```python
def test_trade_store_create(tmp_path):
    """TradeStore creates database with correct schema."""
    store = TradeStore(tmp_path / "trades.db")
    # Verify tables exist
    tables = store.execute("SELECT name FROM sqlite_master WHERE type='table'")
    assert "trades" in [t[0] for t in tables]

def test_insert_and_get_trade(trade_store):
    """Can insert and retrieve a trade."""
    trade = Trade(symbol="AAPL", side="buy", quantity=100, entry_price=150.0)
    trade_id = trade_store.insert_trade(trade)
    retrieved = trade_store.get_trade_by_id(trade_id)
    assert retrieved.symbol == "AAPL"

def test_concurrent_writes(trade_store):
    """Concurrent writes don't corrupt database."""
    # Use threading to write 100 trades simultaneously
```

**Functional Tests:**
- Insert 1000 trades, query by various filters, verify correct results

---

### Task 1.5: Metrics Store

**Description:** Implement metrics storage for performance tracking and health monitoring.

**Implementation Steps:**
1. Create `vibe/trading-bot/storage/metrics_store.py` with MetricsStore class
2. Define metric types: trade, performance, health
3. Implement `record_metric()`, `get_metrics()`, `aggregate_metrics()`
4. Add time-series query support (last N minutes, hourly aggregates)

**Verification Criteria:**
- [ ] Metrics table created with correct schema
- [ ] Can record metrics with dimensions
- [ ] Can query metrics by type, name, time range
- [ ] Can compute aggregates (sum, avg, count)

**Unit Tests:**
```python
def test_record_metric(metrics_store):
    """Can record a metric with dimensions."""
    metrics_store.record_metric(
        metric_type="trade",
        metric_name="pnl",
        metric_value=150.0,
        dimensions={"symbol": "AAPL", "strategy": "orb"}
    )

def test_aggregate_metrics(metrics_store):
    """Can aggregate metrics over time."""
    # Record 10 metrics, verify sum/avg/count
```

**Functional Tests:**
- Record metrics for 1 hour, generate daily performance report

---

### Task 1.6: Service Log Store with Retention

**Description:** Implement service log storage with automatic 3-day retention cleanup.

**Implementation Steps:**
1. Create `vibe/trading-bot/storage/log_store.py` with LogStore class
2. Implement SQLite-based log handler
3. Add `cleanup_old_logs()` method for 3-day retention
4. Create scheduled cleanup task

**Verification Criteria:**
- [ ] Logs inserted into database with correct structure
- [ ] Logs older than 3 days are deleted on cleanup
- [ ] Cleanup runs without affecting recent logs

**Unit Tests:**
```python
def test_log_retention(log_store):
    """Logs older than 3 days are cleaned up."""
    # Insert log with old timestamp
    log_store.insert_log(timestamp=datetime.now() - timedelta(days=4), ...)
    log_store.cleanup_old_logs()
    assert log_store.count_logs() == 0
```

**Functional Tests:**
- Insert logs over 5 simulated days, run cleanup, verify only recent logs remain

---

### Task 1.7: Graceful Shutdown Infrastructure

**Description:** Implement signal handling and graceful shutdown for the trading service.

**Implementation Steps:**
1. Create `vibe/trading-bot/core/service.py` with `TradingService` class
2. Implement `_setup_signal_handlers()` for SIGTERM and SIGINT
3. Implement `_handle_shutdown()` with ordered cleanup:
   - Stop accepting new signals
   - Close open positions (optional, configurable)
   - Cancel pending orders
   - Sync database to cloud
   - Disconnect from data sources
   - Save indicator state
4. Use `asyncio.Event` for shutdown coordination
5. Add timeout for shutdown (default 30s)

**Verification Criteria:**
- [ ] SIGTERM triggers graceful shutdown
- [ ] SIGINT (Ctrl+C) triggers graceful shutdown
- [ ] Shutdown completes within timeout
- [ ] All cleanup steps execute in order
- [ ] State is saved before exit

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_signal_handler_setup():
    """Signal handlers registered correctly."""
    service = TradingService(config=test_config)
    service._setup_signal_handlers()
    # Verify handlers are set (platform-specific)

@pytest.mark.asyncio
async def test_shutdown_sequence(mock_components):
    """Shutdown executes cleanup in correct order."""
    service = TradingService(config=test_config)
    service._shutdown_event.set()

    await service._handle_shutdown(signal.SIGTERM)

    # Verify cleanup order via mock call order
    assert mock_components.cancel_orders.called
    assert mock_components.sync_cloud.called

@pytest.mark.asyncio
async def test_shutdown_timeout():
    """Shutdown times out if cleanup hangs."""
    service = TradingService(config=Config(shutdown_timeout=1))
    # Mock a slow cleanup
    # Verify timeout exception raised
```

**Functional Tests:**
- Start service, send SIGTERM, verify clean exit with state saved

---

### Task 1.8: Health Check Server

**Description:** Implement FastAPI health check endpoints for container orchestration.

**Implementation Steps:**
1. Create `vibe/trading-bot/api/health.py` with FastAPI app
2. Implement `GET /health/live` - liveness probe (process alive)
3. Implement `GET /health/ready` - readiness probe (ready to trade)
4. Implement `GET /metrics` - Prometheus-compatible metrics
5. Add global health state management
6. Run health server in background asyncio task

**Verification Criteria:**
- [ ] `/health/live` returns 200 when process running
- [ ] `/health/ready` returns 200 when all checks pass
- [ ] `/health/ready` returns 503 when checks fail
- [ ] `/metrics` returns key metrics in Prometheus format
- [ ] Server runs on configurable port (default 8080)

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_liveness_endpoint():
    """Liveness returns 200 when alive."""
    async with AsyncClient(app=health_app, base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

@pytest.mark.asyncio
async def test_readiness_healthy():
    """Readiness returns 200 when healthy."""
    set_health_state(websocket_connected=True, recent_heartbeat=True)
    async with AsyncClient(app=health_app, base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200

@pytest.mark.asyncio
async def test_readiness_unhealthy():
    """Readiness returns 503 when unhealthy."""
    set_health_state(websocket_connected=False)
    async with AsyncClient(app=health_app, base_url="http://test") as client:
        response = await client.get("/health/ready")
        assert response.status_code == 503
```

**Functional Tests:**
- Start bot in Docker, verify health checks work with `curl`

---

## Phase 2: Data Layer

**Goal:** Implement data providers, caching, and real-time streaming infrastructure.

**Duration:** 5-7 days

**Status:** ✅ COMPLETE

**Completion Date:** 2026-02-07

**Implementation Summary:**
- Task 2.1: LiveDataProvider base class with rate limiting, retry logic, and health tracking
- Task 2.2: YahooDataProvider for historical OHLCV data fetching
- Task 2.3: FinnhubWebSocketClient for real-time trade streaming
- Task 2.4: BarAggregator for tick-to-OHLCV conversion with timezone handling
- Task 2.5: DataCache with Parquet storage and TTL-based invalidation
- Task 2.6: DataManager coordinating all components

**Test Results:** 35/35 unit tests passing (100% coverage)

**Key Files:**
- `vibe/trading_bot/data/providers/base.py` - LiveDataProvider base class
- `vibe/trading_bot/data/providers/yahoo.py` - Yahoo Finance provider
- `vibe/trading_bot/data/providers/finnhub.py` - Finnhub WebSocket client
- `vibe/trading_bot/data/aggregator.py` - Bar aggregator
- `vibe/trading_bot/data/cache.py` - Data cache manager
- `vibe/trading_bot/data/manager.py` - Data manager coordinator
- `vibe/trading_bot/data/README.md` - Comprehensive documentation
- `vibe/tests/trading_bot/test_data.py` - Test suite

### Task 2.1: Data Provider Base Class

**Description:** Create implementation base for live data providers (extends vibe.common.data.DataProvider).

**Implementation Steps:**
1. Create `vibe/trading-bot/data/providers/base.py` with `LiveDataProvider` base
2. Add rate limiting support
3. Add retry with exponential backoff
4. Add provider health status tracking

**Verification Criteria:**
- [ ] LiveDataProvider extends vibe.common.data.DataProvider
- [ ] Rate limiting configurable
- [ ] Health status trackable

**Unit Tests:**
```python
def test_live_provider_extends_base():
    """LiveDataProvider extends common DataProvider."""
    assert issubclass(LiveDataProvider, DataProvider)
```

---

### Task 2.2: Yahoo Finance Data Provider

**Description:** Implement historical data fetching via yfinance with rate limiting and error handling.

**Implementation Steps:**
1. Create `vibe/trading-bot/data/providers/yahoo.py` with `YahooDataProvider` class
2. Implement `get_historical()` with configurable period and interval
3. Add rate limiting (5 req/sec default, configurable)
4. Implement retry with exponential backoff
5. Handle yfinance exceptions gracefully

**Verification Criteria:**
- [ ] Can fetch 30 days of 5-minute data for a symbol
- [ ] Rate limiter prevents exceeding configured rate
- [ ] Retries on transient errors (up to 3 times)
- [ ] Returns standardized DataFrame schema
- [ ] Handles invalid symbols gracefully

**Unit Tests:**
```python
@patch('yfinance.Ticker')
def test_get_historical_success(mock_ticker):
    """Successfully fetches historical data."""
    mock_ticker.return_value.history.return_value = sample_df
    provider = YahooDataProvider()
    df = provider.get_historical("AAPL", period="7d", interval="5m")
    assert len(df) > 0
    assert all(col in df.columns for col in ["open", "high", "low", "close", "volume"])

def test_rate_limiting():
    """Rate limiter enforces configured limit."""
    provider = YahooDataProvider(rate_limit=2)  # 2 req/sec
    # Make 5 requests, measure time
    # Should take at least 2 seconds
```

**Functional Tests:**
- Fetch historical data for 5 MVP symbols, verify data quality and completeness

---

### Task 2.3: Finnhub WebSocket Client

**Description:** Implement real-time trade streaming with automatic reconnection and message parsing.

**Implementation Steps:**
1. Adapt existing `FinnhubWebSocketClient` to `vibe/trading-bot/data/providers/finnhub.py`
2. Add connection state machine (disconnected, connecting, connected, reconnecting)
3. Implement exponential backoff for reconnection
4. Add gap detection (if reconnect takes > 1 minute)
5. Emit events: on_connected, on_disconnected, on_trade, on_error

**Verification Criteria:**
- [ ] Connects to Finnhub WebSocket successfully
- [ ] Subscribes to symbols and receives trade data
- [ ] Auto-reconnects on disconnect (up to 5 attempts)
- [ ] Backoff increases exponentially (1s, 2s, 4s, 8s, 16s)
- [ ] Gap detection triggers historical backfill request

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_websocket_connect(mock_ws):
    """WebSocket connects and authenticates."""
    client = FinnhubWebSocketClient(api_key="test")
    await client.connect()
    assert client.connected

@pytest.mark.asyncio
async def test_reconnect_on_disconnect(mock_ws):
    """Client reconnects after disconnect."""
    # Simulate disconnect, verify reconnect attempt
```

**Functional Tests:**
- Connect to Finnhub, subscribe to AAPL, receive trades for 1 minute, verify data

---

### Task 2.4: Bar Aggregator

**Description:** Implement tick-to-bar conversion with timezone handling and bar completion detection.

**Implementation Steps:**
1. Adapt existing `BarAggregator` to `vibe/trading-bot/data/aggregator.py`
2. Add configurable bar intervals (1m, 5m, 15m, 1h)
3. Implement timezone-aware bar boundaries
4. Add bar completion callbacks
5. Handle late trades (trades arriving after bar close)

**Verification Criteria:**
- [ ] Trades aggregate into correct bar periods
- [ ] Bar boundaries align to interval (5m bars start at :00, :05, :10...)
- [ ] Completed bars emitted when new period starts
- [ ] Late trades update previous bar or create synthetic bar
- [ ] Volume and trade count accurate

**Unit Tests:**
```python
def test_bar_aggregation_5m():
    """Trades aggregate into 5-minute bars."""
    aggregator = BarAggregator(bar_interval="5m")

    # Add trades at 09:31:00, 09:32:00, 09:33:00
    # Verify single bar with correct OHLCV

def test_bar_boundary_crossing():
    """New bar starts when period boundary crossed."""
    aggregator = BarAggregator(bar_interval="5m")

    # Add trade at 09:34:59 -> belongs to 09:30 bar
    # Add trade at 09:35:00 -> new bar at 09:35
```

**Functional Tests:**
- Stream trades for 15 minutes, verify bars match expected count and values

---

### Task 2.5: Data Cache Manager

**Description:** Implement local Parquet cache for historical data with TTL and invalidation.

**Implementation Steps:**
1. Create `vibe/trading-bot/data/cache.py` with `DataCache` class
2. Store cached data as Parquet files (one per symbol/timeframe)
3. Implement TTL-based invalidation (configurable, default 1 hour)
4. Add cache metadata (last_update, row_count, data_range)
5. Implement cache warming on startup

**Verification Criteria:**
- [ ] Data cached to Parquet file after first fetch
- [ ] Subsequent fetches use cache if within TTL
- [ ] Cache invalidated after TTL expires
- [ ] Cache cleared on explicit request
- [ ] Cache stats available (hit rate, size)

**Unit Tests:**
```python
def test_cache_hit(tmp_path):
    """Subsequent fetches use cached data."""
    cache = DataCache(tmp_path)
    cache.put("AAPL", "5m", sample_df)

    # First get: returns data
    df1 = cache.get("AAPL", "5m")
    assert df1 is not None

    # Second get: same data, cache hit
    df2 = cache.get("AAPL", "5m")
    assert cache.stats()["hits"] == 2

def test_cache_ttl_expiry(tmp_path):
    """Cache expires after TTL."""
    cache = DataCache(tmp_path, ttl_seconds=1)
    cache.put("AAPL", "5m", sample_df)
    time.sleep(2)
    assert cache.get("AAPL", "5m") is None
```

**Functional Tests:**
- Fetch data, verify Parquet file created, restart bot, verify cache used

---

### Task 2.6: Data Manager (Coordinator)

**Description:** Coordinate data providers, cache, and aggregator into unified data access layer.

**Implementation Steps:**
1. Create `vibe/trading-bot/data/manager.py` with `DataManager` class
2. Orchestrate: cache check -> provider fetch -> cache update
3. Merge historical with real-time bars seamlessly
4. Implement data quality checks (gaps, outliers)
5. Emit data events for consumers

**Verification Criteria:**
- [ ] Single interface for both historical and real-time data
- [ ] Cache miss triggers provider fetch
- [ ] Real-time bars append to historical data
- [ ] Gap detection alerts when data missing
- [ ] Data quality metrics tracked

**Unit Tests:**
```python
def test_data_manager_cache_first(mock_provider, mock_cache):
    """DataManager checks cache before provider."""
    mock_cache.get.return_value = sample_df
    manager = DataManager(provider=mock_provider, cache=mock_cache)

    df = manager.get_data("AAPL", "5m", days=7)

    mock_cache.get.assert_called_once()
    mock_provider.get_historical.assert_not_called()
```

**Functional Tests:**
- Start data manager, fetch historical + start streaming, verify continuous data flow

---

## Phase 3: Indicator and Strategy Engine

**Goal:** Implement incremental indicator calculation and trading strategies.

**Duration:** 5-7 days

### Task 3.1: Incremental Indicator Engine

**Description:** Implement IncrementalIndicatorEngine in `vibe/common/indicators/`.

**Implementation Steps:**
1. Create `vibe/common/indicators/engine.py` with `IncrementalIndicatorEngine`
2. Support indicators: EMA, SMA, RSI, ATR, MACD, Bollinger Bands
3. Implement state persistence with pickle
4. Add state trimming for smaller file sizes
5. Validate indicator values against pandas-ta reference

**Verification Criteria:**
- [ ] Incremental values match batch pandas-ta values (within 0.01%)
- [ ] State persists and restores correctly
- [ ] New bars calculate in O(1) time
- [ ] Memory usage stable over long runs
- [ ] Trimmed state files < 100KB per symbol

**Unit Tests:**
```python
def test_ema_incremental_matches_batch():
    """Incremental EMA matches pandas-ta batch calculation."""
    df = generate_ohlcv(1000)

    # Batch calculation with pandas-ta
    batch_ema = ta.ema(df['close'], length=20)

    # Incremental calculation
    engine = IncrementalIndicatorEngine()
    df_inc = engine.update(df, 0, [{'name': 'ema', 'params': {'length': 20}}], 'TEST', '5m')

    # Compare last 100 values
    np.testing.assert_allclose(df_inc['EMA_20'].tail(100), batch_ema.tail(100), rtol=0.0001)

def test_state_persistence(tmp_path):
    """State saves and restores correctly."""
    engine = IncrementalIndicatorEngine()
    # Calculate indicators
    # Save state
    # New engine, load state
    # Add one bar, verify value matches expected
```

**Functional Tests:**
- Calculate indicators for 30 days of data, save state, simulate 1 day of real-time bars, verify accuracy

---

### Task 3.2: ORB Level Calculator

**Description:** Implement Opening Range Breakout level calculation with configurable parameters.

**Implementation Steps:**
1. Create `vibe/common/indicators/orb_levels.py` with `ORBCalculator` class
2. Calculate ORB_High, ORB_Low, ORB_Range for configurable opening window
3. Support body percentage filter (>50% body = valid breakout candle)
4. Handle partial days (market open, early close)
5. Reset levels daily at market open

**Verification Criteria:**
- [ ] ORB levels calculated correctly for 9:30-9:35 window
- [ ] Levels reset at each market open
- [ ] Partial days handled (late start, early close)
- [ ] Body percentage filter works
- [ ] Breakout detection works (price crosses ORB_High/Low)

**Unit Tests:**
```python
def test_orb_levels_calculation():
    """ORB levels calculated from first N minutes."""
    df = generate_market_day_data()  # 9:30 - 16:00

    calculator = ORBCalculator(start_time="09:30", duration_minutes=5)
    levels = calculator.calculate(df)

    # ORB_High should be max of first 5 bars high
    expected_high = df.iloc[:5]['high'].max()
    assert levels['ORB_High'] == expected_high

def test_orb_breakout_detection():
    """Breakout detected when price crosses ORB level."""
    # Setup ORB levels, then test price crossing
```

**Functional Tests:**
- Backtest ORB calculations on 30 days of AAPL data, verify levels match manual calculation

---

### Task 3.3: Strategy Base Class

**Description:** Create abstract strategy base class with signal generation interface in `vibe/common/strategies/`.

**Implementation Steps:**
1. Create `vibe/common/strategies/base.py` with `StrategyBase` ABC
2. Define interface: `generate_signals()`, `generate_signal_incremental()`
3. Add strategy configuration via Pydantic models
4. Implement take-profit and stop-loss calculation hooks
5. Add logging integration

**Verification Criteria:**
- [ ] Abstract methods enforced
- [ ] Configuration validates on instantiation
- [ ] Logger accessible in subclasses
- [ ] Exit check logic handles all scenarios

**Unit Tests:**
```python
def test_strategy_abstract():
    """StrategyBase cannot be instantiated."""
    with pytest.raises(TypeError):
        StrategyBase()

def test_strategy_config_validation():
    """Strategy config validates parameters."""
    with pytest.raises(ValidationError):
        ORBStrategy(config={"take_profit_type": "invalid"})
```

---

### Task 3.4: ORB Strategy Implementation

**Description:** Implement Opening Range Breakout strategy for MVP in `vibe/common/strategies/`.

**Implementation Steps:**
1. Create `vibe/common/strategies/orb.py` with `ORBStrategy` class
2. Implement entry logic: price breaks ORB_High (long) or ORB_Low (short)
3. Implement exit logic: take-profit at 2x range, stop-loss at ORB level
4. Add time filter (no entries after 15:00)
5. Add volume filter (optional: require above-average volume)

**Verification Criteria:**
- [ ] Long entry when price breaks above ORB_High
- [ ] Short entry when price breaks below ORB_Low
- [ ] Take-profit at configured ATR multiple
- [ ] Stop-loss at ORB level
- [ ] No entries in last hour of trading
- [ ] End-of-day exit for open positions

**Unit Tests:**
```python
def test_orb_long_entry():
    """Long signal when price breaks ORB_High."""
    strategy = ORBStrategy()
    df = create_orb_breakout_scenario(direction="up")

    signals = strategy.generate_signals(df)

    # Breakout bar should have signal = 1
    assert signals.iloc[breakout_idx] == 1

def test_orb_stop_loss():
    """Position stopped out at ORB level."""
    # Setup entry, then price falls below ORB_Low
```

**Functional Tests:**
- Backtest ORB strategy on 1 month of data, verify trade count and P&L calculation

---

### Task 3.5: Strategy Factory

**Description:** Create factory for instantiating strategies from configuration.

**Implementation Steps:**
1. Create `vibe/common/strategies/factory.py` with `StrategyFactory` class
2. Register built-in strategies (ORB)
3. Support custom strategy registration
4. Load strategy config from YAML or environment

**Verification Criteria:**
- [ ] Creates ORB strategy from string name
- [ ] Passes configuration to strategy
- [ ] Raises error for unknown strategy
- [ ] Custom strategies can be registered

**Unit Tests:**
```python
def test_factory_creates_orb():
    """Factory creates ORB strategy."""
    strategy = StrategyFactory.create("orb", config={})
    assert isinstance(strategy, ORBStrategy)

def test_factory_custom_strategy():
    """Factory supports custom strategies."""
    StrategyFactory.register("custom", CustomStrategy)
    strategy = StrategyFactory.create("custom", config={})
```

---

### Task 3.6: Multi-Timeframe Data Store

**Description:** Implement efficient data structure to maintain OHLCV bars for multiple timeframes (5m, 15m, 1h).

**Implementation Steps:**
1. Create `vibe/common/validation/mtf_data_store.py` with `MTFDataStore` class
2. Store primary timeframe (5m) bars as source of truth
3. Implement on-demand aggregation to higher timeframes (15m, 1h)
4. Add `add_bar()` method that returns completed HTF bars
5. Add `get_bars()` method to retrieve N bars for any timeframe
6. Implement memory-efficient pruning of old bars

**Verification Criteria:**
- [ ] 5m bars stored correctly
- [ ] 15m bars aggregated from 3x 5m bars
- [ ] 1h bars aggregated from 12x 5m bars
- [ ] HTF bar completion detected correctly at boundaries
- [ ] Memory usage bounded (configurable max bars per TF)

**Unit Tests:**
```python
def test_mtf_bar_aggregation():
    """5m bars aggregate to 15m correctly."""
    store = MTFDataStore(primary_tf="5m", htf_list=["15m", "1h"])

    # Add 3 5m bars at 09:30, 09:35, 09:40
    bars_5m = generate_bars(start="09:30", interval="5m", count=3)
    for bar in bars_5m:
        completed = store.add_bar("AAPL", bar)

    # Third bar should complete the 15m bar
    assert completed["15m"] is not None
    assert completed["15m"].open == bars_5m[0].open
    assert completed["15m"].close == bars_5m[2].close
    assert completed["15m"].high == max(b.high for b in bars_5m)

def test_htf_bar_boundary():
    """HTF bars complete at correct boundaries."""
    store = MTFDataStore(primary_tf="5m", htf_list=["1h"])

    # Add 12 5m bars (09:30 - 10:25)
    for i in range(12):
        bar = generate_bar(time=f"09:{30 + i*5}")
        completed = store.add_bar("AAPL", bar)

    # 12th bar should complete 1h bar
    assert completed["1h"] is not None

def test_get_bars_htf():
    """Can retrieve HTF bars after aggregation."""
    store = MTFDataStore(...)
    # Add 36 5m bars (3 hours)
    # Verify get_bars("AAPL", "1h", 3) returns 3 bars
```

**Functional Tests:**
- Stream 1 day of 5m data, verify all HTF bars aggregate correctly

---

### Task 3.7: Validation Rule Base Class

**Description:** Create abstract base class for pluggable MTF validation rules.

**Implementation Steps:**
1. Create `vibe/common/validation/rules/base.py` with `ValidationRule` ABC
2. Define `ValidationResult` dataclass (passed, score, reason, details)
3. Define abstract `validate()` method signature
4. Add `name` and `weight` abstract properties
5. Create rule registry for dynamic rule loading

**Verification Criteria:**
- [ ] ValidationRule cannot be instantiated
- [ ] ValidationResult contains all required fields
- [ ] Subclasses must implement validate(), name, weight

**Unit Tests:**
```python
def test_validation_rule_abstract():
    """ValidationRule cannot be instantiated."""
    with pytest.raises(TypeError):
        ValidationRule()

def test_validation_result():
    """ValidationResult contains all fields."""
    result = ValidationResult(
        passed=True,
        score=85.0,
        rule_name="trend_alignment",
        reason="2/2 timeframes aligned",
        details={"alignments": [...]}
    )
    assert result.passed
    assert result.score == 85.0
```

---

### Task 3.8: Trend Alignment Validation Rule

**Description:** Implement validation rule checking higher timeframe trend alignment.

**Implementation Steps:**
1. Create `vibe/common/validation/rules/trend_alignment.py` with `TrendAlignmentRule`
2. Check if price > EMA on 15m and 1h for long signals
3. Check if price < EMA on 15m and 1h for short signals
4. Return score based on alignment count
5. Make EMA period and required alignment configurable

**Verification Criteria:**
- [ ] Long signal passes when both 15m and 1h in uptrend
- [ ] Long signal fails when 1h in downtrend
- [ ] Short signal passes when both 15m and 1h in downtrend
- [ ] Score reflects partial alignment (50% if 1 of 2 aligned)

**Unit Tests:**
```python
def test_trend_alignment_long_pass():
    """Long signal passes when HTF trends aligned."""
    rule = TrendAlignmentRule(ema_period=20, required_alignment=2)
    mtf_data = create_mtf_data_uptrend()

    result = rule.validate(signal=1, symbol="AAPL", timestamp=now, mtf_data=mtf_data)

    assert result.passed
    assert result.score == 100.0
    assert "2/2" in result.reason

def test_trend_alignment_partial():
    """Partial alignment returns partial score."""
    rule = TrendAlignmentRule(ema_period=20, required_alignment=2)
    mtf_data = create_mtf_data_mixed_trend()  # 15m up, 1h down

    result = rule.validate(signal=1, ...)

    assert not result.passed
    assert result.score == 50.0
```

**Functional Tests:**
- Backtest 30 days, verify trend alignment correctly identified

---

### Task 3.9: Volume Confirmation Validation Rule

**Description:** Implement validation rule checking volume confirmation across timeframes.

**Implementation Steps:**
1. Create `vibe/common/validation/rules/volume_confirmation.py` with `VolumeConfirmationRule`
2. Check if breakout bar volume > N x average volume
3. Check if 15m volume is increasing
4. Return score based on volume ratio and trend
5. Make threshold and lookback configurable

**Verification Criteria:**
- [ ] Passes when volume > 1.5x average
- [ ] Fails when volume < 1.5x average
- [ ] Score increases with higher volume ratio
- [ ] HTF volume trend affects score

**Unit Tests:**
```python
def test_volume_confirmation_pass():
    """Passes with above-average volume."""
    rule = VolumeConfirmationRule(volume_threshold=1.5, lookback=20)
    mtf_data = create_mtf_data_high_volume(ratio=2.0)

    result = rule.validate(signal=1, ...)

    assert result.passed
    assert result.details["volume_ratio"] == 2.0

def test_volume_confirmation_fail():
    """Fails with below-average volume."""
    rule = VolumeConfirmationRule(volume_threshold=1.5, lookback=20)
    mtf_data = create_mtf_data_low_volume(ratio=0.8)

    result = rule.validate(signal=1, ...)

    assert not result.passed
```

**Functional Tests:**
- Verify volume calculation against manual calculation on sample data

---

### Task 3.10: Support/Resistance Validation Rule

**Description:** Implement validation rule checking for nearby S/R levels on higher timeframes.

**Implementation Steps:**
1. Create `vibe/common/validation/rules/support_resistance.py` with `SupportResistanceRule`
2. Detect swing highs/lows on 1h timeframe
3. For longs: check no resistance within N ATR above
4. For shorts: check no support within N ATR below
5. Make ATR buffer and lookback configurable

**Verification Criteria:**
- [ ] Identifies swing highs as resistance
- [ ] Identifies swing lows as support
- [ ] Passes when path is clear
- [ ] Fails when blocked by nearby level

**Unit Tests:**
```python
def test_sr_clear_path():
    """Passes when no nearby resistance."""
    rule = SupportResistanceRule(atr_buffer=1.5, lookback_bars=50)
    mtf_data = create_mtf_data_clear_path()

    result = rule.validate(signal=1, ...)

    assert result.passed
    assert result.score == 100.0

def test_sr_blocked():
    """Fails when resistance nearby."""
    rule = SupportResistanceRule(atr_buffer=1.5, lookback_bars=50)
    mtf_data = create_mtf_data_with_resistance(distance_atr=0.5)

    result = rule.validate(signal=1, ...)

    assert not result.passed
    assert len(result.details["nearby_levels"]) > 0
```

**Functional Tests:**
- Verify S/R detection on historical data matches visual chart analysis

---

### Task 3.11: MTF Validator Orchestrator

**Description:** Implement orchestrator that runs all validation rules and aggregates results.

**Implementation Steps:**
1. Create `vibe/common/validation/mtf_validator.py` with `MTFValidator` class
2. Accept list of ValidationRule instances
3. Run all rules and collect results
4. Calculate weighted score from individual scores
5. Return (passed, score, results) tuple
6. Make minimum score threshold configurable

**Verification Criteria:**
- [ ] Runs all registered rules
- [ ] Weighted score calculated correctly
- [ ] Passes when weighted score >= threshold
- [ ] Returns individual rule results for logging

**Unit Tests:**
```python
def test_mtf_validator_all_pass():
    """Passes when all rules pass."""
    rules = [
        MockRule(score=100, weight=0.4),
        MockRule(score=100, weight=0.3),
        MockRule(score=100, weight=0.3)
    ]
    validator = MTFValidator(rules=rules, min_score=60)

    passed, score, results = validator.validate(...)

    assert passed
    assert score == 100.0
    assert len(results) == 3

def test_mtf_validator_weighted_score():
    """Weighted score calculated correctly."""
    rules = [
        MockRule(score=100, weight=0.5),  # 50 points
        MockRule(score=50, weight=0.5)    # 25 points
    ]
    validator = MTFValidator(rules=rules, min_score=60)

    passed, score, results = validator.validate(...)

    assert score == 75.0  # 50 + 25
    assert passed  # 75 >= 60

def test_mtf_validator_fails_threshold():
    """Fails when below threshold."""
    rules = [MockRule(score=40, weight=1.0)]
    validator = MTFValidator(rules=rules, min_score=60)

    passed, score, results = validator.validate(...)

    assert not passed
    assert score == 40.0
```

**Functional Tests:**
- Integrate with ORB strategy, verify validation reduces false signals

---

### Task 3.12: ORB Strategy with MTF Validation Integration

**Description:** Integrate MTF validation into ORB strategy signal generation.

**Implementation Steps:**
1. Update `vibe/common/strategies/orb.py` to accept MTFValidator
2. After generating ORB signal, call MTFValidator
3. Only emit signal if validation passes
4. Log validation results for analysis
5. Add config option to enable/disable MTF validation

**Verification Criteria:**
- [ ] ORB signals pass through MTF validation
- [ ] Signals filtered when validation fails
- [ ] Validation results logged with signal
- [ ] Can disable validation via config

**Unit Tests:**
```python
def test_orb_with_mtf_validation_pass():
    """ORB signal emitted when MTF validation passes."""
    strategy = ORBStrategy(config=config, mtf_validator=mock_validator_pass)
    df = create_orb_breakout_scenario()

    signals = strategy.generate_signals(df)

    assert signals.iloc[breakout_idx] == 1

def test_orb_with_mtf_validation_fail():
    """ORB signal filtered when MTF validation fails."""
    strategy = ORBStrategy(config=config, mtf_validator=mock_validator_fail)
    df = create_orb_breakout_scenario()

    signals = strategy.generate_signals(df)

    assert signals.iloc[breakout_idx] == 0  # Filtered
```

**Functional Tests:**
- Backtest ORB with and without MTF validation, compare win rates

---

## Phase 4: Risk Management and Execution

**Goal:** Implement risk controls and order execution with mock exchange.

**Duration:** 4-6 days

### Task 4.1: Position Sizer

**Description:** Calculate position sizes based on account and risk parameters. Located in `vibe/common/risk/`.

**Implementation Steps:**
1. Create `vibe/common/risk/position_sizer.py` with `PositionSizer` class
2. Implement fixed dollar risk sizing (e.g., risk $100 per trade)
3. Implement percentage risk sizing (e.g., 1% of account per trade)
4. Add maximum position size limits
5. Round to valid share quantities

**Verification Criteria:**
- [ ] Position size respects dollar risk limit
- [ ] Position size respects percentage risk limit
- [ ] Position size never exceeds maximum
- [ ] Fractional shares rounded correctly (if not allowed)

**Unit Tests:**
```python
def test_fixed_dollar_risk_sizing():
    """Position sized by fixed dollar risk."""
    sizer = PositionSizer(risk_per_trade=100)  # $100 risk

    # $150 stock, $5 stop loss distance
    size = sizer.calculate(entry_price=150, stop_price=145, account_value=10000)

    # Risk = $100, stop distance = $5, position = 20 shares
    assert size == 20

def test_max_position_limit():
    """Position size capped at maximum."""
    sizer = PositionSizer(max_position_size=10)
    size = sizer.calculate(entry_price=10, stop_price=9, account_value=100000)
    assert size == 10  # Capped
```

**Functional Tests:**
- Verify position sizes across various scenarios match expected values

---

### Task 4.2: Stop Loss Manager

**Description:** Implement stop loss tracking and trailing stop logic. Located in `vibe/common/risk/`.

**Implementation Steps:**
1. Create `vibe/common/risk/stop_loss.py` with `StopLossManager` class
2. Implement fixed stop (at entry - X ATR)
3. Implement trailing stop (moves with price)
4. Track stop levels per position
5. Emit stop triggered events

**Verification Criteria:**
- [ ] Initial stop set at entry
- [ ] Trailing stop moves up with price (long) / down with price (short)
- [ ] Stop never moves against position
- [ ] Stop trigger detected when price crosses

**Unit Tests:**
```python
def test_trailing_stop_long():
    """Trailing stop moves up with price."""
    manager = StopLossManager()

    # Open long at $100, initial stop at $95
    manager.set_stop("trade1", entry_price=100, stop_price=95, is_long=True, trailing=True)

    # Price moves to $110
    manager.update_price("trade1", current_price=110)

    # Stop should have moved to $105 (maintaining $5 distance)
    assert manager.get_stop("trade1") == 105

def test_stop_trigger():
    """Stop triggers when price crosses."""
    manager = StopLossManager()
    manager.set_stop("trade1", entry_price=100, stop_price=95, is_long=True)

    triggered = manager.check_trigger("trade1", current_price=94)
    assert triggered == True
```

**Functional Tests:**
- Simulate price movements, verify stop behavior matches specification

---

### Task 4.3: Risk Manager (Coordinator)

**Description:** Coordinate all risk checks before order execution. Located in `vibe/common/risk/`.

**Implementation Steps:**
1. Create `vibe/common/risk/manager.py` with `RiskManager` class
2. Integrate position sizer and stop loss manager
3. Add exposure limits (max positions, max per symbol)
4. Add drawdown checks (pause trading if daily drawdown > X%)
5. Implement pre-trade risk check pipeline

**Verification Criteria:**
- [ ] Pre-trade check validates position size
- [ ] Pre-trade check validates exposure limits
- [ ] Pre-trade check validates drawdown status
- [ ] Risk check returns pass/fail with reason

**Unit Tests:**
```python
def test_risk_check_passes():
    """Risk check passes for valid trade."""
    manager = RiskManager(max_positions=5, max_drawdown_pct=5)

    result = manager.pre_trade_check(
        symbol="AAPL", side="buy", quantity=10, entry_price=150
    )

    assert result.passed == True

def test_risk_check_exposure_limit():
    """Risk check fails when exposure limit exceeded."""
    manager = RiskManager(max_positions=2)
    # Add 2 positions

    result = manager.pre_trade_check(symbol="MSFT", ...)
    assert result.passed == False
    assert "exposure limit" in result.reason.lower()
```

**Functional Tests:**
- Run simulated trading session, verify all risk limits enforced

---

### Task 4.4: Exchange Base Class

**Description:** Adapt exchange interface for live trading (implements vibe.common.execution.ExecutionEngine).

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/base.py` that implements `ExecutionEngine`
2. Add live-specific concerns: network errors, latency, retries
3. Define order models for live trading context
4. Add order status tracking (open, filled, partial, cancelled)

**Verification Criteria:**
- [ ] Implements ExecutionEngine ABC from vibe.common
- [ ] Order model validates fields
- [ ] Fill model captures execution details

**Unit Tests:**
```python
def test_exchange_implements_interface():
    """Exchange implements ExecutionEngine."""
    assert issubclass(MockExchange, ExecutionEngine)

def test_order_validation():
    """Order validates required fields."""
    with pytest.raises(ValidationError):
        Order(symbol="AAPL", side="buy")  # Missing quantity
```

---

### Task 4.5: Mock Exchange Implementation (ENHANCED)

**Description:** Implement paper trading exchange with realistic slippage, partial fills, and all order types. Initial capital: $10,000.

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/mock_exchange.py` with `MockExchange` class
2. Initialize with $10,000 starting capital
3. Implement order types: Market, Limit, Stop-Loss, Stop-Limit
4. Simulate realistic fill delays (100-500ms configurable)
5. Implement SlippageModel with volatility and size factors
6. Implement partial fill simulation (configurable probability)
7. Track positions, cash, and P&L accurately
8. Implement order status state machine

**Verification Criteria:**
- [ ] Market orders fill at current price + slippage
- [ ] Limit orders fill when price reaches limit (no slippage)
- [ ] Stop-Loss orders trigger and execute as market
- [ ] Stop-Limit orders trigger and execute as limit
- [ ] Partial fills tracked correctly
- [ ] Account P&L calculated correctly
- [ ] Commission deducted from account
- [ ] Initial capital is $10,000

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_market_order_with_slippage():
    """Market order fills with slippage."""
    exchange = MockExchange(
        initial_capital=10000,
        slippage_pct=0.0005  # 0.05%
    )
    await exchange.set_price("AAPL", 150.00)

    response = await exchange.submit_order(
        Order(symbol="AAPL", side="buy", quantity=10, order_type="market")
    )

    # Filled at $150 + 0.05% slippage = $150.075
    assert response.avg_price == pytest.approx(150.075, rel=0.001)
    assert response.filled_qty == 10
    assert response.status == OrderStatus.FILLED

@pytest.mark.asyncio
async def test_limit_order_no_immediate_fill():
    """Limit order doesn't fill if price not reached."""
    exchange = MockExchange(initial_capital=10000)
    await exchange.set_price("AAPL", 150.00)

    response = await exchange.submit_order(
        Order(symbol="AAPL", side="buy", quantity=10, order_type="limit", limit_price=148.00)
    )

    assert response.status == OrderStatus.PENDING
    assert response.filled_qty == 0

@pytest.mark.asyncio
async def test_stop_loss_trigger():
    """Stop-loss triggers when price crosses stop."""
    exchange = MockExchange(initial_capital=10000)
    await exchange.set_price("AAPL", 150.00)

    # Submit stop-loss at $145
    response = await exchange.submit_order(
        Order(symbol="AAPL", side="sell", quantity=10, order_type="stop_loss", stop_price=145.00)
    )
    assert response.status == OrderStatus.PENDING

    # Price drops to $144 - stop should trigger
    await exchange.set_price("AAPL", 144.00)
    await exchange.process_pending_orders()

    order = exchange.get_order(response.order_id)
    assert order.status == OrderStatus.FILLED

@pytest.mark.asyncio
async def test_partial_fill():
    """Partial fill handled correctly."""
    exchange = MockExchange(
        initial_capital=10000,
        partial_fill_probability=1.0  # Force partial fill
    )
    await exchange.set_price("AAPL", 150.00)

    response = await exchange.submit_order(
        Order(symbol="AAPL", side="buy", quantity=100, order_type="market")
    )

    assert response.status == OrderStatus.PARTIAL
    assert response.filled_qty < 100
    assert response.remaining_qty > 0
```

**Functional Tests:**
- Run 100 simulated trades with various order types, verify all fills correct
- Verify ending account value = initial + sum(P&L) - sum(commissions)

---

### Task 4.6: Slippage Model

**Description:** Implement realistic slippage calculation based on market conditions.

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/slippage.py` with `SlippageModel` class
2. Implement base slippage percentage (configurable, e.g., 0.05%)
3. Add volatility component (higher ATR = more slippage)
4. Add order size impact (larger orders = more slippage)
5. Apply direction (buy = slip up, sell = slip down)

**Verification Criteria:**
- [ ] Base slippage applied correctly
- [ ] Volatility increases slippage
- [ ] Larger orders have more slippage
- [ ] Buy orders slip up, sell orders slip down
- [ ] Slippage configurable via parameters

**Unit Tests:**
```python
def test_slippage_buy_direction():
    """Buy orders slip up (worse price)."""
    model = SlippageModel(base_slippage_pct=0.001)  # 0.1%

    slipped = model.apply(price=100.00, side="buy")

    assert slipped > 100.00
    assert slipped == pytest.approx(100.10, rel=0.01)

def test_slippage_sell_direction():
    """Sell orders slip down (worse price)."""
    model = SlippageModel(base_slippage_pct=0.001)

    slipped = model.apply(price=100.00, side="sell")

    assert slipped < 100.00

def test_slippage_volatility_factor():
    """Higher volatility increases slippage."""
    model = SlippageModel(base_slippage_pct=0.001, volatility_factor=0.5)

    low_vol = model.apply(price=100.00, side="buy", volatility=0.01)
    high_vol = model.apply(price=100.00, side="buy", volatility=0.05)

    assert high_vol > low_vol

def test_slippage_size_impact():
    """Larger orders have more slippage."""
    model = SlippageModel(base_slippage_pct=0.001, size_impact_factor=0.0001)

    small_order = model.apply(price=100.00, side="buy", order_size=100)
    large_order = model.apply(price=100.00, side="buy", order_size=1000)

    assert large_order > small_order
```

**Functional Tests:**
- Compare slippage model output to realistic market slippage data

---

### Task 4.7: Order Retry Policy

**Description:** Implement configurable retry policy for handling partial fills and unfilled orders.

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/retry_policy.py` with `OrderRetryPolicy` class
2. Implement `should_retry()` based on retry count and elapsed time
3. Implement `get_delay()` with exponential backoff
4. Configure max retries, base delay, max delay, cancel timeout
5. Add logging for retry decisions

**Verification Criteria:**
- [ ] Retries stop after max_retries
- [ ] Retries stop after cancel_after timeout
- [ ] Delay increases exponentially
- [ ] Delay capped at max_delay

**Unit Tests:**
```python
def test_retry_allowed():
    """Retry allowed when under limits."""
    policy = OrderRetryPolicy(max_retries=3, cancel_after_seconds=60)
    managed = ManagedOrder(retry_count=1, submitted_at=datetime.now())

    assert policy.should_retry(managed) == True

def test_retry_exceeded_max():
    """Retry denied when max retries exceeded."""
    policy = OrderRetryPolicy(max_retries=3)
    managed = ManagedOrder(retry_count=3, submitted_at=datetime.now())

    assert policy.should_retry(managed) == False

def test_retry_timeout():
    """Retry denied when timeout exceeded."""
    policy = OrderRetryPolicy(max_retries=10, cancel_after_seconds=60)
    managed = ManagedOrder(
        retry_count=1,
        submitted_at=datetime.now() - timedelta(seconds=70)
    )

    assert policy.should_retry(managed) == False

def test_exponential_backoff():
    """Delay increases exponentially."""
    policy = OrderRetryPolicy(base_delay_seconds=1.0, backoff_multiplier=2.0)

    assert policy.get_delay(0) == 1.0
    assert policy.get_delay(1) == 2.0
    assert policy.get_delay(2) == 4.0
    assert policy.get_delay(3) == 8.0

def test_delay_capped():
    """Delay capped at max."""
    policy = OrderRetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0, backoff_multiplier=2.0)

    assert policy.get_delay(10) == 10.0  # Capped, not 1024
```

**Functional Tests:**
- Simulate partial fills, verify retry behavior matches policy

---

### Task 4.8: Order Manager

**Description:** Implement order lifecycle management with retry, partial fill handling, and cancellation.

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/order_manager.py` with `OrderManager` class
2. Manage order submission with notification integration
3. Handle partial fills - decide retry vs cancel
4. Implement retry with backoff per OrderRetryPolicy
5. Cancel unfilled portions after timeout
6. Track all order state transitions
7. Emit notifications for ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED

**Verification Criteria:**
- [ ] Orders submitted with ORDER_SENT notification
- [ ] Full fills trigger ORDER_FILLED notification
- [ ] Partial fills retry per policy
- [ ] Timeout triggers ORDER_CANCELLED notification
- [ ] All state transitions logged

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_order_manager_full_fill():
    """Full fill sends ORDER_FILLED notification."""
    manager = OrderManager(
        exchange=mock_exchange_full_fill,
        retry_policy=retry_policy,
        notification_service=mock_notifications
    )

    await manager.submit_order(Order(...))

    mock_notifications.send_order_event.assert_called()
    calls = mock_notifications.send_order_event.call_args_list
    assert calls[0].kwargs["event_type"] == "ORDER_SENT"
    assert calls[1].kwargs["event_type"] == "ORDER_FILLED"

@pytest.mark.asyncio
async def test_order_manager_partial_fill_retry():
    """Partial fill triggers retry."""
    manager = OrderManager(
        exchange=mock_exchange_partial_fill,
        retry_policy=OrderRetryPolicy(max_retries=3),
        notification_service=mock_notifications
    )

    result = await manager.submit_order(Order(quantity=100, ...))

    # Should have retried for remaining quantity
    assert mock_exchange_partial_fill.submit_order.call_count >= 2

@pytest.mark.asyncio
async def test_order_manager_cancel_after_timeout():
    """Unfilled order cancelled after timeout."""
    manager = OrderManager(
        exchange=mock_exchange_never_fills,
        retry_policy=OrderRetryPolicy(cancel_after_seconds=1, max_retries=10),
        notification_service=mock_notifications
    )

    await manager.submit_order(Order(...))
    await asyncio.sleep(2)  # Wait for timeout

    mock_notifications.send_order_event.assert_called_with(
        event_type="ORDER_CANCELLED",
        reason=unittest.mock.ANY
    )
```

**Functional Tests:**
- Simulate various fill scenarios, verify correct behavior and notifications

---

### Task 4.9: Trade Executor (ENHANCED)

**Description:** Coordinate signal-to-order conversion with OrderManager integration for resilient execution.

**Implementation Steps:**
1. Create `vibe/trading-bot/exchange/executor.py` with `TradeExecutor` class
2. Convert strategy signals to appropriate order types
3. Calculate stop-loss and take-profit orders
4. Submit orders via OrderManager (not directly to exchange)
5. Apply risk management checks before submission
6. Track order lifecycle through OrderManager callbacks
7. Update trade store with fills and P&L

**Verification Criteria:**
- [ ] Signals converted to orders with correct parameters
- [ ] Stop-loss orders submitted with entry orders
- [ ] Risk check happens before order submission
- [ ] Orders routed through OrderManager
- [ ] Fills recorded in trade store with P&L
- [ ] Failed risk checks logged and not executed

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_signal_to_order_with_stops():
    """Strategy signal creates entry and stop-loss orders."""
    executor = TradeExecutor(
        order_manager=mock_order_manager,
        risk_manager=mock_risk
    )

    await executor.execute_signal(
        symbol="AAPL",
        signal=1,  # Long
        price=150.00,
        stop_price=145.00,  # ORB low
        take_profit=155.00
    )

    # Verify entry order submitted
    assert mock_order_manager.submit_order.call_count >= 1
    entry_order = mock_order_manager.submit_order.call_args_list[0][0][0]
    assert entry_order.side == "buy"

@pytest.mark.asyncio
async def test_risk_check_blocks_order():
    """Risk check failure prevents order submission."""
    mock_risk.pre_trade_check.return_value = RiskCheckResult(passed=False, reason="Exposure limit")
    executor = TradeExecutor(order_manager=mock_order_manager, risk_manager=mock_risk)

    await executor.execute_signal(symbol="AAPL", signal=1, price=150.00)

    mock_order_manager.submit_order.assert_not_called()
```

**Functional Tests:**
- Run strategy for 1 day (simulated), verify all signals execute with proper order management

---

## Phase 5: Orchestration and Deployment

**Goal:** Integrate all components, add notifications, and prepare for deployment.

**Duration:** 5-7 days

### Task 5.1: Market Calendar Integration

**Description:** Integrate pandas_market_calendars for market hours and holidays.

**Implementation Steps:**
1. Create `vibe/trading-bot/core/scheduler.py` with `MarketScheduler` class
2. Use pandas_market_calendars for NYSE/NASDAQ schedule
3. Implement `is_market_open()`, `next_market_open()`, `next_market_close()`
4. Handle early closes and holidays
5. Add timezone-aware comparisons

**Verification Criteria:**
- [ ] Correctly identifies market open/close times
- [ ] Handles early close days (day before holidays)
- [ ] Handles holidays (markets closed)
- [ ] Works across DST transitions
- [ ] Returns times in configured timezone

**Unit Tests:**
```python
def test_is_market_open():
    """Correctly identifies market hours."""
    scheduler = MarketScheduler(exchange="NYSE")

    # 10:30 AM ET on a trading day
    assert scheduler.is_market_open(datetime(2026, 2, 2, 10, 30)) == True

    # 5:00 PM ET (after close)
    assert scheduler.is_market_open(datetime(2026, 2, 2, 17, 0)) == False

def test_early_close():
    """Handles early close days."""
    scheduler = MarketScheduler(exchange="NYSE")

    # Day before Thanksgiving: closes at 1:00 PM
    close_time = scheduler.get_close_time(datetime(2026, 11, 25))
    assert close_time.hour == 13
```

**Functional Tests:**
- Verify schedule for next 30 days matches official NYSE calendar

---

### Task 5.2: Discord Notification Payload Schema

**Description:** Define notification payload schema for order lifecycle events.

**Implementation Steps:**
1. Create `vibe/trading-bot/notifications/payloads.py` with `OrderNotificationPayload` dataclass
2. Include all fields per design.md specification
3. Add validation for required fields per event type
4. Support serialization to JSON for logging

**Verification Criteria:**
- [ ] Payload contains all required fields
- [ ] Different event types have appropriate optional fields
- [ ] Payload validates correctly
- [ ] Serializes to JSON without errors

**Unit Tests:**
```python
def test_order_sent_payload():
    """ORDER_SENT payload validates correctly."""
    payload = OrderNotificationPayload(
        event_type="ORDER_SENT",
        timestamp=datetime.now(),
        order_id="ord_123",
        symbol="AAPL",
        side="buy",
        order_type="market",
        quantity=50,
        strategy_name="ORB",
        signal_reason="ORB breakout above $185.50"
    )
    assert payload.event_type == "ORDER_SENT"

def test_order_filled_payload_with_pnl():
    """ORDER_FILLED payload includes P&L for closing trades."""
    payload = OrderNotificationPayload(
        event_type="ORDER_FILLED",
        timestamp=datetime.now(),
        order_id="ord_456",
        symbol="AAPL",
        side="sell",
        order_type="market",
        quantity=50,
        fill_price=187.25,
        filled_quantity=50,
        realized_pnl=83.00,
        realized_pnl_pct=0.90,
        position_size=0,  # Closed
        strategy_name="ORB",
        signal_reason="Take profit hit"
    )
    assert payload.realized_pnl == 83.00
```

---

### Task 5.3: Discord Message Formatter

**Description:** Implement formatter to convert payloads into Discord embed messages.

**Implementation Steps:**
1. Create `vibe/trading-bot/notifications/formatter.py` with `DiscordNotificationFormatter` class
2. Implement color scheme per event type (blue=sent, green=filled, red=cancelled)
3. Format ORDER_SENT with order details and signal reason
4. Format ORDER_FILLED with fill details, slippage, P&L
5. Format ORDER_CANCELLED with filled/unfilled quantities and reason
6. Keep messages under 2000 char limit

**Verification Criteria:**
- [ ] Each event type formatted correctly
- [ ] Colors match event type
- [ ] P&L shown for closing trades
- [ ] Slippage shown when significant
- [ ] Messages under 2000 characters

**Unit Tests:**
```python
def test_format_order_sent():
    """ORDER_SENT formatted with correct fields."""
    formatter = DiscordNotificationFormatter()
    payload = create_order_sent_payload()

    message = formatter.format(payload)

    assert message["embeds"][0]["color"] == 0x3498db  # Blue
    assert "Order Sent" in message["embeds"][0]["title"]
    assert any(f["name"] == "Symbol" for f in message["embeds"][0]["fields"])

def test_format_order_filled_with_pnl():
    """ORDER_FILLED shows P&L for closing trades."""
    formatter = DiscordNotificationFormatter()
    payload = create_order_filled_payload(realized_pnl=83.00)

    message = formatter.format(payload)

    assert message["embeds"][0]["color"] == 0x2ecc71  # Green
    pnl_field = next(f for f in message["embeds"][0]["fields"] if "P&L" in f["name"])
    assert "$83.00" in pnl_field["value"]

def test_format_order_cancelled():
    """ORDER_CANCELLED shows reason."""
    formatter = DiscordNotificationFormatter()
    payload = create_order_cancelled_payload(reason="Timeout after 60s")

    message = formatter.format(payload)

    assert message["embeds"][0]["color"] == 0xe74c3c  # Red
    reason_field = next(f for f in message["embeds"][0]["fields"] if f["name"] == "Reason")
    assert "Timeout" in reason_field["value"]
```

**Functional Tests:**
- Generate sample messages for all event types, verify visual appearance in Discord

---

### Task 5.4: Token Bucket Rate Limiter

**Description:** Implement token bucket rate limiter for Discord webhook compliance (5 req/2s).

**Implementation Steps:**
1. Create `vibe/trading-bot/notifications/rate_limiter.py` with `TokenBucketRateLimiter` class
2. Implement token bucket algorithm with configurable rate
3. Add async `acquire()` method that waits if no tokens
4. Support burst up to max_tokens
5. Thread-safe with asyncio lock

**Verification Criteria:**
- [ ] Respects 5 requests per 2 seconds limit
- [ ] Allows burst up to max_tokens
- [ ] Blocks and waits when no tokens
- [ ] Thread-safe for concurrent access

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_rate_limiter_allows_burst():
    """Allows burst up to max_tokens."""
    limiter = TokenBucketRateLimiter(tokens_per_period=5, period_seconds=2.0)

    # 5 requests should be immediate
    for _ in range(5):
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

@pytest.mark.asyncio
async def test_rate_limiter_blocks_excess():
    """Blocks when tokens exhausted."""
    limiter = TokenBucketRateLimiter(tokens_per_period=5, period_seconds=2.0)

    # Exhaust tokens
    for _ in range(5):
        await limiter.acquire()

    # 6th request should wait
    start = time.time()
    await limiter.acquire()
    elapsed = time.time() - start

    assert elapsed >= 0.3  # At least 0.4s wait (2s/5 tokens)

@pytest.mark.asyncio
async def test_rate_limiter_refills():
    """Tokens refill over time."""
    limiter = TokenBucketRateLimiter(tokens_per_period=5, period_seconds=2.0)

    # Exhaust tokens
    for _ in range(5):
        await limiter.acquire()

    # Wait for refill
    await asyncio.sleep(2.0)

    # Should have tokens again
    wait_time = await limiter.acquire()
    assert wait_time == 0.0
```

**Functional Tests:**
- Send 20 messages in rapid succession, verify no Discord rate limit errors

---

### Task 5.5: Discord Notification Service (ENHANCED)

**Description:** Implement Discord webhook notifications with event queue and rate limiting.

**Implementation Steps:**
1. Create `vibe/trading-bot/notifications/discord.py` with `DiscordNotifier` class
2. Integrate TokenBucketRateLimiter
3. Implement async notification queue
4. Use DiscordNotificationFormatter for message formatting
5. Add `send_order_event()` method accepting OrderNotificationPayload
6. Handle webhook errors gracefully (retry on 429, log on 4xx/5xx)
7. Support notification batching for multiple events

**Verification Criteria:**
- [ ] ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED events sent
- [ ] Rate limiter prevents webhook ban
- [ ] Messages queued when rate limited
- [ ] Webhook errors handled gracefully
- [ ] Async queue processes in order

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_discord_send_order_sent(mock_aiohttp):
    """ORDER_SENT notification sent correctly."""
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/...")
    payload = create_order_sent_payload()

    await notifier.send_order_event(payload)

    mock_aiohttp.post.assert_called_once()
    call_data = mock_aiohttp.post.call_args[1]["json"]
    assert "Order Sent" in call_data["embeds"][0]["title"]

@pytest.mark.asyncio
async def test_discord_rate_limiting_integration(mock_aiohttp):
    """Rate limiter integrated correctly."""
    notifier = DiscordNotifier(webhook_url="...")

    # Send 10 messages rapidly
    start = time.time()
    for i in range(10):
        await notifier.send_order_event(create_payload(i))
    elapsed = time.time() - start

    # Should have taken at least 2s due to rate limiting
    assert elapsed >= 2.0

@pytest.mark.asyncio
async def test_discord_handles_rate_limit_response(mock_aiohttp):
    """Handles 429 rate limit response."""
    mock_aiohttp.post.side_effect = [
        MockResponse(status=429, headers={"Retry-After": "1"}),
        MockResponse(status=200)
    ]
    notifier = DiscordNotifier(webhook_url="...")

    await notifier.send_order_event(create_payload())

    # Should have retried after delay
    assert mock_aiohttp.post.call_count == 2
```

**Functional Tests:**
- Send test notifications for all event types, verify received in Discord
- Verify rate limiting works under load (50 rapid notifications)

---

### Task 5.6: Cloud Sync Service

**Description:** Implement periodic sync of trade database to cloud storage.

**Implementation Steps:**
1. Create `vibe/trading-bot/storage/sync.py` with `DatabaseSync` class
2. Upload trade database on schedule (every 5 minutes)
3. Download database on startup (if newer than local)
4. Handle sync conflicts (cloud vs local)
5. Compress database before upload

**Verification Criteria:**
- [ ] Database uploaded to cloud on schedule
- [ ] Database downloaded on startup if cloud is newer
- [ ] Compression reduces upload size
- [ ] Sync failure doesn't crash bot
- [ ] Sync metrics tracked (last_sync, success_rate)

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_database_upload(mock_storage):
    """Database uploaded to cloud storage."""
    sync = DatabaseSync(storage=mock_storage, db_path="trades.db")

    await sync.upload()

    mock_storage.upload_file.assert_called_once()

def test_compression():
    """Database compressed before upload."""
    # Create 1MB database, verify upload is smaller
```

**Functional Tests:**
- Create trades, trigger sync, verify file in cloud storage

---

### Task 5.7: Health Monitor

**Description:** Implement system health monitoring and status reporting.

**Implementation Steps:**
1. Create `vibe/trading-bot/core/health_monitor.py` with `HealthMonitor` class
2. Aggregate health from all components (data, exchange, storage)
3. Implement heartbeat (periodic status log/metric)
4. Add health check endpoint (for external monitoring)
5. Alert on unhealthy status

**Verification Criteria:**
- [ ] Health aggregated from all components
- [ ] Unhealthy component triggers alert
- [ ] Heartbeat logged every minute
- [ ] Health status queryable

**Unit Tests:**
```python
def test_health_aggregation():
    """Health aggregated from components."""
    monitor = HealthMonitor()
    monitor.register_component("data", lambda: {"status": "healthy"})
    monitor.register_component("exchange", lambda: {"status": "unhealthy"})

    health = monitor.get_health()

    assert health["overall"] == "unhealthy"
    assert health["components"]["data"]["status"] == "healthy"
```

**Functional Tests:**
- Start bot, verify health endpoint returns status

---

### Task 5.8: Main Orchestrator

**Description:** Implement main trading loop coordinating all components.

**Implementation Steps:**
1. Create `vibe/trading-bot/core/orchestrator.py` with `TradingOrchestrator` class
2. Initialize all components in correct order
3. Implement main loop: check market hours -> get data -> generate signals -> execute
4. Handle graceful shutdown (close positions, sync data)
5. Implement sleep/wake cycle for off-hours

**Verification Criteria:**
- [ ] All components initialized successfully
- [ ] Trading loop runs during market hours
- [ ] Bot sleeps during off-hours (low CPU)
- [ ] Graceful shutdown saves state
- [ ] Errors in one symbol don't crash entire bot

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_orchestrator_lifecycle():
    """Orchestrator starts and stops cleanly."""
    orchestrator = TradingOrchestrator(config={...})

    # Start in background
    task = asyncio.create_task(orchestrator.run())

    await asyncio.sleep(1)

    # Trigger shutdown
    await orchestrator.shutdown()

    await task  # Should complete without error

def test_component_initialization():
    """All components initialize in correct order."""
    # Verify data manager initializes before strategy
    # Verify exchange initializes before executor
```

**Functional Tests:**
- Run orchestrator in mock mode for 1 simulated trading day, verify complete cycle

---

### Task 5.9: Entry Point and CLI

**Description:** Create main entry point with command-line interface.

**Implementation Steps:**
1. Create `vibe/trading-bot/main.py` with CLI using argparse or click
2. Support commands: run, backtest, validate-config, show-status
3. Add signal handling (SIGTERM, SIGINT for graceful shutdown)
4. Implement --dry-run mode (no real orders)
5. Add --config flag for custom config file

**Verification Criteria:**
- [ ] `python -m vibe.trading_bot run` starts bot
- [ ] `python -m vibe.trading_bot --help` shows usage
- [ ] SIGTERM triggers graceful shutdown
- [ ] --dry-run mode logs orders without executing

**Unit Tests:**
```python
def test_cli_help():
    """CLI shows help text."""
    result = subprocess.run(["python", "-m", "vibe.trading_bot", "--help"])
    assert result.returncode == 0
    assert "run" in result.stdout

def test_dry_run_mode():
    """Dry run mode doesn't execute orders."""
    # Start with --dry-run, verify no orders submitted
```

**Functional Tests:**
- Start bot via CLI, run for 5 minutes, stop via SIGTERM, verify clean shutdown

---

### Task 5.10: Docker Containerization

**Description:** Create Docker image for cloud deployment.

**Implementation Steps:**
1. Create `Dockerfile` with Python 3.11 slim base
2. Install dependencies with optional cloud SDKs
3. Create `docker-compose.yml` for local development
4. Add health check to container
5. Configure for low memory usage (<512MB)
6. Use exec form for proper signal handling (PID 1)

**Verification Criteria:**
- [ ] Image builds successfully
- [ ] Container starts and runs trading bot
- [ ] Health check passes when bot healthy
- [ ] Memory usage under 512MB
- [ ] Logs stream to stdout
- [ ] SIGTERM properly handled (exec form ENTRYPOINT)

**Unit Tests:**
```bash
# Build test
docker build -t trading-bot-test .
echo "Build successful"

# Run test
docker run --rm trading-bot-test python -c "import vibe.trading_bot"
echo "Import successful"
```

**Functional Tests:**
- Build image, run container with mock config, verify bot starts and runs

---

### Task 5.11: E2E Integration Test

**Description:** Create comprehensive end-to-end test of entire system.

**Implementation Steps:**
1. Create `vibe/tests/e2e/test_full_cycle.py`
2. Test complete flow: startup -> data fetch -> signal -> order -> fill -> close
3. Use mock exchange and cached data
4. Verify trade recorded in database
5. Verify notification sent (mocked Discord)

**Verification Criteria:**
- [ ] Bot starts without errors
- [ ] Historical data loads successfully
- [ ] Real-time data streams (mocked)
- [ ] Strategy generates expected signals
- [ ] Orders execute through mock exchange
- [ ] Trades recorded in database
- [ ] Notifications sent for trades

**Unit Tests:** N/A (this is an integration test)

**Functional Tests:**
```python
@pytest.mark.e2e
async def test_full_trading_cycle():
    """Complete trading cycle from startup to trade execution."""
    # Setup mocks
    with mock_finnhub(), mock_yahoo(), mock_discord():
        orchestrator = TradingOrchestrator(config=test_config)

        # Run for simulated day
        await orchestrator.run_backtest(
            start_date="2026-01-15",
            end_date="2026-01-15"
        )

        # Verify trades
        trades = orchestrator.trade_store.get_trades()
        assert len(trades) > 0

        # Verify P&L calculated
        assert all(t.pnl is not None for t in trades if t.status == "closed")

        # Verify notifications
        assert mock_discord.call_count > 0
```

---

## Phase 6: Dashboard Infrastructure

**Goal:** Implement real-time monitoring dashboard with FastAPI backend and Streamlit frontend.

**Duration:** 3-4 days

### Task 6.1: Dashboard API Endpoints

**Description:** Implement FastAPI REST endpoints for dashboard data access.

**Implementation Steps:**
1. Create `vibe/trading-bot/api/dashboard.py` with FastAPI app
2. Implement `GET /api/trades` - recent trades with filters
3. Implement `GET /api/positions` - current open positions
4. Implement `GET /api/account` - account summary
5. Implement `GET /api/metrics/performance` - performance metrics
6. Implement `GET /api/metrics/health` - service health metrics
7. Add CORS middleware for Streamlit access

**Verification Criteria:**
- [ ] All endpoints return correct data
- [ ] Filters work (symbol, status, limit)
- [ ] CORS allows Streamlit origin
- [ ] Responses are JSON serializable

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_get_trades():
    """GET /api/trades returns trades."""
    async with AsyncClient(app=dashboard_app, base_url="http://test") as client:
        response = await client.get("/api/trades?limit=10")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

@pytest.mark.asyncio
async def test_get_account():
    """GET /api/account returns account summary."""
    async with AsyncClient(app=dashboard_app, base_url="http://test") as client:
        response = await client.get("/api/account")
        assert response.status_code == 200
        data = response.json()
        assert "cash" in data
        assert "equity" in data
```

**Functional Tests:**
- Start API server, query all endpoints, verify data matches database

---

### Task 6.2: Dashboard WebSocket for Real-Time Updates

**Description:** Implement WebSocket endpoint for pushing real-time updates to dashboard.

**Implementation Steps:**
1. Add WebSocket endpoint `ws://host/ws/updates` to dashboard API
2. Implement `ConnectionManager` for managing multiple clients
3. Add `broadcast_trade_update()` function
4. Add `broadcast_position_update()` function
5. Add `broadcast_metrics_update()` function
6. Handle client disconnections gracefully

**Verification Criteria:**
- [ ] WebSocket connects successfully
- [ ] Trade updates broadcast to all clients
- [ ] Position updates broadcast to all clients
- [ ] Disconnections handled without errors

**Unit Tests:**
```python
@pytest.mark.asyncio
async def test_websocket_connect():
    """WebSocket connects successfully."""
    async with AsyncClient(app=dashboard_app, base_url="http://test") as client:
        async with client.websocket_connect("/ws/updates") as ws:
            assert ws is not None

@pytest.mark.asyncio
async def test_broadcast_trade():
    """Trade updates broadcast to clients."""
    # Connect 2 clients, broadcast trade, verify both receive
```

**Functional Tests:**
- Connect Streamlit to WebSocket, make trade, verify real-time update

---

### Task 6.3: Streamlit Dashboard Main App

**Description:** Implement main Streamlit dashboard application.

**Implementation Steps:**
1. Create `vibe/dashboard/app.py` with main Streamlit app
2. Configure page layout (wide mode, sidebar)
3. Implement auto-refresh toggle (5 second interval)
4. Add connection to Dashboard API
5. Handle API errors gracefully

**Verification Criteria:**
- [ ] Dashboard loads without errors
- [ ] Auto-refresh works
- [ ] API connection established
- [ ] Errors displayed gracefully

**Unit Tests:**
- Manual testing (Streamlit apps are difficult to unit test)

**Functional Tests:**
- Start dashboard, verify page loads with data

---

### Task 6.4: Dashboard Account Summary Component

**Description:** Implement account summary display (cash, equity, daily P&L).

**Implementation Steps:**
1. Create metrics row showing: Cash, Equity, Daily P&L, Total Trades
2. Add delta indicators for P&L (green/red)
3. Fetch data from `/api/account` endpoint
4. Auto-refresh with main app

**Verification Criteria:**
- [ ] All 4 metrics displayed
- [ ] P&L shows percentage change
- [ ] Green/red color coding works
- [ ] Data refreshes automatically

**Functional Tests:**
- Verify metrics match backend data

---

### Task 6.5: Dashboard Performance Metrics Component

**Description:** Implement performance metrics display (win rate, Sharpe, drawdown).

**Implementation Steps:**
1. Create metrics row showing: Win Rate, Sharpe Ratio, Max Drawdown, Avg Trade Duration
2. Fetch data from `/api/metrics/performance` endpoint
3. Add period selector (daily, weekly, monthly)

**Verification Criteria:**
- [ ] All 4 metrics displayed
- [ ] Period selector works
- [ ] Metrics update with period change

**Functional Tests:**
- Verify metrics calculation matches expected values

---

### Task 6.6: Dashboard Positions Table

**Description:** Implement current positions table with real-time unrealized P&L.

**Implementation Steps:**
1. Create positions table with columns: Symbol, Quantity, Entry Price, Current Price, Unrealized P&L, P&L %
2. Fetch data from `/api/positions` endpoint
3. Color code P&L (green/red)
4. Show "No open positions" when empty

**Verification Criteria:**
- [ ] Table displays all positions
- [ ] P&L calculated correctly
- [ ] Color coding works
- [ ] Empty state handled

**Functional Tests:**
- Open position, verify appears in table with correct P&L

---

### Task 6.7: Dashboard Trades History Table

**Description:** Implement recent trades table with P&L highlighting.

**Implementation Steps:**
1. Create trades table with columns: Symbol, Side, Quantity, Entry Price, Exit Price, P&L, Status, Time
2. Fetch data from `/api/trades` endpoint
3. Color code P&L rows (green/red)
4. Add pagination or limit

**Verification Criteria:**
- [ ] Table displays recent trades
- [ ] P&L color coding works
- [ ] Pagination/limit works
- [ ] Date formatting correct

**Functional Tests:**
- Execute trades, verify appear in table

---

### Task 6.8: Dashboard P&L Chart

**Description:** Implement cumulative P&L chart over time.

**Implementation Steps:**
1. Create line chart showing cumulative P&L
2. Use Plotly for interactive chart
3. Calculate cumulative sum from trades
4. Add time period selector

**Verification Criteria:**
- [ ] Chart renders correctly
- [ ] Cumulative P&L calculated correctly
- [ ] Interactive (hover, zoom)
- [ ] Period selector works

**Functional Tests:**
- Execute trades over time, verify chart updates

---

### Task 6.9: Dashboard Health Status Component

**Description:** Implement service health status display.

**Implementation Steps:**
1. Create health status section showing: WebSocket status, Uptime, Errors (1h)
2. Fetch data from `/api/metrics/health` endpoint
3. Color code status indicators (green/red)

**Verification Criteria:**
- [ ] All health indicators displayed
- [ ] Color coding works (green=healthy, red=unhealthy)
- [ ] Data refreshes automatically

**Functional Tests:**
- Disconnect WebSocket, verify status changes to red

---

### Task 6.10: Dashboard Deployment Configuration

**Description:** Configure dashboard for Streamlit Community Cloud deployment.

**Implementation Steps:**
1. Create `.streamlit/config.toml` with theme settings
2. Create `requirements.txt` for dashboard dependencies
3. Create `.streamlit/secrets.toml.example` for API URL
4. Document deployment steps for Streamlit Community Cloud

**Verification Criteria:**
- [ ] Config files created
- [ ] Dashboard deploys to Streamlit Cloud
- [ ] Connects to API via secrets
- [ ] Theme consistent

**Functional Tests:**
- Deploy to Streamlit Cloud, verify functionality

---

## Testing Strategy

### Unit Test Coverage Expectations

| Component | Target Coverage |
|-----------|-----------------|
| Shared Models | 95% |
| Shared Interfaces | 90% |
| Configuration | 95% |
| Data Providers | 85% |
| Indicators | 90% |
| Strategies | 90% |
| MTF Validation | 90% |
| Risk Management | 95% |
| Exchange/Mock | 90% |
| Order Manager | 95% |
| Slippage Model | 95% |
| Storage | 90% |
| Notifications | 85% |
| Rate Limiter | 95% |
| Health Checks | 90% |
| Dashboard API | 85% |

### Integration Test Requirements

1. **Data Pipeline**: Yahoo -> Cache -> DataManager -> MTFDataStore -> Indicators
2. **Strategy Pipeline**: Data -> ORBStrategy -> MTFValidator -> Signal
3. **Trading Pipeline**: Signal -> RiskManager -> OrderManager -> MockExchange -> TradeStore
4. **Notification Pipeline**: OrderManager -> DiscordNotifier -> Discord Webhook
5. **Sync Pipeline**: TradeStore -> Compress -> CloudStorage
6. **Dashboard Pipeline**: TradeStore -> Dashboard API -> Streamlit UI

### Functional Test Scenarios

| Scenario | Steps | Expected Outcome |
|----------|-------|------------------|
| Market Open | Start bot at 9:25 AM | Bot waits until 9:30, then starts trading |
| ORB Long Entry | Price breaks ORB_High | Long order submitted, filled, position opened |
| ORB with MTF Fail | ORB breakout but 1h downtrend | Signal filtered, no order submitted |
| ORB with MTF Pass | ORB breakout with HTF alignment | Signal validated, order submitted |
| Stop Loss Hit | Price falls below stop | Position closed, loss recorded |
| Take Profit Hit | Price rises to target | Position closed, profit recorded |
| Partial Fill | Order partially fills | Retry until filled or timeout |
| Partial Fill Timeout | Partial fill, no more fills | Remainder cancelled after 60s |
| EOD Exit | Position open at 3:55 PM | Position closed before 4:00 PM |
| WebSocket Disconnect | Finnhub disconnects | Auto-reconnect, resume trading |
| Cloud Sync | 5 minutes elapsed | Database uploaded to cloud |
| Graceful Shutdown | SIGTERM received | Positions closed, state saved, clean exit |
| Discord ORDER_SENT | Order submitted | Discord notification with order details |
| Discord ORDER_FILLED | Order filled | Discord notification with P&L if closing |
| Discord ORDER_CANCELLED | Order cancelled | Discord notification with reason |
| Discord Rate Limit | 10 rapid orders | Messages queued, no Discord errors |
| Health Check Live | Process running | /health/live returns 200 |
| Health Check Ready | All components healthy | /health/ready returns 200 |
| Health Check Unhealthy | WebSocket disconnected | /health/ready returns 503 |
| Dashboard Trades | Execute trade | Trade appears in dashboard table |
| Dashboard Real-time | Execute trade | WebSocket pushes update to UI |

---

## Verification Checklist

### Phase 0 Complete
- [x] `vibe/common/` structure created
- [x] All shared models import correctly
- [x] ExecutionEngine ABC defined
- [x] DataProvider ABC defined
- [x] Clock ABC defined with LiveClock
- [x] Unit tests pass: `pytest vibe/tests/common/` (111 tests passed)

### Phase 1 Complete
- [x] All modules import without errors
- [x] Configuration loads from environment
- [x] Logging outputs structured JSON
- [x] SQLite databases created with correct schema
- [x] Graceful shutdown handles SIGTERM
- [x] Health check endpoints respond correctly
- [x] Unit tests pass: `pytest vibe/tests/trading_bot/test_config.py vibe/tests/trading_bot/test_storage.py` (121 tests passed)

### Phase 2 Complete
- [x] Yahoo provider fetches historical data
- [x] Finnhub WebSocket connects and streams
- [x] Bar aggregator produces correct OHLCV
- [x] Data cache reduces API calls
- [x] Unit tests pass: `pytest vibe/tests/trading_bot/test_data.py` (35 tests passed)
- [x] Code review completed (2 rounds, 75 issues identified, 1 critical fix applied)

### Phase 3 Complete
- [x] Incremental indicators match batch values
- [x] ORB levels calculate correctly
- [x] ORB strategy generates valid signals
- [x] MTF data store aggregates bars correctly
- [x] All validation rules pass unit tests
- [x] MTF validator orchestrates rules correctly
- [x] ORB with MTF validation filters false signals
- [x] Unit tests pass: `pytest vibe/tests/common/test_indicators.py vibe/tests/common/test_strategies.py vibe/tests/common/test_validation.py` (61 tests passing)
- [x] Code review completed (2 rounds, 10 critical issues documented for future fixes)

### Phase 4 Complete
- [ ] Position sizer respects risk limits
- [ ] Stop loss manager tracks correctly
- [ ] Slippage model applies correctly
- [ ] Mock exchange handles all 4 order types
- [ ] Partial fills handled with retry policy
- [ ] Order manager tracks lifecycle correctly
- [ ] Trade executor integrates all components
- [ ] Initial capital set to $10,000
- [ ] Unit tests pass: `pytest vibe/tests/common/test_risk.py vibe/tests/trading_bot/test_exchange.py`

### Phase 5 Complete
- [ ] Market calendar identifies trading days
- [ ] Discord notifications send for ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED
- [ ] Discord rate limiter prevents ban (5 req/2s)
- [ ] Notification payloads include P&L, slippage, signal reason
- [ ] Cloud sync uploads/downloads database
- [ ] Orchestrator runs complete trading cycle
- [ ] Docker container builds and runs
- [ ] E2E tests pass: `pytest vibe/tests/e2e/`

### Phase 6 Complete
- [ ] Dashboard API endpoints return correct data
- [ ] WebSocket broadcasts real-time updates
- [ ] Streamlit dashboard loads without errors
- [ ] Account summary displays correctly
- [ ] Positions table shows open positions
- [ ] Trades table shows history with P&L
- [ ] P&L chart renders correctly
- [ ] Health status indicators work
- [ ] Dashboard deploys to Streamlit Cloud

### MVP Ready
- [ ] All phase verification checkboxes complete
- [ ] Bot runs 8 hours without errors (paper trading)
- [ ] At least 10 trades executed in mock mode
- [ ] All notifications received in Discord
- [ ] Database synced to cloud storage
- [ ] Memory usage stable (<512MB)
- [ ] No unhandled exceptions in logs
- [ ] Dashboard accessible and functional
- [ ] Graceful shutdown works from Docker

---

## Risk Mitigation Checkpoints

### Before Phase 3
- Verify Yahoo Finance API still accessible (check for rate limit changes)
- Verify Finnhub WebSocket connects with current API

### Before Phase 5
- Test Docker build on target cloud platform
- Verify cloud storage credentials work
- Test Discord webhook still functional

### Before Phase 6
- Verify Streamlit Community Cloud account active
- Test API accessibility from Streamlit Cloud

### Before Production
- Paper trade for 5 full trading days
- Review all trades for accuracy
- Verify cloud failover works (disconnect cloud, verify local operation)
- Load test with 20 symbols (if planning to scale)
- Verify dashboard shows accurate real-time data
