#!/usr/bin/env python3
"""
Market Hours Orchestrator - Runs trading scripts during market hours only.

This script runs 24/7 and automatically starts the trading orchestrator
1 hour before market open (8:30 AM ET) and stops before market close
(4:00 PM ET). It handles:
- Market open/close detection
- Timezone management (US/Eastern)
- Script execution and monitoring
- Graceful shutdown and error handling
- Logging of all activities
"""

import sys
import time
import subprocess
import signal
from pathlib import Path
from datetime import datetime, timedelta
import pytz
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import get_logger

logger = get_logger("OrchestratorMain")

# Market configuration (US/Eastern)
MARKET_TZ = pytz.timezone("America/New_York")
MARKET_OPEN_TIME = "09:30"  # 9:30 AM ET
MARKET_CLOSE_TIME = "16:00"  # 4:00 PM ET
PRE_MARKET_START = "08:30"  # 1 hour before open
POST_MARKET_WAIT = 30  # seconds to wait after market close

# Script to run
TRADING_SCRIPT = "scripts/test_finnhub_orchestrator.py"
MAX_SCRIPT_DURATION = 7 * 3600  # 7 hours max (8:30 AM - 3:30 PM)
STATUS_LOG_INTERVAL = 60  # seconds between status logs (set to 10-30 for production, lower for debugging)


