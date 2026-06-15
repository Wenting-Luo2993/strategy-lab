# System Patterns

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Trading Orchestrator                         │
│  (Lifecycle: Warmup → Trading → Cooldown)                       │
└────────┬────────────────────────────────┬──────────────────┬────┘
         │                                │                  │
    ┌────▼─────┐                    ┌────▼─────┐      ┌────▼─────┐
    │  Phase   │                    │  Data    │      │ Discord  │
    │ Managers │                    │ Pipeline │      │ Notifier │
    └────┬─────┘                    └────┬─────┘      └──────────┘
         │                                │
    ┌────▼─────────────┐          ┌──────▼────────────────┐
    │ - WarmupPhase    │          │ - Yahoo (historical)  │
    │ - CooldownPhase  │          │ - Polygon (real-time) │
    │                  │          │ - Finnhub (backup)    │
    └──────────────────┘          └───────────────────────┘
                                           │
                                   ┌───────▼────────┐
                                   │ Indicator      │
                                   │ Engine (ATR)   │
                                   └────────────────┘
```

**Core Components:**
1. **TradingOrchestrator**: Central coordinator for lifecycle and dependencies
2. **Phase Managers**: Modular pre/post-market logic (warmup, cooldown)
3. **Data Providers**: Abstract interface with Polygon/Finnhub implementations
4. **Market Scheduler**: Timezone-aware market timing and session detection
5. **Indicator Engine**: Batch computation of technical indicators (ATR, ADX)
6. **Discord Notifier**: Asynchronous webhook-based notifications with embeds
7. **Backtester**: Event-driven simulation with regime research framework

## Technology Stack

### Core
- **Language**: Python 3.12+ (asyncio-native for real-time operations)
- **Orchestration**: `asyncio` event loops for concurrent data/trading workflows
- **Configuration**: YAML files (`config/dev.yaml`, `config/prod.yaml`)
- **Logging**: Structured JSON logs with `structlog` (Cloud Logging compatible)

### Data
- **Historical**: Yahoo Finance via `yfinance` (5-minute bars, 60-day cache)
- **Real-time**: 
  - Polygon.io WebSocket API (tick/bar aggregation)
  - Finnhub WebSocket API (backup, lower quality)
- **Storage**: CSV files in `data/yahoo/`, cloud storage planned

### Backtesting
- **Engine**: Custom event-driven backtester (`vibe/backtester/`)
- **Execution Model**: Intrabar stop detection, bar flush timing, slippage modeling
- **Analytics**: Pandas/NumPy for metrics, regime research framework

### Notifications
- **Discord**: Webhook API with structured embeds (color-coded, versioned)
- **Payloads**: Dataclasses with validation (`vibe/trading_bot/notifications/payloads.py`)

## Data Flow

### Warmup Phase (9:25-9:30 AM EST)
```
1. Prefetch historical data (Yahoo) → cache to disk
2. Calculate indicators (ATR, volatility) → store in memory
3. Connect to primary provider (Polygon) → health check
4. If primary fails → connect to backup (Finnhub)
5. Send "Market Start" Discord notification → include version + health status
```

### Trading Phase (9:30 AM-4:00 PM EST)
```
1. Real-time bars arrive → aggregate ticks to 5-minute bars
2. Detect ORB setup → calculate entry/stop levels
3. Submit order → track fill status
4. Monitor position → enforce stop-loss or EOD exit
5. Send order notifications → Discord (sent, filled, cancelled)
```

### Cooldown Phase (4:00-4:05 PM EST)
```
1. Flush final bars → ensure EOD data captured
2. Calculate daily metrics → P&L, R-multiple, win rate
3. Disconnect providers → clean shutdown
4. Send "Daily Summary" Discord notification → include equity curve
```

## Recurring Design Patterns

### 1. Phase Manager Pattern
**Purpose**: Modular, self-contained pre/post-market logic

**Structure**:
```python
from vibe.trading_bot.core.phases.base import BasePhase

class MyPhaseManager(BasePhase):
    async def execute(self) -> bool:
        """Execute phase logic. Returns True if successful."""
        self.logger.info("Starting phase...")
        
        # Access dependencies via properties
        symbols = self.config.trading.symbols
        scheduler = self.market_scheduler
        
        # Phase implementation
        success = await self._do_work()
        return success
```

**Benefits**: Testability, dependency injection, clear lifecycle

### 2. Discord Notification Pattern
**Purpose**: Consistent, versioned, structured notifications

**Standard Pattern**:
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

**Requirements**: Use payloads (not dicts), use embeds (not plain text), include version

### 3. Timezone-Aware DateTime Pattern
**Purpose**: Prevent DST bugs and ensure market-aligned date changes

**Standard Pattern**:
```python
from vibe.trading_bot.utils.datetime_utils import get_market_now, get_market_date

# Get current time in market timezone (EST/EDT)
now = get_market_now(self.market_scheduler)

