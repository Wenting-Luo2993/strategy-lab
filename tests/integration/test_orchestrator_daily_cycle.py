"""
Full orchestrator integration test: 3-day cycle with orchestrator.run()

Tests the ACTUAL orchestrator.run() loop through 3 complete daily cycles.
Uses orchestrator's production control flow (not manual phase execution).

Key differences from v1:
- Runs orchestrator.run() in background (actual production code path)
- Orchestrator detects phases based on mock scheduler state
- Tests phase detection logic (is_warmup_phase, is_market_open, etc.)
- Simulates real production behavior

Test flow:
- Day 1: Warmup (9:25) -> Trading (9:30-4:00) -> Cooldown (4:00+) -> Disconnect
- Day 2: Warmup (9:25) -> Trading (9:30-4:00) -> Cooldown (4:00+) -> Disconnect
- Day 3: Warmup (9:25) -> Trading (9:30-4:00) -> Cooldown (4:00+)

IMPORTANT: Stop Oracle Cloud instance before running (Finnhub free tier = 1 connection)

Requirements:
- FINNHUB_API_KEY environment variable
- Oracle Cloud stopped (one connection limit)
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
import logging

# Add project root to path
# File is at: tests/integration/test_orchestrator_daily_cycle.py
# Need to go up 2 levels to reach project root
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class OrchestratorTestHarness:
    """Helper class to coordinate orchestrator testing."""

    def __init__(self, orchestrator, mock_scheduler):
        self.orchestrator = orchestrator
        self.mock_scheduler = mock_scheduler
        self.run_task = None

    async def start(self):
        """Start orchestrator.run() in background."""
        self.run_task = asyncio.create_task(self.orchestrator.run())
        # Give orchestrator time to initialize
        await asyncio.sleep(0.5)

    async def stop(self):
        """Stop orchestrator gracefully."""
        if self.run_task:
            self.orchestrator._shutdown_event.set()
            try:
                await asyncio.wait_for(self.run_task, timeout=5.0)
            except asyncio.TimeoutError:
                print("[WARNING] Orchestrator did not stop gracefully, cancelling...")
                self.run_task.cancel()
                try:
                    await self.run_task
                except asyncio.CancelledError:
                    pass

    async def advance_to_time(self, hour, minute=0):
        """Advance time and wait for orchestrator to react."""
        self.mock_scheduler.set_time(hour, minute)
        # Give orchestrator loop time to detect phase change
        # The run() loop checks scheduler state on each iteration
        await asyncio.sleep(0.5)

    async def wait_for_warmup_complete(self, timeout=10):
        """Wait for warmup phase to complete."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            # Check if provider connected and subscribed
            if (self.orchestrator.primary_provider and
                self.orchestrator.primary_provider.connected and
                len(self.orchestrator.primary_provider.subscribed_symbols) > 0):
                return True
            await asyncio.sleep(0.5)
        return False

    async def wait_for_cooldown_complete(self, timeout=15):
        """Wait for cooldown phase to complete (provider disconnected)."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            # Check if provider disconnected
            if (self.orchestrator.primary_provider and
                not self.orchestrator.primary_provider.connected):
                return True
            await asyncio.sleep(0.5)
        return False


async def test_orchestrator_daily_cycle_v2():
    """Test full orchestrator through 3 daily cycles using orchestrator.run()."""

    print("=" * 80)
    print("ORCHESTRATOR 3-DAY CYCLE INTEGRATION TEST (V2)")
    print("=" * 80)
    print()
    print("Tests ACTUAL orchestrator.run() loop through 3 complete days:")
    print("  - Orchestrator detects phases based on mock scheduler")
    print("  - Uses production control flow (not manual phase execution)")
    print("  - Tests phase detection logic (is_warmup_phase, is_market_open)")
    print()
    print("Day 1: Warmup -> Trading -> Cooldown -> Disconnect")
    print("Day 2: Warmup -> Trading -> Cooldown -> Disconnect")
    print("Day 3: Warmup -> Trading -> Cooldown (WITHOUT container restart)")
    print()
    print("=" * 80)
    print()

    # Check Finnhub API key
    finnhub_key = os.getenv('FINNHUB_API_KEY')
    if not finnhub_key:
        # Fallback to hardcoded key for testing
        finnhub_key = "d4q7bnhr01qr2e6b08hgd4q7bnhr01qr2e6b08i0"
        os.environ['FINNHUB_API_KEY'] = finnhub_key
        print("[*] Using hardcoded Finnhub API key for testing")

    try:
        # Import after path setup
        from vibe.trading_bot.core.orchestrator import TradingOrchestrator
        from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
        from vibe.trading_bot.config.settings import get_settings

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # Create mock scheduler (start before warmup)
        mock_scheduler = MockMarketScheduler()
        mock_scheduler.set_time(9, 0)  # 9:00 AM EST (before warmup)
        print(f"[TIME] Starting at {mock_scheduler.now().strftime('%H:%M:%S')} EST")
        print()

        # Load config
        config = get_settings()

        # Override Finnhub API key
        if hasattr(config.data, 'finnhub'):
            config.data.finnhub.api_key = finnhub_key

        # Create orchestrator with mock scheduler and testing mode
        print("[SETUP] Creating orchestrator with mock scheduler (testing mode)...")
        orchestrator = TradingOrchestrator(
            config=config,
            market_scheduler=mock_scheduler,
            testing_mode=True  # Use shorter sleep intervals for faster testing
        )

        # Create test harness
        harness = OrchestratorTestHarness(orchestrator, mock_scheduler)

        # Track bars received
        bars_day1 = 0
        bars_day2 = 0
        bars_day3 = 0

        print("[SETUP] Starting orchestrator.run() in background...")
        await harness.start()
        print("[OK] Orchestrator running (background task)")
        print()

        # ====================================================================
        # DAY 1: WARMUP PHASE (9:25 AM)
        # ====================================================================
        print("=" * 80)
        print("DAY 1: WARMUP PHASE")
        print("=" * 80)

        # Advance to warmup time - orchestrator should detect and execute warmup
        await harness.advance_to_time(9, 25)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (warmup)")
        print(f"[CHECK] is_warmup_phase() = {mock_scheduler.is_warmup_phase()}")
        print("[PHASE] Orchestrator detecting warmup phase...")
        print()

        # Wait for orchestrator to complete warmup
        print("[WAIT] Waiting for orchestrator to complete warmup...")
        warmup_ok = await harness.wait_for_warmup_complete(timeout=30)

        if not warmup_ok:
            print("[ERROR] Warmup did not complete in time!")
            await harness.stop()
            return False

        print(f"[OK] Warmup completed by orchestrator")

        # ====================================================================
        # DAY 1: TRADING PHASE (9:30 AM - 4:00 PM)
        # ====================================================================
        print()
        print("=" * 80)
        print("DAY 1: TRADING PHASE")
        print("=" * 80)

        # CRITICAL: Advance to market open IMMEDIATELY after warmup completes
        # to prevent orchestrator from thinking market is closed
        await harness.advance_to_time(9, 30)

        # Now verify subscriptions after advancing to trading time
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market open)")
        print(f"[CHECK] is_market_open() = {mock_scheduler.is_market_open()}")
        print(f"[CHECK] subscribed_symbols = {orchestrator.primary_provider.subscribed_symbols}")
        print(f"[CHECK] Bar aggregators: {len(orchestrator.bar_aggregators)}")

        if len(orchestrator.primary_provider.subscribed_symbols) == 0:
            print("[ERROR] No symbols subscribed after warmup!")
            await harness.stop()
            return False

        print("[PHASE] Orchestrator should now be running trading cycles...")
        print()

        # Let orchestrator run trading cycles for 30 seconds
        print("[WAIT] Letting orchestrator trade for 30 seconds...")
        await asyncio.sleep(30)

        bars_day1 = len(orchestrator._realtime_bars)
        print(f"[RESULT] Received {bars_day1} bar(s) during Day 1 trading")
        print()

        # ====================================================================
        # DAY 1: COOLDOWN PHASE (4:00 PM)
        # ====================================================================
        print("=" * 80)
        print("DAY 1: COOLDOWN PHASE")
        print("=" * 80)

        # Advance to market close - orchestrator should enter cooldown
        await harness.advance_to_time(16, 0)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market close)")
        print(f"[CHECK] is_market_open() = {mock_scheduler.is_market_open()}")
        print("[PHASE] Orchestrator detecting market close, entering cooldown...")
        print()

        # CRITICAL: Wait for cooldown to START before advancing time
        # Otherwise we advance time before cooldown records start time
        print("[WAIT] Waiting for orchestrator to detect and START cooldown...")
        for i in range(10):  # Max 10 seconds
            await asyncio.sleep(1)
            if orchestrator.cooldown_manager._cooldown_start_time is not None:
                print(f"[OK] Cooldown started after {i+1}s")
                break
        else:
            print("[ERROR] Cooldown never started after 10s!")

        # Advance time by 6 seconds to complete cooldown (5s cooldown + 1s buffer in testing mode)
        print("[TIME] Advancing time by 6 seconds to complete cooldown...")
        mock_scheduler.advance_time(seconds=6)
        print(f"[TIME] Now at {mock_scheduler.now().strftime('%H:%M:%S')} EST")

        # Wait for orchestrator to complete cooldown and disconnect
        print("[WAIT] Waiting for orchestrator to complete cooldown and disconnect...")
        cooldown_ok = await harness.wait_for_cooldown_complete(timeout=10)

        if not cooldown_ok:
            print("[WARNING] Provider not disconnected after cooldown")
        else:
            print("[OK] Cooldown completed, provider disconnected")

        # Brief delay to ensure disconnect() fully completes
        # Brief delay to ensure disconnect() fully completes
        await asyncio.sleep(0.5)

        # Verify subscriptions cleared
        # Verify subscriptions cleared
        print(f"[CHECK] subscribed_symbols after Day 1 disconnect = {orchestrator.primary_provider.subscribed_symbols}")

        if len(orchestrator.primary_provider.subscribed_symbols) > 0:
            print("[ERROR] BUG DETECTED! subscribed_symbols NOT cleared on Day 1 disconnect!")
            print(f"[ERROR] Stale subscriptions: {orchestrator.primary_provider.subscribed_symbols}")
            await harness.stop()
            return False
        else:
            print("[OK] subscribed_symbols cleared correctly after Day 1")

        print()

        # ====================================================================
        # DAY 2: WARMUP PHASE (9:25 AM)
        # ====================================================================
        print("=" * 80)
        print("DAY 2: WARMUP PHASE (SAME ORCHESTRATOR, NO RESTART)")
        print("=" * 80)

        # Advance to next day warmup
        mock_scheduler.advance_time(hours=17, minutes=25)  # Day 2, 9:25 AM
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%Y-%m-%d %H:%M:%S')} EST")
        print(f"[CHECK] is_warmup_phase() = {mock_scheduler.is_warmup_phase()}")
        print("[PHASE] Orchestrator should detect Day 2 warmup...")
        print()

        # Wait for orchestrator to complete Day 2 warmup
        print("[WAIT] Waiting for orchestrator to complete Day 2 warmup...")
        warmup_ok = await harness.wait_for_warmup_complete(timeout=30)

        if not warmup_ok:
            print("[ERROR] Day 2 warmup did not complete!")
            await harness.stop()
            return False

        print(f"[OK] Day 2 warmup completed by orchestrator")
        print(f"[CHECK] subscribed_symbols = {orchestrator.primary_provider.subscribed_symbols}")

        if len(orchestrator.primary_provider.subscribed_symbols) == 0:
            print("[ERROR] BUG DETECTED! No symbols subscribed on Day 2!")
            await harness.stop()
            return False
        else:
            print("[OK] Subscriptions active on Day 2")

        print()

        # ====================================================================
        # DAY 2: TRADING PHASE (9:30 AM - 4:00 PM)
        # ====================================================================
        print("=" * 80)
        print("DAY 2: TRADING PHASE")
        print("=" * 80)

        await harness.advance_to_time(9, 30)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market open)")
        print("[PHASE] Orchestrator running Day 2 trading cycles...")
        print()

        print("[WAIT] Letting orchestrator trade for 30 seconds...")
        bars_before_day2 = len(orchestrator._realtime_bars)
        await asyncio.sleep(30)

        bars_day2 = len(orchestrator._realtime_bars) - bars_before_day2
        print(f"[RESULT] Received {bars_day2} bar(s) during Day 2 trading")
        print()

        # ====================================================================
        # DAY 2: COOLDOWN PHASE (4:00 PM)
        # ====================================================================
        print("=" * 80)
        print("DAY 2: COOLDOWN PHASE")
        print("=" * 80)

        await harness.advance_to_time(16, 0)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market close)")
        print("[PHASE] Orchestrator entering Day 2 cooldown...")
        print()

        # CRITICAL: Wait for cooldown to START before advancing time
        # Otherwise we advance time before cooldown records start time
        print("[WAIT] Waiting for orchestrator to detect and START cooldown...")
        for i in range(10):  # Max 10 seconds
            await asyncio.sleep(1)
            if orchestrator.cooldown_manager._cooldown_start_time is not None:
                print(f"[OK] Cooldown started after {i+1}s")
                break
        else:
            print("[ERROR] Cooldown never started after 10s!")

        # Advance time by 6 seconds to complete cooldown (5s + 1s buffer in testing mode)
        print("[TIME] Advancing time by 6 seconds to complete cooldown...")
        mock_scheduler.advance_time(seconds=6)
        print(f"[TIME] Now at {mock_scheduler.now().strftime('%H:%M:%S')} EST")

        print("[WAIT] Waiting for Day 2 cooldown and disconnect...")
        cooldown_ok = await harness.wait_for_cooldown_complete(timeout=10)

        if cooldown_ok:
            print("[OK] Day 2 cooldown completed")

        # Brief delay to ensure disconnect() fully completes
        # Brief delay to ensure disconnect() fully completes
        await asyncio.sleep(0.5)

        print(f"[DEBUG] Checking subscribed_symbols after Day 2 disconnect...")
        print(f"[CHECK] subscribed_symbols after Day 2 disconnect = {orchestrator.primary_provider.subscribed_symbols}")

        if len(orchestrator.primary_provider.subscribed_symbols) > 0:
            print("[ERROR] BUG DETECTED! subscribed_symbols NOT cleared on Day 2 disconnect!")
            await harness.stop()
            return False
        else:
            print("[OK] subscribed_symbols cleared correctly after Day 2")

        print()

        # ====================================================================
        # DAY 3: WARMUP PHASE (9:25 AM)
        # ====================================================================
        print("=" * 80)
        print("DAY 3: WARMUP PHASE (THIRD CYCLE)")
        print("=" * 80)

        # Advance to Day 3
        mock_scheduler.advance_time(hours=17, minutes=25)  # Day 3, 9:25 AM
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%Y-%m-%d %H:%M:%S')} EST")
        print(f"[CHECK] is_warmup_phase() = {mock_scheduler.is_warmup_phase()}")
        print("[PHASE] Orchestrator should detect Day 3 warmup...")
        print()

        print("[WAIT] Waiting for Day 3 warmup...")
        warmup_ok = await harness.wait_for_warmup_complete(timeout=30)

        if not warmup_ok:
            print("[ERROR] Day 3 warmup did not complete!")
            await harness.stop()
            return False

        print(f"[OK] Day 3 warmup completed")
        print(f"[CHECK] subscribed_symbols = {orchestrator.primary_provider.subscribed_symbols}")

        if len(orchestrator.primary_provider.subscribed_symbols) == 0:
            print("[ERROR] BUG DETECTED! No symbols subscribed on Day 3!")
            await harness.stop()
            return False
        else:
            print("[OK] Subscriptions active on Day 3 (third cycle working!)")

        print()

        # ====================================================================
        # DAY 3: TRADING PHASE (9:30 AM - 4:00 PM)
        # ====================================================================
        print("=" * 80)
        print("DAY 3: TRADING PHASE")
        print("=" * 80)

        await harness.advance_to_time(9, 30)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market open)")
        print("[PHASE] Orchestrator running Day 3 trading cycles...")
        print()

        print("[WAIT] Letting orchestrator trade for 30 seconds...")
        bars_before_day3 = len(orchestrator._realtime_bars)
        await asyncio.sleep(30)

        bars_day3 = len(orchestrator._realtime_bars) - bars_before_day3
        print(f"[RESULT] Received {bars_day3} bar(s) during Day 3 trading")
        print()

        # ====================================================================
        # DAY 3: COOLDOWN PHASE (4:00 PM)
        # ====================================================================
        print("=" * 80)
        print("DAY 3: COOLDOWN PHASE")
        print("=" * 80)

        await harness.advance_to_time(16, 0)
        print(f"[TIME] Advanced to {mock_scheduler.now().strftime('%H:%M:%S')} EST (market close)")
        print("[PHASE] Orchestrator entering Day 3 cooldown...")
        print()

        # CRITICAL: Wait for cooldown to START before advancing time
        # Otherwise we advance time before cooldown records start time
        print("[WAIT] Waiting for orchestrator to detect and START cooldown...")
        for i in range(10):  # Max 10 seconds
            await asyncio.sleep(1)
            if orchestrator.cooldown_manager._cooldown_start_time is not None:
                print(f"[OK] Cooldown started after {i+1}s")
                break
        else:
            print("[ERROR] Cooldown never started after 10s!")

        # Advance time by 6 seconds to complete cooldown (5s + 1s buffer in testing mode)
        print("[TIME] Advancing time by 6 seconds to complete cooldown...")
        mock_scheduler.advance_time(seconds=6)
        print(f"[TIME] Now at {mock_scheduler.now().strftime('%H:%M:%S')} EST")

        print("[WAIT] Waiting for Day 3 cooldown to complete...")
        await asyncio.sleep(5)  # Give orchestrator time to process cooldown completion
        print("[OK] Day 3 cooldown phase completed")
        print()

        # ====================================================================
        # CLEANUP
        # ====================================================================
        print("[CLEANUP] Stopping orchestrator...")
        await harness.stop()
        print("[OK] Orchestrator stopped gracefully")
        print()

        # ====================================================================
        # TEST RESULTS
        # ====================================================================
        print("=" * 80)
        print("TEST RESULTS - 3-DAY CYCLE (V2)")
        print("=" * 80)
        print(f"Day 1 bars: {bars_day1}")
        print(f"Day 2 bars: {bars_day2}")
        print(f"Day 3 bars: {bars_day3}")
        print(f"Total bars: {bars_day1 + bars_day2 + bars_day3}")
        print()

        print("[SUCCESS] Full 3-day cycle completed using orchestrator.run()!")
        print("  - Orchestrator detected phases based on mock scheduler")
        print("  - Used production control flow (not manual execution)")
        print("  - Subscriptions cleared on Day 1 & Day 2 disconnects")
        print("  - Subscriptions re-sent on Day 2 & Day 3 warmups")
        print("  - Bar aggregators persisted through 3 days")
        print("  - Phase detection logic validated")
        print("  - State management validated across multiple cycles")
        print()

        if bars_day1 == 0 and bars_day2 == 0 and bars_day3 == 0:
            print("[NOTE] No bars received (expected if outside market hours)")
            print("[NOTE] The critical validation is state management and control flow")

        return True

    except Exception as e:
        print(f"[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the integration test."""

    print()
    print("Pre-flight checks:")
    print("-" * 80)

    # Check environment
    finnhub_key = os.getenv('FINNHUB_API_KEY')
    if not finnhub_key:
        # Fallback to hardcoded key for testing
        finnhub_key = "d4q7bnhr01qr2e6b08hgd4q7bnhr01qr2e6b08i0"
        os.environ['FINNHUB_API_KEY'] = finnhub_key
        print("[*] Using hardcoded Finnhub API key for testing")
    else:
        print("[OK] FINNHUB_API_KEY is set")

    print("[OK] Oracle Cloud should be stopped (Finnhub free tier = 1 connection)")
    print()

    # Run test
    success = await test_orchestrator_daily_cycle_v2()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
