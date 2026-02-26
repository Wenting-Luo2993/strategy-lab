# Finnhub Tick Logging for Bar Aggregation Validation

**Purpose**: Capture all raw ticks from Finnhub WebSocket to validate bar aggregation accuracy against TradingView.

---

## Enable Tick Logging

### Method 1: Environment Variable

Add to your `.env` file:
```bash
LOG_FINNHUB_TICKS=true
TICK_LOG_DIR=./data/tick_logs  # Optional, default location
TICK_LOG_TTL_DAYS=3  # Optional, auto-cleanup after 3 days (default)
```

### Method 2: Docker Compose

Add to `docker-compose.yml`:
```yaml
environment:
  - LOG_FINNHUB_TICKS=true
  - TICK_LOG_DIR=/app/data/tick_logs
```

---

## Tick Log Format

Ticks are logged as JSON Lines (JSONL) with one tick per line:

```jsonl
{"received_at":"2026-02-25T18:45:23.123456Z","symbol":"AAPL","price":275.50,"volume":100,"timestamp_ms":1708888523123,"timestamp":"2026-02-25T18:45:23.123000Z","conditions":[],"bid_price":275.49,"ask_price":275.51,"bid_size":200,"ask_size":150}
{"received_at":"2026-02-25T18:45:23.456789Z","symbol":"AAPL","price":275.51,"volume":50,"timestamp_ms":1708888523456,"timestamp":"2026-02-25T18:45:23.456000Z","conditions":[],"bid_price":275.50,"ask_price":275.52,"bid_size":300,"ask_size":100}
```

### Fields

| Field | Description | Example |
|-------|-------------|---------|
| `received_at` | When bot received tick (UTC) | `2026-02-25T18:45:23.123456Z` |
| `symbol` | Trading symbol | `AAPL` |
| `price` | Trade price | `275.50` |
| `volume` | Trade volume (shares) | `100` |
| `timestamp_ms` | Finnhub timestamp (milliseconds) | `1708888523123` |
| `timestamp` | Trade timestamp (ISO 8601) | `2026-02-25T18:45:23.123000Z` |
| `conditions` | Trade conditions (if any) | `[]` |
| `bid_price` | Best bid price | `275.49` |
| `ask_price` | Best ask price | `275.51` |
| `bid_size` | Best bid size | `200` |
| `ask_size` | Best ask size | `150` |

---

## Analyzing Tick Logs

### Example: Validate 5-Minute Bar

1. **Identify the 5-minute bar window** you want to validate:
   ```
   Bar: 2026-02-25 09:35:00 - 09:40:00 EST
   ```

2. **Extract ticks for that window** from the JSONL file:
   ```python
   import json
   from datetime import datetime
   import pytz

   # Load ticks
   ticks = []
   with open("data/tick_logs/finnhub_ticks_20260225_093000.jsonl") as f:
       for line in f:
           tick = json.loads(line)
           if tick["symbol"] == "AAPL":
               ticks.append(tick)

   # Filter ticks for 09:35-09:40 EST
   est = pytz.timezone("US/Eastern")
   start = est.localize(datetime(2026, 2, 25, 9, 35, 0))
   end = est.localize(datetime(2026, 2, 25, 9, 40, 0))

   bar_ticks = [
       t for t in ticks
       if start <= datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")).astimezone(est) < end
   ]

   print(f"Found {len(bar_ticks)} ticks in 09:35-09:40 bar")
   ```

3. **Calculate OHLCV from ticks**:
   ```python
   if bar_ticks:
       open_price = bar_ticks[0]["price"]
       high_price = max(t["price"] for t in bar_ticks)
       low_price = min(t["price"] for t in bar_ticks)
       close_price = bar_ticks[-1]["price"]
       volume = sum(t["volume"] for t in bar_ticks)

       print(f"O: {open_price}, H: {high_price}, L: {low_price}, C: {close_price}, V: {volume}")
   ```

4. **Compare with bot's aggregated bar**:
   ```
   Bot Bar:         O=275.50, H=275.75, L=275.40, C=275.70, V=15000
   From Ticks:      O=275.50, H=275.75, L=275.40, C=275.70, V=15000
   TradingView:     O=275.50, H=275.76, L=275.39, C=275.71, V=15200

   Diff vs TradingView:
   - High: +$0.01 (0.004%)
   - Low: -$0.01 (0.004%)
   - Close: +$0.01 (0.004%)
   - Volume: -200 shares (1.3%)
   ```

---

## Common Discrepancies Explained

### 1. Volume Differences

**Cause**: Finnhub free tier doesn't include all trades (only "last sale" trades)

**Example**:
- Bot (from Finnhub): 15,000 shares
- TradingView: 15,200 shares
- Difference: ~1.3%

**Mitigation**: Upgrade to Finnhub paid tier for full market depth, or accept small volume discrepancies.

