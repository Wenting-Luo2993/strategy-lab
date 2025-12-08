# Finnhub.io WebSocket Integration - Requirements & Todo List

## Overview

This document outlines the requirements and implementation plan for integrating Finnhub.io WebSocket API to support real-time market data streaming in the strategy-lab trading system.

**Goal**: Enable live trading capabilities by adding Finnhub as a real-time data feed alongside the existing Yahoo Finance loader (which remains valuable for backtesting historical data). Finnhub will provide WebSocket-based live market data for intraday trading strategies.

---

## 1. Background & Context

### Current Architecture

- **Data Layer**: `src/data/` with `DataLoader` base class and factory pattern
- **Existing Loaders**: Yahoo Finance (batch), Interactive Brokers (stub), Polygon (stub)
- **Cache Layer**: `CacheDataLoader` wraps loaders with caching/replay capabilities
- **Orchestrator**: `DarkTradingOrchestrator` coordinates data fetching, strategy signals, and order execution
- **Exchange**: `MockExchange` simulates order fills; designed to work with real exchanges
- **Strategies**: Time-based strategies (ORB) that process bar-by-bar updates

### Why Finnhub?

- **Real-time data**: WebSocket streaming for live trades and quotes
- **Free tier available**: Up to 60 API calls/minute, WebSocket access
- **Comprehensive coverage**: US stocks, forex, crypto
- **Simple API**: Clean REST + WebSocket interface
- **Low latency**: Sub-second updates for active trading

---

## 2. Requirements

### 2.1 Functional Requirements

#### FR-1: WebSocket Connection Management

- **FR-1.1**: Establish secure WebSocket connection to `wss://ws.finnhub.io`
- **FR-1.2**: Authenticate using API token from JSON config file (stored in `src/config/`)
- **FR-1.3**: Handle connection lifecycle (connect, disconnect, reconnect)
- **FR-1.4**: Implement exponential backoff for reconnection attempts
- **FR-1.5**: Gracefully handle connection drops and resume subscriptions

#### FR-2: Market Data Subscription

- **FR-2.1**: Subscribe to real-time trades for multiple tickers
- **FR-2.2**: Support subscription management (add/remove symbols dynamically)
- **FR-2.3**: Handle subscription confirmation messages
- **FR-2.4**: Process trade messages with timestamp, price, volume

#### FR-3: Data Normalization & Aggregation

- **FR-3.1**: Convert tick-level trades to OHLCV bars (1m, 5m, etc.)
- **FR-3.2**: Maintain running bar state for each subscribed ticker
- **FR-3.3**: Emit completed bars when time window closes
- **FR-3.4**: Ensure timestamp alignment with US/Eastern timezone
- **FR-3.5**: Handle pre-market, regular hours, and after-hours sessions

#### FR-4: Integration with Existing Architecture

- **FR-4.1**: Implement `FinnhubWebSocketLoader` inheriting from `DataLoader` base class
- **FR-4.2**: Register loader in `DataLoaderFactory` with `DataSource.FINNHUB` enum
- **FR-4.3**: Support both REST API (historical backfill) and WebSocket (live) modes
- **FR-4.4**: Design clear separation between live WebSocket subscription mode and replay/caching (consider separate adapter class if needed to keep `CacheDataLoader` focused)
- **FR-4.5**: Integrate with `DarkTradingOrchestrator` for live trading

#### FR-5: Error Handling & Resilience

- **FR-5.1**: Handle malformed WebSocket messages gracefully
- **FR-5.2**: Log all connection errors and data quality issues
- **FR-5.3**: Implement rate limiting awareness (Finnhub limits)
- **FR-5.4**: Provide fallback to REST API if WebSocket unavailable
- **FR-5.5**: Validate data quality (missing bars, price anomalies)

#### FR-6: Configuration & Credentials

- **FR-6.1**: Load Finnhub API key and settings from JSON config file (`src/config/finnhub_config.json`)
- **FR-6.2**: Support configuration for WebSocket URL, bar intervals, market hours, and reconnection settings
- **FR-6.3**: Allow override of WebSocket URL for testing environments
- **FR-6.4**: Securely store API credentials (add `finnhub_config.json` to `.gitignore`)

