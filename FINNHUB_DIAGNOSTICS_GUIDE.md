# Finnhub WebSocket Diagnostics Guide

## Enhanced Test Script: `test_finnhub_bar_accuracy.py`

This guide explains all the diagnostic information logged by the test script to help troubleshoot issues.

---

## 🔍 What Gets Logged

### 1. **Trade-Level Diagnostics**

For the first 20 trades, you'll see detailed information:

```
Trade #1: AAPL
  Price: $150.25 x 100 shares
  Timestamp (TZ-aware): 2026-02-21 19:32:15.123000+00:00
  -> EST: 14:32:15.123
  -> UTC: 19:32:15.123
  Latency: 45ms
  Price change: +$0.02
```

**What to look for:**
- ✅ **TZ-aware timestamps** (not "naive") - confirms timezone fix working
- ✅ **EST times during market hours** (9:30 AM - 4:00 PM EST)
- ✅ **Low latency** (<500ms) - websocket is fast
- ⚠️ **High latency** (>2000ms) - network issues or API throttling

---

### 2. **Gap Detection**

Alerts when >10 seconds pass between trades:

```
[GAP] AAPL: 15.3s gap since last trade
```

**What this means:**
- ⚠️ During market hours: Possible websocket connection issue
- ✅ After hours: Expected (low trading volume)
- ⚠️ Multiple gaps: Connection unstable, investigate network

---

### 3. **Bar Completion Details**

When a 5-minute bar completes:

```
======================================================================
[BAR COMPLETE] AAPL @ 2026-02-21 14:30:00 EST
======================================================================
  OHLCV:
    Open:   $150.20
    High:   $150.45
    Low:    $150.15
    Close:  $150.32
    Volume: 125000 shares
  Metrics:
    Range: $0.25 (0.17%)
    Change: +$0.12 (+0.08%)
    Trades: 450
    Avg trade size: 278 shares
  Timing:
    Bar period: 14:30:00 - 14:35:00 EST
    Completion latency: 2.5s after bar end
    Bar end (UTC): 2026-02-21 19:35:00
    Callback (UTC): 2026-02-21 19:35:02
```

**What to look for:**
- ✅ **Bar aligned to 5-min boundaries** (e.g., 14:30, 14:35, 14:40)
- ✅ **Low completion latency** (<10s) - aggregator is fast
- ⚠️ **Bar not aligned** - aggregator timezone bug
- ⚠️ **High latency** (>60s) - processing delay, check CPU

---

### 4. **Status Updates (Every Minute)**

```
[STATUS @ 5.2m] Remaining: 9.8m
  Trades: 2,453 total (8.2/sec current rate)
  Bars completed: 1
  WebSocket: connected
```

**What to look for:**
- ✅ **Steady trade rate** (>1/sec for AAPL during market hours)
- ✅ **WebSocket: connected** - healthy connection
- ⚠️ **No trades received** - market closed or connection dead
- ⚠️ **WebSocket: disconnected** - connection lost, check logs

---

### 5. **Collection Summary**

After data collection ends:

```
DATA COLLECTION SUMMARY
======================================================================

Collection duration: 15.0 minutes (900s)
Total trades received: 2,500
Trade rate: 2.8 trades/second

Per-symbol trade distribution:
  AAPL: 2500 trades (100.0%)

Trade timing analysis:
  Average gap between trades: 0.36s
  Maximum gap: 12.3s
  Gaps >5s: 3 occurrences

Bar completion latencies:
  Average: 3.2s after bar end
  Min: 1.1s
  Max: 8.5s

Price volatility (per symbol):
  AAPL:
    Avg price change: $0.015
    Max price change: $0.25
```

**What to look for:**
- ✅ **Trade rate 2-10/sec** - healthy volume
- ✅ **Avg gap <1s** - consistent stream
- ✅ **Few gaps >5s** - occasional is OK
- ⚠️ **Trade rate <0.5/sec** - low volume or connection issue
- ⚠️ **Many gaps >5s** - unstable connection

---

### 6. **Yahoo Finance Comparison**

```
Comparing first bar with Yahoo Finance...
  Finnhub bar: 2026-02-21 14:30:00 EST
    O=150.20 H=150.45 L=150.15 C=150.32 V=125000

  Yahoo Finance bar: 2026-02-21 14:30:00 EST
    O=150.19 H=150.46 L=150.14 C=150.33 V=425000

  Comparison:
    Open:  $0.01 diff [OK]
    High:  $0.01 diff [OK]
    Low:   $0.01 diff [OK]
    Close: $0.01 diff [OK]
    Volume: 29.4% of Yahoo Finance [WARN] (free tier expected)
    Timestamp: 0s diff [OK]

  [OK] Bar data matches Yahoo Finance within tolerance!
```

