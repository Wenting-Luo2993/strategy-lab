# Trading Bot Troubleshooting Guide

Quick reference for debugging common issues and checking logs.

---

## Warm-Up Phase Diagnostics

The warm-up phase runs at **9:25 AM EST** (5 minutes before market open).

### Quick Check - Did Warm-Up Complete?

```bash
# Show warm-up logs (all 4 steps)
docker-compose logs trading-bot 2>&1 | grep -E "(warm-up|Warm-up|WARM-UP)" | tail -50

# Check just today's warm-up
docker-compose logs --since 9:20am trading-bot 2>&1 | grep -E "(warm-up|Step [1-4])"

# Verify all 4 steps completed
docker-compose logs trading-bot 2>&1 | grep "Step [1-4]/4" | tail -4
```

### Expected Warm-Up Output

```
✅ Starting pre-market warm-up phase
✅ Step 1/4: Loading yesterday's data...
✅ Step 2/4: Connecting to Finnhub WebSocket...
✅ Step 3/4: Warming up bar aggregators...
✅ Step 4/4: Running health checks...
✅ Warm-up complete - ready for market open
```

### Check for Warm-Up Errors

```bash
# Look for errors during warm-up window
docker-compose logs --since 9:20am --until 9:30am trading-bot 2>&1 | grep -E "(ERROR|CRITICAL|Exception|Traceback)"

# Check HealthMonitor call (common failure point)
docker-compose logs trading-bot 2>&1 | grep -E "(health_monitor|HealthMonitor)" | tail -10
```

---

## ORB Strategy Diagnostics

ORB (Opening Range Breakout) executes during **9:30-9:35 AM EST**.

### Check ORB Execution

```bash
# Did ORB calculate levels?
docker-compose logs --since 9:25am trading-bot 2>&1 | grep -E "(ORB|Opening Range)"

# Check for ORB signals
docker-compose logs --since 9:30am --until 10:00am trading-bot 2>&1 | grep -i "signal"

# Check for ORB trades
docker-compose logs --since 9:30am --until 10:00am trading-bot 2>&1 | grep -E "(ENTRY|EXIT|ORDER)"
```

### Expected ORB Output

```
✅ ORB levels calculated for AAPL: high=$145.20, low=$144.80
✅ ORB levels calculated for MSFT: high=$380.50, low=$379.20
✅ Signal generated: AAPL LONG breakout above $145.20
✅ Order submitted: BUY 10 AAPL @ $145.25
```

---

## Discord Notification Diagnostics

### Check Discord Messages

```bash
# Look for Discord webhook calls
docker-compose logs --since 9:30am trading-bot 2>&1 | grep -i discord

# Check for rate limiting
docker-compose logs trading-bot 2>&1 | grep -E "(rate.?limit|429)" | tail -10

# Check notification errors
docker-compose logs trading-bot 2>&1 | grep -E "(notification|Discord)" | grep -i error
```

### Common Discord Issues

1. **Rate limited** - Discord allows 30 messages per minute
2. **Webhook URL expired/invalid** - Check `NOTIFICATION__DISCORD_WEBHOOK_URL` in `.env`
3. **Network error** - Check internet connectivity from Oracle Cloud

---

## Data Feed Diagnostics

### Check Finnhub WebSocket

```bash
# Check connection status
docker-compose logs trading-bot 2>&1 | grep -E "(Finnhub|WebSocket|Connected|Disconnected)" | tail -20

# Check for rate limiting (HTTP 429)
docker-compose logs trading-bot 2>&1 | grep -E "(429|rate.?limit)" | tail -10

# Check when last trade received
docker-compose logs trading-bot 2>&1 | grep "REALTIME BAR" | tail -5

# Check for websocket timeouts
docker-compose logs trading-bot 2>&1 | grep -E "(timeout|disconnect)" | tail -20
```

### Check Yahoo Finance Fallback

