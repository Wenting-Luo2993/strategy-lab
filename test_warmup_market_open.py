"""
Test script to verify warm-up phase → market open transition.

This simulates the exact flow that happens in production:
1. 9:25 AM - Warm-up phase starts, connects to Finnhub
2. 9:25-9:30 - Monitor for timeout/reconnect issues during warm-up
3. 9:30 AM - Market opens, trading cycle starts
4. 9:30-9:32 - Monitor connection stability during trading

This tests the fix for:
- Stale ping timestamps on reconnection
- Timeout issues during warm-up (90s timeout instead of 30s)
- Smooth transition from warm-up to market open
"""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from unittest.mock import Mock, patch
from pathlib import Path

from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress some verbose logs
logging.getLogger("vibe.trading_bot.data.cache").setLevel(logging.WARNING)
logging.getLogger("vibe.trading_bot.data.providers.yahoo").setLevel(logging.WARNING)


class MarketSchedulerMock:
    """Mock market scheduler that simulates warm-up → market open transition."""

    def __init__(self):
        self.current_phase = "warmup"  # warmup, market_open, market_closed
        self.timezone = __import__("pytz").timezone("US/Eastern")
        self.warmup_start_time = None
        self.market_open_time = None
        logger.info(f"MarketSchedulerMock initialized in '{self.current_phase}' phase")

    def set_phase(self, phase: str):
        """Set the current market phase for testing."""
        old_phase = self.current_phase
        self.current_phase = phase
        logger.info(f"=" * 60)
        logger.info(f"PHASE TRANSITION: {old_phase} → {phase}")
        logger.info(f"=" * 60)

    def should_bot_be_active(self) -> bool:
        """Bot is active during warm-up and market open."""
        return self.current_phase in ["warmup", "market_open"]

    def is_warmup_phase(self) -> bool:
        """Check if we're in warm-up phase."""
        return self.current_phase == "warmup"

    def is_market_open(self) -> bool:
        """Check if market is open."""
        return self.current_phase == "market_open"

    def get_warmup_time(self):
        """Get warm-up start time."""
        if self.warmup_start_time is None:
            now = datetime.now(self.timezone)
            self.warmup_start_time = now.replace(hour=9, minute=25, second=0, microsecond=0)
        return self.warmup_start_time

    def get_open_time(self, date=None):
        """Get market open time."""
        if self.market_open_time is None:
            now = datetime.now(self.timezone)
            self.market_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        return self.market_open_time

    def get_close_time(self, date=None):
        """Get market close time."""
        now = datetime.now(self.timezone)
        return now.replace(hour=16, minute=0, second=0, microsecond=0)

    def next_market_open(self):
        """Get next market open time."""
        return self.get_open_time()


