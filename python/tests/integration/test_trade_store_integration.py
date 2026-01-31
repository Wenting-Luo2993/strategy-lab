"""
Test TradeStore integration with DarkTradingOrchestrator

Quick test to verify trades are being recorded to the database.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
import pandas as pd
from src.orchestrator.dark_trading_orchestrator import DarkTradingOrchestrator
from src.exchange.mock_exchange import MockExchange
from src.strategies.orb import ORBStrategy
from src.config.strategy_config_factory import build_default_orb_strategy_config
from src.risk_management.percentage_stop import PercentageStop
from src.core.trade_manager import TradeManager
from src.data.cache import CacheDataLoader
from src.data.trade_store import TradeStore
from src.utils.logger import get_logger

logger = get_logger("TestTradeStoreIntegration")


def test_trade_store_integration():
    """Test that trades are recorded to TradeStore database."""
    logger.info("=== Testing TradeStore Integration ===\n")

    # 1. Create minimal orchestrator setup
    logger.info("1. Setting up orchestrator components...")
    strategy = ORBStrategy(build_default_orb_strategy_config())
    risk_manager = PercentageStop(stop_loss_pct=2.0)
    trade_manager = TradeManager(initial_capital=100000, position_size_pct=0.5, max_positions=3)
    exchange = MockExchange()

    # Create a mock data fetcher (we won't actually use it for this test)
    class MockDataFetcher(CacheDataLoader):
        def fetch(self, ticker, **kwargs):
            return pd.DataFrame()

    data_fetcher = MockDataFetcher(None, interval='5m')

    # 2. Initialize orchestrator
    logger.info("2. Initializing orchestrator...")
    orchestrator = DarkTradingOrchestrator(
        strategy=strategy,
        risk_manager=risk_manager,
        trade_manager=trade_manager,
        exchange=exchange,
        data_fetcher=data_fetcher,
        tickers=["AAPL"],
        run_id="test_integration"
    )

    # Verify TradeStore was initialized
    assert orchestrator.trade_store is not None, "TradeStore should be initialized"
    logger.info("   ✓ Orchestrator created with TradeStore")

    # 3. Simulate a trade by calling _record_trade directly
    logger.info("\n3. Simulating trade execution...")

    test_order = {
        "ticker": "AAPL",
        "side": "buy",
        "qty": 100,
        "timestamp": pd.Timestamp.now()
    }

    test_response = {
        "status": "filled",
        "filled_qty": 100,
        "avg_fill_price": 150.25,
        "commission": 1.0,
        "order_id": "TEST_001",
        "timestamp": pd.Timestamp.now()
    }

    orchestrator._record_trade(test_order, test_response)
    logger.info("   ✓ Trade recorded via _record_trade()")

    # 4. Verify trade was saved to database
    logger.info("\n4. Verifying trade in database...")
    recent_trades = orchestrator.trade_store.get_recent_trades(limit=1)

    assert len(recent_trades) > 0, "At least one trade should be in database"
    trade = recent_trades[0]

    assert trade['symbol'] == 'AAPL', f"Symbol should be AAPL, got {trade['symbol']}"
    assert trade['side'] == 'BUY', f"Side should be BUY, got {trade['side']}"
    assert trade['quantity'] == 100, f"Quantity should be 100, got {trade['quantity']}"
    assert trade['price'] == 150.25, f"Price should be 150.25, got {trade['price']}"

    logger.info("   ✓ Trade found in database:")
    logger.info(f"      Symbol: {trade['symbol']}")
    logger.info(f"      Side: {trade['side']}")
    logger.info(f"      Quantity: {trade['quantity']}")
    logger.info(f"      Price: ${trade['price']:.2f}")
    logger.info(f"      Strategy: {trade['strategy']}")

    # 5. Test metadata
    logger.info("\n5. Checking metadata...")
    metadata = trade['metadata']
    assert metadata is not None, "Metadata should not be None"
    assert metadata['run_id'] == 'test_integration', f"Run ID should match, got {metadata.get('run_id')}"
    assert metadata['order_id'] == 'TEST_001', f"Order ID should match, got {metadata.get('order_id')}"

    logger.info("   ✓ Metadata captured correctly:")
    logger.info(f"      run_id: {metadata['run_id']}")
    logger.info(f"      order_id: {metadata['order_id']}")
    logger.info(f"      commission: ${metadata['commission']:.2f}")

    # 6. Cleanup
    logger.info("\n6. Cleaning up...")
    orchestrator.stop()
    logger.info("   ✓ Orchestrator stopped and TradeStore closed")

    # 7. Verify we can still query after orchestrator stops
    logger.info("\n7. Testing standalone database access...")
    standalone_store = TradeStore('data/trades.db')
    trades = standalone_store.get_recent_trades(limit=5)
    logger.info(f"   ✓ Found {len(trades)} trade(s) in standalone access")
    standalone_store.close()

    logger.info("\n" + "="*80)
    logger.info("✅ All tests passed! TradeStore integration working correctly.")
    logger.info("="*80 + "\n")

    return 0


if __name__ == '__main__':
    try:
        sys.exit(test_trade_store_integration())
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