```bash
# See if Yahoo Finance is being used during market hours (should NOT happen)
docker-compose logs --since 9:30am --until 4:00pm trading-bot 2>&1 | grep -i yfinance

# Check cache staleness
docker-compose logs trading-bot 2>&1 | grep -E "(CACHE STALE|NO FALLBACK)" | tail -10
```

---

## Full Morning Activity Review

### Comprehensive Morning Log (9:20 AM - 9:40 AM)

```bash
# View everything during warm-up + ORB window
docker-compose logs --since 9:20am --until 9:40am trading-bot 2>&1 | less

# Save to file for detailed analysis
docker-compose logs --since 9:20am --until 9:40am trading-bot > ~/morning-logs.txt

# Filter for key events only
cat ~/morning-logs.txt | grep -E "(warm-up|Step|ORB|Discord|ERROR|CRITICAL)" | grep -v "Cache" | grep -v "NO FALLBACK"
```

### One-Liner for Quick Diagnosis

```bash
# Shows key events in chronological order
docker-compose logs --since 9:20am trading-bot 2>&1 | grep -E "(warm-up|Step [1-4]|ORB|Discord|ERROR)" | grep -v "Cache" | grep -v "NO FALLBACK"
```

---

## System Health Diagnostics

### Check Service Status

```bash
# Is container running?
docker-compose ps

# Container resource usage
docker stats trading-bot --no-stream

# Check restart count
docker inspect trading-bot | grep -E "(RestartCount|StartedAt)"
```

### Check Health Endpoint

```bash
# From inside Oracle Cloud VM
curl http://localhost:8080/health/live

# Detailed health check
curl http://localhost:8080/health/ready

# Get all metrics
curl http://localhost:8080/metrics
```

### Check Disk Space

```bash
# Disk usage
df -h

# Log file sizes
du -sh ~/trading-bot/logs/*
du -sh ~/trading-bot/data/*

# Clean up old logs if needed
find ~/trading-bot/logs -name "*.log.*" -mtime +7 -delete
```

---

## Error Pattern Analysis

### Find All Errors Today

```bash
# All errors since midnight
docker-compose logs --since 00:00 trading-bot 2>&1 | grep -E "(ERROR|CRITICAL)" > ~/errors-today.txt
cat ~/errors-today.txt

# Count errors by type
grep -oP "ERROR - \K[^:]*" ~/errors-today.txt | sort | uniq -c | sort -rn
```

### Common Error Patterns

```bash
# Connection errors
docker-compose logs trading-bot 2>&1 | grep -E "(Connection|Timeout|Disconnect)" | tail -20

# Permission errors
docker-compose logs trading-bot 2>&1 | grep -i "permission denied"

# Module/Import errors
docker-compose logs trading-bot 2>&1 | grep -E "(ImportError|ModuleNotFoundError)"

# API errors
docker-compose logs trading-bot 2>&1 | grep -E "(401|403|429|500|502|503)"
```

---

## Real-Time Monitoring

### Follow Logs Live

```bash
# Follow all logs
docker-compose logs -f trading-bot

# Follow with timestamp
docker-compose logs -f -t trading-bot

# Follow only errors
docker-compose logs -f trading-bot 2>&1 | grep -E "(ERROR|CRITICAL|WARNING)"

# Follow only trading activity
docker-compose logs -f trading-bot 2>&1 | grep -E "(SIGNAL|ORDER|FILL|TRADE)"
```

---

## Log File Direct Access

If logs are volume-mounted to `~/trading-bot/logs/`:

### View Log Files Directly

```bash
# Latest log file
tail -f ~/trading-bot/logs/trading_bot.log

# Last 1000 lines
tail -1000 ~/trading-bot/logs/trading_bot.log

# Morning activity (9:20-9:40)
tail -5000 ~/trading-bot/logs/trading_bot.log | awk '/2026-02-[0-9]+ 09:(2[0-9]|3[0-5])/'

# Search for specific pattern
grep -n "warm-up" ~/trading-bot/logs/trading_bot.log | tail -20
```

### Rotate Large Logs

