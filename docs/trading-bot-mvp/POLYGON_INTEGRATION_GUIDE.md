# Polygon.io Integration Guide

## Overview

This guide documents the integration of Polygon.io (Massive) as the primary real-time data provider, replacing Finnhub WebSocket due to reliability issues.

## Why Switch from Finnhub?

### Finnhub Issues Observed:
1. **Frequent disconnections** - WebSocket timeouts every 60 seconds
2. **Silent failures** - Connected but no data received
3. **HTTP 502 errors** - Gateway issues during market open
4. **Zero data on critical days** - No bars during ORB window (9:30-9:35 AM)

### Polygon.io Advantages:
1. ✅ **Higher reliability** - Stable REST API, no WebSocket issues
2. ✅ **Better data quality** - More accurate pricing and volume
3. ✅ **Rate limiting** - Clear limits (5 calls/min free tier)
4. ✅ **Fallback-friendly** - Easy to chain with secondary providers

## Architecture

### Provider Selection System

```
┌─────────────────────────────────────────────────────────────┐
│                  CONFIGURABLE PROVIDER SYSTEM                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  .env Configuration:                                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ DATA__PRIMARY_PROVIDER=polygon                         │ │
│  │ DATA__SECONDARY_PROVIDER=finnhub                       │ │
│  │ DATA__POLYGON_API_KEY=your_key_here                    │ │
│  │ DATA__FINNHUB_API_KEY=backup_key                       │ │
│  └────────────────────────────────────────────────────────┘ │
│                          │                                   │
│                          v                                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ DataProviderFactory                                    │ │
│  │ - Creates primary provider (Polygon)                   │ │
│  │ - Creates secondary provider (Finnhub)                 │ │
│  │ - Validates API keys                                   │ │
│  └────────────────────────────────────────────────────────┘ │
│                          │                                   │
│                          v                                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Orchestrator                                           │ │
│  │ - Tries primary provider first                         │ │
│  │ - Falls back to secondary on failure                   │ │
│  │ - Logs provider switches                               │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Polling Strategy (Polygon REST API)

Unlike Finnhub's push-based WebSocket, Polygon uses pull-based REST API:

```
Market Open (9:30 AM)
     │
     v
┌─────────────────────────────────────────┐
│  Every 60 seconds:                       │
│  1. Check if market is open              │
│  2. Poll Polygon for latest bars         │
│  3. Update bar aggregators               │
│  4. Trigger strategy if bar complete     │
└─────────────────────────────────────────┘
     │
     v
Market Close (4:00 PM)
```

**Rate Limiting:**
- Polygon free tier: 5 calls/minute
- 5 symbols × 1 call/min = 5 calls/min ✅ (exactly at limit)
- If need >5 symbols, reduce poll frequency or upgrade

## Implementation

### 1. Provider Classes

#### PolygonDataProvider
- **File**: `vibe/trading_bot/data/providers/polygon.py`
- **Type**: REST API client
- **Methods**:
  - `get_latest_bar(symbol)` - Get most recent 1-minute bar
  - `get_multiple_latest_bars(symbols)` - Batch fetch for multiple symbols
  - `get_historical_bars(symbol, days)` - Historical data for warm-up
- **Rate Limiting**: Built-in (5 calls/min)

#### DataProviderFactory
- **File**: `vibe/trading_bot/data/providers/factory.py`
- **Purpose**: Create provider instances from config
- **Methods**:
  - `create_realtime_provider(type, **api_keys)` - Factory method
  - `get_provider_info(type)` - Provider metadata

### 2. Configuration

#### Environment Variables (.env)
```bash
# Primary provider (used first)
DATA__PRIMARY_PROVIDER=polygon
DATA__POLYGON_API_KEY=your_polygon_key_here

# Secondary provider (fallback)
DATA__SECONDARY_PROVIDER=finnhub
DATA__FINNHUB_API_KEY=your_finnhub_key_here
```

#### Supported Providers
| Provider | Type | Real-time | Free Tier | Recommended |
|----------|------|-----------|-----------|-------------|
| `polygon` | REST | No (15-min delay) | Yes (5 calls/min) | ✅ Yes |
| `finnhub` | WebSocket | Yes | Yes (1 connection) | ⚠️ Unreliable |
| `alpaca` | WebSocket | Yes | Yes (paper account) | ✅ Future |
| `yfinance` | REST | No (15-min delay) | Yes | Fallback only |

### 3. Orchestrator Changes

#### Initialization (Warm-Up Phase)
```python
# Old: Hardcoded Finnhub
self.finnhub_ws = FinnhubWebSocketClient(api_key=finnhub_api_key)

# New: Configurable primary/secondary
from vibe.trading_bot.data.providers.factory import DataProviderFactory

# Create primary provider
self.primary_provider = DataProviderFactory.create_realtime_provider(
    provider_type=self.config.data.primary_provider,
    polygon_api_key=self.config.data.polygon_api_key,
    finnhub_api_key=self.config.data.finnhub_api_key
)

# Create secondary provider (fallback)
if self.config.data.secondary_provider:
    self.secondary_provider = DataProviderFactory.create_realtime_provider(
        provider_type=self.config.data.secondary_provider,
        polygon_api_key=self.config.data.polygon_api_key,
        finnhub_api_key=self.config.data.finnhub_api_key
    )
```

#### Polling Loop (Market Hours)
```python
# New polling loop for REST API providers (Polygon)
async def _poll_realtime_data(self):
    """Poll REST API provider for latest bars (Polygon-style)."""
    while self.market_scheduler.is_market_open():
        try:
            # Fetch latest bars for all symbols
            bars = await self.primary_provider.get_multiple_latest_bars(
                symbols=self.config.trading.symbols
            )

            # Update bar aggregators
            for symbol, bar in bars.items():
                if bar:
                    await self._handle_realtime_bar(symbol, bar)

            # Wait 60 seconds before next poll
            await asyncio.sleep(60)

        except Exception as e:
            self.logger.error(f"Error polling primary provider: {e}")

            # Try secondary provider
            if self.secondary_provider:
                await self._try_secondary_provider()
