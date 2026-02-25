# Finnhub WebSocket Connection Fixes

**Date**: February 24, 2026
**Status**: ✅ Resolved

## Problem Summary

The trading bot was experiencing persistent WebSocket disconnect/reconnect issues with Finnhub during market hours. Despite successful warm-up completion, the WebSocket would disconnect immediately when the market opened, preventing real-time data flow.

## Root Cause Analysis

### Primary Issue: Duplicate Connection Attempts

The bot was attempting to connect to Finnhub **twice**:

1. **Initialization Step 8** (orchestrator.py lines 296-305): Legacy code that connected immediately if market was already open
2. **Warm-up Step 2** (orchestrator.py line 774): New provider system that was supposed to handle all connections

### Impact

This caused a rapid reconnect loop that triggered Finnhub's rate limiting:

- **Finnhub Free Tier Limits**: 60 requests/minute, 1 WebSocket connection max
- **Result**: HTTP 429 errors → forced disconnects → reconnect attempts → more rate limiting

**Observed Behavior**:
```
Error receiving message: no close frame received or sent
WebSocket disconnected, attempting reconnection
⚠️  RATE LIMITED (HTTP 429) - Finnhub free tier quota exceeded
```

### Secondary Issues Discovered

1. **Mid-Market Start Problem**: Bot didn't connect if started after warm-up window (9:25-9:30 AM)
2. **False Ping Warnings**: Overly aggressive timeout (45s) vs actual ping frequency (~60s)
3. **Missing Provider Files**: `.gitignore` excluded source code in `data/providers/`
4. **Docker Healthcheck**: No health endpoint, container showed "unhealthy"

---

## Fixes Applied

### 1. Removed Duplicate Connection Code ✅

**File**: `vibe/trading_bot/core/orchestrator.py`

**Removed** (lines 296-305):
```python
# 8. Connect Finnhub websocket if market is already open
market_is_open = self.market_scheduler.is_market_open()
if self.finnhub_ws and market_is_open:
    try:
        await self._connect_finnhub_websocket()
        self.logger.info("Finnhub WebSocket connected (market already open)")
```

**Replaced with**:
```python
# 8. Provider connection now handled in warm-up phase (Step 2)
# Removed old duplicate connection code that was causing rate limiting
```

**Impact**: Eliminated duplicate connection attempts and rate limiting

---

### 2. Auto-Connect When Bot Starts Mid-Market ✅

**File**: `vibe/trading_bot/core/orchestrator.py` (lines 883-909)

**Problem**:
- Warm-up phase only runs 9:25-9:30 AM
- If bot starts at 1:00 PM, it skips warm-up and never connects to Finnhub
- Trading cycle runs without real-time data

**Solution Added**:
```python
elif self.market_scheduler.is_market_open():
    # If bot started during market hours (after warm-up), ensure provider is connected
    if self.primary_provider and not self.primary_provider.connected:
        self.logger.info("Bot started during market hours - connecting to real-time provider...")
        try:
            await self.primary_provider.connect()
            if self.primary_provider.connected:
                self.logger.info(f"   [OK] Connected to {self.primary_provider.provider_name}")
                set_health_state(websocket_connected=True, recent_heartbeat=True)

                # Subscribe if WebSocket
                if isinstance(self.primary_provider, WebSocketDataProvider):
                    for symbol in self.config.trading.symbols:
                        await self.primary_provider.subscribe(symbol)
                    self.logger.info(f"   [OK] Subscribed to {len(self.config.trading.symbols)} symbols")
```

**Impact**: Bot now connects to Finnhub regardless of start time during market hours

---

### 3. Adjusted Ping Timeout Threshold ✅

**File**: `vibe/trading_bot/data/providers/finnhub.py` (lines 458-468)

**Before**:
```python
# Finnhub typically sends pings every 20-30 seconds
# If no ping for > 45s, connection might be stale
if time_since_last_ping > 45:
    logger.warning(
        f"⚠️  No ping from Finnhub for {time_since_last_ping:.0f}s "
        f"(expected every ~20-30s). Connection may be stale."
    )
```

**After**:
```python
# Finnhub typically sends pings every 60 seconds
# If no ping for > 90s, connection might be stale
if time_since_last_ping > 90:
    logger.warning(
        f"⚠️  No ping from Finnhub for {time_since_last_ping:.0f}s "
        f"(expected every ~60s). Connection may be stale."
    )
```

**Rationale**:
- Testing showed Finnhub sends pings every ~60 seconds, not 20-30s
- Adjusted threshold to 90s (60s + 30s buffer) to prevent false alarms
- Connection remained stable with data flowing despite longer ping intervals

**Impact**: Eliminated false warning spam while maintaining genuine stale connection detection

---

### 4. Added Health API Server ✅

**File**: `vibe/trading_bot/api/health.py`

**Added Endpoint**:
```python
@app.get("/api/health", response_model=LiveResponse)
async def api_health() -> LiveResponse:
    """Simple health check for Docker healthcheck."""
    state = get_health_state()
    if not state.is_alive:
        raise HTTPException(status_code=503, detail="Process not alive")
    return LiveResponse(
        status="alive",
        timestamp=datetime.utcnow().isoformat(),
    )
```

