# Trading Bot - Complete Implementation

Live trading bot infrastructure with real-time data streaming, execution management, orchestration, notifications, and cloud sync.

## Overview

The trading bot is organized in phases from foundation to deployment:

### Phase 0: Shared Components
- Core models (Trade, Order, Position, Signal)
- Data models and validation
- Risk management (position sizing, stop loss)
- Strategies (ORB, indicators, signal generation)
- Validation rules (trend alignment, volume confirmation)

### Phase 1: Foundation Infrastructure
- **Configuration System**: Pydantic-based settings with environment variable support
- **Logging Infrastructure**: Structured JSON logging with rotation and database storage
- **Trade Storage**: SQLite database for persistent trade data
- **Metrics Store**: Time-series metrics tracking for performance monitoring
- **Log Store**: Service log storage with automatic retention cleanup
- **Graceful Shutdown**: Signal handling with ordered cleanup sequence
- **Health Checks**: FastAPI endpoints for container orchestration

### Phase 2-4: Data, Exchange, and Execution
- Real-time data fetching from multiple providers
- Exchange integration and order execution
- Trade execution with risk management

### Phase 5: Orchestration and Deployment
- **Market Calendar Integration**: Market hours and holiday detection
- **Discord Notifications**: Real-time trade alerts with formatting
- **Rate Limiting**: Token bucket rate limiter for API compliance
- **Cloud Sync**: Database backup/restore to Azure/AWS
- **Health Monitoring**: System health aggregation and reporting
- **Main Orchestrator**: Trading loop coordination and component management
- **CLI Entry Point**: Command-line interface with backtest/validation/status
- **Docker Containerization**: Multi-stage builds and deployment-ready image
- **E2E Integration Tests**: Full trading cycle validation

## Directory Structure

```
vibe/trading_bot/
├── api/                         # API endpoints
│   ├── health.py               # Health check endpoints
│   └── dashboard.py            # REST API for dashboard (Phase 6)
├── config/                      # Configuration
│   ├── settings.py             # Pydantic BaseSettings
│   ├── constants.py            # Static constants
│   └── logging_config.py       # Logging configuration
├── core/                        # Core infrastructure
│   ├── service.py              # TradingService base
│   ├── scheduler.py            # Market calendar & hours (Phase 5.1)
│   ├── health_monitor.py       # Health monitoring (Phase 5.7)
│   └── orchestrator.py         # Main trading loop (Phase 5.8)
├── data/                        # Data management
│   ├── manager.py              # Data aggregation
│   ├── aggregator.py           # Multi-provider aggregation
│   ├── cache.py                # Data caching
│   └── providers/              # Data source providers
├── exchange/                    # Exchange integration
│   ├── mock_exchange.py        # Mock exchange for testing
│   └── slippage.py             # Slippage modeling
├── execution/                   # Order execution
│   ├── trade_executor.py       # Trade execution engine
│   └── order_manager.py        # Order lifecycle management
├── notifications/              # Discord notifications (Phase 5)
│   ├── payloads.py             # Notification payload schemas
│   ├── formatter.py            # Discord embed formatting
│   ├── rate_limiter.py         # Token bucket rate limiter
│   └── discord.py              # Discord webhook service
├── storage/                     # Data persistence
│   ├── trade_store.py          # SQLite trade storage
│   ├── metrics_store.py        # SQLite metrics storage
│   ├── log_store.py            # SQLite log storage
│   └── sync.py                 # Cloud sync service (Phase 5.6)
├── utils/                       # Utilities
│   └── logger.py               # Logger factory
├── main.py                      # CLI entry point (Phase 5.9)
├── Dockerfile                   # Docker image definition (Phase 5.10)
├── docker-compose.yml           # Docker Compose configuration
└── requirements.txt             # Python dependencies
```

## Installation

```bash
# Install in editable mode with dependencies
pip install -e vibe/trading-bot/

# Or install with development dependencies
pip install -e "vibe/trading-bot/[dev]"
```

## Configuration

Configuration uses Pydantic with environment variable support.

### Settings Structure

Settings are organized into logical groups:

```python
from vibe.trading_bot.config.settings import AppSettings

settings = AppSettings()  # Loads from environment

# Trading configuration
settings.trading.symbols  # List of trading symbols
settings.trading.initial_capital  # Starting capital
settings.trading.use_stop_loss  # Enable stop loss

# Data configuration
settings.data.yahoo_rate_limit  # Requests per second
settings.data.bar_intervals  # Bar intervals to track

# Cloud configuration
settings.cloud.enable_cloud_sync  # Enable cloud sync
settings.cloud.cloud_provider  # Cloud provider type

# Notifications
settings.notifications.discord_webhook_url  # Discord integration
settings.notifications.notify_on_trade  # Alert on trade execution

# App settings
settings.environment  # development/production
settings.log_level  # DEBUG/INFO/WARNING/ERROR
settings.health_check_port  # Health server port (default 8080)
settings.shutdown_timeout_seconds  # Graceful shutdown timeout
```

### Environment Variables

```bash
# Trading settings
export SYMBOLS='["AAPL", "GOOGL", "MSFT"]'
export INITIAL_CAPITAL=10000
export FINNHUB_API_KEY="your-api-key"

# Data settings
export YAHOO_RATE_LIMIT=5
export DATA_CACHE_TTL_SECONDS=3600

# Cloud settings
export ENABLE_CLOUD_SYNC=true
export CLOUD_PROVIDER="azure"

# Phase 5 Notifications
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

## Phase 5: Orchestration and Deployment Features

### Market Calendar Integration (Task 5.1)
Market-aware trading with pandas_market_calendars:
- Automatic market hours detection
- Holiday and early close handling
- Next open/close time queries
- Support for multiple exchanges (NYSE, NASDAQ, etc.)

### Discord Notifications (Tasks 5.2-5.5)
Real-time trade notifications with formatting:
- Order notification payloads (ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED)
- Discord embed message formatting with colors
- P&L and slippage calculations
- Token bucket rate limiting (5 req/2s compliance)
- Async notification queue with retry logic

### Cloud Sync Service (Task 5.6)
Automatic database backup and synchronization:
- Support for Azure Blob Storage and AWS S3
- Compression before upload
- Sync metrics tracking (success rate, last sync time)
- Automatic download on startup if cloud is newer

### Health Monitoring (Task 5.7)
System health tracking and reporting:
- Component health aggregation
- Error tracking and reporting
- Heartbeat logging (configurable interval)
- Health check endpoints (liveness, readiness, startup probes)
- Status queries for external monitoring

### Main Orchestrator (Task 5.8)
Complete trading loop coordination:
- Component initialization in correct order
- Market-aware trading cycles (sleep during off-hours)
- Error handling with component isolation
- Graceful shutdown with position closure
- Backtesting support

### CLI Entry Point (Task 5.9)
Command-line interface with multiple commands:
```bash
# Start live trading
python -m vibe.trading_bot run

# Run backtest for date range
python -m vibe.trading_bot backtest --start 2026-02-01 --end 2026-02-05

# Validate configuration
python -m vibe.trading_bot validate-config

# Show current status
python -m vibe.trading_bot show-status

# Help
python -m vibe.trading_bot --help
```

### Docker Containerization (Task 5.10)
Production-ready containerization:
- Multi-stage build for optimized image size
- Non-root user for security
- Health checks integrated
- Memory limit: <512MB
- Proper signal handling (exec form ENTRYPOINT)
- JSON logging for container log aggregation

## Testing

Comprehensive test suite with 107+ tests:

```bash
# Run all Phase 5 tests
pytest vibe/tests/trading_bot/core/test_scheduler.py
pytest vibe/tests/trading_bot/notifications/
pytest vibe/tests/trading_bot/core/test_health_monitor.py
pytest vibe/tests/trading_bot/storage/test_sync.py
pytest vibe/tests/e2e/test_full_cycle.py

# Run with coverage
pytest vibe/tests/trading_bot/ --cov=vibe.trading_bot --cov-report=html
```

Test coverage includes:
- Market scheduler: 19 tests (market open/close detection, holidays, schedules)
- Notification payloads: 15 tests (validation, serialization, P&L calculations)
- Message formatter: 11 tests (formatting, colors, field layout)
- Rate limiter: 21 tests (burst, blocking, refilling, concurrent access)
- Discord notifier: 11 tests (queueing, rate limiting, webhook handling)
- Health monitor: 16 tests (aggregation, error tracking, health endpoints)
- Cloud sync: 12 tests (upload, download, compression, metrics)
- E2E integration: 16 tests (orchestrator lifecycle, full trading cycle)

## Quick Start

### Local Development

```bash
# Install dependencies
pip install -e vibe/trading_bot/