### 2.2 Non-Functional Requirements

#### NFR-1: Performance

- Process WebSocket messages with < 10ms latency
- Support simultaneous subscriptions for 50+ tickers
- Aggregate bars efficiently without blocking event loop

#### NFR-2: Reliability

- Proper cleanup on shutdown
- Basic error handling for malformed messages
- Graceful degradation if WebSocket connection fails (MVP: log error and alert user)
- Auto-reconnect capability (lower priority, implement after core MVP works)

#### NFR-3: Observability

- Structured logging for all WebSocket events
- Metrics for message throughput, latency, reconnection count
- Diagnostic endpoints for connection status

#### NFR-4: Testing

- Unit tests for message parsing and bar aggregation
- Integration tests with mock WebSocket server
- Replay tests using recorded WebSocket messages
- End-to-end tests with paper trading

---

## 3. Technical Design

### 3.1 Architecture Components

```
┌─────────────────────────────────────────────────────────┐
│              DarkTradingOrchestrator                    │
│  (Live mode: fetch_interval = 60s for 1m bars)         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│            CacheDataLoader (optional)                   │
│    (Can cache live bars for replay/debugging)           │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│         FinnhubWebSocketLoader : DataLoader             │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  WebSocketClient                                  │  │
│  │  - Connection management                          │  │
│  │  - Subscribe/unsubscribe                          │  │
│  │  - Message queue (async)                          │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  BarAggregator                                    │  │
│  │  - Tick → OHLCV conversion                        │  │
│  │  - Per-ticker bar state                           │  │
│  │  - Time window management                         │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  FinnhubRESTClient (fallback/backfill)           │  │
│  │  - Historical candle data                         │  │
│  │  - Quote snapshots                                │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                     │
                     ▼
         wss://ws.finnhub.io (Finnhub WebSocket)
```

### 3.2 WebSocket Client Architecture (Phase 2 Implementation)

Detailed internal architecture of the `FinnhubWebSocketClient`:

