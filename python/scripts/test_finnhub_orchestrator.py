#!/usr/bin/env python3
"""
Test Finnhub integration with DarkTradingOrchestrator using ORBStrategy.

Run during market hours to test live signal generation and order placement.
"""

import sys
import time
from pathlib import Path
from datetime import datetime
import pytz

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.finnhub_loader import FinnhubWebSocketLoader
from src.exchange.mock_exchange import MockExchange
from src.strategies.orb import ORBStrategy
from src.config.strategy_config_factory import StrategyConfigFactory
from src.risk_management.percentage_stop import PercentageStopLoss
from src.core.trade_manager import TradeManager
from src.orchestrator.dark_trading_orchestrator import DarkTradingOrchestrator
from src.config.orchestrator_config import MarketHoursConfig, OrchestratorConfig
from src.utils.logger import get_logger

logger = get_logger("TestFinnhubOrchestrator")

def main():
    logger.info("=== Finnhub Orchestrator Integration Test ===")

    # Configuration
    tickers = ["AAPL", "MSFT", "NVDA"]  # 3 tickers for testing
    initial_capital = 100000.0
    test_duration = 3600  # 1 hour

    # Check market hours
    market_tz = pytz.timezone("America/New_York")
    now = datetime.now(market_tz)
    logger.info(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    if now.weekday() >= 5:
        logger.error("ERROR: Market is closed (weekend)")
        return 1

    market_open = datetime.strptime("09:30", "%H:%M").time()
    market_close = datetime.strptime("16:00", "%H:%M").time()

    if now.time() < market_open or now.time() > market_close:
        logger.error(f"ERROR: Market is closed ({now.time()})")
        logger.info("Please run during market hours: 09:30-16:00 ET")
        return 1

    logger.info("✓ Market is OPEN - proceeding with test")

    # 1. Create Finnhub Loader
    logger.info("\n1. Initializing Finnhub WebSocket Loader...")
    try:
        loader = FinnhubWebSocketLoader(mode="live", auto_connect=False)
        logger.info("✓ Loader created (live mode)")
    except Exception as e:
        logger.error(f"Failed to create loader: {e}")
        return 1

    # 2. Create strategy
    logger.info("\n2. Initializing ORBStrategy...")
    try:
        config = StrategyConfigFactory.create_config("ORB", timeframe="5m")
        strategy = ORBStrategy(config)
        logger.info("✓ Strategy created (ORB, 5m timeframe)")
    except Exception as e:
        logger.error(f"Failed to create strategy: {e}")
        return 1

    # 3. Create risk manager
    logger.info("\n3. Initializing Risk Manager...")
    try:
        risk_manager = PercentageStopLoss(stop_loss_pct=2.0)
        logger.info("✓ Risk manager created (2% stop loss)")
    except Exception as e:
        logger.error(f"Failed to create risk manager: {e}")
        return 1

    # 4. Create trade manager
    logger.info("\n4. Initializing Trade Manager...")
    try:
        trade_manager = TradeManager(
            initial_capital=initial_capital,
            position_size_pct=0.5,
            max_positions=3
        )
        logger.info(f"✓ Trade manager created (${initial_capital:,.0f} capital)")
    except Exception as e:
        logger.error(f"Failed to create trade manager: {e}")
        return 1

    # 5. Create exchange
    logger.info("\n5. Initializing Mock Exchange...")
    try:
        exchange = MockExchange()
        logger.info("✓ Mock exchange created")
    except Exception as e:
        logger.error(f"Failed to create exchange: {e}")
        return 1

    # 6. Create orchestrator with live mode
    logger.info("\n6. Initializing Orchestrator (LIVE MODE)...")
    try:
        orchestrator = DarkTradingOrchestrator(
            strategy=strategy,
            risk_manager=risk_manager,
            trade_manager=trade_manager,
            exchange=exchange,
            data_fetcher=loader,
            tickers=tickers,
            market_hours=MarketHoursConfig(),
            orchestrator_cfg=OrchestratorConfig(polling_seconds=65),
            run_id="finnhub_test"
        )
        logger.info(f"✓ Orchestrator created (live_mode={orchestrator.live_mode})")
        logger.info(f"  Tickers: {', '.join(tickers)}")
    except Exception as e:
        logger.error(f"Failed to create orchestrator: {e}")
        return 1

    # 7. Run orchestrator
    logger.info(f"\n7. Starting orchestrator (duration: {test_duration}s = {test_duration//60}m)...")
    logger.info("   Monitoring signal generation and order placement...")
    logger.info("   Press Ctrl+C to stop early\n")

    try:
        start_time = time.time()
        orchestrator.start(run_duration=test_duration)
        elapsed = time.time() - start_time

        logger.info(f"\n✓ Test completed successfully")
        logger.info(f"  Duration: {elapsed:.1f}s ({elapsed/60:.1f}m)")

    except KeyboardInterrupt:
        logger.info("\n⚠ Test interrupted by user")
    except Exception as e:
        logger.error(f"\n✗ Test failed: {e}")
        return 1
    finally:
        # 8. Print final statistics
        logger.info("\n8. Final Statistics:")
        logger.info(f"   Cycles run: {orchestrator.tick_count}")
        logger.info(f"   Orders executed: {orchestrator.orders_executed_this_cycle}")

        # Get Finnhub statistics
        if hasattr(loader, 'get_statistics'):
            stats = loader.get_statistics()
            if 'aggregator' in stats:
                agg_stats = stats['aggregator']
                logger.info(f"   Trades processed: {agg_stats.get('trades_processed', 0)}")
                logger.info(f"   Bars completed: {agg_stats.get('bars_completed', 0)}")

        # Get account status
        account = orchestrator.exchange.get_account_balance()
        logger.info(f"   Account equity: ${account.get('total_balance', 0):,.2f}")
        logger.info(f"   Open positions: {len(orchestrator.exchange.positions)}")

        logger.info("\n✓ Test finished")
        return 0

if __name__ == "__main__":
    sys.exit(main())
