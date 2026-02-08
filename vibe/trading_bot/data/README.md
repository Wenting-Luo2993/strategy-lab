# Trading Bot Data Layer (Phase 2)

This module provides the complete data layer for the trading bot, including real-time data streaming, historical data fetching, caching, and bar aggregation.

## Architecture

The data layer consists of 6 main components:

```
┌─────────────────────────────────────────────────────┐
│              DataManager (Coordinator)               │
│  Unified interface for historical and real-time data│
└──────────┬──────────────────────┬──────────────────┘
           │                      │
    ┌──────▼─────────┐   ┌────────▼───────┐
    │   DataCache    │   │   Providers    │
    │(Parquet TTL)   │   │ (Yahoo, etc)   │
    └────────────────┘   └────────────────┘
                              │
                    ┌─────────┴──────────┐
                    │                    │
              ┌─────▼─────┐      ┌──────▼──────────┐
              │   Yahoo   │      │FinnhubWebSocket│
              │ Provider  │      │   (Real-time)  │
              └───────────┘      └─────────────────┘
                    │                    │
                    └─────────┬──────────┘
                              │
                        ┌─────▼──────┐
                        │BarAggregator│
                        │(Tick→OHLCV) │
                        └──────────────┘
```

## Components

### 1. LiveDataProvider (Base Class)

Base class for all live data providers. Provides:
- **Rate Limiting**: Token bucket algorithm to respect API rate limits
- **Retry Logic**: Exponential backoff for transient failures
- **Health Tracking**: Monitor provider health status

**Key Files:**
- `vibe/trading_bot/data/providers/base.py`

**Usage:**
```python
from vibe.trading_bot.data.providers import LiveDataProvider

provider = YahooDataProvider(
    rate_limit=5.0,           # 5 requests per second
    max_retries=3,
    retry_backoff_base=1.0,   # Initial backoff: 1 second
    retry_backoff_multiplier=2.0  # Exponential: 1s, 2s, 4s, 8s, ...
)

# Check health
status = provider.get_health_status()
print(f"Provider health: {status['status']}")
```

### 2. YahooDataProvider

Fetches historical OHLCV data from Yahoo Finance via yfinance library.

**Features:**
- Configurable period and interval
- Rate limiting (5 req/sec default)
- Exponential backoff retry (up to 3 retries)
- Standardized DataFrame schema
- Graceful error handling

**Key Files:**
- `vibe/trading_bot/data/providers/yahoo.py`

**Usage:**
```python
from vibe.trading_bot.data.providers import YahooDataProvider
import asyncio

async def fetch_data():
    provider = YahooDataProvider(rate_limit=5)

    # Get last 30 days of 5-minute bars
    df = await provider.get_historical(
        symbol="AAPL",
        period="30d",
        interval="5m"
    )

    # Or with specific dates
    df = await provider.get_historical(
        symbol="AAPL",
        interval="1h",
        start_time=datetime(2024, 1, 1),
        end_time=datetime(2024, 1, 31)
    )

    print(df.head())
    # Returns DataFrame with columns:
    # timestamp, open, high, low, close, volume

asyncio.run(fetch_data())
```

**Supported Intervals:**
- `1m`, `5m`, `15m`, `30m`, `1h` (intraday)
- `1d`, `1wk`, `1mo` (other timeframes)

**Supported Periods:**
- `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `10y`, `ytd`, `max`

### 3. FinnhubWebSocketClient

Real-time trade streaming via Finnhub WebSocket API.

**Features:**
- Connection state machine (disconnected, connecting, connected, reconnecting)
- Exponential backoff reconnection (1s, 2s, 4s, 8s, 16s, max 5 attempts)
- Gap detection (if reconnect takes > 60 seconds)
- Event-driven architecture with callbacks
- Symbol subscription/unsubscription

**Key Files:**
- `vibe/trading_bot/data/providers/finnhub.py`

**Usage:**
```python
from vibe.trading_bot.data.providers import FinnhubWebSocketClient
import asyncio

async def stream_trades():
    client = FinnhubWebSocketClient(api_key="your_api_key")

    # Register event handlers
    async def on_connected():
        print("Connected to Finnhub")

    async def on_trade(trade):
        print(f"{trade['symbol']}: {trade['price']} x {trade['size']}")

    async def on_error(error):
        print(f"Error: {error['message']}")

    client.on_connected(on_connected)
    client.on_trade(on_trade)
    client.on_error(on_error)

    # Connect and subscribe
    await client.connect()
    await client.subscribe("AAPL")
    await client.subscribe("MSFT")

    # Keep running
    await asyncio.sleep(3600)

    await client.disconnect()

