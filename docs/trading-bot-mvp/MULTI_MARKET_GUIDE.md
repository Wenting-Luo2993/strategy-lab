# Multi-Market Trading Bot Guide

## Overview

The trading bot now supports **stocks, forex, and crypto** markets with flexible configuration. Switch between markets by simply changing environment variables—no code changes needed!

## Quick Start

### Stocks (Default)
```bash
# .env file
MARKET_TYPE=stocks
EXCHANGE=NYSE
SYMBOLS=["AAPL","GOOGL","MSFT"]
```

**Trading Hours**: 9:30 AM - 4:00 PM ET, Monday-Friday (excludes holidays)

### Forex
```bash
# .env file
MARKET_TYPE=forex
SYMBOLS=["EURUSD","GBPUSD","USDJPY"]
```

**Trading Hours**: 24/5 (Sunday 5:00 PM ET - Friday 5:00 PM ET)

### Crypto
```bash
# .env file
MARKET_TYPE=crypto
SYMBOLS=["BTCUSD","ETHUSD","SOLUSD"]
```

**Trading Hours**: 24/7 (never closes)

## Architecture

### Market Schedulers

Each market type has its own scheduler implementation:

```
BaseMarketScheduler (Abstract)
├── StockMarketScheduler    # Uses pandas_market_calendars
├── ForexMarketScheduler    # 24/5 logic
└── CryptoMarketScheduler   # 24/7 always-open
```

**Key Methods**:
- `is_market_open()` - Check if market is currently trading
- `next_market_open()` - Get next market open time
- `next_market_close()` - Get next market close time
- `get_session_end_time()` - Get logical end-of-day for summaries

### Factory Pattern

The bot automatically instantiates the correct scheduler:

```python
from vibe.trading_bot.core.market_schedulers import create_scheduler

scheduler = create_scheduler(
    market_type="stocks",  # or "forex" or "crypto"
    exchange="NYSE",        # required for stocks
)
```

## End-of-Day Summaries

Daily summaries are sent to Discord at the logical "end of session":

- **Stocks**: Market close (4:00 PM ET)
- **Forex**: End of trading week (Friday 5:00 PM ET)
- **Crypto**: Midnight UTC (configurable)

### Summary Contents

```
📊 Daily Summary - 2026-02-17

Account:
• Equity: $10,250.00
• P/L: $250.00 (+2.50%)

Opening Range Levels:
• AAPL: $150.25-$151.80 (range: $1.55)
• GOOGL: $142.10-$143.50 (range: $1.40)
• MSFT: $378.90-$380.25 (range: $1.35)

Activity:
• Breakouts Detected: 5
• Signals Generated: 2
• Trades Executed: 2

Signals by Symbol:
• AAPL: 1
• GOOGL: 1

Breakouts Rejected:
• insufficient_volume: 2
• after_entry_cutoff_time: 1
```

## Smart Strategy Logging

Event-based logging that only logs interesting events:

### ORB Establishment
```
[ORB] AAPL: Opening range established $150.00-$152.00 (range: $2.00)
```
Logged **once per day per symbol** when ORB levels are first calculated.

### Price Approaching Breakout
```
[ORB] AAPL: Price approaching HIGH breakout - Current: $151.92, Breakout: $152.00 (0.05% away)
```
Logged when price is within **0.5%** of breakout, throttled to once per 5 minutes.

### Breakout Rejected
```
[ORB] AAPL: HIGH breakout detected at $152.15 but REJECTED - Reason: insufficient_volume (1.3x < 1.5x threshold)
[ORB] MSFT: LOW breakout detected at $380.50 but REJECTED - Reason: after_entry_cutoff_time
```
Logged when breakout occurs but filters reject it, throttled to once per 10 minutes per reason.

### Signal Generated
```
[SIGNAL] AAPL: LONG_BREAKOUT at $152.15 (ORB: $150.00-$152.00, TP: $156.15, SL: $150.00, R/R: 2.0)
```
Logged every time a trading signal is generated with full context.

## Configuration Options

### Stock Market Options