**Started Server** in `orchestrator.py`:
```python
# Start health API server for Docker healthcheck
self._health_server_task = await start_health_server_task(
    host="0.0.0.0",
    port=self.config.health_check_port
)
self.logger.info(f"Health API server started on port {self.config.health_check_port}")

# Mark bot as alive for healthcheck
set_health_state(is_alive=True)
```

**Docker Compose** (`docker-compose.yml`):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/api/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

**Impact**: Docker container now shows "healthy" status instead of "unhealthy"

---

### 5. Fixed .gitignore and Missing Files ✅

**File**: `.gitignore`

**Problem**:
- Pattern `vibe/trading_bot/data/` excluded source code in `data/providers/`
- Missing files caused `ModuleNotFoundError` on deployment

**Before**:
```gitignore
# Trading bot data (MVP)
data/
*.db
vibe/trading-bot/data/
vibe/backtester/data/
```

**After**:
```gitignore
# Trading bot data (MVP)
data/
*.db
vibe/trading-bot/data/
vibe/trading_bot/data/*.db
vibe/trading_bot/data/cache/
vibe/backtester/data/

# EXCEPTION: Source code in data/ folders must not be ignored
!vibe/common/data/
!vibe/trading_bot/data/
!vibe/trading_bot/data/**/*.py
```

**Added Missing Files**:
- `vibe/trading_bot/data/providers/types.py` (247 lines)
- `vibe/trading_bot/data/providers/factory.py` (145 lines)
- `vibe/trading_bot/data/providers/polygon.py` (370 lines)

**Impact**: All provider source files now tracked in git and deploy correctly

---

### 6. Additional Code Fixes ✅

#### Fixed connect() Return Value Checks

**Problem**: Code was checking `if success:` but `connect()` returns `None`, not a boolean

**Before**:
```python
success = await self.primary_provider.connect()
if success:  # BUG: connect() returns None, not bool
```

**After**:
```python
await self.primary_provider.connect()
if self.primary_provider.connected:
    self.logger.info(f"   [OK] Connected to {self.primary_provider.provider_name}")
```

#### Fixed Missing Variable

**Problem**: `market_is_open` used but not defined after removing duplicate connection code

**Fix**: Added variable definition:
```python
market_is_open = self.market_scheduler.is_market_open()
```

#### Removed Emoji Characters

**Problem**: Emoji characters (✅) caused Windows console encoding errors

**Fix**: Replaced emojis with `[OK]` text marker

#### Consolidated Multi-line Logs

**File**: `vibe/trading_bot/data/manager.py`

**Before** (6 separate log lines):
```python
logger.warning("[NO FALLBACK] GOOGL (5m): Cache is stale...")
logger.warning("   This is expected during market hours...")
logger.warning("   Returning stale cached data...")
logger.warning("   Waiting for real-time Finnhub bars...")
logger.info("   Returning 624 rows...")
```

**After** (1 line):
```python
logger.warning(
    f"[NO FALLBACK] {symbol} ({timeframe}): Cache is stale but yfinance fallback disabled "
    f"(expected during market hours). Returning {len(existing_cached_df)} rows of stale "
    f"cached data, waiting for real-time Finnhub bars..."
)
```

**Impact**: Cleaner logs, easier to read and troubleshoot

---

## Before vs After

### Before Fixes

**Symptoms**:
- ❌ Disconnects every ~60 seconds
- ❌ HTTP 429 rate limiting errors
- ❌ No real-time bars flowing
- ❌ Docker status: "unhealthy"
- ❌ False "No ping" warnings every 45-60s
- ❌ Bot wouldn't connect if started mid-market

**Logs**:
```
WebSocket disconnected, attempting reconnection
⚠️  RATE LIMITED (HTTP 429) - Finnhub free tier quota exceeded
⚠️  No ping from Finnhub for 47s (expected every ~20-30s). Connection may be stale.
⚠️  No ping from Finnhub for 57s (expected every ~20-30s). Connection may be stale.
ModuleNotFoundError: No module named 'vibe.trading_bot.data.providers.types'
```

### After Fixes

**Results**:
- ✅ Stable WebSocket connection (0 disconnects)
- ✅ Real-time bars flowing every 5 minutes
- ✅ Docker status: "healthy"
- ✅ No false warnings
- ✅ Auto-connects regardless of start time
- ✅ Clean, consolidated logs

**Logs**:
```
Bot started during market hours - connecting to real-time provider...
   [OK] Connected to Finnhub
   [OK] Subscribed to 3 symbols
[REALTIME BAR] AAPL: timestamp=2026-02-24 14:00:00-05:00, O=271.98, V=12128
[REALTIME BAR] GOOGL: timestamp=2026-02-24 14:00:00-05:00, O=310.52, V=2004
[REALTIME BAR] MSFT: timestamp=2026-02-24 14:00:00-05:00, O=387.55, V=10244
Heartbeat: overall=healthy, uptime=301s, errors=0
GET /api/health HTTP/1.1" 200 OK
```