```
┌─────────────────────────────────────────────┐
│     FinnhubWebSocketClient                  │
│                                             │
│  ┌────────────────────────────────────┐    │
│  │  Connection (websockets lib)       │    │
│  │  - wss://ws.finnhub.io?token=...   │    │
│  │  - Auto ping/pong keep-alive        │    │
│  │  - Async connection management      │    │
│  └────────────────────────────────────┘    │
│                                             │
│  ┌────────────────────────────────────┐    │
│  │  _receive_loop() [async task]      │    │
│  │  - Continuous message reception     │    │
│  │  - JSON parsing & validation        │    │
│  │  - Message type routing             │    │
│  │  - Error handling & logging         │    │
│  └────────────────────────────────────┘    │
│                                             │
│  ┌────────────────────────────────────┐    │
│  │  Message Queue (asyncio.Queue)     │    │
│  │  - Thread-safe producer/consumer    │    │
│  │  - Max 1000 messages buffered       │    │
│  │  - Non-blocking get_message()       │    │
│  │  - Backpressure handling            │    │
│  └────────────────────────────────────┘    │
│                                             │
│  ┌────────────────────────────────────┐    │
│  │  Statistics & Monitoring           │    │
│  │  - Messages received/parsed/errors  │    │
│  │  - Connection uptime                │    │
│  │  - Subscribed symbols tracking      │    │
│  │  - Queue size metrics               │    │
│  └────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

**Key Features:**

- **Async/Await Pattern**: Non-blocking I/O for efficient WebSocket handling
- **Producer-Consumer**: Receive loop produces messages, aggregator consumes
- **Comprehensive Error Handling**: Connection failures, parse errors, timeouts
- **Statistics Tracking**: Full observability for debugging and monitoring

### 3.3 Live vs. Cache/Replay Separation

**Architecture Decision**: Keep live WebSocket subscription logic separate from cache/replay logic to maintain clean responsibilities.

**Options**:

- **Option A**: `FinnhubWebSocketLoader` for live mode only, use existing `CacheDataLoader` to wrap it for caching
- **Option B**: Create adapter/wrapper pattern specifically for live subscriptions
- **Rationale**: Python doesn't support partial classes (like C#), so use composition and clear class boundaries instead

**Responsibilities**:

- `FinnhubWebSocketLoader`: Live WebSocket connection, bar aggregation, real-time data feed
- `CacheDataLoader`: Historical data caching, parquet files, indicator calculation
- `DataReplayCacheDataLoader`: Time-based replay of cached data (existing, separate concern)

### 3.3 Data Flow

1. **Initialization**:

   - `FinnhubWebSocketLoader` instantiated with config from `finnhub_config.json`
   - WebSocket connection established with authentication
   - Subscribe to trades for all tickers

2. **Live Data Stream**:

   - WebSocket receives trade messages: `{"type": "trade", "data": [...]}`
   - Each trade: `{s: "AAPL", p: 150.25, t: 1638360000000, v: 100}`
   - `BarAggregator` updates current bar for ticker
   - On bar completion (time window closes), emit OHLCV row

3. **Data Fetch (Orchestrator)**:

   - Orchestrator calls `loader.fetch(ticker, timeframe="5m", ...)`
   - Returns DataFrame with latest completed bars (incremental)
   - Orchestrator applies indicators and generates signals

4. **Reconnection** (Post-MVP, Phase 8):
   - Detect connection loss via ping/pong or message timeout
   - Trigger exponential backoff reconnect
   - Resubscribe to all tickers
   - Backfill any missed bars via REST API (optional)

### 3.4 Data Structures

#### WebSocket Trade Message (Finnhub)

```json
{
  "type": "trade",
  "data": [
    {
      "s": "AAPL", // symbol
      "p": 150.25, // price
      "t": 1638360123000, // timestamp (ms)
      "v": 100, // volume
      "c": ["12", "37"] // conditions (optional)
    }
  ]
}
```

#### Aggregated Bar (Internal)

```python
{
    "ticker": "AAPL",
    "timestamp": pd.Timestamp("2025-11-30 09:30:00", tz="US/Eastern"),
    "open": 150.20,
    "high": 150.50,
    "low": 150.10,
    "close": 150.25,
    "volume": 15000
}
```

---

## 4. Implementation Todo List

### Phase 1: Configuration & Credentials Setup

- [x] **T1.1**: Create `src/config/finnhub_config.json` schema

  - Fields: `api_key`, `websocket_url`, `bar_interval`, `symbols`, `market_hours`
  - Add `finnhub_config.json` to `.gitignore` for security
  - Create `finnhub_config.example.json` as template

- [x] **T1.2**: Implement config loader utility

  - Function to read and validate JSON config
  - Error handling for missing/invalid config
  - Default values for optional fields

- [x] **T1.3**: Add dependencies to `requirements.txt`

  - `websockets>=12.0` (async WebSocket client)
  - `finnhub-python>=2.4.0` (REST API client)
  - `pytest-asyncio>=0.23.0` (async test support)

- [x] **T1.4**: Configure market hours

  - Regular hours: 09:30-16:00 ET
  - Pre-market: 04:00-09:30, After-hours: 16:00-20:00
  - Filter trades by session configuration

- [x] **T1.5**: **VALIDATION PENDING**: Manual config test
  - Create test script `scripts/test_finnhub_config.py`
  - Verify config loads correctly with valid API key
  - Test error handling for missing/invalid config

### Phase 2: Foundation (Core WebSocket Client)

- [ ] **T2.1**: Create `src/data/finnhub_websocket.py` module

  - Skeleton class structure
  - Import statements and type hints

- [ ] **T2.2**: Implement `FinnhubWebSocketClient` class

  - `async connect()`: Establish WebSocket connection
  - `async disconnect()`: Clean shutdown
  - `async subscribe(symbols: List[str])`: Subscribe to trades
  - `async unsubscribe(symbols: List[str])`: Unsubscribe
  - `async _receive_loop()`: Message reception loop
  - `_parse_message(msg: str)`: Parse JSON messages

- [ ] **T2.3**: Add authentication logic

  - Read API key from JSON config file
  - Construct auth URL: `wss://ws.finnhub.io?token={api_key}`
  - Handle auth failure gracefully