```

## Migration Steps

### Phase 1: Add Polygon Provider (No Breaking Changes)
1. ✅ Create `polygon.py` provider
2. ✅ Create `factory.py` for provider creation
3. ✅ Update `.env.example` with new config options
4. Test Polygon provider independently

### Phase 2: Update Orchestrator (Breaking Changes)
1. Add primary/secondary provider initialization
2. Replace WebSocket callback with polling loop
3. Add fallback logic when primary fails
4. Update warm-up phase to use primary provider

### Phase 3: Testing & Validation
1. Test with Polygon as primary, Finnhub as secondary
2. Verify rate limiting (5 calls/min)
3. Test fallback when Polygon fails
4. Monitor for full trading day

### Phase 4: Cleanup (Optional)
1. Remove Finnhub-specific code if not using as fallback
2. Update documentation
3. Add Alpaca provider for better free real-time data

## Usage Examples

### Example 1: Polygon Primary, Finnhub Secondary
```bash
# .env
DATA__PRIMARY_PROVIDER=polygon
DATA__SECONDARY_PROVIDER=finnhub
DATA__POLYGON_API_KEY=pk_abc123
DATA__FINNHUB_API_KEY=fh_xyz789
```

**Behavior:**
- Uses Polygon for all data fetching
- Falls back to Finnhub if Polygon fails
- Rate: 5 calls/min (Polygon limit)

### Example 2: Polygon Only (No Fallback)
```bash
# .env
DATA__PRIMARY_PROVIDER=polygon
DATA__SECONDARY_PROVIDER=yfinance  # Silent fallback (no API key needed)
DATA__POLYGON_API_KEY=pk_abc123
```

**Behavior:**
- Uses Polygon exclusively
- Falls back to Yahoo Finance if Polygon fails
- Rate: 5 calls/min

### Example 3: Finnhub Only (Keep Current Setup)
```bash
# .env
DATA__PRIMARY_PROVIDER=finnhub
DATA__SECONDARY_PROVIDER=yfinance
DATA__FINNHUB_API_KEY=fh_xyz789
```

**Behavior:**
- No changes to current system
- Continues using Finnhub WebSocket
- Same reliability issues

## Rate Limit Management

### Polygon Free Tier: 5 Calls/Minute

**Strategy:**
- **5 symbols × 1 call/min = 5 calls/min** ✅ (exactly at limit)
- Poll every 60 seconds per symbol
- Use `get_multiple_latest_bars()` to batch requests

**If You Need More Symbols:**
```python
# Option 1: Reduce poll frequency
POLL_INTERVAL = 120  # Poll every 2 minutes for 10 symbols (2.5 calls/min)

# Option 2: Stagger requests
for i, symbol in enumerate(symbols):
    await asyncio.sleep(i * 12)  # 12 seconds between symbols
    await provider.get_latest_bar(symbol)

# Option 3: Upgrade to paid tier ($29/month for 100 calls/min)
```

## Monitoring & Debugging

### Check Current Provider
```python
# In orchestrator
self.logger.info(f"Active provider: {self.primary_provider.__class__.__name__}")
```

### Monitor Rate Limiting
```python
# Polygon provider logs rate limit waits
# Look for:
"Rate limit reached (5/min), waiting 10.5s"
```

### Test Provider Switch
```python
# Simulate primary failure
self.primary_provider = None
# Should auto-switch to secondary_provider
```

## Troubleshooting

### Issue: "Rate limit exceeded"
**Cause:** >5 calls/minute to Polygon
**Fix:**
- Reduce poll frequency
- Reduce number of symbols
- Upgrade to paid tier

### Issue: "No data during ORB window"
**Cause:** Polling missed the ORB window (9:30-9:35 AM)
**Fix:**
- Ensure polling starts before 9:30 AM (in warm-up)
- Poll every 60s (don't miss the 5-minute window)
- Check Polygon API status

### Issue: "Fallback not working"
**Cause:** Secondary provider not configured or also failing
**Fix:**
- Check `DATA__SECONDARY_PROVIDER` in .env
- Verify secondary provider API key
- Check logs for secondary provider errors

## Cost Analysis

| Tier | Provider | Cost | Rate Limit | Real-time | Recommended For |
|------|----------|------|------------|-----------|-----------------|
| **Free** | Polygon | $0 | 5 calls/min | No (15-min delay) | Testing, <5 symbols |
| **Free** | Finnhub | $0 | 1 websocket | Yes | Not recommended |
| **Free** | Alpaca | $0 | Unlimited | Yes | **Best free option** |
| **Paid** | Polygon Starter | $29/mo | 100 calls/min | Yes | Production, <20 symbols |
| **Paid** | Polygon Developer | $99/mo | 500 calls/min | Yes | Production, <100 symbols |

## Next Steps

1. **Immediate:** Implement Polygon integration as documented
2. **Short-term:** Test for 1 week, monitor reliability
3. **Medium-term:** Add Alpaca provider (best free real-time option)
4. **Long-term:** Evaluate paid tiers based on strategy needs

## References

- [Polygon.io API Documentation](https://polygon.io/docs/stocks/getting-started)
- [Polygon.io Pricing](https://polygon.io/pricing)
- [Rate Limits Best Practices](https://polygon.io/docs/stocks/getting-started#rate-limits)
- [Alpaca Markets Data API](https://alpaca.markets/docs/api-references/market-data-api/)