**What to look for:**
- ✅ **OHLC diff <$0.10** - data is accurate
- ✅ **Timestamp 0s diff** - bars aligned perfectly
- ✅ **Volume 20-50%** - expected for free tier
- ⚠️ **OHLC diff >$0.50** - aggregation bug or bad data
- ⚠️ **Timestamp >5min diff** - timezone bug or misalignment

---

## 🚨 Common Issues & What They Mean

### Issue: "No trades received in last 60s"
**Possible causes:**
- Market is closed
- Very low trading volume (rare for AAPL)
- WebSocket disconnected
- API rate limit hit

**Action:** Check if market is open, look for websocket disconnect events

---

### Issue: "WebSocket disconnected! Attempting reconnect..."
**Possible causes:**
- Network interruption
- Finnhub server issue
- API key invalid/expired
- Rate limit exceeded (too many requests)

**Action:** Check network connection, verify API key is valid

---

### Issue: "Bar not aligned to 5-minute boundary! (minute=32)"
**Possible causes:**
- Timezone bug (timestamps not in correct TZ)
- Aggregator initialization at wrong time
- Clock drift on server

**Action:** Check timezone handling in BarAggregator, ensure EST is used

---

### Issue: High bar completion latency (>60s)
**Possible causes:**
- CPU overload (aggregator can't keep up)
- Too many symbols being aggregated
- Blocking I/O in aggregator callbacks

**Action:** Reduce symbols, profile CPU usage, check for blocking operations

---

### Issue: OHLCV values differ >$1.00 from Yahoo Finance
**Possible causes:**
- Wrong timestamp (comparing different bars)
- Timezone bug (Finnhub bar offset by hours)
- Aggregation bug (trades not being added correctly)
- Bad data from Finnhub

**Action:** Verify timestamps match exactly, check timezone conversion

---

## 📊 Performance Benchmarks

**Healthy metrics for AAPL during market hours:**

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| Trade rate | >2/sec | 0.5-2/sec | <0.5/sec |
| Trade latency | <500ms | 500-2000ms | >2000ms |
| Bar completion | <10s | 10-30s | >30s |
| Gaps >5s | <5/15min | 5-15/15min | >15/15min |
| OHLC accuracy | <$0.10 | $0.10-0.50 | >$0.50 |
| Volume ratio | 20-50% | 10-20% | <10% |

---

## 💡 Tips for Debugging

1. **Run during peak hours** (10 AM - 2 PM EST) for best results
2. **Compare multiple bars** - don't judge on just one bar
3. **Check system time** - ensure your clock is accurate
4. **Monitor network** - use `ping finnhub.io` to check connectivity
5. **Test with high-volume stocks** - AAPL, SPY, QQQ are best
6. **Save output to file** - `python test_finnhub_bar_accuracy.py > test_output.txt 2>&1`

---

## 🔧 Customizing the Test

Edit these lines in the script:

```python
# Line ~390
symbols = ["AAPL"]  # Change to test multiple symbols
duration = 15  # Change test duration (minutes)
verbose = True  # Set False to reduce logging
```

**Multi-symbol test:**
```python
symbols = ["AAPL", "GOOGL", "MSFT"]  # Test 3 symbols
```

**Quick test (5 minutes):**
```python
duration = 5
```

**Minimal logging:**
```python
verbose = False  # Only show bar completions
```

---

## 📝 Saving Diagnostic Output

**Save full output:**
```bash
python test_finnhub_bar_accuracy.py > finnhub_test_$(date +%Y%m%d_%H%M).txt 2>&1
```

**Save and watch:**
```bash
python test_finnhub_bar_accuracy.py 2>&1 | tee finnhub_test.txt
```

This creates a log file you can analyze later or share for troubleshooting.

---

## ✅ Success Checklist

After running the test, verify:

- [ ] Trades received (>100 trades in 15 minutes)
- [ ] At least 1 complete bar
- [ ] Bar timestamps aligned to 5-minute boundaries
- [ ] Bar timestamps in EST (not UTC)
- [ ] OHLC values match Yahoo Finance within $0.10
- [ ] No websocket disconnections
- [ ] Trade latency <500ms
- [ ] Bar completion latency <10s

If all checked, your Finnhub integration is working correctly! 🎉

---

**Last Updated:** 2026-02-20
**Test Script Version:** 2.0 (Enhanced Diagnostics)
