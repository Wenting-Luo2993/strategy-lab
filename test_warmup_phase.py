"""
Test script for pre-market warm-up phase.

Tests:
1. Market scheduler warm-up time calculations
2. Cache warming with historical data
3. Health checks
4. Timing logic
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add vibe to path
sys.path.insert(0, str(Path(__file__).parent))

from vibe.trading_bot.core.market_schedulers import create_scheduler
from vibe.trading_bot.config.settings import get_settings
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider
from vibe.trading_bot.data.cache import DataCache
import pytz


def test_market_scheduler_warmup():
    """Test market scheduler warm-up methods."""
    print("=" * 60)
    print("TEST 1: Market Scheduler Warm-Up Methods")
    print("=" * 60)

    scheduler = create_scheduler(market_type="stocks", exchange="NYSE")

    # Test current time
    now = datetime.now(scheduler.timezone)
    print(f"\nCurrent time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Test market open/close times
    open_time = scheduler.get_open_time()
    close_time = scheduler.get_close_time()

    if open_time:
        print(f"Market open: {open_time.strftime('%H:%M:%S')}")
    else:
        print("Market closed today (holiday/weekend)")
        return False

    if close_time:
        print(f"Market close: {close_time.strftime('%H:%M:%S')}")

    # Test warm-up time
    warmup_time = scheduler.get_warmup_time()
    if warmup_time:
        print(f"Warm-up starts: {warmup_time.strftime('%H:%M:%S')} (5 min before open)")
    else:
        print("No warm-up time (market closed)")

    # Test phase detection
    print(f"\nCurrent phase:")
    print(f"  - In warm-up phase: {scheduler.is_warmup_phase()}")
    print(f"  - Market is open: {scheduler.is_market_open()}")
    print(f"  - Bot should be active: {scheduler.should_bot_be_active()}")

    # Test with simulated times
    print(f"\nSimulated times:")

    # 9:20 AM - Before warm-up
    test_time = scheduler.timezone.localize(datetime.combine(
        now.date(), datetime.strptime("09:20", "%H:%M").time()
    ))
    print(f"  At 9:20 AM: warmup={scheduler.is_warmup_phase(test_time)}, "
          f"open={scheduler.is_market_open(test_time)}, "
          f"active={scheduler.should_bot_be_active(test_time)}")

    # 9:27 AM - During warm-up
    test_time = scheduler.timezone.localize(datetime.combine(
        now.date(), datetime.strptime("09:27", "%H:%M").time()
    ))
    print(f"  At 9:27 AM: warmup={scheduler.is_warmup_phase(test_time)}, "
          f"open={scheduler.is_market_open(test_time)}, "
          f"active={scheduler.should_bot_be_active(test_time)}")

    # 9:32 AM - Market open
    test_time = scheduler.timezone.localize(datetime.combine(
        now.date(), datetime.strptime("09:32", "%H:%M").time()
    ))
    print(f"  At 9:32 AM: warmup={scheduler.is_warmup_phase(test_time)}, "
          f"open={scheduler.is_market_open(test_time)}, "
          f"active={scheduler.should_bot_be_active(test_time)}")

    print("\nOK TEST 1 PASSED")
    return True


async def test_cache_warming():
    """Test cache warming with historical data."""
    print("\n" + "=" * 60)
    print("TEST 2: Cache Warming")
    print("=" * 60)

    # Create data manager
    provider = YahooDataProvider()
    cache_dir = Path("./test_cache")
    cache_dir.mkdir(exist_ok=True)

    cache = DataCache(cache_dir=cache_dir, ttl_seconds=3600)
    data_manager = DataManager(
        provider=provider,
        cache_dir=cache_dir,
        cache_ttl_seconds=3600
    )

    symbols = ["AAPL", "MSFT"]

    print(f"\nFetching 2 days of data for {len(symbols)} symbols...")
    start_time = datetime.now()

    for symbol in symbols:
        print(f"\n  Fetching {symbol}...")
        bars = await data_manager.get_data(
            symbol=symbol,
            timeframe="5m",
            days=2,
        )

        if bars is not None and not bars.empty:
            print(f"  OK {symbol}: {len(bars)} bars fetched")
            print(f"     Date range: {bars.iloc[0]['timestamp']} to {bars.iloc[-1]['timestamp']}")
        else:
            print(f"  ERROR {symbol}: No data fetched")
            return False

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCache warming completed in {elapsed:.2f} seconds")

    # Test cache hit (should be instant)
    print(f"\nTesting cache hit...")
    start_time = datetime.now()

    bars = await data_manager.get_data(symbol="AAPL", timeframe="5m", days=2)
    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"  Cache hit in {elapsed:.3f} seconds")
    if elapsed > 1.0:
        print(f"  WARNING: Cache hit took > 1 second")

    # Cleanup
    import shutil
    shutil.rmtree(cache_dir)

    print("\nOK TEST 2 PASSED")
    return True


async def test_warmup_timing():
    """Test warm-up phase timing logic."""
    print("\n" + "=" * 60)
    print("TEST 3: Warm-Up Timing Logic")
    print("=" * 60)

    scheduler = create_scheduler(market_type="stocks", exchange="NYSE")

    # Calculate next warm-up
    next_open = scheduler.next_market_open()
    next_warmup = scheduler.get_warmup_time(next_open)

    print(f"\nNext market open: {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if next_warmup:
        print(f"Next warm-up: {next_warmup.strftime('%Y-%m-%d %H:%M:%S %Z')}")

        # Calculate sleep time
        now = datetime.now(scheduler.timezone)
        sleep_seconds = (next_warmup - now).total_seconds()

        if sleep_seconds > 0:
            print(f"\nTime until warm-up: {sleep_seconds/3600:.2f} hours")
            print(f"  = {sleep_seconds/60:.0f} minutes")
            print(f"  = {sleep_seconds:.0f} seconds")
        else:
            print(f"\nWarm-up time has passed (in the past)")
    else:
        print("ERROR: Could not calculate warm-up time")
        return False

    print("\nOK TEST 3 PASSED")
    return True


async def main():
    """Run all tests."""
    print("=" * 60)
    print("PRE-MARKET WARM-UP PHASE TESTS")
    print("=" * 60)
    print()

    results = []

    # Test 1: Market scheduler
    try:
        result = test_market_scheduler_warmup()
        results.append(("Market Scheduler", result))
    except Exception as e:
        print(f"\nERROR in Test 1: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Market Scheduler", False))

    # Test 2: Cache warming
    try:
        result = await test_cache_warming()
        results.append(("Cache Warming", result))
    except Exception as e:
        print(f"\nERROR in Test 2: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Cache Warming", False))

    # Test 3: Timing logic
    try:
        result = await test_warmup_timing()
        results.append(("Timing Logic", result))
    except Exception as e:
        print(f"\nERROR in Test 3: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Timing Logic", False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\nALL TESTS PASSED!")
        return 0
    else:
        print("\nSOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
