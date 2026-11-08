#!/usr/bin/env python3
"""Measure trading-cycle time and process memory with N symbols (mock dry run).

Runs the real ``TradingOrchestrator._trading_cycle`` (real ORB strategy,
indicator engine, portfolio risk manager, resting-stop placement) against a
mock exchange and synthetic 5-minute bars for a full session, then reports
per-cycle loop time and resident memory. Use it on the VM (956 MiB host)
before switching ``ACTIVE_RULESET`` to the five-symbol ruleset::

    python scripts/measure_multi_symbol_footprint.py --ruleset orb_resting_stop_paper_5sym

No broker, network or secrets are used.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ET = ZoneInfo("America/New_York")


def rss_mib() -> float:
    """Current resident set size in MiB (Linux /proc or Windows API)."""
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
        ]
        kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        if not kernel32.K32GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return float("nan")
        return counters.WorkingSetSize / (1024.0 * 1024.0)
    return float("nan")


def session_bars(day: datetime, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    stamps = [day + timedelta(minutes=5 * i) for i in range(78)]
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(stamps))))
    opens = np.concatenate([[100.0], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.001, len(stamps))) * closes
    return pd.DataFrame({
        "timestamp": pd.to_datetime(stamps),
        "open": opens,
        "high": np.maximum(opens, closes) + spread,
        "low": np.minimum(opens, closes) - spread,
        "close": closes,
        "volume": rng.integers(10_000, 90_000, len(stamps)).astype(float),
    })


async def run(ruleset_name: str, cycles: int) -> None:
    from vibe.common.indicators.engine import IncrementalIndicatorEngine
    from vibe.common.risk import PositionSizer
    from vibe.common.ruleset.loader import RuleSetLoader
    from vibe.common.strategies import ORBStrategy
    from vibe.common.strategies.orb import ORBStrategyConfig
    from vibe.trading_bot.config.settings import AppSettings
    from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator
    from vibe.trading_bot.exchange.mock_exchange import MockExchange
    from vibe.trading_bot.execution.order_manager import OrderManager
    from vibe.trading_bot.execution.portfolio_risk import (
        PortfolioRiskManager,
        SessionEquitySnapshot,
    )
    from vibe.trading_bot.execution.trade_executor import TradeExecutor

    baseline = rss_mib()
    ruleset = RuleSetLoader.from_name(ruleset_name)
    symbols = list(ruleset.instruments.symbols)
    day = datetime(2026, 7, 20, 9, 30, tzinfo=ET)
    bars = {symbol: session_bars(day, seed) for seed, symbol in enumerate(symbols)}

    with tempfile.TemporaryDirectory() as tmp:
        scheduler = MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 9, 36), timezone="America/New_York"
        )
        orchestrator = TradingOrchestrator(
            config=AppSettings(
                environment="test",
                database_path=str(Path(tmp) / "trades.db"),
                broker={"broker_type": "mock", "mode": "paper"},
                trading={"symbols": symbols},
                dashboard={"enabled": False},
                operational_metrics={"enabled": False},
                notifications={"discord_webhook_url": None},
            ),
            ruleset=ruleset,
            market_scheduler=scheduler,
            testing_mode=True,
        )
        exchange = MockExchange(initial_capital=100_000.0, partial_fill_probability=0.0)
        manager = PortfolioRiskManager(ruleset.portfolio, PositionSizer(risk_pct=0.01))
        manager.record_snapshot(SessionEquitySnapshot(day.date(), 100_000.0, day))
        orchestrator.exchange = exchange
        orchestrator.portfolio_risk_manager = manager
        orchestrator.trade_executor = TradeExecutor(
            exchange=exchange,
            order_manager=OrderManager(exchange=exchange),
            position_sizer=PositionSizer(risk_pct=0.01),
            risk_manager=manager,
        )
        orchestrator.strategy = ORBStrategy(config=ORBStrategyConfig(
            name="ORB", orb_start_time="09:30", orb_duration_minutes=5,
            orb_body_pct_filter=0.0, entry_cutoff_time="15:00", take_profit_multiplier=0.0,
        ))
        orchestrator.indicator_engine = IncrementalIndicatorEngine(state_dir=Path(tmp) / "ind")
        orchestrator.active_provider = None
        state = {"n": 2}

        async def get_data(symbol, **kwargs):
            return bars[symbol].iloc[: state["n"]].copy()

        orchestrator.data_manager = SimpleNamespace(get_data=get_data)

        durations, peak = [], baseline
        for cycle in range(min(cycles, 77)):
            state["n"] = cycle + 2
            current = day + timedelta(minutes=5 * (cycle + 1), seconds=30)
            scheduler.set_time(current.hour, current.minute)
            for symbol in symbols:
                await exchange.set_price(symbol, float(bars[symbol]["close"].iloc[state["n"] - 1]))
            await exchange.process_pending_orders()
            started = time.perf_counter()
            await orchestrator._trading_cycle()
            durations.append((time.perf_counter() - started) * 1000.0)
            peak = max(peak, rss_mib())
        await orchestrator.trade_executor.order_manager.shutdown()
        orchestrator.trade_store.close()

    print(f"ruleset={ruleset_name} symbols={len(symbols)} cycles={len(durations)}")
    print(
        f"cycle_ms: median={statistics.median(durations):.0f} "
        f"p95={sorted(durations)[int(0.95 * (len(durations) - 1))]:.0f} max={max(durations):.0f} "
        f"(one 5m bar = 300000 ms)"
    )
    print(f"rss_mib: before={baseline:.0f} peak={peak:.0f} delta={peak - baseline:.0f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ruleset", default="orb_resting_stop_paper_5sym")
    parser.add_argument("--cycles", type=int, default=77)
    args = parser.parse_args(argv)
    asyncio.run(run(args.ruleset, args.cycles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
