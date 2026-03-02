# Trading Bot - Code Patterns & Architecture

This document defines standard patterns for trading bot development.

**Version:** 1.1.0
**Last Updated:** 2026-02-28

---

## Discord Notification Pattern

All Discord notifications follow a standardized pattern for consistency and maintainability.

### Requirements

1. **Use payload dataclasses** from `vibe/trading_bot/notifications/payloads.py`
2. **Use context manager** from `vibe/trading_bot/notifications/helper.py`
3. **Use embeds** (not plain text) for all notifications
4. **Include version** in footer for traceability

### Standard Pattern

```python
from vibe.trading_bot.notifications.payloads import SystemStatusPayload
from vibe.trading_bot.notifications.helper import discord_notification_context
from vibe.trading_bot.version import BUILD_VERSION

# 1. Create payload
payload = SystemStatusPayload(
    event_type="MARKET_START",
    timestamp=datetime.now(),
    overall_status="healthy",
    warmup_completed=True,
    version=BUILD_VERSION
)

# 2. Send with context manager (auto-handles lifecycle)
async with discord_notification_context(webhook_url) as notifier:
    await notifier.send_system_status(payload)
```

### Available Notification Types

| Payload Class | Event Type | When to Use | Color |
|---------------|------------|-------------|-------|
| `SystemStatusPayload` | `MARKET_START` | Pre-market warmup complete | Green (0x2ecc71) |
| `SystemStatusPayload` | `MARKET_CLOSE` | Market closed | Orange (0xf39c12) |
| `ORBLevelsPayload` | `ORB_ESTABLISHED` | ORB levels calculated | Purple (0x9b59b6) |
| `DailySummaryPayload` | `DAILY_SUMMARY` | End-of-day summary | Green/Red/Gray (P&L based) |
| `OrderNotificationPayload` | `ORDER_SENT` | Order submitted | Blue (0x3498db) |
| `OrderNotificationPayload` | `ORDER_FILLED` | Order filled | Green (0x2ecc71) |
| `OrderNotificationPayload` | `ORDER_CANCELLED` | Order cancelled | Red (0xe74c3c) |

### Message Format Standards

All Discord embeds must include:

- **Title**: Brief, descriptive (e.g., "📊 Daily Summary")
- **Description**: Context or summary (1-2 sentences)
- **Color**: Match event type (see table above)
- **Fields**: Structured data as key-value pairs
  - Use `inline: True` for metrics (up to 3 per row)
  - Use `inline: False` for detailed text
- **Timestamp**: ISO format (`datetime.isoformat()`)
- **Footer**: `"Trading Bot {BUILD_VERSION}"`

### Adding New Notification Types

1. **Create payload** in `vibe/trading_bot/notifications/payloads.py`:
   ```python
   @dataclass
   class MyPayload:
       event_type: str
       timestamp: datetime
       # ... other fields

       def __post_init__(self):
           if self.event_type != "MY_EVENT":
               raise ValueError(f"Invalid event_type: {self.event_type}")
   ```

2. **Add formatter** in `vibe/trading_bot/notifications/formatter.py`:
   ```python
   def format_my_notification(self, payload: MyPayload) -> Dict[str, Any]:
       return {
           "embeds": [{
               "title": "📌 My Event",
               "description": "...",
               "color": 0x3498db,
               "fields": [...],
               "timestamp": payload.timestamp.isoformat(),
               "footer": {"text": f"Trading Bot {payload.version}"}
           }]
       }
   ```

3. **Add notifier method** (if needed) in `vibe/trading_bot/notifications/discord.py`:
   ```python
   async def send_my_notification(self, payload: MyPayload):
       webhook_payload = self.formatter.format_my_notification(payload)
       await self._queue.put(webhook_payload)
   ```

### Anti-Patterns ❌

**Don't do this:**

```python
# ❌ Plain text (not embeds)
await session.post(webhook_url, json={"content": "Market opened"})

# ❌ Manual lifecycle management
notifier = DiscordNotifier(webhook_url)
await notifier.start()
await notifier.send_system_status(payload)
await notifier.stop()  # Easy to forget!

# ❌ Missing version info
"footer": {"text": "Trading Bot"}  # Should include BUILD_VERSION
```

**Do this instead:**

```python
# ✅ Use embeds, context manager, and version
async with discord_notification_context(webhook_url) as notifier:
    await notifier.send_system_status(payload)  # Includes version in footer
```

---

## Phase Manager Pattern

Trading bot lifecycle is organized into phases (warmup, trading, cooldown).

### Creating a Phase Manager

1. **Inherit from `BasePhase`**:

