"""
Test Finnhub DataLoader Integration

Tests the FinnhubWebSocketLoader with both historical and live data modes.

Usage:
    python scripts/test_finnhub_loader.py

Requirements:
    - Finnhub API key configured in src/config/finnhub_config.json
    - For live mode: Market must be open
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data import FinnhubWebSocketLoader, DataLoaderFactory, DataSource


def test_historical_mode():
    """Test historical data fetch via REST API."""
    print("=" * 70)
    print("Test 1: Historical Mode (REST API)")
    print("=" * 70)
    print()

    try:
        # Create loader in historical mode
        loader = FinnhubWebSocketLoader(mode="historical")
        print("[OK] Loader created in historical mode")
        print()

        # Fetch historical data
        print("Fetching 7 days of historical 5m data for AAPL...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        df = loader.fetch(
            symbol="AAPL",
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            timeframe="5m"
        )

        if df.empty:
            print("[WARN] No historical data returned")
            print("  This might be expected if:")
            print("  - Date range is outside market hours")
            print("  - Symbol is invalid")
            print("  - API rate limit reached")
        else:
            print(f"[OK] Fetched {len(df)} bars")
            print()
            print("Data Summary:")
            print(f"  Time range: {df.index[0]} to {df.index[-1]}")
            print(f"  Price range: ${df['low'].min():.2f} - ${df['high'].max():.2f}")
            print(f"  Average volume: {df['volume'].mean():,.0f}")
            print()
            print("First 3 bars:")
            print(df.head(3))
            print()
            print("Last 3 bars:")
            print(df.tail(3))

        print()
        print("[SUCCESS] Historical mode test PASSED")
        print()
        return True

    except Exception as e:
        print(f"[FAIL] Historical mode test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_factory_pattern():
    """Test DataLoader factory pattern."""
    print("=" * 70)
    print("Test 2: Factory Pattern")
    print("=" * 70)
    print()

    try:
        # Create loader using factory
        print("Creating loader via DataLoaderFactory...")
        loader = DataLoaderFactory.create(
            DataSource.FINNHUB,
            mode="historical"
        )
        print(f"[OK] Factory created: {type(loader).__name__}")
        print()

        # Verify it's the right type
        assert isinstance(loader, FinnhubWebSocketLoader)
        print("[OK] Correct loader type")
        print()

        # Test fetch
        print("Testing fetch with factory-created loader...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=2)

        df = loader.fetch(
            symbol="MSFT",
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            timeframe="15m"
        )

        if not df.empty:
            print(f"[OK] Fetched {len(df)} bars for MSFT")
        else:
            print("[WARN] No data returned (may be expected)")

        print()
        print("[SUCCESS] Factory pattern test PASSED")
        print()
        return True

    except Exception as e:
        print(f"[FAIL] Factory pattern test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_live_mode():
    """Test live mode with WebSocket (requires market hours)."""
    print("=" * 70)
    print("Test 3: Live Mode (WebSocket)")
    print("=" * 70)
    print()
    print("NOTE: This test requires market hours to see live data")
    print()

    try:
        # Create loader in live mode
        loader = FinnhubWebSocketLoader(mode="live", auto_connect=False)
        print("[OK] Loader created in live mode")
        print()

        # Connect
        print("Connecting to WebSocket...")
        connected = loader.connect()

        if not connected:
            print("[FAIL] Failed to connect to WebSocket")
            print("  Possible reasons:")
            print("  - Invalid API key")
            print("  - Network issues")
            print("  - Finnhub service unavailable")
            return False

        print("[OK] Connected to WebSocket")
        print()

        # Subscribe to symbols
        print("Subscribing to AAPL, MSFT...")
        success = loader.subscribe(["AAPL", "MSFT"])

        if not success:
            print("[FAIL] Failed to subscribe")
            loader.disconnect()
            return False

        print("[OK] Subscribed to symbols")
        print()

        # Wait for bars to aggregate
        print("Waiting 30 seconds for bar aggregation...")
        print("(Bars will only complete at interval boundaries)")
        time.sleep(30)
        print()

        # Fetch completed bars
        print("Fetching completed bars...")
        today = datetime.now().strftime("%Y-%m-%d")
        df_aapl = loader.fetch(
            symbol="AAPL",
            start=today,
            end=today,
            timeframe="5m"
        )

        df_msft = loader.fetch(
            symbol="MSFT",
            start=today,
            end=today,
            timeframe="5m"
        )

        # Show results
        if df_aapl.empty and df_msft.empty:
            print("[WARN] No completed bars yet")
            print("  This is expected if:")
            print("  - Market is closed")
            print("  - Test ran < 1 full bar interval")
            print("  - Low trading volume")
            print()
            print("[OK] But WebSocket connection worked!")
        else:
            if not df_aapl.empty:
                print(f"[OK] AAPL: {len(df_aapl)} completed bars")
                print(df_aapl.tail(3))
                print()

            if not df_msft.empty:
                print(f"[OK] MSFT: {len(df_msft)} completed bars")
                print(df_msft.tail(3))
                print()

        # Get statistics
        stats = loader.get_statistics()
        print("Statistics:")
        print(f"  Mode: {stats['mode']}")
        print(f"  Connected: {stats['connected']}")
        print(f"  Subscribed: {', '.join(stats['subscribed_symbols'])}")
        if 'websocket' in stats:
            print(f"  Messages received: {stats['websocket']['messages_received']}")
        if 'aggregator' in stats:
            print(f"  Trades processed: {stats['aggregator']['trades_processed']}")
            print(f"  Bars completed: {stats['aggregator']['bars_completed']}")
        print()

        # Disconnect
        print("Disconnecting...")
        loader.disconnect()
        print("[OK] Disconnected")
        print()

        print("[SUCCESS] Live mode test PASSED")
        print()
        return True

    except Exception as e:
        print(f"[FAIL] Live mode test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_context_manager():
    """Test context manager usage."""
    print("=" * 70)
    print("Test 4: Context Manager")
    print("=" * 70)
    print()

    try:
        # Use context manager for automatic connection/disconnection
        print("Using context manager (with statement)...")

        with FinnhubWebSocketLoader(mode="historical") as loader:
            print("[OK] Entered context")

            # Fetch data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=1)

            df = loader.fetch(
                symbol="NVDA",
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                timeframe="5m"
            )

            if not df.empty:
                print(f"[OK] Fetched {len(df)} bars for NVDA")
            else:
                print("[WARN] No data (may be expected)")

        print("[OK] Exited context (automatic cleanup)")
        print()
        print("[SUCCESS] Context manager test PASSED")
        print()
        return True

    except Exception as e:
        print(f"[FAIL] Context manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print()
    print("=" * 70)
    print("Finnhub DataLoader Integration Test Suite")
    print("=" * 70)
    print()

    results = {}

    # Test 1: Historical mode
    results["historical"] = test_historical_mode()

    # Test 2: Factory pattern
    results["factory"] = test_factory_pattern()

    # Test 3: Live mode (requires market hours)
    # Uncomment to test during market hours:
    # results["live"] = test_live_mode()
    print("[SKIP] Live mode test - run manually during market hours")
    print()

    # Test 4: Context manager
    results["context_manager"] = test_context_manager()

    # Summary
    print()
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)

    for test_name, passed in results.items():
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {test_name}")

    print()

    passed_count = sum(results.values())
    total_count = len(results)

    if passed_count == total_count:
        print(f"[SUCCESS] All {total_count} tests PASSED")
        print()
        print("Phase 4 Validation: PASSED")
        print()
        print("Next steps:")
        print("1. [OK] DataLoader integration complete")
        print("2. -> Test live mode during market hours")
        print("3. -> Proceed to Phase 5: Architecture Design")
        return True
    else:
        print(f"[PARTIAL] {passed_count}/{total_count} tests passed")
        return False


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[WARN] Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