- [ ] **T2.4**: Implement message queue

  - Use `asyncio.Queue` for thread-safe message passing
  - Separate producer (WebSocket) and consumer (aggregator) threads

- [ ] **T2.5**: **VALIDATION**: Connection test script
  - Create `scripts/test_finnhub_connection.py`
  - Connect to Finnhub WebSocket
  - Subscribe to 1 ticker (e.g., AAPL)
  - Print raw trade messages for 30 seconds
  - Run manually during market hours to verify connection

### Phase 3: Bar Aggregation Engine

- [ ] **T3.1**: Create `BarAggregator` class in `finnhub_websocket.py`

  - `add_trade(trade: dict)`: Process incoming trade
  - `get_completed_bars()`: Return finished bars
  - `_finalize_bar(ticker: str)`: Complete current bar

- [ ] **T3.2**: Implement time window logic

  - Parse trade timestamp to US/Eastern timezone
  - Determine bar period (e.g., 5-minute window: 09:30-09:35)
  - Detect bar boundary crossing

- [ ] **T3.3**: Maintain per-ticker bar state

  - Dictionary: `{ticker: current_bar_dict}`
  - Initialize bar on first trade in window
  - Update OHLCV: open (first), high (max), low (min), close (last), volume (sum)

- [ ] **T3.4**: Handle edge cases

  - First bar of the day (no previous close)
  - Gaps in data (missing trades for a window)
  - After-hours trades (filter or separate flag)

- [ ] **T3.5**: **VALIDATION**: Bar aggregation test script
  - Create `scripts/test_finnhub_aggregation.py`
  - Connect and subscribe to 1-2 tickers
  - Run for 10-15 minutes during market hours
  - Print completed bars with OHLCV values
  - Manually verify bars align with expected timeframes
  - Compare against Yahoo Finance data for same period

### Phase 4: DataLoader Integration

- [ ] **T4.1**: Extend `DataSource` enum in `src/data/base.py`

  - Add `FINNHUB = "finnhub"`

- [ ] **T4.2**: Implement `FinnhubWebSocketLoader(DataLoader)`

  - Constructor: Initialize WebSocket client and aggregator
  - `fetch(symbol, start, end, timeframe)`: Return DataFrame
  - Handle both live (WebSocket) and historical (REST) modes

- [ ] **T4.3**: Register loader with decorator

  - `@register_loader("finnhub")` in `finnhub_websocket.py`

- [ ] **T4.4**: Implement `fetch()` method for live mode

  - If `end` is None or recent: return latest bars from aggregator
  - If historical range: call Finnhub REST API for candles
  - Merge live and historical data if needed

- [ ] **T4.5**: Add REST API fallback client

  - Use `finnhub-python` library for `/stock/candle` endpoint
  - Convert response to DataFrame matching `fetch()` schema
  - Handle rate limiting (60 calls/min on free tier)

- [ ] **T4.6**: **VALIDATION**: DataLoader fetch test
  - Create unit tests in `tests/data/test_finnhub_loader.py`
  - Test historical fetch (REST API)
  - Test live fetch (returns recent bars)
  - Verify DataFrame schema matches other loaders
  - **Manual test**: Run `scripts/test_finnhub_fetch.py` during market hours

### Phase 5: Live Data vs. Replay Architecture

- [ ] **T5.1**: Design separation of concerns

  - **Option A**: Keep `CacheDataLoader` simple, create `LiveDataAdapter` wrapper for WebSocket mode
  - **Option B**: Create `FinnhubLiveLoader` (WebSocket only) separate from `FinnhubCacheLoader`
  - **Recommendation**: Separate classes to avoid mixing live subscription logic with cache/replay logic
  - Note: Python doesn't have partial classes, use composition/inheritance for separation

- [ ] **T5.2**: Implement adapter pattern (if Option A)

  - `LiveDataAdapter` wraps `FinnhubWebSocketLoader`
  - Handles subscription lifecycle independently from caching
  - Compatible with orchestrator's fetch pattern

