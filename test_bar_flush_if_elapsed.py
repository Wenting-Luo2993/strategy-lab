"""
Test for hybrid bar completion (trade-triggered + time-triggered).

Validates that:
1. Trade-triggered completion works (existing behavior)
2. Time-triggered completion works (new flush_if_elapsed)
3. Quiet markets don't lose bars
"""

import asyncio
from datetime import datetime, timedelta
import pytz
from vibe.trading_bot.data.aggregator import BarAggregator


def test_trade_triggered_completion():
    """Test existing trade-triggered bar completion."""
    print("\n" + "="*80)
    print("TEST 1: Trade-Triggered Completion (Existing Behavior)")
    print("="*80)

    aggregator = BarAggregator(bar_interval="1m", timezone="US/Eastern")
    tz = pytz.timezone("US/Eastern")

    # Add trades in 9:32:xx period
    t1 = tz.localize(datetime(2026, 3, 10, 9, 32, 5))
    t2 = tz.localize(datetime(2026, 3, 10, 9, 32, 30))

    bar1 = aggregator.add_trade(t1, 100.0, 1000)
    print(f"[9:32:05] Trade at $100 -> Bar completed: {bar1 is not None}")
    assert bar1 is None, "First trade should not complete a bar"

    bar2 = aggregator.add_trade(t2, 101.0, 500)
    print(f"[9:32:30] Trade at $101 -> Bar completed: {bar2 is not None}")
    assert bar2 is None, "Trade in same minute should not complete bar"

    # Trade in NEXT minute triggers completion
    t3 = tz.localize(datetime(2026, 3, 10, 9, 33, 5))
    bar3 = aggregator.add_trade(t3, 102.0, 800)
    print(f"[9:33:05] Trade at $102 -> Bar completed: {bar3 is not None}")
    assert bar3 is not None, "Trade in next minute should complete previous bar"
    assert bar3['timestamp'] == tz.localize(datetime(2026, 3, 10, 9, 32, 0))

    print(f"\n[OK] Trade-triggered completion works!")
    print(f"     9:32:00 bar completed when 9:33:05 trade arrived")


def test_time_triggered_completion():
    """Test new time-triggered bar completion (quiet markets)."""
    print("\n" + "="*80)
    print("TEST 2: Time-Triggered Completion (New flush_if_elapsed)")
    print("="*80)

    aggregator = BarAggregator(bar_interval="1m", timezone="US/Eastern")
    tz = pytz.timezone("US/Eastern")

    # Add trades in 9:32:xx period
    t1 = tz.localize(datetime(2026, 3, 10, 9, 32, 5))
    t2 = tz.localize(datetime(2026, 3, 10, 9, 32, 30))

    aggregator.add_trade(t1, 100.0, 1000)
    aggregator.add_trade(t2, 101.0, 500)
    print(f"[9:32:05, 9:32:30] Added 2 trades in 9:32:00 minute")

    # Simulate quiet market - NO trades in 9:33:xx
    # Call flush_if_elapsed at 9:33:15
    current_time = tz.localize(datetime(2026, 3, 10, 9, 33, 15))
    bar = aggregator.flush_if_elapsed(current_time)

    print(f"[9:33:15] No trades in 9:33:xx, but flush_if_elapsed called")
    print(f"          -> Bar completed: {bar is not None}")
    assert bar is not None, "flush_if_elapsed should complete bar when time boundary crossed"
    assert bar['timestamp'] == tz.localize(datetime(2026, 3, 10, 9, 32, 0))

    print(f"\n[OK] Time-triggered completion works!")
    print(f"     9:32:00 bar completed at 9:33:15 even without trades")


def test_no_premature_flush():
    """Test that flush_if_elapsed doesn't complete bar prematurely."""
    print("\n" + "="*80)
    print("TEST 3: No Premature Flush (Within Same Minute)")
    print("="*80)

    aggregator = BarAggregator(bar_interval="1m", timezone="US/Eastern")
    tz = pytz.timezone("US/Eastern")

    # Add trade in 9:32:xx period
    t1 = tz.localize(datetime(2026, 3, 10, 9, 32, 5))
    aggregator.add_trade(t1, 100.0, 1000)
    print(f"[9:32:05] Added trade in 9:32:00 minute")

    # Call flush_if_elapsed at 9:32:45 (still in same minute)
    current_time = tz.localize(datetime(2026, 3, 10, 9, 32, 45))
    bar = aggregator.flush_if_elapsed(current_time)

    print(f"[9:32:45] Called flush_if_elapsed (still in 9:32:00 minute)")
    print(f"          -> Bar completed: {bar is not None}")
    assert bar is None, "flush_if_elapsed should NOT complete bar within same minute"

    print(f"\n[OK] No premature flush - bar still building")


def test_hybrid_approach_timeline():
    """Test realistic timeline with both completion methods."""
    print("\n" + "="*80)
    print("TEST 4: Hybrid Approach Timeline (Realistic Scenario)")
    print("="*80)

    aggregator = BarAggregator(bar_interval="1m", timezone="US/Eastern")
    tz = pytz.timezone("US/Eastern")

    print("\nScenario: High volume period -> Quiet period")
    print("-" * 80)

    # Minute 1: High volume (trade-triggered)
    print("\n[9:32:00-9:32:59] Minute 1: HIGH VOLUME")
    t1 = tz.localize(datetime(2026, 3, 10, 9, 32, 5))
    t2 = tz.localize(datetime(2026, 3, 10, 9, 32, 30))
    aggregator.add_trade(t1, 100.0, 1000)
    aggregator.add_trade(t2, 101.0, 500)
    print(f"  Added 2 trades")

    # Minute 2: Trade arrives -> bar completes (trade-triggered)
    print("\n[9:33:02] Minute 2: Trade arrives")
    t3 = tz.localize(datetime(2026, 3, 10, 9, 33, 2))
    bar1 = aggregator.add_trade(t3, 102.0, 800)
    print(f"  -> 9:32:00 bar completed (trade-triggered, 2 seconds after boundary)")
    assert bar1 is not None

    # Minute 3: Market goes QUIET
    print("\n[9:34:00-9:34:59] Minute 3: QUIET (no trades)")
    print("  ... no trades received ...")

    # flush_if_elapsed called at 9:34:15
    print("\n[9:34:15] Orchestrator calls flush_if_elapsed()")
    current_time = tz.localize(datetime(2026, 3, 10, 9, 34, 15))
    bar2 = aggregator.flush_if_elapsed(current_time)
    print(f"  -> 9:33:00 bar completed (time-triggered, 15 seconds after boundary)")
    assert bar2 is not None
    assert bar2['timestamp'] == tz.localize(datetime(2026, 3, 10, 9, 33, 0))

    print(f"\n[OK] Hybrid approach handles both high-volume and quiet periods!")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("TESTING HYBRID BAR COMPLETION (Trade + Time Triggered)")
    print("="*80)

    try:
        test_trade_triggered_completion()
        test_time_triggered_completion()
        test_no_premature_flush()
        test_hybrid_approach_timeline()

        print("\n" + "="*80)
        print("ALL TESTS PASSED!")
        print("="*80)
        print("\nSummary:")
        print("  [OK] Trade-triggered completion works (existing behavior)")
        print("  [OK] Time-triggered completion works (new flush_if_elapsed)")
        print("  [OK] No premature flushes (waits for boundary)")
        print("  [OK] Hybrid approach handles high-volume and quiet markets")
        print("\nThe bar aggregator now guarantees bar completion even in quiet markets!")

    except AssertionError as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