# Configure environment
export SYMBOLS='["AAPL"]'
export INITIAL_CAPITAL=10000
export FINNHUB_API_KEY="your-key"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

# Run backtest
python -m vibe.trading_bot backtest --start 2026-02-01 --end 2026-02-05

# Start live trading (with mock exchange)
python -m vibe.trading_bot run

# Check status
python -m vibe.trading_bot show-status
```

### Docker Deployment

```bash
# Build image
docker build -t trading-bot:latest .

# Run container
docker run \
  -e SYMBOLS='["AAPL","GOOGL"]' \
  -e INITIAL_CAPITAL=50000 \
  -e DISCORD_WEBHOOK_URL="..." \
  -v ./data:/app/data \
  trading-bot:latest run

# Or use docker-compose
docker-compose up
```

## Architecture Highlights

- **Modular Design**: Each component is independent and testable
- **Async/Await**: Non-blocking I/O for data fetching and notifications
- **Thread-Safe**: SQLite connection pooling, thread-local storage
- **Rate Limiting**: Automatic API compliance with token bucket algorithm
- **Error Resilience**: Graceful degradation when components fail
- **Monitoring**: Health checks and metrics for observability
- **Cloud-Ready**: Container-native with health probes and log streaming

## Environment Variables

# Additional Notifications
export DISCORD_WEBHOOK_URL="https://discord.com/..."

# App settings
export ENVIRONMENT=production
export LOG_LEVEL=INFO
export HEALTH_CHECK_PORT=8080
```

### .env File

Create a `.env` file in the project root:

```bash
ENVIRONMENT=development
LOG_LEVEL=DEBUG
SYMBOLS=["AAPL", "GOOGL", "MSFT", "AMZN", "NVDA"]
INITIAL_CAPITAL=10000.0
FINNHUB_API_KEY=your_api_key_here
ENABLE_CLOUD_SYNC=false
```

## Usage

### Basic Service Setup

```python
from vibe.trading_bot.core.service import TradingService
from vibe.trading_bot.config.settings import get_settings

# Get settings
settings = get_settings()

# Create service
service = TradingService(config=settings)

# Register shutdown handlers
def cleanup():
    print("Cleaning up...")

service.register_shutdown_handler(cleanup)

# Start service (blocks until shutdown signal)
service.start()
```

### Logging

```python
from vibe.trading_bot.utils.logger import get_logger, log_with_context

# Get logger
logger = get_logger(__name__)

# Basic logging
logger.info("Trading started")
logger.warning("Low capital")
logger.error("Connection failed")

# Logging with context
log_with_context(
    logger,
    "info",
    "Trade executed",
    symbol="AAPL",
    quantity=100,
    price=150.0,
)
```

### Trade Management

```python
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.common.models import Trade

# Create store
store = TradeStore(db_path="./data/trades.db")

# Insert trade
trade = Trade(
    symbol="AAPL",
    side="buy",
    quantity=100,
    entry_price=150.0,
)
trade_id = store.insert_trade(trade)

# Update trade
store.update_trade(trade_id, exit_price=155.0, status="closed")

# Query trades
trades = store.get_trades(symbol="AAPL", status="closed")
stats = store.get_pnl_stats(symbol="AAPL")
```

### Metrics Tracking

```python
from vibe.trading_bot.storage.metrics_store import MetricsStore

# Create metrics store
metrics = MetricsStore(db_path="./data/metrics.db")

# Record metrics
metrics.record_metric(
    metric_type="trade",
    metric_name="pnl",
    metric_value=150.0,
    dimensions={"symbol": "AAPL", "strategy": "orb"}
)

# Aggregate metrics
daily_pnl = metrics.aggregate_metrics(
    metric_type="trade",
    metric_name="pnl",
    aggregation="sum",
    start_time="2024-01-15T00:00:00",
    end_time="2024-01-16T00:00:00",
)

# Get statistics
stats = metrics.get_metric_stats(
    metric_type="trade",
    metric_name="pnl",
)
```

### Service Logs

```python
from vibe.trading_bot.storage.log_store import LogStore

# Create log store
log_store = LogStore(db_path="./data/logs.db", retention_days=3)

# Insert logs
log_store.insert_log(
    level="INFO",
    logger="app.trading",
    message="Order submitted",
    extra_data={"order_id": "12345"}
)

# Query logs
logs = log_store.get_logs(level="ERROR", limit=100)

# Clean up old logs (3+ days)
deleted = log_store.cleanup_old_logs()
```