- [ ] **T5.3**: Document architecture decision

  - Update `src/data/README.md` with class responsibilities
  - Explain when to use `FinnhubWebSocketLoader` (live) vs `CacheDataLoader` (replay)
  - Provide usage examples for both modes

- [ ] **T5.4**: **VALIDATION**: Architecture review
  - Review class design with stakeholders
  - Verify separation of concerns is clear
  - Confirm no bloat in `CacheDataLoader` class

### Phase 6: Logging & Observability

- [ ] **T6.1**: Add structured logging

  - Use existing `src.utils.logger.get_logger()`
  - Log levels: INFO (connections), DEBUG (messages), ERROR (failures)
  - Include ticker, timestamp, latency in log context

- [ ] **T6.2**: Log connection lifecycle events

  - Connected, disconnected, subscribed, unsubscribed
  - Basic error logging (connection failures, malformed messages)

- [ ] **T6.3**: Log data quality metrics

  - Bars aggregated per ticker
  - Gaps detected (missing bars)
  - Message processing latency

- [ ] **T6.4**: Add diagnostic utilities

  - `get_connection_status()`: Return connected, symbols, uptime
  - `get_statistics()`: Message count, bar count, error count

- [ ] **T6.5**: **VALIDATION**: Log monitoring test
  - Run live connection for 30+ minutes
  - Review logs for proper formatting and useful information
  - Verify error scenarios are logged correctly
  - Check log file rotation and size

### Phase 7: Testing

- [ ] **T7.1**: Unit tests for `BarAggregator`

  - `tests/data/test_bar_aggregator.py`
  - Test cases: single trade, multiple trades, bar completion, timezone handling

- [ ] **T7.2**: Unit tests for message parsing

  - Valid trade messages
  - Malformed JSON
  - Unknown message types

- [ ] **T7.3**: Integration test with mock WebSocket server

  - Use `pytest-asyncio` for async tests
  - Mock WebSocket server sends canned trade messages
  - Verify bars are aggregated correctly

- [ ] **T7.4**: Replay test using recorded messages

  - Record real Finnhub WebSocket messages to file
  - Replay and verify output matches expected
  - Store in `tests/__scenarios__/finnhub_messages.json`

- [ ] **T7.5**: End-to-end test with `DarkTradingOrchestrator`

  - Run orchestrator in paper trading mode with Finnhub loader
  - Verify signals are generated from live data
  - Check orders are placed correctly in MockExchange

- [ ] **T7.6**: Add pytest markers for live tests

  - `@pytest.mark.live`: Requires real API key and connection
  - Skip by default, run explicitly with `pytest -m live`

- [ ] **T7.7**: **VALIDATION**: Run full test suite
  - Execute `pytest tests/data/test_finnhub*.py`
  - Verify 80%+ code coverage for new modules
  - Run live tests manually: `pytest -m live` during market hours
  - Document any test limitations or known issues

### Phase 8: Reconnection & Resilience (Post-MVP)

- [ ] **T8.1**: Implement ping/pong heartbeat

  - Send periodic pings to keep connection alive
  - Detect timeout if no pong received

- [ ] **T8.2**: Add reconnection logic with exponential backoff

  - Initial retry: 1s, then 2s, 4s, 8s, max 60s
  - Track reconnection attempts in logs
  - Reset backoff on successful connection

- [ ] **T8.3**: Resubscribe on reconnect

  - Store active subscriptions in state
  - Automatically resubscribe after reconnection

- [ ] **T8.4**: Handle connection errors gracefully

  - Catch `websockets.exceptions.ConnectionClosed`
  - Log error details and trigger reconnect
  - Expose connection status via property

- [ ] **T8.5**: Implement graceful shutdown

  - Close WebSocket on SIGINT/SIGTERM
  - Flush pending bars before exit
  - Unsubscribe from all symbols

- [ ] **T8.6**: **VALIDATION**: Resilience testing
  - Simulate network disconnection during live trading hours
  - Verify reconnection logic triggers automatically
  - Confirm no data loss or duplicate bars
  - Test graceful shutdown with Ctrl+C

### Phase 9: Orchestrator Integration

