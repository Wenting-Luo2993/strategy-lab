# Trading Bot - Lessons Learned & Key Learnings

Accumulated knowledge from production incidents, bugs, and architectural decisions.
Read this alongside `CLAUDE.md` to understand both patterns AND hard-won lessons.

---

## CRITICAL: Timezone Handling for Timestamp Comparisons

**Date**: 2026-03-12
**Rule**: ALWAYS use exchange timezone (EDT/EST) for time comparisons, NEVER use UTC

### The Problem (Bug Found in Production)

**Symptom**: Bars at 2:45 PM EDT were incorrectly rejected as "after_entry_cutoff_time" (3:00 PM cutoff)

**Root Cause**: Timestamps in UTC were compared directly to EDT cutoff times:
```python
# ❌ WRONG: Comparing UTC time to EDT cutoff
current_time = "2026-03-12 18:45:00+00:00"  # 18:45 UTC = 2:45 PM EDT
bar_time = current_time.time()  # 18:45:00 (still UTC!)
entry_cutoff = time(15, 0, 0)   # 15:00 (meant to be EDT)
is_after_cutoff = bar_time >= entry_cutoff  # 18:45 >= 15:00 → True ❌ WRONG!
```

**Why timestamps can be in different timezones:**
- Data providers may send UTC timestamps (Finnhub WebSocket)
- Historical data may be in exchange time (yfinance)
- System time may be UTC (Docker containers, cloud servers)
- Cached data may have mixed timezones

### The Solution

**ALWAYS convert to exchange timezone before extracting time:**

```python
# ✅ CORRECT
import pytz
market_tz = pytz.timezone("America/New_York")

if current_time.tzinfo is None:
    current_time = current_time.replace(tzinfo=pytz.UTC)

current_time_local = current_time.astimezone(market_tz)  # 14:45:00-04:00
bar_time = current_time_local.time()  # 14:45 EDT (correct!)
is_after_cutoff = bar_time >= entry_cutoff  # 14:45 >= 15:00 → False ✅
```

**The `.time()` method preserves the timezone of the datetime object. If your datetime is in UTC, `.time()` gives UTC time. Always convert to exchange timezone FIRST.**

### When This Applies

Use exchange timezone for ALL time-based logic:
- Entry cutoff checks (e.g., "no entries after 3:00 PM")
- Market hours checks (e.g., "is market open?")
- ORB window detection (e.g., "is this the 9:30-9:35 AM bar?")
- EOD exit logic (e.g., "close all positions at 3:55 PM")

### Files Where This Matters

- `vibe/common/strategies/orb.py` - Entry cutoff check (FIXED v1.2.8)
- `vibe/trading_bot/core/market_schedulers/` - Market hours logic
- `vibe/trading_bot/core/orchestrator.py` - EOD exit checks
- Any strategy that uses time-of-day filters

### Testing Checklist

When testing time-based logic, verify:
- [ ] UTC timestamps (`+00:00`)
- [ ] EDT timestamps (`-04:00`)
- [ ] EST timestamps (`-05:00`) for winter months
- [ ] Naive timestamps (no timezone)
- [ ] Boundary conditions (exactly at cutoff time)
- [ ] Before/after cutoff (e.g., 14:59 vs 15:01)

---

## CRITICAL: Strategy-Agnostic Orchestrator Design

**Date**: 2026-03-11
**Rule**: ALWAYS design for multiple strategies - NEVER deeply couple strategy-specific logic into orchestrator

**Always design for potentially multiple different strategies. Never deeply couple a specific strategy into orchestrator.**

```python
# ❌ WRONG - Hardcoded ORB logic in orchestrator:
if bar_dict['timestamp'].hour == 9 and bar_dict['timestamp'].minute == 30:
    orb_levels = self.orb_calculator.calculate(...)

# ✅ CORRECT - Strategy defines what's important:
if self.strategy.should_process_bar_immediately(bar_dict['timestamp']):
    event = self.strategy.on_important_bar_complete(symbol, bars_df)
```

When implementing new features, ask: "Is this specific to ONE strategy, or applicable to ALL strategies?"
- Specific to one strategy → put it IN the strategy class
- Applicable to all → put it in orchestrator as a generic interface

---

## Market Scheduler Dependency Injection (v1.2.0)

**Date**: 2026-03-06
**Rule**: Inject market scheduler to enable full integration testing with time control

Multi-day persistence bugs manifest "next day" and can't be caught by intraday restarts. Refactor orchestrator to accept market scheduler as a dependency:

```python
# Production (unchanged)
orchestrator = TradingOrchestrator(config)

# Testing (new capability)
mock_scheduler = MockMarketScheduler()
mock_scheduler.set_time(9, 30)  # Jump to market open
orchestrator = TradingOrchestrator(config, mock_scheduler)
```

**CRITICAL: Run 3-day cycle integration test before deploying orchestrator or phase changes!**

```bash
# Stop Oracle Cloud first (Finnhub 1 connection limit)
ssh ubuntu@146.235.228.116 "cd strategy-lab/vibe/trading_bot && docker compose down"
python test_orchestrator_daily_cycle.py
```

