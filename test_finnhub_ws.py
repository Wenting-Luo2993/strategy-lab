"""
Test script to validate Finnhub WebSocket connection.

This script connects to Finnhub, subscribes to symbols, and prints
any trades received to validate the connection and data flow.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add vibe to path
sys.path.insert(0, str(Path(__file__).parent))

# Enable debug logging to see raw messages
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')

from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient


async def test_finnhub_connection():
    """Test Finnhub WebSocket connection and trade reception."""

    # Get API key from .env
    env_path = Path(__file__).parent / "vibe" / "trading_bot" / ".env"
    api_key = None

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("FINNHUB_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key:
        print("ERROR: FINNHUB_API_KEY not found in .env file")
        return

    print(f"Using Finnhub API key: {api_key[:10]}...")
    print("-" * 60)

    # Track statistics
    stats = {
        "connected": False,
        "trades_received": 0,
        "symbols_seen": set(),
        "first_trade_time": None,
    }

    # Create websocket client
    client = FinnhubWebSocketClient(api_key=api_key)

    # Set up event handlers
    async def on_connected():
        stats["connected"] = True
        print(f"OK Connected to Finnhub WebSocket at {datetime.now().strftime('%H:%M:%S')}")

    async def on_disconnected():
        print(f"X Disconnected from Finnhub WebSocket at {datetime.now().strftime('%H:%M:%S')}")

    async def on_trade(trade):
        stats["trades_received"] += 1

        # Print raw trade data for first 5 trades to debug format
        if stats["trades_received"] <= 5:
            print(f"  RAW TRADE #{stats['trades_received']}: {trade}")

        symbol = trade.get("symbol")
        if symbol:
            stats["symbols_seen"].add(symbol)

        if stats["first_trade_time"] is None:
            stats["first_trade_time"] = datetime.now()

        # Print formatted trades
        if stats["trades_received"] <= 10 or stats["trades_received"] % 100 == 0:
            print(
                f"  Trade #{stats['trades_received']}: "
                f"{symbol} @ ${trade.get('price', 0):.2f} "
                f"(size: {trade.get('size', 0)}) "
                f"at {trade.get('timestamp', datetime.now()).strftime('%H:%M:%S') if trade.get('timestamp') else 'N/A'}"
            )

    async def on_error(error):
        print(f"X Error: {error.get('message', 'Unknown error')}")

    client.on_connected(on_connected)
    client.on_disconnected(on_disconnected)
    client.on_trade(on_trade)
    client.on_error(on_error)

    # Test symbols
    symbols = ["AAPL", "MSFT", "GOOGL"]

    try:
        # Connect
        print(f"Connecting to Finnhub WebSocket...")
        await client.connect()

        # Subscribe to symbols
        print(f"\nSubscribing to symbols: {', '.join(symbols)}...")
        for symbol in symbols:
            await client.subscribe(symbol)
            print(f"  OK Subscribed to {symbol}")

        print(f"\nListening for trades (will run for 60 seconds)...")
        print(f"Note: If you see no trades, it might mean:")
        print(f"  1. Finnhub free tier doesn't provide real-time trades")
        print(f"  2. Market is closed or low trading activity")
        print(f"  3. API key has insufficient permissions")
        print("-" * 60)

        # Wait for 60 seconds to receive trades
        await asyncio.sleep(60)

        # Disconnect
        print("\nDisconnecting...")
        await client.disconnect()

        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Connected: {stats['connected']}")
        print(f"Trades received: {stats['trades_received']}")
        print(f"Symbols seen: {', '.join(sorted(stats['symbols_seen'])) if stats['symbols_seen'] else 'None'}")

        if stats['first_trade_time']:
            elapsed = (datetime.now() - stats['first_trade_time']).total_seconds()
            rate = stats['trades_received'] / elapsed if elapsed > 0 else 0
            print(f"Trade rate: {rate:.1f} trades/second")

        if stats['trades_received'] == 0:
            print("\nWARNING WARNING: No trades received!")
            print("This suggests:")
            print("  - Finnhub free tier may not provide real-time trade data")
            print("  - Market might be closed")
            print("  - API key may need premium subscription")
            print("\nRecommendation: Check Finnhub pricing and API tier limits")
        else:
            print("\nOK SUCCESS: Finnhub WebSocket is working correctly!")

    except Exception as e:
        print(f"\nX ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("=" * 60)
    print("FINNHUB WEBSOCKET CONNECTION TEST")
    print("=" * 60)
    print()

    try:
        asyncio.run(test_finnhub_connection())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nX Test failed: {e}")
        import traceback
        traceback.print_exc()
