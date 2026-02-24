"""
Test Finnhub WebSocket stability for 5 minutes.

This test monitors:
- Connection stability (no disconnects)
- Ping/pong frequency and response time
- Background task health
- Any errors or warnings
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from vibe.trading_bot.config.settings import get_settings
from vibe.trading_bot.data.providers.factory import DataProviderFactory


class ConnectionMonitor:
    """Monitor Finnhub connection health."""

    def __init__(self):
        self.start_time = datetime.now()
        self.ping_count = 0
        self.pong_count = 0
        self.disconnect_count = 0
        self.reconnect_count = 0
        self.errors = []

    def log(self, message: str, level: str = "INFO"):
        """Log with timestamp."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"[{elapsed:6.1f}s] [{level:5s}] {message}")

    def report(self):
        """Print final report."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print("\n" + "=" * 70)
        print("STABILITY TEST REPORT")
        print("=" * 70)
        print(f"Duration: {elapsed:.1f} seconds")
        print(f"Pings received: {self.ping_count}")
        print(f"Pongs sent: {self.pong_count}")
        print(f"Disconnects: {self.disconnect_count}")
        print(f"Reconnects: {self.reconnect_count}")
        print(f"Errors: {len(self.errors)}")

        if self.errors:
            print("\nErrors encountered:")
            for i, error in enumerate(self.errors[:5], 1):
                print(f"  {i}. {error}")

        print("\n" + "=" * 70)

        if self.disconnect_count == 0 and len(self.errors) == 0:
            print("[SUCCESS] Connection was stable for entire test!")
            return True
        elif self.disconnect_count > 0:
            print(f"[FAIL] Had {self.disconnect_count} disconnects")
            return False
        else:
            print(f"[WARN] Had {len(self.errors)} errors but no disconnects")
            return True


async def monitor_connection(provider, monitor: ConnectionMonitor, duration: int = 300):
    """Monitor connection for specified duration."""

    # Track initial state
    last_ping_time = None
    ping_check_interval = 10  # Check every 10 seconds

    monitor.log("Starting connection monitoring...")

    end_time = asyncio.get_event_loop().time() + duration

    while asyncio.get_event_loop().time() < end_time:
        try:
            # Check connection state
            if not provider.connected:
                monitor.log("DISCONNECTED!", "ERROR")
                monitor.disconnect_count += 1
                break

            # Check ping/pong activity
            if provider.last_ping_time:
                if last_ping_time != provider.last_ping_time:
                    # New ping received
                    monitor.ping_count += 1
                    last_ping_time = provider.last_ping_time

                    # Check pong response time
                    if provider.last_pong_time and provider.last_pong_time >= provider.last_ping_time:
                        response_time = (provider.last_pong_time - provider.last_ping_time).total_seconds()
                        monitor.pong_count += 1

                        if response_time < 0.1:
                            monitor.log(f"Ping/Pong: {response_time*1000:.1f}ms (excellent)", "INFO")
                        elif response_time < 1.0:
                            monitor.log(f"Ping/Pong: {response_time*1000:.1f}ms (good)", "INFO")
                        else:
                            monitor.log(f"Ping/Pong: {response_time:.2f}s (SLOW!)", "WARN")
                    else:
                        monitor.log(f"Ping received, waiting for pong...", "DEBUG")
            else:
                # Check how long since connection without pings
                if monitor.ping_count == 0:
                    elapsed = (datetime.now() - monitor.start_time).total_seconds()
                    if elapsed > 60:
                        monitor.log(f"No pings for {elapsed:.0f}s - unusual!", "WARN")

            # Check task health
            if hasattr(provider, '_listen_task'):
                if provider._listen_task and provider._listen_task.done():
                    monitor.log("Listen task DIED!", "ERROR")
                    monitor.errors.append("Listen task terminated")
                    try:
                        exception = provider._listen_task.exception()
                        monitor.log(f"Task exception: {exception}", "ERROR")
                    except:
                        pass

            if hasattr(provider, '_heartbeat_task'):
                if provider._heartbeat_task and provider._heartbeat_task.done():
                    monitor.log("Heartbeat task DIED!", "ERROR")
                    monitor.errors.append("Heartbeat task terminated")

            # Wait before next check
            await asyncio.sleep(ping_check_interval)

        except Exception as e:
            monitor.log(f"Monitor error: {e}", "ERROR")
            monitor.errors.append(str(e))

    monitor.log("Monitoring complete")


async def main():
    """Run 5-minute stability test."""
    print("=" * 70)
    print("FINNHUB WEBSOCKET STABILITY TEST - 5 MINUTES")
    print("=" * 70)
    print()

    # Load settings
    print("[1/4] Loading configuration...")
    settings = get_settings()
    print(f"      Primary provider: {settings.data.primary_provider}")
    print(f"      Finnhub key: {'SET' if settings.data.finnhub_api_key else 'NOT SET'}")

    if not settings.data.finnhub_api_key:
        print("\n[ERROR] Finnhub API key not set!")
        return 1

    # Create provider
    print("\n[2/4] Creating Finnhub provider...")
    provider = DataProviderFactory.create_realtime_provider(
        provider_type="finnhub",
        finnhub_api_key=settings.data.finnhub_api_key
    )
    print(f"      Provider: {provider.provider_name}")

    # Connect
    print("\n[3/4] Connecting to Finnhub...")
    try:
        await provider.connect()
        if not provider.connected:
            print("[ERROR] Connected but connection state is False!")
            return 1
    except Exception as e:
        print(f"[ERROR] Failed to connect: {e}")
        return 1

    print(f"      Connected: {provider.connected}")
    print(f"      State: {provider.state}")

    # Subscribe to test symbols
    test_symbols = ["AAPL", "GOOGL", "MSFT"]
    for symbol in test_symbols:
        await provider.subscribe(symbol)
    print(f"      Subscribed to: {', '.join(test_symbols)}")

    # Run stability test
    print("\n[4/4] Running 5-minute stability test...")
    print("      (Monitoring ping/pong, connection state, task health)")
    print("      Press Ctrl+C to stop early\n")

    monitor = ConnectionMonitor()

    try:
        await monitor_connection(provider, monitor, duration=300)  # 5 minutes
    except KeyboardInterrupt:
        monitor.log("Test interrupted by user", "WARN")
    finally:
        # Disconnect
        await provider.disconnect()

    # Report
    monitor.report()

    return 0 if monitor.disconnect_count == 0 else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
