"""Example: DarkTradingOrchestrator in data replay mode using ORBStrategy + FixedATRStop.

Prerequisites:
  * Cached parquet files exist in data_cache/ (e.g., AAPL_5m.parquet) containing
    at least two distinct trading days of intraday 5m bars.

This script replays the final cached day bar-by-bar (one bar per loop) and
executes an Opening Range Breakout strategy with ATR-based risk management.
"""
import logging
from src.config.strategy_config_factory import build_orb_atr_strategy_config_with_or_stop
from src.exchange.mock_exchange import MockExchange
from src.data.cache import CacheDataLoader
from src.data.replay_cache import DataReplayCacheDataLoader
from src.orchestrator.dark_trading_orchestrator import DarkTradingOrchestrator
from src.config.orchestrator_config import MarketHoursConfig, OrchestratorConfig, DataReplayConfig
from src.core.trade_manager import TradeManager
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.visualization.signal_plots import plot_signals_for_run

def main():
    logging.basicConfig(level=logging.INFO)
    symbols = ["NVDA", "AAPL", "AMZN"]
    runId = "example03"

    # Replay loader (cache-only)
    replay_loader = DataReplayCacheDataLoader(
        market_open=MarketHoursConfig().open_time,
        timezone=MarketHoursConfig().timezone,
        start_offset_minutes=10,
        reveal_increment=1,
    )

    strategy_cfg = build_orb_atr_strategy_config_with_or_stop()
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
        run_id=runId,
    )

    orchestrator.start()
    orchestrator.stop()

    paths = plot_signals_for_run(
        run_id=runId,
        results_dir="python/results",
        output_dir="python/results/images",
        style="candlestick",  # or 'line' if mplfinance missing
        show=False
    )
    print(paths)

if __name__ == "__main__":
    main()