- [ ] **T9.1**: Update `DarkTradingOrchestrator` for live mode

  - Detect Finnhub loader and adjust fetch logic
  - Set `fetch_interval` based on bar timeframe (e.g., 60s for 1m bars)
  - Handle incremental data (only new bars since last fetch)

- [ ] **T9.2**: Add live mode flag to orchestrator config

  - `live_mode: bool` (default False)
  - If True: use WebSocket loader, adjust timing

- [ ] **T9.3**: Synchronize orchestrator loop with bar completion

  - Wait for bar aggregator to emit completed bar
  - Avoid fetching mid-bar (incomplete data)

- [ ] **T9.4**: Handle market hours in orchestrator

  - Check if market is open before starting
  - Auto-stop after market close (configurable)

- [ ] **T9.5**: Test with example strategies

  - Run `ORBStrategy` with live Finnhub data
  - Verify entry/exit signals match expectations
  - Compare with backtested results

- [ ] **T9.6**: **VALIDATION**: Live orchestrator test script
  - Create `scripts/test_finnhub_orchestrator.py`
  - Run full orchestrator with 2-3 tickers during market hours
  - Monitor for 1+ hour with real strategy
  - Verify signals, order placement, and position management
  - Compare live results with expected behavior from backtests

### Phase 10: Documentation

- [ ] **T10.1**: Update `src/data/README.md`

  - Add Finnhub WebSocket loader documentation
  - Usage examples for live and historical modes
  - Architecture diagram showing live vs. cache/replay separation

- [ ] **T10.2**: Create setup guide

  - How to get Finnhub API key (free tier)
  - JSON config file setup (`finnhub_config.json`)
  - First-time setup steps and troubleshooting

- [ ] **T10.3**: Add example script

  - `example_finnhub_live.py`: Minimal live trading example
  - Subscribe to 1-2 tickers, run for 5 minutes, print bars
  - Include comments explaining each step

- [ ] **T10.4**: Update main README.md

  - Mention Finnhub integration in features
  - Highlight Yahoo Finance for backtesting, Finnhub for live trading
  - Link to setup guide

- [ ] **T10.5**: Document limitations

  - Free tier: 60 API calls/min, WebSocket limit unclear
  - Latency considerations (best effort, not HFT)
  - Data quality (gaps, delayed trades)
  - MVP: Basic error handling, reconnection in Phase 8

- [ ] **T10.6**: **VALIDATION**: Documentation review
  - Have another developer follow setup guide from scratch
  - Verify all example scripts run successfully
  - Collect feedback on clarity and completeness
  - Update based on feedback

### Phase 11: Optimization & Polish (Optional)

- [ ] **T11.1**: Optimize message parsing

  - Use `orjson` instead of `json` for faster parsing
  - Profile hot paths with `cProfile`

- [ ] **T11.2**: Add connection pooling (if needed)

  - Multiple WebSocket connections for symbol groups
  - Load balancing across connections

- [ ] **T11.3**: Implement data validation

  - Check for price anomalies (e.g., 100x spike)
  - Flag suspicious bars for manual review
  - Optionally reject outliers

- [ ] **T11.4**: Add caching for REST API calls

  - Cache historical candles to reduce API usage
  - Integrate with existing `CacheDataLoader`

- [ ] **T11.5**: Performance benchmarking

  - Measure message throughput (msgs/sec)
  - Measure bar aggregation latency
  - Measure memory usage with 50+ tickers

- [ ] **T11.6**: **VALIDATION**: Performance testing
  - Run benchmarks with 10, 25, 50 tickers
  - Measure CPU and memory usage
  - Identify and address bottlenecks
  - Document performance characteristics

---

## 5. Dependencies

### New Python Packages

Add to `python/requirements.txt`:

```txt
websockets>=12.0        # Async WebSocket client
finnhub-python>=2.4.0   # Official Finnhub REST API client (optional but recommended)
pytest-asyncio>=0.23.0  # Async test support
orjson>=3.9.0           # Fast JSON parsing (optional optimization - Phase 11)
```

### External Services