asyncio.run(stream_trades())
```

### 4. BarAggregator

Converts individual trades into OHLCV bars with configurable intervals.

**Features:**
- Configurable intervals: 1m, 5m, 15m, 30m, 1h, 4h, 1d
- Timezone-aware bar boundaries (aligns to interval)
- Bar completion callbacks
- Late trade handling (update previous bar or create synthetic)
- Trade and volume counting

**Key Files:**
- `vibe/trading_bot/data/aggregator.py`

**Usage:**
```python
from vibe.trading_bot.data.aggregator import BarAggregator
from datetime import datetime

# Create aggregator for 5-minute bars
aggregator = BarAggregator(
    bar_interval="5m",
    timezone="US/Eastern",
    late_trade_handling="previous"  # or "synthetic"
)

# Register bar completion callback
def on_bar_complete(bar):
    print(f"Bar closed: {bar['timestamp']} OHLCV: {bar}")

aggregator.on_bar_complete(on_bar_complete)

# Add trades
aggregator.add_trade(
    timestamp=datetime(2024, 1, 15, 9, 30, 0),
    price=100.0,
    size=100
)

# Continue adding trades...

# Flush current bar at end of trading day
final_bar = aggregator.flush()
```

**Bar Output:**
```python
{
    'timestamp': datetime(2024, 1, 15, 9, 30),
    'open': 100.0,
    'high': 101.5,
    'low': 99.5,
    'close': 101.0,
    'volume': 50000,
    'trade_count': 156
}
```

### 5. DataCache

Local Parquet-based cache for historical data with TTL invalidation.

**Features:**
- Parquet format storage (compact, efficient)
- TTL-based invalidation (default 1 hour)
- Cache metadata tracking (last update, row count, data range)
- Cache warming on startup
- Hit rate statistics

**Key Files:**
- `vibe/trading_bot/data/cache.py`

**Usage:**
```python
from vibe.trading_bot.data.cache import DataCache
from pathlib import Path
import pandas as pd

cache = DataCache(
    cache_dir=Path("/tmp/cache"),
    ttl_seconds=3600  # 1 hour
)

# Cache data
df = pd.DataFrame({...})
cache.put("AAPL", "5m", df)

# Retrieve cached data (returns None if expired or missing)
cached_df = cache.get("AAPL", "5m")
if cached_df is not None:
    print(f"Cache hit! {len(cached_df)} rows")

# Get cache statistics
stats = cache.stats()
print(f"Hit rate: {stats['hit_rate']:.1f}%")
print(f"Cached items: {stats['cached_items']}")
print(f"Total size: {stats['total_size_mb']:.2f} MB")

# Clear specific or all cache
cache.clear("AAPL", "5m")  # Clear AAPL 5m cache
cache.clear("AAPL")        # Clear all AAPL timeframes
cache.clear()              # Clear all cache

# Warm cache on startup
items = cache.warm_cache()  # Load all cached item metadata
```

### 6. DataManager (Coordinator)

Unified interface that orchestrates all data components.

**Features:**
- Cache-first strategy (check cache before provider)
- Seamless merge of historical and real-time data
- Data quality checks (gaps, OHLC relationships, NaNs)
- Quality metrics and event emission
- Automatic cache population

**Key Files:**
- `vibe/trading_bot/data/manager.py`

**Usage:**
```python
from vibe.trading_bot.data import DataManager, YahooDataProvider
from pathlib import Path
import asyncio

async def manage_data():
    # Initialize components
    provider = YahooDataProvider(rate_limit=5)
    manager = DataManager(
        provider=provider,
        cache_dir=Path("/tmp/cache"),
        cache_ttl_seconds=3600
    )

    # Register event handlers
    def on_gap(gap_info):
        print(f"Data gap detected: {gap_info['gap_count']} gaps")

    manager.on_data_gap(on_gap)

    # Get historical data (cache-aware)
    df = await manager.get_data(
        symbol="AAPL",
        timeframe="5m",
        days=7
    )
    print(f"Fetched {len(df)} bars")

    # Add real-time bars
    await manager.add_real_time_bar(
        symbol="AAPL",
        timeframe="5m",
        bar={
            "timestamp": datetime.now(),
            "open": 150.0,
            "high": 151.0,
            "low": 149.5,
            "close": 150.5,
            "volume": 1000000
        }
    )

    # Get merged data (historical + real-time)
    merged = await manager.get_merged_data("AAPL", "5m", days=7)
    print(f"Merged data: {len(merged)} rows")

    # Get metrics
    metrics = manager.get_metrics()
    print(f"Cache hit rate: {metrics['cache_hit_rate']:.1f}%")
    print(f"Provider health: {metrics['provider_health']['status']}")