---

## Finnhub Subscription Persistence Bug (Fixed v1.1.12)

**Date**: 2026-03-06
**Rule**: `disconnect()` MUST clear `subscribed_symbols`, or stale subscriptions persist across days

**Silent failure pattern:**
- Day 1: Subscribe → `subscribed_symbols = {AAPL, GOOGL, MSFT}`
- Cooldown: Disconnect → `_connected = False`, but `subscribed_symbols` intact
- Day 2 warmup: Try subscribe → Already in set → Skip (silent!)
- Result: No subscription messages → No trades → No bars → No ORB

```python
# ✅ Fix: Explicitly clear subscriptions on disconnect
async def disconnect(self) -> None:
    self._connected = False
    self.subscribed_symbols.clear()  # CRITICAL!
```

**Verify fix is working:** Look for individual "Subscribed to AAPL/GOOGL/MSFT" logs in warmup.

---

## Finnhub WebSocket Gap Detection Threshold (Fixed v1.1.9)

**Date**: 2026-03-05
**Rule**: Gap detection threshold must accommodate Finnhub's ping interval + network variability

```python
# ❌ OLD: Too aggressive (same as ping interval = false reconnects)
GAP_DETECTION_THRESHOLD = 60

# ✅ NEW: Allows for ping interval + buffer
GAP_DETECTION_THRESHOLD = 90  # 60s ping + 30s buffer
```

One bad threshold caused: gap detected → reconnect at 9:30 AM → callbacks disrupted → no bars all day.

---

## Exception Logging Standard

**Date**: 2026-03-04
**Rule**: Every exception handler MUST log with `exc_info=True`

```python
# ✅ CORRECT
except Exception as e:
    self.logger.error(f"Operation failed: {e}", exc_info=True)

# ❌ WRONG - silent failure
except Exception as e:
    return {"status": "fail", "output": str(e)}
```

---

## Bar Aggregator Lifecycle (CRITICAL!)

**Date**: 2026-03-03
**Rule**: NEVER clear `bar_aggregators` dict - it destroys registered callbacks

```python
# ❌ WRONG: Destroys aggregator objects and callbacks
self.bar_aggregators.clear()

# ✅ CORRECT: Only clear stored bar data, keep aggregators alive
self._realtime_bars.clear()
```

Clearing aggregators destroys callbacks. Even if the provider reconnects later, bars will never be generated. Symptoms: trades flowing in logs ✅, no bars generated ❌, no errors ❌.

---

## Cooldown Phase - Tight Loop Bug (Fixed v1.1.7)

**Date**: 2026-03-04
**Rule**: Async phase `execute()` methods must explicitly `return` when work is done

```python
async def execute(self) -> None:
    if not self._market_closed_logged:
        await self._complete_cooldown()
        self._market_closed_logged = True
    return  # ✅ REQUIRED: prevents tight loop
```

Also: warmup phase must reset cooldown manager state for fresh start each day.

---

## Deployment & Operations

### Docker Rebuild Required for Code Changes

```bash
# ❌ WRONG: Does NOT pick up code changes
docker compose restart

# ✅ RIGHT: Rebuilds with new code
docker compose down && docker compose build && docker compose up -d
```

### Always Validate Locally Before Deploying

```bash
# Lightweight: validate imports/syntax
python -m py_compile path/to/modified_file.py

# Or run unit tests
pytest tests/test_my_module.py -v
```

### Version Verification After Deploy

```bash
docker compose logs trading-bot | grep "Trading Bot"
# Should show: Trading Bot v1.x.x (YYYY-MM-DD HH:MM:SS UTC)
```

### Oracle Cloud SSH

```bash
ssh -i "C:\Users\wentingluo\OneDrive - Microsoft\Personal\Development\ssh-key-2025-12-21-oracle-cloud-private.key" ubuntu@146.235.228.116
```

### Finnhub Free Tier: ONE WebSocket Connection Only

When WebSocket testing is needed locally, stop Oracle Cloud first:
```bash
ssh ubuntu@146.235.228.116 "cd strategy-lab/vibe/trading_bot && docker compose down"
```

### Permission Fixes

```bash
cd strategy-lab && sudo chown -R ubuntu:ubuntu .
cd vibe/trading_bot && sudo chown -R 1000:1000 data logs
```

---

## Windows Unicode Encoding in Test Scripts

```python
# ❌ BAD: Unicode emojis cause errors on Windows console
print("✅ Success")

# ✅ GOOD: ASCII-safe
print("[OK] Success")
print("[!] Warning")
print("[ERROR] Error")
```

---

## Phase Responsibility Principle

> All cleanup/setup steps for market start should live in warmup phase. All cleanup steps for market close should live in cooldown phase.

The orchestrator should just call phases at appropriate times, with no scattered setup/cleanup logic.

---

## Tick Logging Best Practices

1. Daily rotation: New file each UTC day
2. Fail-safe: Disable on error, trading continues
3. Flush immediately: Don't buffer ticks
4. Auto-clean: Delete files older than TTL (3 days default)
5. UTC timestamps for consistency