# Get current date (ISO format YYYY-MM-DD)
date = get_market_date(self.market_scheduler)
```

**Anti-pattern**: ❌ `datetime.now()` or `now.date().isoformat()` (naive timezone)

### 4. Provider Abstraction Pattern
**Purpose**: Swappable real-time data sources with consistent interface

**Interface**:
```python
class BaseRealTimeProvider(ABC):
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to provider. Returns True if successful."""
        
    @abstractmethod
    async def subscribe_bars(self, symbols: List[str], timeframe: str):
        """Subscribe to real-time bars."""
        
    @abstractmethod
    async def disconnect(self):
        """Clean shutdown."""
```

**Implementations**: `PolygonRealTimeProvider`, `FinnhubRealTimeProvider`

### 5. Regime Research Framework Pattern
**Purpose**: Test trading filters with overfitting prevention

**Workflow**:
```
1. Feature Engine → Compute indicators with forward-observable rules
2. Trade Attribution → Join trades with as-of features (no leakage)
3. Day Regime Labeler → Classify days (trending_up/down/ranging + vol)
4. Filter Evaluator → Test hypothesis across full history
5. Reporting → Markdown + JSON output with year-by-year breakdown
```

**Key Guardrails**: Full-history validation, out-of-sample testing, mechanistic reasoning

**Causality Guardrail (Backtest Research)**:
- Features must be available at decision time for the trade being evaluated.
- Treat non-causal features as diagnostic only; do not rank or promote them as candidate gates.
- Examples of non-causal usage to avoid for early entries: full-day `range_vs_adr`, completed-window `first3_rel_vol` before that window closes.
- Prefer forward-safe variants such as `range_so_far_vs_adr` and time-aligned `rel_vol_so_far`.

### 6. Reusable Hypothesis Optimization Pattern
**Purpose**: Run hypothesis-driven sweeps without creating one-off scripts

**Standard Pattern**:
```powershell
# Reusable optimization + journal workflow
python scripts/optimize_strategy.py \
    --strategy orb \
    --mode full \
    --trailing-breakeven \
    --trailing-only \
    --trigger-rs 1.0,2.0,2.5,3.0 \
    --plus-ticks 0,1,2,3,5 \
    --journal \
    --hypothesis-id HYP-004
```

**Best Practice Rules**:
1. Prefer extending generic infrastructure (`optimize_strategy.py`, optimization pipeline, ruleset models) over adding hypothesis-specific scripts.
2. Register each sweep row as a journal experiment for full lineage and reproducibility.
3. Keep hypothesis logic in parameters and metadata (CLI flags, hypothesis IDs, tags), not in ad-hoc orchestration files.
4. Retire one-off research scripts after generic capability lands.
5. Default to quiet logging for sweeps: unless debugging strategy/indicator/engine internals, force `vibe.common.strategies*` and `vibe.common.indicators*` loggers to WARNING.

### 7. Execution Mode Contract Pattern (ROES)
**Purpose**: Preserve historical comparability while enabling realistic execution research.

**Contract**:
1. `BacktestEngine(..., execution_config=None)` keeps legacy-compatible default behavior.
2. Realistic fill is enabled only by explicitly passing `execution_config`.
3. In realistic mode, avoid forcing ORB `price_override` so slippage/impact models can apply.

**Reference**: `memory-bank/features/realistic-fill-guide.md`

### 8. Research Journal Workflow Pattern
**Purpose**: Make strategy research reproducible, traceable, and auditable.

**Workflow**:
1. Create hypothesis via `ResearchRegistry`.
2. Create experiments with execution metadata capture.
3. Complete experiments with immutable results and conclusions.
4. Track lineage for parameter iterations.
5. Query and verify artifacts for analysis and reporting.

**Reference**: `memory-bank/features/research-journal-guide.md`

## Integration Points

### External Services
- **Polygon.io**: WebSocket API for real-time QQQ ticks/bars
- **Finnhub**: WebSocket API (backup provider, lower quality)
- **Yahoo Finance**: HTTP API for historical 5-minute bars
- **Discord**: Webhook API for notifications

### Configuration
- **Environment**: `ENV` variable selects config file (`dev`, `local`, `prod`)
- **Secrets**: Polygon API key, Finnhub API key, Discord webhook URL
- **Trading Params**: Position size, symbols, stop-loss, take-profit (optional)

## File Organization

```
vibe/trading_bot/
├── core/
│   ├── orchestrator.py          # Main coordinator
│   ├── phases/
│   │   ├── base.py              # BasePhase abstract class
│   │   ├── warmup.py            # Pre-market warmup
│   │   └── cooldown.py          # Post-market cooldown
│   └── market_schedulers/       # Market timing logic
├── notifications/
│   ├── discord.py               # Discord notifier
│   ├── payloads.py              # Payload dataclasses
│   ├── formatter.py             # Embed formatters
│   └── helper.py                # Context managers
├── data/
│   ├── providers/
│   │   ├── base.py              # Abstract provider interface
│   │   ├── polygon.py           # Polygon implementation
│   │   └── finnhub.py           # Finnhub implementation
│   ├── yahoo.py                 # Historical data fetcher
│   └── indicator_engine.py      # ATR, ADX, etc.
└── utils/
    ├── datetime_utils.py        # Timezone-aware helpers
    └── logger.py                # Structured logging

vibe/backtester/
├── backtester.py                # Event-driven backtest engine
├── execution/                   # Order execution simulation
├── analysis/
│   └── regime_research/         # 6-stage regime framework
└── strategies/
    └── orb.py                   # ORB strategy implementation
```
