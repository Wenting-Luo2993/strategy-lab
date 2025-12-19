"""Configuration models for DarkTradingOrchestrator and data replay mode.

Moved from src/orchestrator/config.py to central config package.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time, timedelta
from typing import List, Optional

@dataclass
class MarketHoursConfig:
    timezone: str = "America/New_York"
    open_time: time = time(9, 30)
    close_time: time = time(16, 0)
    trading_days: List[int] = field(default_factory=lambda: [0,1,2,3,4])
    auto_stop_grace: Optional[timedelta] = timedelta(minutes=10)
    def should_trade_today(self, weekday: int) -> bool:
        return weekday in self.trading_days

@dataclass
class DataReplayConfig:
    enabled: bool = False
    timeframe: str = "5m"
    start_offset_minutes: int = 0
    reveal_increment: int = 1
    # When True, market hours checks are bypassed (treat market as always open)
    ignore_market_hours: bool = True
    # Sleep duration between cycles in seconds when in replay mode (e.g. 0.05 for 50ms)
    replay_sleep_seconds: float = 0.05

@dataclass
class OrchestratorConfig:
    polling_seconds: int = 60
    speedup: float = 1.0
    initial_capital: float = 10_000.0
    dry_run: bool = False
    # Live mode configuration
    live_mode: bool = False
    live_min_bars: int = 20  # Minimum bars before trading in live mode
    live_warmup_cycles: int = 5  # Number of polling cycles to wait before first trade