```bash
# Check log size
ls -lh ~/trading-bot/logs/trading_bot.log

# Rotate manually if too large
mv ~/trading-bot/logs/trading_bot.log ~/trading-bot/logs/trading_bot.log.old
docker-compose restart trading-bot
```

---

## Debugging Specific Issues

### Issue: No ORB Discord Notification

**Diagnosis Steps:**
1. Check warm-up completed: `docker-compose logs trading-bot | grep "warm-up complete"`
2. Check ORB levels calculated: `docker-compose logs --since 9:30am trading-bot | grep "ORB levels"`
3. Check Discord webhook: `docker-compose logs --since 9:30am trading-bot | grep -i discord`
4. Check for errors: `docker-compose logs --since 9:20am trading-bot | grep ERROR`

**Common Causes:**
- Warm-up failed at Step 4 (HealthMonitor)
- No breakout detected (price stayed within ORB range)
- Discord rate limited or webhook failed
- Trading paused due to risk limits

### Issue: Finnhub WebSocket Keeps Disconnecting

**Diagnosis Steps:**
1. Check rate limiting: `docker-compose logs trading-bot | grep "429"`
2. Check connection attempts: `docker-compose logs trading-bot | grep "Reconnecting"`
3. Check last message time: `docker-compose logs trading-bot | grep "WebSocket receive timeout"`

**Common Causes:**
- HTTP 429 rate limit (free tier: 60 req/min, 1 websocket)
- Multiple bot instances running (check for duplicates)
- Network issues on Oracle Cloud

### Issue: No Trades Executed

**Diagnosis Steps:**
1. Check signals generated: `docker-compose logs --since 9:30am trading-bot | grep -i signal`
2. Check risk validation: `docker-compose logs trading-bot | grep "risk.*rejected"`
3. Check order submission: `docker-compose logs trading-bot | grep "ORDER"`
4. Check circuit breakers: `docker-compose logs trading-bot | grep "CIRCUIT BREAKER"`

**Common Causes:**
- No signals generated (no breakout)
- Risk manager rejected trade (insufficient capital, exposure limits)
- Circuit breaker triggered (drawdown/loss limits)
- Market closed or pre-market window

---

## Performance Analysis

### Latency Metrics

```bash
# WebSocket latency
docker-compose logs trading-bot 2>&1 | grep "latency" | tail -20

# Order execution time
docker-compose logs trading-bot 2>&1 | grep -E "(Order submitted|Order filled)" | tail -10

# Bar aggregation time
docker-compose logs trading-bot 2>&1 | grep "Bar aggregation" | tail -20
```

### Memory Usage Over Time

```bash
# Sample every 5 seconds for 1 minute
for i in {1..12}; do docker stats trading-bot --no-stream --format "{{.MemUsage}}" && sleep 5; done
```

---

## Quick Reference Card

| Scenario | Command |
|----------|---------|
| **Did warm-up run?** | `docker-compose logs trading-bot \| grep "warm-up complete"` |
| **Any errors today?** | `docker-compose logs --since 00:00 trading-bot \| grep ERROR` |
| **ORB levels calculated?** | `docker-compose logs --since 9:30am trading-bot \| grep "ORB levels"` |
| **Discord sent?** | `docker-compose logs --since 9:30am trading-bot \| grep -i discord` |
| **WebSocket connected?** | `docker-compose logs trading-bot \| grep "Connected to Finnhub" \| tail -1` |
| **Follow live** | `docker-compose logs -f trading-bot` |
| **Morning activity** | `docker-compose logs --since 9:20am --until 9:40am trading-bot` |

---

## Getting Help

If you're stuck after trying these commands:

1. **Save full logs:** `docker-compose logs trading-bot > ~/debug-logs.txt`
2. **Note the timestamp** of when the issue occurred
3. **Check for patterns** - does it happen every day or randomly?
4. **Review recent code changes** - did you deploy recently?
5. **Check environment** - any config changes?

---

**Last Updated:** 2026-02-23 (Phase 1 - Post warm-up fix)
