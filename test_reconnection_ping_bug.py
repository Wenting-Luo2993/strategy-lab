"""
Test script to replicate the stale last_ping_time bug.

This test simulates the warm-up -> market open reconnection flow:
1. Connect to Finnhub
2. Wait for a ping
3. Disconnect (simulating warm-up end)
4. Reconnect (simulating market open)
5. Verify last_ping_time is reset (should be None, not stale)
"""

import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_reconnection_ping_timestamps():
    """Test that last_ping_time is properly reset on reconnection."""

    # Load from trading bot's .env file
    env_path = os.path.join(os.path.dirname(__file__), "vibe", "trading_bot", ".env")
    load_dotenv(env_path)

    # Try both variable names
    api_key = os.getenv("FINNHUB_API_KEY") or os.getenv("finnhub_api_key")

    if not api_key:
        raise ValueError("FINNHUB_API_KEY or finnhub_api_key not found in environment")

    client = FinnhubWebSocketClient(api_key)

    try:
        # Phase 1: Initial connection (simulating warm-up phase)
        logger.info("=" * 60)
        logger.info("PHASE 1: Initial Connection (Warm-up Phase)")
        logger.info("=" * 60)

        await client.connect()
        await client.subscribe("AAPL")

        logger.info(f"Initial state after connection:")
        logger.info(f"  - connected: {client.connected}")
        logger.info(f"  - last_message_time: {client.last_message_time}")
        logger.info(f"  - last_ping_time: {client.last_ping_time}")
        logger.info(f"  - last_pong_time: {client.last_pong_time}")

        # Wait for at least one ping
        logger.info("\nWaiting 70 seconds for at least one ping...")
        for i in range(7):
            await asyncio.sleep(10)
            if client.last_ping_time:
                logger.info(f"  [{i*10}s] Ping received at {client.last_ping_time}")
                logger.info(f"  [{i*10}s] Pong sent at {client.last_pong_time}")
                break
        else:
            logger.warning("No ping received in 70 seconds")

        # Record timestamps before disconnect
        first_ping_time = client.last_ping_time
        first_pong_time = client.last_pong_time

        logger.info(f"\nTimestamps before disconnect:")
        logger.info(f"  - last_ping_time: {first_ping_time}")
        logger.info(f"  - last_pong_time: {first_pong_time}")

        # Phase 2: Disconnect (simulating warm-up end)
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 2: Disconnect (End of Warm-up)")
        logger.info("=" * 60)

        await client.disconnect()

        logger.info(f"State after disconnect:")
        logger.info(f"  - connected: {client.connected}")
        logger.info(f"  - last_message_time: {client.last_message_time}")
        logger.info(f"  - last_ping_time: {client.last_ping_time}")
        logger.info(f"  - last_pong_time: {client.last_pong_time}")

        # Wait a bit (simulating time between warm-up end and market open)
        logger.info("\nWaiting 5 seconds (simulating gap between warm-up and market open)...")
        await asyncio.sleep(5)

        # Phase 3: Reconnect (simulating market open)
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 3: Reconnect (Market Open)")
        logger.info("=" * 60)

        await client.connect()
        await client.subscribe("AAPL")

        logger.info(f"State immediately after reconnection:")
        logger.info(f"  - connected: {client.connected}")
        logger.info(f"  - last_message_time: {client.last_message_time}")
        logger.info(f"  - last_ping_time: {client.last_ping_time}")
        logger.info(f"  - last_pong_time: {client.last_pong_time}")

        # Phase 4: Verify timestamps are reset
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 4: Verification")
        logger.info("=" * 60)

        # Check if last_ping_time was properly reset
        if client.last_ping_time is None:
            logger.info("✅ SUCCESS: last_ping_time properly reset to None after reconnection")
        else:
            # Calculate how old the timestamp is
            age = (datetime.now() - client.last_ping_time).total_seconds()
            if age > 10:
                logger.error(f"❌ FAILURE: last_ping_time shows stale timestamp ({age:.0f}s old)")
                logger.error(f"   This is the bug that causes 'No ping for 62650s' warnings!")
            else:
                logger.info(f"✅ SUCCESS: last_ping_time is fresh ({age:.1f}s old)")

        # Check pong timestamp too
        if client.last_pong_time is None:
            logger.info("✅ SUCCESS: last_pong_time properly reset to None after reconnection")
        else:
            age = (datetime.now() - client.last_pong_time).total_seconds()
            if age > 10:
                logger.error(f"❌ FAILURE: last_pong_time shows stale timestamp ({age:.0f}s old)")
            else:
                logger.info(f"✅ SUCCESS: last_pong_time is fresh ({age:.1f}s old)")

        # Wait for new ping after reconnection
        logger.info("\nWaiting 70 seconds for first ping after reconnection...")
        for i in range(7):
            await asyncio.sleep(10)
            if client.last_ping_time:
                age = (datetime.now() - client.last_ping_time).total_seconds()
                logger.info(f"  [{i*10}s] New ping received! Age: {age:.1f}s")
                logger.info(f"  [{i*10}s] Pong sent at {client.last_pong_time}")

                if age < 15:
                    logger.info("✅ SUCCESS: Received fresh ping after reconnection")
                else:
                    logger.warning(f"⚠️  WARNING: Ping timestamp seems old ({age:.0f}s)")
                break
        else:
            logger.warning("No ping received in 70 seconds after reconnection")

    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
    finally:
        # Cleanup
        if client.connected:
            await client.disconnect()
        logger.info("\n" + "=" * 60)
        logger.info("Test completed")
        logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_reconnection_ping_timestamps())
