"""Example: DarkTradingOrchestrator in data replay mode using ORBStrategy + FixedATRStop.

Prerequisites:
  * Cached parquet files exist in data_cache/ (e.g., AAPL_5m.parquet) containing
    at least two distinct trading days of intraday 5m bars.

This script replays the final cached day bar-by-bar (one bar per loop) and
executes an Opening Range Breakout strategy with ATR-based risk management.
"""
import logging
from src.exchange.mock_exchange import MockExchange
from src.data.cache import CacheDataLoader
from src.data.replay_cache import DataReplayCacheDataLoader
from src.orchestrator.dark_trading_orchestrator import DarkTradingOrchestrator
from src.config.orchestrator_config import MarketHoursConfig, OrchestratorConfig, DataReplayConfig
from src.core.trade_manager import TradeManager
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.config.parameters import StrategyConfig, OrbConfig, RiskConfig, TrailingStopConfig
from src.visualization.signal_plots import plot_signals_for_run


def build_strategy_config() -> StrategyConfig:
    # Minimal realistic config values (tune as desired)
    orb_cfg = OrbConfig(
        timeframe="5",
        start_time="09:30",
        body_breakout_percentage=0.5,
    )
    trailing_cfg = TrailingStopConfig(
        enabled=True,
        dynamic_mode=True,
        base_trail_r=0.5,
        breakpoints=[[2.0, 1.0], [3.0, 1.5], [5.0, 2.0]],
        levels={2.0: 0.5, 3.0: 1.0, 4.0: 2.0},
    )
    risk_cfg = RiskConfig(
        stop_loss_type="atr", stop_loss_value=1.5,
        take_profit_type="atr", take_profit_value=3.0,
        risk_per_trade=0.01,
        position_allocation_cap_percent=0.25,
        trailing_stop=trailing_cfg,
    )
    # entry_volume_filter (placeholder numeric) + eod_exit flag
    return StrategyConfig(
        orb_config=orb_cfg,
        entry_volume_filter=0,
        risk=risk_cfg,
        eod_exit=True,
    )


def main():
    logging.basicConfig(level=logging.INFO)
    symbols = ["NVDA", "AAPL", "AMZN"]

    # Replay loader (cache-only)
    _ = CacheDataLoader(wrapped_loader=None)  # not strictly needed but shows pattern
    replay_loader = DataReplayCacheDataLoader(
        market_open=MarketHoursConfig().open_time,
        timezone=MarketHoursConfig().timezone,
        start_offset_minutes=10,
        reveal_increment=1,
        cache_dir="data_cache",
    )

    strategy_cfg = build_strategy_config()
    strategy = ORBStrategy(strategy_config=strategy_cfg)
    risk_manager = FixedATRStop(config=strategy_cfg.risk)
    trade_manager = TradeManager(risk_manager=risk_manager, initial_capital=10_000)

    exchange = MockExchange(initial_capital=10_000, force_fill=True)

    orchestrator = DarkTradingOrchestrator(
        strategy=strategy,
        risk_manager=risk_manager,
        trade_manager=trade_manager,
        exchange=exchange,
        data_fetcher=replay_loader,
        tickers=symbols,
        market_hours=MarketHoursConfig(),
        orchestrator_cfg=OrchestratorConfig(polling_seconds=1, speedup=15, initial_capital=10_000, dry_run=False),
        replay_cfg=DataReplayConfig(enabled=True, timeframe="5m", start_offset_minutes=15, reveal_increment=1, ignore_market_hours=True, replay_sleep_seconds=0.05),
        results_dir="python/results",
        run_id="example01",
    )

    orchestrator.start()
    orchestrator.stop()

    paths = plot_signals_for_run(
        run_id="example01",
        results_dir="python/results",
        output_dir="python/results/images",
        style="candlestick",  # or 'line' if mplfinance missing
        show=False
    )
    print(paths)

if __name__ == "__main__":
    main()