```python
from vibe.trading_bot.core.phases.base import BasePhase

class MyPhaseManager(BasePhase):
    async def execute(self) -> bool:
        """Execute phase logic.

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("Starting my phase...")

        # Access dependencies via properties
        symbols = self.config.trading.symbols
        scheduler = self.market_scheduler

        # Phase implementation here
        success = await self._do_work()

        return success
```

2. **Register in `TradingOrchestrator.__init__`**:

```python
from vibe.trading_bot.core.phases import MyPhaseManager

# In initialize() method after dependencies are ready
self.my_phase_manager = MyPhaseManager(self)
```

3. **Call in `TradingOrchestrator.run()`**:

```python
if should_run_my_phase():
    await self.my_phase_manager.execute()
```

### Existing Phase Managers

| Phase | Class | When | Duration | Purpose |
|-------|-------|------|----------|---------|
| Warmup | `WarmupPhaseManager` | 9:25-9:30 AM EST | ~5 min | Prefetch data, connect provider, health checks |
| Trading | (inline in orchestrator) | 9:30 AM-4:00 PM EST | Market hours | Execute trading strategy |
| Cooldown | `CooldownPhaseManager` | 4:00-4:05 PM EST | 5 min | Process final data, disconnect provider |

### Phase Manager Benefits

- **Modularity**: Each phase is self-contained
- **Testability**: Can test phases in isolation
- **Reusability**: Common patterns abstracted to base class
- **Maintainability**: Clear separation of concerns

### Available Dependencies (via properties)

```python
self.config              # Application configuration
self.market_scheduler    # Market timing & session info
self.data_manager        # Yahoo Finance historical data
self.primary_provider    # Primary real-time data provider
self.secondary_provider  # Fallback data provider
self.active_provider     # Currently active provider
self.indicator_engine    # Indicator calculation engine (ATR, etc.)
self.health_monitor      # System health monitoring
```

---

## Utility Patterns

### DateTime Utilities

Always use timezone-aware datetime operations:

```python
from vibe.trading_bot.utils.datetime_utils import get_market_now, get_market_date

# Get current time in market timezone
now = get_market_now(self.market_scheduler)

# Get current date (ISO format YYYY-MM-DD)
date = get_market_date(self.market_scheduler)

# Format datetime with timezone
formatted = format_market_time(now, "%H:%M:%S %Z")
```

**Don't do this:**

```python
# ❌ Naive datetime (no timezone)
now = datetime.now()
date = now.date().isoformat()
```

**Why it matters:**

- Market timezone (EST/EDT) may differ from system timezone
- Timezone-naive datetimes cause subtle bugs across DST boundaries
- Date changes should be detected in market timezone, not UTC

---

## File Organization

```
vibe/trading_bot/
├── core/
│   ├── orchestrator.py          # Main coordinator
│   ├── phases/
│   │   ├── __init__.py
│   │   ├── base.py              # BasePhase abstract class
│   │   ├── warmup.py            # Pre-market warm-up
│   │   └── cooldown.py          # Post-market cooldown
│   └── market_schedulers/       # Market timing logic
├── notifications/
│   ├── discord.py               # Discord notifier
│   ├── payloads.py              # Payload dataclasses
│   ├── formatter.py             # Embed formatters
│   └── helper.py                # Context managers
└── utils/
    ├── datetime_utils.py        # Timezone-aware datetime helpers
    └── logger.py                # Logging configuration
```

---

## Summary

**Key Principles:**

1. **Consistency**: Follow established patterns (Discord embeds, phase managers, timezone-aware datetimes)
2. **Modularity**: Extract reusable components (phases, utilities, formatters)
3. **Documentation**: Include version info, timestamps, clear logging
4. **Testing**: Test in isolation before integration
5. **Type Safety**: Use dataclasses with validation for structured data

**When in doubt, look at existing implementations:**

- Discord notifications → `WarmupPhaseManager._send_discord_notification()`
- Phase managers → `WarmupPhaseManager` or `CooldownPhaseManager`
- DateTime handling → `vibe/trading_bot/utils/datetime_utils.py`
- Payload dataclasses → `vibe/trading_bot/notifications/payloads.py`

---

## Version History

- **v1.1.0** (2026-02-28): Refactoring for modularity
  - Extracted warmup and cooldown into phase managers
  - Standardized Discord notifications with embeds
  - Created datetime utilities
  - Created this documentation

- **v1.0.8** (2026-02-27): Logging improvements for cloud monitoring
- **v1.0.7** (2026-02-26): ORB Discord notifications and ATR fixes
- **v1.0.6** (2026-02-25): Provider connection state fixes