---

## Testing & Validation

### Local Testing

1. **5-Minute Stability Test** (`test_finnhub_stability.py`):
   - Duration: 300 seconds
   - Pings received: 5
   - Pongs sent: 5
   - Disconnects: 0
   - Result: ✅ SUCCESS

2. **Health Endpoint Test**:
   ```bash
   $ curl http://localhost:8080/api/health
   {"status":"alive","timestamp":"2026-02-24T18:24:27.511086"}
   ```

### Production Testing (Oracle Cloud)

1. **Initial Connection**: ✅ Connected at 18:57:48
2. **Real-time Data Flow**: ✅ Bars received at 19:00:06, 19:05:02
3. **Stability**: ✅ No disconnects over multiple hours
4. **Health Status**: ✅ Container shows "healthy"
5. **Mid-Market Restart**: ✅ Auto-connected after restart at 19:11:01

---

## Commits

1. **7067167** - `feat: add health API server and fix Docker healthcheck`
2. **19c9391** - `fix: add missing types.py file to repository`
3. **28d2968** - `fix: add missing provider files and update .gitignore`
4. **a673f94** - `fix: connect to Finnhub when bot starts during market hours`
5. **c2d89fc** - `fix: adjust Finnhub ping timeout to match actual behavior`

---

## Key Learnings

1. **Rate Limiting is Serious**: Free tier limits are strict - even 2 connections can trigger HTTP 429
2. **Warm-up Window is Narrow**: 9:25-9:30 AM is only 5 minutes - bots started outside this window need fallback logic
3. **Ping Frequency Varies**: Don't assume WebSocket ping intervals - measure and adjust thresholds accordingly
4. **Test Realistically**: 5-minute stability test caught issues that quick tests missed
5. **.gitignore Can Hide Bugs**: Overly broad patterns can exclude critical source files

---

## Operational Notes

### Monitoring

Watch for these indicators of healthy operation:

✅ **Connection Logs**:
```
Bot started during market hours - connecting to real-time provider...
   [OK] Connected to Finnhub
   [OK] Subscribed to 3 symbols
```

✅ **Real-time Bar Flow** (every 5 minutes):
```
[REALTIME BAR] AAPL: timestamp=2026-02-24 14:00:00-05:00, O=271.98, V=12128
```

✅ **Heartbeat** (every 5 minutes):
```
Heartbeat: overall=healthy, uptime=301s, errors=0
```

✅ **Docker Health**:
```bash
$ docker ps
CONTAINER    STATUS
trading-bot  Up 10 minutes (healthy)
```

### Troubleshooting

If connection issues recur:

1. **Check for Rate Limiting**:
   ```bash
   docker logs trading-bot | grep "RATE LIMITED"
   ```

2. **Check Connection State**:
   ```bash
   docker logs trading-bot | grep -E "Connected to|Disconnected from"
   ```

3. **Verify Provider is Set**:
   ```bash
   docker logs trading-bot | grep "primary_provider"
   ```

4. **Check Health Endpoint**:
   ```bash
   curl http://localhost:8080/api/health
   ```

### Restarting During Market Hours

When restarting the bot during market hours (9:30 AM - 4:00 PM EST):

1. Bot will skip warm-up phase (already passed)
2. Will detect market is open
3. Will automatically connect to Finnhub
4. Will subscribe to configured symbols
5. Should start receiving bars within 5 minutes

**Expected Logs**:
```
Market Status: OPEN
Trading loop started
Bot started during market hours - connecting to real-time provider...
   [OK] Connected to Finnhub
   [OK] Subscribed to 3 symbols
```

---

## Future Improvements

### Potential Enhancements

1. **Finnhub REST API Backfill**: Eliminate yfinance 15-min delay gap
2. **Connection Pooling**: Reuse connections instead of recreating
3. **Exponential Backoff**: Smart retry logic for transient failures
4. **Metrics Collection**: Track ping/pong latency, reconnect frequency
5. **Alert Thresholds**: Notify on abnormal connection patterns

### Known Limitations

1. **yfinance 15-min Delay**: Creates data gap when bot restarts mid-market
2. **Single WebSocket**: No redundancy if Finnhub has issues
3. **Rate Limit Recovery**: Takes 60s to recover from rate limiting
4. **No Ping Control**: Rely on Finnhub's ping frequency (~60s)

---

## References

- **Finnhub API Docs**: https://finnhub.io/docs/api/websocket-trades
- **Finnhub Rate Limits**: Free tier = 60 API calls/min, 1 WebSocket connection
- **WebSocket RFC 6455**: Standard ping/pong mechanism
- **Docker Healthcheck**: https://docs.docker.com/engine/reference/builder/#healthcheck

---

## Conclusion

The Finnhub WebSocket connection issues were caused by duplicate connection attempts triggering rate limiting. By removing the duplicate code, adding mid-market auto-connect logic, and adjusting ping thresholds, the bot now maintains stable connections with consistent real-time data flow.

**Status**: ✅ Production-ready and fully operational