- **Finnhub Account**: Sign up at https://finnhub.io/register
  - Free tier: 60 API calls/minute
  - WebSocket: Real-time trades (US stocks)
  - API key required

### Configuration Files

Create `src/config/finnhub_config.json`:

```json
{
  "api_key": "your_finnhub_api_key_here",
  "websocket_url": "wss://ws.finnhub.io",
  "bar_interval": "5m",
  "symbols": ["AAPL", "MSFT", "NVDA"],
  "market_hours": {
    "timezone": "America/New_York",
    "pre_market_start": "04:00",
    "regular_start": "09:30",
    "regular_end": "16:00",
    "after_hours_end": "20:00"
  },
  "filter_after_hours": false
}
```

**Security Note**: Add `finnhub_config.json` to `.gitignore`. Create `finnhub_config.example.json` as a template for version control.

---

## 6. Testing Strategy

### 6.1 Unit Tests

- BarAggregator logic (OHLCV calculation)
- Message parsing (valid/invalid JSON)
- Timezone conversions
- Bar boundary detection

### 6.2 Integration Tests

- Mock WebSocket server sends canned messages
- Verify correct subscription/unsubscription
- Reconnection logic with simulated disconnects

### 6.3 Replay Tests

- Record real WebSocket messages to fixture files
- Replay and assert output matches baseline
- Use existing snapshot testing framework

### 6.4 Live Tests (Manual/CI Optional)

- Require `FINNHUB_API_KEY` environment variable
- Connect to real Finnhub WebSocket
- Subscribe to 1-2 tickers for 1 minute
- Verify bars are aggregated correctly
- Mark with `@pytest.mark.live` (skip by default)

### 6.5 End-to-End Tests

- Run full orchestrator with Finnhub in paper trading mode
- Verify strategy signals and order placement
- Compare results with backtested baseline (sanity check)

---

## 7. Risks & Mitigations

### Risk 1: Rate Limiting (Free Tier)

- **Impact**: 60 API calls/min may not be enough for REST fallback
- **Mitigation**: Prioritize WebSocket, cache REST data aggressively

### Risk 2: WebSocket Disconnections

- **Impact**: Data loss during connection drop
- **Mitigation**: Exponential backoff, REST API backfill for gaps

### Risk 3: Data Quality Issues

- **Impact**: Missing trades, delayed bars, incorrect prices
- **Mitigation**: Validate data, log anomalies, compare with secondary source

### Risk 4: Latency vs. Bar Aggregation

- **Impact**: Bar may not be "complete" until several seconds after period ends
- **Mitigation**: Add configurable delay (e.g., wait 5s after bar close to ensure all trades received)

### Risk 5: Timezone Confusion

- **Impact**: Bars aggregated in wrong timezone, misaligned with strategy
- **Mitigation**: Strict use of `US/Eastern`, test thoroughly with market open/close

### Risk 6: Testing Without Live Access

- **Impact**: Cannot test live behavior without API key
- **Mitigation**: Use recorded messages for replay tests, mock WebSocket server for CI

---

## 8. Success Criteria

1. ✅ WebSocket connection established and maintained for 1+ hour without manual intervention
2. ✅ Bars aggregated correctly (OHLCV values match expected from trades)
3. ✅ Reconnection works automatically after simulated disconnect
4. ✅ Orchestrator generates strategy signals from live data
5. ✅ Orders placed in MockExchange based on live signals
6. ✅ 95%+ test coverage for core WebSocket and aggregation logic
7. ✅ Documentation complete with setup guide and examples
8. ✅ Performance: Process 100+ messages/sec with <10ms latency per message

---

## 9. Timeline Estimate