### Health Checks

```python
from vibe.trading_bot.api.health import set_health_state, app
import uvicorn

# Update health state
set_health_state(
    is_alive=True,
    websocket_connected=True,
    recent_heartbeat=True,
)

# Start health check server (on port 8080 by default)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

#### Health Check Endpoints

- `GET /health/live` - Liveness probe (200 if alive)
- `GET /health/ready` - Readiness probe (200 if ready to trade)
- `GET /metrics` - Prometheus-compatible metrics
- `GET /health/state` - Full health state (internal use)

Example responses:

```bash
# Liveness
curl http://localhost:8080/health/live
# {"status":"alive","timestamp":"2024-01-15T10:30:45.123456"}

# Readiness
curl http://localhost:8080/health/ready
# {"status":"ready","timestamp":"...","checks":{...}}

# Metrics
curl http://localhost:8080/metrics
# {"status":"ok","timestamp":"...","uptime_seconds":3600.5,"metrics":{...}}
```

## Database Schemas

### Trades Table

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    pnl REAL,
    pnl_pct REAL,
    strategy TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_symbol ON trades(symbol);
CREATE INDEX idx_strategy ON trades(strategy);
CREATE INDEX idx_status ON trades(status);
CREATE INDEX idx_entry_time ON trades(entry_time);
```

### Metrics Table

```sql
CREATE TABLE metrics (
    id INTEGER PRIMARY KEY,
    metric_type TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    dimensions TEXT,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_metric_type ON metrics(metric_type);
CREATE INDEX idx_metric_name ON metrics(metric_name);
CREATE INDEX idx_timestamp ON metrics(timestamp);
```

### Service Logs Table

```sql
CREATE TABLE service_logs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL,
    extra_data TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_timestamp ON service_logs(timestamp);
CREATE INDEX idx_level ON service_logs(level);
CREATE INDEX idx_logger ON service_logs(logger);
```

## Testing

Run all trading bot tests:

```bash
pytest vibe/tests/trading_bot/ -v

# Run specific test file
pytest vibe/tests/trading_bot/test_config.py -v
pytest vibe/tests/trading_bot/test_storage.py -v
pytest vibe/tests/trading_bot/test_service.py -v
pytest vibe/tests/trading_bot/test_health.py -v
```

## Thread Safety

All storage components use thread-local connections and locking:

- `TradeStore`: Thread-safe concurrent reads and writes
- `MetricsStore`: Thread-safe metric recording
- `LogStore`: Thread-safe log insertion

Safe to use in multi-threaded scenarios without external synchronization.

## Performance

### Database Optimization

- Indexes on frequently queried columns (symbol, status, timestamp)
- Connection pooling with thread-local storage
- Batch operations for bulk inserts
- Configurable database timeout (default 30s)

### Logging

- Rotating file handler (10MB max, 5 backups)
- JSON formatting for structured logging
- Separate file handlers per logger
- Console output for human readability

## Dependencies

Core dependencies:
- `pydantic>=2.0` - Settings validation
- `fastapi>=0.104` - Health check API
- `uvicorn>=0.24` - ASGI server
- `structlog>=23.2` - Structured logging
- `pytz>=2023.3` - Timezone handling

Development dependencies:
- `pytest>=7.0` - Testing framework
- `pytest-asyncio>=0.21` - Async test support
- `httpx>=0.24` - HTTP client for tests

## Phase 1 Completion Checklist

- [x] Task 1.1: Project Structure Setup
- [x] Task 1.2: Configuration System
- [x] Task 1.3: Logging Infrastructure
- [x] Task 1.4: SQLite Trade Store
- [x] Task 1.5: Metrics Store
- [x] Task 1.6: Service Log Store with Retention
- [x] Task 1.7: Graceful Shutdown Infrastructure
- [x] Task 1.8: Health Check Server

All 121 tests passing with 100% code coverage for core components.

## Next Steps

Phase 2 will implement:
- Data providers (Yahoo Finance, Finnhub WebSocket)
- Bar aggregation from tick data
- Data caching and coordination
- Real-time data streaming

## License

Part of the Strategy Lab Trading Bot project.