asyncio.run(manage_data())
```

## Configuration

### Environment Variables

```bash
# Finnhub API Key
FINNHUB_API_KEY=pk_...

# Cache directory
DATA_CACHE_DIR=/var/cache/trading-bot
```

### Settings

Create a configuration file (e.g., `data_config.yaml`):

```yaml
providers:
  yahoo:
    rate_limit: 5.0      # requests per second
    max_retries: 3
    retry_backoff_base: 1.0
    retry_backoff_multiplier: 2.0

  finnhub:
    api_key: ${FINNHUB_API_KEY}
    max_reconnect_attempts: 5
    gap_detection_threshold: 60  # seconds

cache:
  ttl_seconds: 3600      # 1 hour
  directory: ${DATA_CACHE_DIR}

aggregator:
  bar_intervals:
    - 1m
    - 5m
    - 15m
    - 1h
  timezone: US/Eastern
```

## Testing

Run the comprehensive test suite:

```bash
# Run all Phase 2 tests
pytest vibe/tests/trading_bot/test_data.py -v

# Run specific test class
pytest vibe/tests/trading_bot/test_data.py::TestDataCache -v

# Run with coverage
pytest vibe/tests/trading_bot/test_data.py --cov=vibe.trading_bot.data --cov-report=html
```

**Test Coverage:**
- Task 2.1: LiveDataProvider base class (4 tests)
- Task 2.2: YahooDataProvider (7 tests)
- Task 2.3: FinnhubWebSocketClient (3 tests)
- Task 2.4: BarAggregator (7 tests)
- Task 2.5: DataCache (6 tests)
- Task 2.6: DataManager (6 tests)

**Total: 35 unit tests** covering:
- Rate limiting and token bucket algorithm
- Retry logic with exponential backoff
- Health status tracking
- Data provider implementation
- Bar aggregation with timezone handling
- Cache management with TTL
- Data quality checks and gap detection

## Performance Characteristics

### Rate Limiting
- Token bucket algorithm: O(1) per request
- Configurable rate: 1-100+ requests per second

### Caching
- Parquet storage: ~10-30% of raw CSV size
- Cache lookup: O(1) with metadata validation
- TTL check: O(1) per cache access

### Bar Aggregation
- Trade processing: O(1) per trade
- Bar completion: O(1) with deduplication
- Memory: ~1-10KB per active bar

### Data Quality
- Gap detection: O(n) on fetched data
- OHLC validation: O(n) on fetched data
- NaN check: O(n) on critical columns

## Dependencies

```
pandas>=2.0.0
yfinance>=0.2.0
websockets>=11.0
pyarrow>=12.0.0
pytz>=2023.0
```

## Error Handling

The data layer provides comprehensive error handling:

1. **Rate Limit Exceeded**: Automatic backoff and retry
2. **Network Errors**: Exponential backoff with max retries
3. **Invalid Data**: Clear validation errors with details
4. **Missing Columns**: Descriptive error messages
5. **Cache Failures**: Graceful fallback to provider
6. **Data Gaps**: Event emission for monitoring

## Monitoring

Track data layer health:

```python
# Provider health
provider_status = manager.get_metrics()['provider_health']
print(f"Provider status: {provider_status['status']}")
print(f"Recent error: {provider_status['last_error']}")
print(f"Error rate: {provider_status['error_rate']:.2%}")

# Cache performance
cache_stats = manager.get_metrics()['cache_stats']
print(f"Cache hit rate: {cache_stats['hit_rate']:.1f}%")
print(f"Cached items: {cache_stats['cached_items']}")

# Data quality
metrics = manager.get_metrics()
print(f"Data gaps detected: {metrics['data_gaps_detected']}")
```

## Next Steps (Phase 3)

The data layer is ready to support:
- Indicator calculation engine
- Strategy signal generation
- Multi-timeframe analysis
- Real-time backtesting

See `docs/trading-bot-mvp/implementation.md` for Phase 3 specifications.