| Phase                             | Effort (Hours) | Description                          |
| --------------------------------- | -------------- | ------------------------------------ |
| Phase 1: Configuration Setup      | 2-4            | JSON config, credentials, validation |
| Phase 2: WebSocket Client         | 8-12           | Connection, auth, messages           |
| Phase 3: Bar Aggregation          | 6-10           | OHLCV aggregation, validation        |
| Phase 4: DataLoader Integration   | 4-6            | Factory pattern, fetch logic         |
| Phase 5: Architecture Design      | 3-5            | Live vs replay separation            |
| Phase 6: Logging & Observability  | 3-5            | Structured logging, diagnostics      |
| Phase 7: Testing                  | 10-15          | Unit, integration, live tests        |
| Phase 8: Reconnection (Post-MVP)  | 6-8            | Error handling, resilience           |
| Phase 9: Orchestrator Integration | 4-6            | Live mode, strategy testing          |
| Phase 10: Documentation           | 4-6            | Guides, examples, validation         |
| Phase 11: Optimization (Optional) | 4-8            | Performance tuning                   |
| **MVP Total (Phases 1-7, 9-10)**  | **44-69**      | ~1-1.5 weeks full-time               |
| **Complete Total (All Phases)**   | **54-85**      | ~1.5-2 weeks full-time               |

**Note**: MVP excludes advanced reconnection logic (Phase 8) and optimization (Phase 11), focusing on core functionality with basic error handling. These can be added after MVP validation.

---

## 10. Future Enhancements (Post-MVP)

- [ ] Support Finnhub quote WebSocket (bid/ask spreads)
- [ ] Add Level 2 order book data (if available)
- [ ] Implement multi-exchange aggregation (Finnhub + others)
- [ ] Add data quality dashboard (Grafana/Plotly)
- [ ] Support crypto and forex via Finnhub
- [ ] Implement smart order routing based on data latency
- [ ] Add machine learning for data quality scoring

---

## 11. References

- **Finnhub API Docs**: https://finnhub.io/docs/api
- **Finnhub WebSocket Docs**: https://finnhub.io/docs/api/websocket-trades
- **Python WebSocket Library**: https://websockets.readthedocs.io/
- **Asyncio Guide**: https://docs.python.org/3/library/asyncio.html

---

## Appendix A: Example WebSocket Message Flow

```
→ CONNECT: wss://ws.finnhub.io?token=YOUR_API_KEY
← {"type":"ping"}  (heartbeat from server)
→ {"type":"pong"}  (heartbeat response)

→ {"type":"subscribe","symbol":"AAPL"}
← {"type":"subscription","symbol":"AAPL","status":"subscribed"}

← {
    "type": "trade",
    "data": [
      {"s":"AAPL","p":150.25,"t":1638360123456,"v":100,"c":["12"]},
      {"s":"AAPL","p":150.26,"t":1638360123789,"v":50,"c":["12"]}
    ]
  }

→ {"type":"unsubscribe","symbol":"AAPL"}
← {"type":"subscription","symbol":"AAPL","status":"unsubscribed"}

→ DISCONNECT
```

---

## Appendix B: Configuration Examples

### `src/config/finnhub_config.json` (Full Configuration)

```json
{
  "api_key": "your_finnhub_api_key_here",
  "websocket_url": "wss://ws.finnhub.io",
  "bar_interval": "5m",
  "bar_delay_seconds": 5,
  "symbols": ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"],
  "market_hours": {
    "timezone": "America/New_York",
    "pre_market_start": "04:00",
    "regular_start": "09:30",
    "regular_end": "16:00",
    "after_hours_end": "20:00"
  },
  "filter_after_hours": false,
  "reconnect": {
    "enabled": true,
    "max_attempts": 10,
    "initial_backoff_seconds": 1,
    "max_backoff_seconds": 60
  },
  "rest_api": {
    "enabled": true,
    "cache_ttl_seconds": 3600
  }
}
```

### `src/config/finnhub_config.example.json` (Template for Git)

```json
{
  "api_key": "REPLACE_WITH_YOUR_API_KEY",
  "websocket_url": "wss://ws.finnhub.io",
  "bar_interval": "5m",
  "symbols": ["AAPL", "MSFT"],
  "market_hours": {
    "timezone": "America/New_York",
    "regular_start": "09:30",
    "regular_end": "16:00"
  }
}
```

### `.gitignore` Addition

```
# Finnhub credentials
src/config/finnhub_config.json
```

---

**Document Version**: 1.0
**Last Updated**: 2025-11-30
**Author**: GitHub Copilot
**Status**: Draft - Ready for Review