async def test_warmup_and_market_open():
    """Test warm-up phase and market open transition."""

    logger.info("=" * 80)
    logger.info("WARM-UP → MARKET OPEN TRANSITION TEST")
    logger.info("=" * 80)
    logger.info("")
    logger.info("This test simulates:")
    logger.info("  1. Warm-up phase (9:25 AM) - Connect to Finnhub, market closed")
    logger.info("  2. Monitor for 90 seconds - Check for timeouts/reconnects")
    logger.info("  3. Market open (9:30 AM) - Transition to trading")
    logger.info("  4. Monitor stability - Ensure connection stays healthy")
    logger.info("")

    # Load environment
    env_path = Path(__file__).parent / "vibe" / "trading_bot" / ".env"
    load_dotenv(env_path)

    # Save Finnhub API key
    finnhub_api_key = os.getenv("FINNHUB_API_KEY") or os.getenv("finnhub_api_key")
    if not finnhub_api_key:
        raise ValueError("FINNHUB_API_KEY not found in environment")

    # Clear problematic env vars that might cause JSON parsing issues
    for key in list(os.environ.keys()):
        if key.lower() in ['symbols', 'bar_intervals']:
            del os.environ[key]

    # Import after env cleanup
    from vibe.trading_bot.config.settings import AppSettings, TradingSettings, DataSettings
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator

    # Create mock scheduler
    mock_scheduler = MarketSchedulerMock()

    # Create orchestrator with explicit test config
    config = AppSettings(
        environment="development",
        log_level="INFO",
        trading=TradingSettings(
            symbols=["AAPL", "GOOGL", "MSFT"],
            initial_capital=10000.0
        ),
        data=DataSettings(
            primary_provider="finnhub",
            finnhub_api_key=finnhub_api_key
        )
    )

    orchestrator = TradingOrchestrator(config)

    # Replace scheduler with mock
    orchestrator.market_scheduler = mock_scheduler

    # Track metrics
    metrics = {
        "warmup_disconnects": 0,
        "warmup_reconnects": 0,
        "trading_disconnects": 0,
        "pings_received": 0,
        "pongs_sent": 0,
        "timeout_errors": 0,
    }

    # Monitor provider health
    async def monitor_provider():
        """Monitor provider connection and track metrics."""
        last_connected = True
        while True:
            await asyncio.sleep(5)

            if orchestrator.primary_provider:
                provider = orchestrator.primary_provider

                # Check connection state
                if not provider.connected and last_connected:
                    if mock_scheduler.current_phase == "warmup":
                        metrics["warmup_disconnects"] += 1
                        logger.warning(f"⚠️  DISCONNECT during WARM-UP (count: {metrics['warmup_disconnects']})")
                    else:
                        metrics["trading_disconnects"] += 1
                        logger.warning(f"⚠️  DISCONNECT during TRADING (count: {metrics['trading_disconnects']})")

                elif provider.connected and not last_connected:
                    if mock_scheduler.current_phase == "warmup":
                        metrics["warmup_reconnects"] += 1
                        logger.info(f"🔄 RECONNECT during WARM-UP (count: {metrics['warmup_reconnects']})")

                last_connected = provider.connected

                # Log provider health
                if provider.connected:
                    if hasattr(provider, 'last_ping_time') and provider.last_ping_time:
                        ping_age = (datetime.now() - provider.last_ping_time).total_seconds()
                        logger.info(
                            f"Provider health: connected=True, "
                            f"last_ping={ping_age:.1f}s ago"
                        )
                    else:
                        logger.info(f"Provider health: connected=True, last_ping=None (waiting for first ping)")
                else:
                    logger.warning(f"Provider health: connected=False")

    try:
        # Start monitoring task
        monitor_task = asyncio.create_task(monitor_provider())

        # Initialize orchestrator
        logger.info("")
        logger.info("=" * 60)
        logger.info("INITIALIZING ORCHESTRATOR")
        logger.info("=" * 60)
        await orchestrator.initialize()

        # Phase 1: Warm-up (90 seconds)
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 1: WARM-UP PHASE (90 seconds)")
        logger.info("=" * 60)
        logger.info("Market is CLOSED - No trade messages expected")
        logger.info("Finnhub should send ping at ~60s")
        logger.info("Monitoring for disconnects/reconnects...")
        logger.info("")

        mock_scheduler.set_phase("warmup")

        # Run warm-up
        await orchestrator._pre_market_warmup()

        # Monitor during warm-up
        logger.info("Warm-up complete. Monitoring connection for 90 seconds...")
        for i in range(9):  # 9 * 10s = 90s
            await asyncio.sleep(10)
            elapsed = (i + 1) * 10
            logger.info(f"  [{elapsed}s] Warm-up monitoring... (waiting for 60s ping)")

        # Phase 2: Market Open
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 2: MARKET OPEN")
        logger.info("=" * 60)
        logger.info("Transitioning to trading mode...")
        logger.info("Trade messages should start flowing")
        logger.info("")

        mock_scheduler.set_phase("market_open")

        # Give it a moment to process the transition
        await asyncio.sleep(5)

        # Monitor during trading
        logger.info("Monitoring connection during trading for 60 seconds...")
        for i in range(6):  # 6 * 10s = 60s
            await asyncio.sleep(10)
            elapsed = (i + 1) * 10
            logger.info(f"  [{elapsed}s] Trading monitoring...")

        # Summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("TEST COMPLETE - RESULTS")
        logger.info("=" * 60)
        logger.info(f"Warm-up disconnects: {metrics['warmup_disconnects']}")
        logger.info(f"Warm-up reconnects: {metrics['warmup_reconnects']}")
        logger.info(f"Trading disconnects: {metrics['trading_disconnects']}")
        logger.info("")

        # Evaluate results
        success = True

        if metrics["warmup_disconnects"] > 0:
            logger.error("❌ FAILURE: Disconnects during warm-up (should be 0)")
            success = False
        else:
            logger.info("✅ SUCCESS: No disconnects during warm-up")

        if metrics["warmup_reconnects"] > 0:
            logger.warning(f"⚠️  WARNING: {metrics['warmup_reconnects']} reconnects during warm-up (should be 0)")
            success = False
        else:
            logger.info("✅ SUCCESS: No reconnects during warm-up")

        if metrics["trading_disconnects"] > 0:
            logger.error("❌ FAILURE: Disconnects during trading (should be 0)")
            success = False
        else:
            logger.info("✅ SUCCESS: No disconnects during trading")

        # Check final provider state
        if orchestrator.primary_provider:
            if orchestrator.primary_provider.connected:
                logger.info("✅ SUCCESS: Provider still connected at end of test")
            else:
                logger.error("❌ FAILURE: Provider disconnected at end of test")
                success = False

            # Check timestamp freshness
            if hasattr(orchestrator.primary_provider, 'last_ping_time'):
                if orchestrator.primary_provider.last_ping_time:
                    ping_age = (datetime.now() - orchestrator.primary_provider.last_ping_time).total_seconds()
                    if ping_age < 120:  # Less than 2 minutes old
                        logger.info(f"✅ SUCCESS: Last ping is fresh ({ping_age:.1f}s ago)")
                    else:
                        logger.warning(f"⚠️  WARNING: Last ping is old ({ping_age:.1f}s ago)")

        logger.info("")
        if success:
            logger.info("🎉 ALL TESTS PASSED!")
        else:
            logger.error("💥 SOME TESTS FAILED - Review errors above")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
    finally:
        # Cleanup
        logger.info("Cleaning up...")
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        if orchestrator.primary_provider and orchestrator.primary_provider.connected:
            await orchestrator.primary_provider.disconnect()

        logger.info("Cleanup complete")


if __name__ == "__main__":
    asyncio.run(test_warmup_and_market_open())
