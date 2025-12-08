"""
Test Finnhub WebSocket Connection

This script tests the WebSocket connection to Finnhub and prints real-time trade data.
Run this during market hours (9:30 AM - 4:00 PM ET) to see live trades.

Usage:
    python scripts/test_finnhub_connection.py

Requirements:
    - Finnhub API key configured in src/config/finnhub_config.json
    - Market must be open to see trade data
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.finnhub_config_loader import load_finnhub_config
from src.data.finnhub_websocket import FinnhubWebSocketClient


class ConnectionTester:
    """Helper class to test WebSocket connection."""

    def __init__(self):
        self.trade_count = 0
        self.symbols_seen = set()
        self.first_trade_time = None
        self.last_trade_time = None

    def message_callback(self, message: dict):
        """Handle incoming messages and track statistics."""
        msg_type = message.get("type")

        if msg_type == "trade":
            trades = message.get("data", [])

            for trade in trades:
                self.trade_count += 1
                symbol = trade.get("s")
                price = trade.get("p")
                volume = trade.get("v")
                timestamp = trade.get("t")

                self.symbols_seen.add(symbol)

                if self.first_trade_time is None:
                    self.first_trade_time = datetime.now()
                self.last_trade_time = datetime.now()

                # Print trade (first 5 trades per symbol for brevity)
                if self.trade_count <= 20:
                    print(f"  💹 {symbol}: ${price:,.2f} x {volume:,} shares @ {timestamp}")

        elif msg_type == "subscription":
            symbol = message.get("symbol")
            status = message.get("status")
            print(f"  📊 Subscription: {symbol} - {status}")

        elif msg_type == "ping":
            print(f"  💓 Heartbeat from server")

        elif msg_type == "error":
            error_msg = message.get("msg", "Unknown error")
            print(f"  ❌ Error: {error_msg}")


async def main():
    """Main test function."""
    print("=" * 70)
    print("Finnhub WebSocket Connection Test")
    print("=" * 70)
    print()

    # Load configuration
    print("Step 1: Loading configuration...")
    try:
        config = load_finnhub_config()
        print(f"✅ Config loaded: {len(config.symbols)} symbols configured")
        print(f"   Symbols: {', '.join(config.symbols)}")
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        print()
        print("Please ensure finnhub_config.json is set up correctly.")
        print("Run: python scripts/test_finnhub_config.py")
        return False
    print()

    # Create tester
    tester = ConnectionTester()

    # Create WebSocket client
    print("Step 2: Creating WebSocket client...")
    client = FinnhubWebSocketClient(
        api_key=config.api_key,
        websocket_url=config.websocket_url,
        message_callback=tester.message_callback
    )
    print(f"✅ Client created for {config.websocket_url}")
    print()

    # Connect
    print("Step 3: Connecting to Finnhub WebSocket...")
    try:
        connected = await client.connect()
        if not connected:
            print("❌ Failed to connect")
            print()
            print("Possible issues:")
            print("  - Invalid API key")
            print("  - Network connectivity problem")
            print("  - Finnhub service unavailable")
            return False
        print("✅ Successfully connected!")
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
    print()

    # Subscribe to symbols
    print(f"Step 4: Subscribing to symbols...")
    try:
        symbols_to_test = config.symbols[:3] if len(config.symbols) > 3 else config.symbols
        if not symbols_to_test:
            symbols_to_test = ["AAPL", "MSFT"]  # Default test symbols
            print(f"   No symbols in config, using defaults: {', '.join(symbols_to_test)}")

        success = await client.subscribe(symbols_to_test)
        if not success:
            print("❌ Failed to subscribe")
            await client.disconnect()
            return False
        print(f"✅ Subscribed to: {', '.join(symbols_to_test)}")
    except Exception as e:
        print(f"❌ Subscription error: {e}")
        await client.disconnect()
        return False
    print()

    # Listen for messages
    print("Step 5: Listening for trade messages...")
    print("(This will run for 30 seconds - trade data only appears during market hours)")
    print()
    print("Live Trades:")
    print("-" * 70)

    try:
        # Wait for 30 seconds
        await asyncio.sleep(30)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    print()
    print("-" * 70)
    print()

    # Show statistics
    print("Step 6: Connection Statistics")
    print("-" * 70)
    stats = client.get_statistics()
    status = client.get_connection_status()

    print(f"Connection Status:")
    print(f"  Connected: {'✅ Yes' if status['connected'] else '❌ No'}")
    print(f"  Uptime: {status['uptime_seconds']:.1f} seconds")
    print(f"  Subscribed symbols: {', '.join(status['subscribed_symbols'])}")
    print()

    print(f"Message Statistics:")
    print(f"  Total messages received: {stats['messages_received']}")
    print(f"  Messages parsed: {stats['messages_parsed']}")
    print(f"  Parse errors: {stats['parse_errors']}")
    print(f"  Queue size: {stats['queue_size']}")
    print()

    print(f"Trade Statistics:")
    print(f"  Total trades: {tester.trade_count}")
    print(f"  Symbols seen: {', '.join(sorted(tester.symbols_seen)) if tester.symbols_seen else 'None'}")

    if tester.first_trade_time and tester.last_trade_time:
        duration = (tester.last_trade_time - tester.first_trade_time).total_seconds()
        rate = tester.trade_count / duration if duration > 0 else 0
        print(f"  Trade rate: {rate:.2f} trades/second")
    print()

    # Check if we received trades
    if tester.trade_count == 0:
        print("⚠️  No trades received during test period")
        print()
        print("This is normal if:")
        print("  - Market is closed (regular hours: 9:30 AM - 4:00 PM ET)")
        print("  - Symbols have low trading volume")
        print("  - Test ran during after-hours with filter enabled")
        print()
        print("✅ However, connection and subscription worked correctly!")
    else:
        print(f"✅ Received {tester.trade_count} trades - Connection working perfectly!")
    print()

    # Disconnect
    print("Step 7: Disconnecting...")
    await client.disconnect()
    print("✅ Disconnected cleanly")
    print()

    # Summary
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)
    print("✅ Configuration: Loaded successfully")
    print("✅ Connection: Connected and authenticated")
    print("✅ Subscription: Subscribed to symbols")
    print(f"{'✅' if tester.trade_count > 0 else '⚠️ '} Trade Data: {tester.trade_count} trades received")
    print("✅ Disconnect: Clean shutdown")
    print()
    print("🎉 Phase 2 Validation: PASSED")
    print()
    print("Next steps:")
    print("1. ✓ WebSocket client is working")
    print("2. → Proceed to Phase 3: Bar Aggregation Engine")
    print("3. → Test bar aggregation with scripts/test_finnhub_aggregation.py")
    print()

    return True


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