class MarketHoursOrchestrator:
    """Manages execution of trading scripts during market hours."""

    def __init__(self, script_path: str):
        """Initialize orchestrator.

        Args:
            script_path: Path to the trading script to run
        """
        self.script_path = Path(script_path)
        self.process: Optional[subprocess.Popen] = None
        self.script_start_time: Optional[datetime] = None
        self.should_exit = False

    def setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        def signal_handler(signum, frame):
            logger.info(f"Signal {signum} received. Shutting down gracefully...")
            self.should_exit = True
            self.stop_script()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def is_market_open_day(self) -> bool:
        """Check if today is a market trading day (not weekend)."""
        now = datetime.now(MARKET_TZ)
        # 0-4 = Monday-Friday, 5-6 = Saturday-Sunday
        return now.weekday() < 5

    def get_market_hours_today(self) -> tuple[datetime, datetime]:
        """Get market open and close times for today in UTC."""
        now = datetime.now(MARKET_TZ)
        today = now.date()

        market_open = MARKET_TZ.localize(
            datetime.combine(today, datetime.strptime(MARKET_OPEN_TIME, "%H:%M").time())
        )
        market_close = MARKET_TZ.localize(
            datetime.combine(today, datetime.strptime(MARKET_CLOSE_TIME, "%H:%M").time())
        )

        return market_open, market_close

    def get_pre_market_start_time(self) -> datetime:
        """Get pre-market start time (1 hour before market open)."""
        now = datetime.now(MARKET_TZ)
        today = now.date()

        pre_market = MARKET_TZ.localize(
            datetime.combine(today, datetime.strptime(PRE_MARKET_START, "%H:%M").time())
        )

        # If we're past pre-market start today, return tomorrow's pre-market start
        if now > pre_market:
            tomorrow = now.date() + timedelta(days=1)
            pre_market = MARKET_TZ.localize(
                datetime.combine(tomorrow, datetime.strptime(PRE_MARKET_START, "%H:%M").time())
            )

        return pre_market

    def is_market_hours(self) -> bool:
        """Check if we are currently in market trading hours."""
        if not self.is_market_open_day():
            return False

        now = datetime.now(MARKET_TZ)
        market_open, market_close = self.get_market_hours_today()

        return market_open <= now <= market_close

    def should_start_script(self) -> bool:
        """Check if we should start the trading script."""
        if self.process is not None:
            # Script already running
            return False

        now = datetime.now(MARKET_TZ)

        # If not a market day, don't start
        if not self.is_market_open_day():
            return False

        # Get pre-market start time
        pre_market_time = MARKET_TZ.localize(
            datetime.combine(
                now.date(),
                datetime.strptime(PRE_MARKET_START, "%H:%M").time(),
            )
        )

        # Start if we're between pre-market and market close
        market_close = MARKET_TZ.localize(
            datetime.combine(
                now.date(),
                datetime.strptime(MARKET_CLOSE_TIME, "%H:%M").time(),
            )
        )

        return pre_market_time <= now <= market_close

    def should_stop_script(self) -> bool:
        """Check if we should stop the running script."""
        if self.process is None:
            return False

        now = datetime.now(MARKET_TZ)

        # Stop if market has closed
        if not self.is_market_open_day():
            logger.info("Market is closed (weekend). Stopping script.")
            return True

        market_close = MARKET_TZ.localize(
            datetime.combine(
                now.date(),
                datetime.strptime(MARKET_CLOSE_TIME, "%H:%M").time(),
            )
        )

        # Allow script to run for 1 hour after market close
        stop_time = market_close + timedelta(hours=1)
        if now > stop_time:
            logger.info("Stop time (1 hour after market close) reached. Stopping script.")
            return True

        # Stop if script has been running too long
        if self.script_start_time:
            elapsed = (now - self.script_start_time).total_seconds()
            if elapsed > MAX_SCRIPT_DURATION:
                logger.warning(f"Script exceeded max duration ({elapsed}s). Stopping.")
                return True

        return False

    def start_script(self):
        """Start the trading script in a subprocess."""
        try:
            now = datetime.now(MARKET_TZ)
            logger.info(f"Starting trading script at {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info(f"Running: {self.script_path}")

            # Start the process
            self.process = subprocess.Popen(
                [sys.executable, str(self.script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.script_start_time = now

            logger.info(f"Script started with PID {self.process.pid}")

        except Exception as e:
            logger.error(f"Failed to start script: {e}")
            self.process = None

    def stop_script(self):
        """Stop the running trading script gracefully."""
        if self.process is None:
            return

        try:
            logger.info(f"Stopping trading script (PID {self.process.pid})")

            # Try graceful shutdown first
            self.process.terminate()

            try:
                # Wait up to 10 seconds for graceful shutdown
                self.process.wait(timeout=10)
                logger.info("Script stopped gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't stop
                logger.warning("Script did not stop gracefully. Force killing...")
                self.process.kill()
                self.process.wait()
                logger.info("Script force killed")

        except Exception as e:
            logger.error(f"Error stopping script: {e}")
        finally:
            self.process = None

    def monitor_script(self):
        """Monitor the running script and log its output."""
        if self.process is None:
            return

        try:
            # Check if process is still running
            if self.process.poll() is not None:
                # Process has finished
                return_code = self.process.returncode
                logger.info(f"Script exited with return code: {return_code}")
                self.process = None
                return

            # Try to read available output (non-blocking)
            if self.process.stdout:
                import select

                # Use select for non-blocking read (Unix-like systems)
                # On Windows, we'll just skip this part
                if hasattr(select, "select"):
                    try:
                        ready, _, _ = select.select([self.process.stdout], [], [], 0)
                        if ready:
                            line = self.process.stdout.readline()
                            if line:
                                logger.debug(f"Script output: {line.rstrip()}")
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error monitoring script: {e}")

    def run(self):
        """Main orchestration loop - runs 24/7."""
        logger.info("=== Market Hours Orchestrator Started ===")
        logger.info(f"Market timezone: {MARKET_TZ}")
        logger.info(f"Script: {self.script_path}")
        logger.info(f"Pre-market start: {PRE_MARKET_START}")
        logger.info(f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME}")

        self.setup_signal_handlers()

        last_status_log = datetime.now(MARKET_TZ)
        check_interval = 5  # Check every 5 seconds

        while not self.should_exit:
            try:
                now = datetime.now(MARKET_TZ)

                # Log status periodically
                if (now - last_status_log).total_seconds() >= STATUS_LOG_INTERVAL:
                    is_market_day = self.is_market_open_day()
                    status = "running" if self.process else "idle"
                    logger.info(
                        f"Status: {status} | Market day: {is_market_day} | "
                        f"Time: {now.strftime('%H:%M:%S')} ET"
                    )
                    last_status_log = now

                # Check if we should start the script
                if self.should_start_script():
                    self.start_script()

                # Check if we should stop the script
                if self.should_stop_script():
                    self.stop_script()

                # Monitor the running script
                self.monitor_script()

                # Sleep before next check
                time.sleep(check_interval)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                self.should_exit = True
            except Exception as e:
                logger.error(f"Error in orchestration loop: {e}", exc_info=True)
                time.sleep(check_interval)

        # Cleanup
        self.stop_script()
        logger.info("=== Market Hours Orchestrator Stopped ===")


def main():
    """Entry point."""
    try:
        # Verify script exists
        script_path = Path(__file__).parent / TRADING_SCRIPT
        if not script_path.exists():
            logger.error(f"Trading script not found: {script_path}")
            return 1

        # Run orchestrator
        orchestrator = MarketHoursOrchestrator(str(script_path))
        orchestrator.run()
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