---

### 2. High/Low Differences (Small)

**Cause**: Tick arrival timing - some extreme prices may occur between tick deliveries

**Example**:
- Bot High: $275.75
- TradingView High: $275.76
- Difference: $0.01 (0.004%)

**Normal**: Differences < $0.05 (< 0.02%) are typical and acceptable.

---

### 3. Missing Ticks

**Cause**: WebSocket disconnections, network issues, or Finnhub rate limiting

**Check**: Look for gaps in `timestamp` values:
```python
# Check for gaps > 5 seconds between consecutive ticks
for i in range(1, len(bar_ticks)):
    prev_ts = datetime.fromisoformat(bar_ticks[i-1]["timestamp"].replace("Z", "+00:00"))
    curr_ts = datetime.fromisoformat(bar_ticks[i]["timestamp"].replace("Z", "+00:00"))
    gap = (curr_ts - prev_ts).total_seconds()
    if gap > 5:
        print(f"Gap detected: {gap:.1f}s between {prev_ts} and {curr_ts}")
```

---

## Performance Impact

**Disk I/O**: ~1-5 MB/hour (depending on trading volume)

**CPU**: Negligible (~0.1% overhead)

**Recommendation**:
- Enable during validation/debugging
- Disable in production once validated
- Rotate/delete old logs regularly

---

## Automatic Features

### Daily Log Rotation (New!)

**New tick log file created automatically each trading day** for long-running bots.

- **When**: Automatically rotates at midnight (date change)
- **How**: Old file closed cleanly, new file opened with current date
- **Why**: Prevents single file from growing indefinitely; supports continuous bot operation
- **Example**:
  - Day 1: `finnhub_ticks_20260226_093000.jsonl`
  - Day 2: `finnhub_ticks_20260227_000015.jsonl` (rotated at midnight)

**No bot restart required!** The bot can run continuously, and tick logging will automatically rotate to a new file each day.

### Automatic Log Cleanup

**Old tick logs are automatically deleted** on bot startup to prevent disk space issues.

- **Default TTL**: 3 days
- **When**: Cleanup runs automatically when bot starts (before market open)
- **What**: Deletes tick log files older than TTL
- **Configure**: Set `TICK_LOG_TTL_DAYS=7` to keep logs for 7 days instead

### Manual Cleanup (Optional)

If you need to clean up logs manually:

```bash
# Delete logs older than 7 days
find ./data/tick_logs -name "finnhub_ticks_*.jsonl" -mtime +7 -delete

# Or compress old logs
find ./data/tick_logs -name "finnhub_ticks_*.jsonl" -mtime +1 -exec gzip {} \;
```

---

## Example Validation Script

See `scripts/validate_bar_aggregation.py` for a complete example script that:
1. Loads tick logs
2. Reconstructs bars from ticks
3. Compares with bot's aggregated bars
4. Generates validation report

```bash
python scripts/validate_bar_aggregation.py \
    --tick-log data/tick_logs/finnhub_ticks_20260225_093000.jsonl \
    --symbol AAPL \
    --start "2026-02-25 09:30:00" \
    --end "2026-02-25 16:00:00" \
    --interval 5m
```

---

## Troubleshooting

### No ticks logged

**Check**:
1. `LOG_FINNHUB_TICKS=true` in `.env`
2. Bot logs show: "Tick logging ENABLED → ..."
3. Directory exists and is writable: `ls -la ./data/tick_logs`

### File permissions error

**Fix**:
```bash
mkdir -p ./data/tick_logs
chmod 755 ./data/tick_logs
```

### Logs too large

**Options**:
1. Compress with gzip: `gzip finnhub_ticks_*.jsonl`
2. Sample ticks (log every Nth tick)
3. Log only specific symbols
4. Shorter logging windows (1-2 hours)

---

## Best Practices

1. **Validate Once**: Run tick logging for 1-2 trading days, validate aggregation, then disable
2. **Monitor Disk Space**: Check available space before enabling: `df -h`
3. **Clean Up**: Delete logs after validation is complete
4. **Document Findings**: Note any consistent discrepancies for future reference
5. **Compare Apples to Apples**: Use same data source (TradingView uses different providers for different assets)

---

## References

- **Finnhub WebSocket API**: https://finnhub.io/docs/api/websocket-trades
- **JSON Lines Format**: https://jsonlines.org/
- **Bar Aggregation Logic**: See `vibe/trading_bot/data/aggregator.py`

---

## Conclusion

Tick logging provides a complete audit trail of all raw market data received from Finnhub. This enables:
- ✅ Validating bar aggregation accuracy
- ✅ Debugging data inconsistencies
- ✅ Comparing against other data sources (TradingView, etc.)
- ✅ Understanding Finnhub data quality

Use it as a diagnostic tool, not a permanent feature.
