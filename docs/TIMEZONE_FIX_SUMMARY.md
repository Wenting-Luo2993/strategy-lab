# Timezone Fix Summary

## The Bug

**Before Fix:**
```python
"timestamp": datetime.fromtimestamp(trade.get("t", 0) / 1000)
# Creates NAIVE datetime in system timezone
```

If Finnhub sends: `timestamp=1771534200000` (milliseconds)
- = 20:50:00 UTC (8:50 PM UTC)
- = 15:50:00 EST (3:50 PM EST)

**What happened (BROKEN):**
1. `datetime.fromtimestamp(1771534200)` creates naive datetime
2. On UTC server: Creates `2026-02-19 20:50:00` (naive, treated as local = UTC)
3. BarAggregator tries to localize to EST
4. Assumes naive time is already EST, adds `-05:00` offset
5. **Result: `2026-02-19 20:50:00-05:00` (8:50 PM "EST")**
6. **This is WRONG - it's actually 8:50 PM, not 3:50 PM!**

## The Fix

**After Fix:**
```python
"timestamp": datetime.fromtimestamp(
    trade.get("t", 0) / 1000,
    tz=pytz.UTC  # Make it UTC-aware
)
```

**What happens now (FIXED):**
1. `datetime.fromtimestamp(1771534200, tz=pytz.UTC)` creates UTC-aware datetime
2. Creates `2026-02-19 20:50:00+00:00` (UTC)
3. BarAggregator converts to EST properly
4. **Result: `2026-02-19 15:50:00-05:00` (3:50 PM EST)**
5. **This is CORRECT!**

## Bar Alignment

**5-minute bars:**
- 3:45:00 PM - 3:49:59 PM → Bar timestamp: `15:45:00-05:00`
- 3:50:00 PM - 3:54:59 PM → Bar timestamp: `15:50:00-05:00`
- 3:55:00 PM - 3:59:59 PM → Bar timestamp: `15:55:00-05:00`

**When bars are logged:**
- Bar completes at END of period
- But logged with START timestamp
- Example: 3:45-3:50 PM bar completes at 3:50 PM, logged as `15:45:00-05:00`

## Volume Discrepancy

**Issue:** Our bar volume is ~26% of TradingView's volume

**Likely cause:** Finnhub FREE TIER limitations
- Free tier provides SAMPLED trades (not all trades)
- This is a known limitation of free websocket APIs
- Solutions:
  1. Accept the discrepancy (free tier is for testing)
  2. Upgrade to Finnhub paid tier ($$$ but gets all trades)
  3. Use a different data provider for production

**Note:** For ORB strategy, the OHLC values matter more than exact volume.
- Our O/H/L/C should be close to TradingView (±0.01)
- Volume discrepancy won't affect ORB level calculation
- But may affect volume-based filters if enabled

## Testing Tomorrow

Run the debug script during market hours:
```bash
python debug_realtime_bars.py
```

This will:
1. Connect to Finnhub websocket
2. Collect trades for 5 minutes
3. Show completed bars with timestamps
4. Verify timezone is EST (UTC-5)
5. Compare with TradingView data