```bash
# Available exchanges (via pandas_market_calendars)
EXCHANGE=NYSE      # New York Stock Exchange
EXCHANGE=NASDAQ    # NASDAQ
EXCHANGE=LSE       # London Stock Exchange
EXCHANGE=TSX       # Toronto Stock Exchange
# ... many more supported
```

### Forex Market Options

```bash
MARKET_TYPE=forex
# Forex pairs follow standard naming
SYMBOLS=["EURUSD","GBPUSD","USDJPY","AUDUSD"]
```

### Crypto Market Options

```bash
MARKET_TYPE=crypto
# Crypto pairs with USD base
SYMBOLS=["BTCUSD","ETHUSD","SOLUSD","ADAUSD"]
```

## Position-Based Loop Optimization

The bot adjusts polling frequency based on position status:

- **With positions**: 60s intervals (active monitoring for exits)
- **No positions**: 300s intervals (idle, checking for entries)
- **Market closed**: Sleeps until next open, wakes every 60s for shutdown

## Cache Optimization

Historical data is cached intelligently:

- **TTL**: 30 days (historical bars never change)
- **Smart append**: New data merged with expired cache
- **Fallback**: Uses expired cache if fetch fails

## Example: Switching Markets

### From Stocks to Forex

```bash
# Before (.env)
MARKET_TYPE=stocks
EXCHANGE=NYSE
SYMBOLS=["AAPL","GOOGL","MSFT"]

# After (.env)
MARKET_TYPE=forex
# No exchange needed for forex
SYMBOLS=["EURUSD","GBPUSD","USDJPY"]
```

Restart the bot—no code changes required!

### From Stocks to Crypto

```bash
# Before (.env)
MARKET_TYPE=stocks
EXCHANGE=NYSE
SYMBOLS=["AAPL","GOOGL","MSFT"]

# After (.env)
MARKET_TYPE=crypto
# No exchange needed for crypto
SYMBOLS=["BTCUSD","ETHUSD"]
```

## Strategy Considerations

### ORB Strategy
- **Best for**: Stocks (works with defined market open)
- **Forex**: Requires adaptation (use session-based ORB, e.g., London/NY open)
- **Crypto**: Requires adaptation (use arbitrary time windows, e.g., 00:00 UTC)

For 24-hour markets, you may want to implement alternative strategies like:
- **Mean reversion**
- **Trend following**
- **Breakout with ATR-based ranges** (instead of opening range)

## Docker Compose Example

```yaml
services:
  trading-bot:
    environment:
      - MARKET_TYPE=stocks      # Change to forex or crypto
      - EXCHANGE=NYSE           # Only for stocks
      - SYMBOLS=["AAPL","GOOGL","MSFT"]
      - INITIAL_CAPITAL=10000
      - DISCORD_WEBHOOK_URL=${DISCORD_WEBHOOK_URL}
```

## Testing Different Markets

```bash
# Test stocks (default)
docker-compose up

# Test forex
docker-compose down
# Edit .env: MARKET_TYPE=forex
docker-compose up

# Test crypto
docker-compose down
# Edit .env: MARKET_TYPE=crypto
docker-compose up
```

## Limitations

1. **Data Providers**: Current implementation uses Yahoo Finance, which primarily supports stocks. For forex/crypto, you'll need to:
   - Integrate forex/crypto data providers (e.g., Alpha Vantage, Binance API, IEX Cloud)
   - Update `YahooDataProvider` or create new provider classes

2. **Strategy Adaptation**: ORB strategy is designed for stock market opens. For 24-hour markets:
   - Adapt ORB to use session times (e.g., London/NY open for forex)
   - Or implement different strategies better suited for continuous markets

3. **Order Execution**: `MockExchange` supports all market types, but real exchanges will need:
   - Different API integrations (stocks: Alpaca/IB, forex: OANDA, crypto: Binance)
   - Different order types and conventions

## Next Steps

To fully support forex/crypto trading:

1. **Add data providers** for forex/crypto markets
2. **Adapt strategies** for 24-hour trading (session-based ORB or new strategies)
3. **Integrate real exchanges** with forex/crypto support
4. **Update risk management** for different market characteristics

The architecture is now in place—just plug in the market-specific components!
