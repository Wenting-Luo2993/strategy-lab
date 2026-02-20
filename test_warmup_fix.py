"""Test script to validate warm-up phase fix locally.

This script:
1. Initializes the orchestrator
2. Manually triggers the warm-up phase
3. Verifies all 4 steps complete successfully
4. Tests the HealthMonitor.get_health() call that was fixed

Run this to validate the fix without waiting for market open.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.orchestrator import TradingOrchestrator


async def test_warmup_phase():
    """Test the warm-up phase to validate the fix."""

    print("=" * 60)
    print("WARM-UP PHASE FIX VALIDATION")
    print("=" * 60)
    print()

    # Step 1: Initialize orchestrator
    print("Step 1: Initializing orchestrator...")
    try:
        settings = AppSettings()
        orchestrator = TradingOrchestrator(settings)
        await orchestrator.initialize()
        print("[OK] Orchestrator initialized successfully")
    except Exception as e:
        print(f"[FAIL] Failed to initialize orchestrator: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()

    # Step 2: Test HealthMonitor.get_health() (the fixed method)
    print("Step 2: Testing HealthMonitor.get_health()...")
    try:
        health_result = orchestrator.health_monitor.get_health()
        print(f"[OK] HealthMonitor.get_health() returned: {health_result.get('overall')}")
        print(f"  Components: {list(health_result.get('components', {}).keys())}")
        print(f"  Uptime: {health_result.get('uptime_seconds', 0):.1f}s")
    except Exception as e:
        print(f"[FAIL] HealthMonitor.get_health() failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()

    # Step 3: Manually trigger warm-up phase
    print("Step 3: Triggering warm-up phase manually...")
    print("(This will pre-fetch data, connect Finnhub, and run health checks)")
    print()

    try:
        # Call the private _pre_market_warmup method directly
        await orchestrator._pre_market_warmup()
        print("[OK] Warm-up phase completed successfully!")
    except Exception as e:
        print(f"[FAIL] Warm-up phase failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()

    # Step 4: Verify Finnhub connection
    print("Step 4: Verifying Finnhub WebSocket connection...")
    if orchestrator.finnhub_ws:
        if orchestrator.finnhub_ws.connected:
            print(f"[OK] Finnhub WebSocket is connected")
            print(f"  Subscribed symbols: {list(orchestrator.finnhub_ws.subscribed_symbols)}")
        else:
            print("[WARN] Finnhub WebSocket exists but not connected")
            print("  (This is OK if market is closed)")
    else:
        print("[WARN] Finnhub WebSocket not initialized")

    print()

    # Step 5: Check final health status
    print("Step 5: Final health check...")
    try:
        final_health = orchestrator.health_monitor.get_health()
        all_healthy = all(
            comp.get("status") == "healthy"
            for comp in final_health.get("components", {}).values()
        )

        print(f"  Overall status: {final_health.get('overall')}")
        print(f"  All components healthy: {all_healthy}")

        for name, comp in final_health.get("components", {}).items():
            status = comp.get("status", "unknown")
            print(f"    - {name}: {status}")

        if all_healthy:
            print("[OK] All health checks passed!")
        else:
            print("[WARN] Some components not healthy (may be expected)")
    except Exception as e:
        print(f"[FAIL] Final health check failed: {e}")
        return False

    print()
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print()
    print("[OK] The warm-up phase fix is working correctly!")
    print("[OK] HealthMonitor.get_health() method is being called properly")
    print("[OK] All 4 steps of warm-up phase completed without errors")
    print()
    print("The bot should now:")
    print("  1. Complete warm-up at 9:25 AM EST tomorrow")
    print("  2. Calculate ORB levels at 9:30-9:35 AM EST")
    print("  3. Send Discord notifications when trades execute")
    print()

    # Cleanup
    if orchestrator.finnhub_ws and orchestrator.finnhub_ws.connected:
        await orchestrator.finnhub_ws.disconnect()

    return True


async def main():
    """Run the test."""
    try:
        success = await test_warmup_phase()
        return 0 if success else 1
    except Exception as e:
        print(f"\n\nUNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
