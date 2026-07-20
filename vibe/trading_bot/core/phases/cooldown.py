"""Post-market cooldown phase manager.

This module handles the post-market cooldown phase (5 minutes after market close)
which processes final data, disconnects providers, and prepares for the next trading day.
"""

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from vibe.trading_bot.core.phases.base import BasePhase
from vibe.trading_bot.notifications.helper import discord_notification_context
from vibe.trading_bot.notifications.payloads import SystemAlertPayload
from vibe.trading_bot.utils.datetime_utils import get_market_now
from vibe.trading_bot.version import BUILD_VERSION

if TYPE_CHECKING:
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator


class CooldownPhaseManager(BasePhase):
    """Manages post-market cooldown phase (5 min after close).

    The cooldown phase:
    - Keeps provider connected for 5 minutes after market close
    - Processes final real-time bars and trades
    - Completes pending operations
    - Rotates tick log files for next session
    - Disconnects from provider
    - Calculates sleep time until next warm-up
    """

    DEFAULT_COOLDOWN_DURATION = 300  # 5 minutes (production)
    TESTING_COOLDOWN_DURATION = 5    # 5 seconds (testing)

    def __init__(self, orchestrator: 'TradingOrchestrator'):
        """Initialize cooldown manager.

        Args:
            orchestrator: Parent orchestrator
        """
        super().__init__(orchestrator)

        # Use shorter cooldown duration in testing mode for faster tests
        if orchestrator._testing_mode:
            self.COOLDOWN_DURATION = self.TESTING_COOLDOWN_DURATION
        else:
            self.COOLDOWN_DURATION = self.DEFAULT_COOLDOWN_DURATION

        self._cooldown_start_time: Optional[datetime] = None
        self._cooldown_started = False  # Dedicated flag to prevent re-entry
        self._market_closed_logged = False

    def should_enter_cooldown(self) -> bool:
        """Check if should enter cooldown phase.

        Returns:
            True if market is closed and cooldown hasn't started yet
        """
        return (
            not self.market_scheduler.is_market_open() and
            not self._cooldown_started and
            self.active_provider and
            self.active_provider.connected
        )

    def is_cooldown_complete(self) -> bool:
        """Check if cooldown phase is complete.

        Returns:
            True if cooldown duration has elapsed
        """
        if self._cooldown_start_time is None:
            return False

        now = get_market_now(self.market_scheduler)
        elapsed = (now - self._cooldown_start_time).total_seconds()
        return elapsed >= self.COOLDOWN_DURATION

    def get_remaining_seconds(self) -> float:
        """Get remaining cooldown time in seconds.

        Returns:
            Remaining seconds (0 if cooldown complete or not started)
        """
        if self._cooldown_start_time is None:
            return 0

        now = get_market_now(self.market_scheduler)
        elapsed = (now - self._cooldown_start_time).total_seconds()
        return max(0, self.COOLDOWN_DURATION - elapsed)

    def reset(self) -> None:
        """Reset cooldown state for next trading day."""
        self._cooldown_start_time = None
        self._cooldown_started = False
        self._market_closed_logged = False

    async def execute(self) -> None:
        """Execute cooldown phase logic.

        Handles:
        - Cooldown initialization
        - Final data processing during cooldown
        - Provider disconnect after cooldown
        - Tick log rotation
        """
        now = get_market_now(self.market_scheduler)

        # Enter cooldown if needed
        if self.should_enter_cooldown():
            self._cooldown_start_time = now
            self._cooldown_started = True  # Set dedicated flag to prevent re-entry
            self._log_cooldown_start(now)

        # Still in cooldown - keep processing
        if not self.is_cooldown_complete():
            remaining = self.get_remaining_seconds()
            self.logger.info(
                f"Cooldown phase: {remaining:.0f}s remaining. "
                f"Processing final data..."
            )
            # Sleep briefly to allow final data processing
            # Use shorter interval in testing mode for faster tests
            sleep_interval = 1 if self.orchestrator._testing_mode else 30
            await asyncio.sleep(sleep_interval)
            return

        # Cooldown complete - cleanup (only once)
        if not self._market_closed_logged:
            await self._complete_cooldown()
            self._market_closed_logged = True

        # IMPORTANT: Return immediately if already completed to prevent tight loop
        # The orchestrator will handle sleep until next trading day
        return

    def _log_cooldown_start(self, now: datetime) -> None:
        """Log cooldown phase start.

        Args:
            now: Current time in market timezone
        """
        disconnect_time = now + timedelta(seconds=self.COOLDOWN_DURATION)

        self.logger.info("="*60)
        self.logger.info("MARKET CLOSED - ENTERING COOLDOWN PHASE")
        self.logger.info("="*60)
        self.logger.info(
            f"Market closed at {now.strftime('%H:%M:%S')}. "
            f"Entering {self.COOLDOWN_DURATION}s cooldown phase for cleanup:"
        )
        self.logger.info("  - Processing final real-time bars")
        self.logger.info("  - Completing pending operations")
        self.logger.info("  - Generating daily summaries")
        self.logger.info(f"  - Will disconnect from provider at {disconnect_time.strftime('%H:%M:%S')}")

    async def _complete_cooldown(self) -> None:
        """Complete cooldown phase: rotate logs, disconnect provider."""
        self.logger.info("=" * 60)
        self.logger.info("COOLDOWN PHASE COMPLETE")
        self.logger.info("=" * 60)

        # Warn if any positions are still open — EOD exit at 15:55 should have closed them
        # We cannot execute trades after market close; this is a signal that EOD exit missed something
        self._warn_open_positions()

        await self._flush_dashboard_publisher()

        # Disconnect from provider first (closes current tick log handle cleanly)
        await self._disconnect_provider()

        # Rotate tick log AFTER disconnect so the new file handle is not
        # immediately closed by disconnect()
        await self._rotate_tick_logs()

    def _warn_open_positions(self) -> None:
        """Log a warning if any positions are still open at market close.

        EOD exit logic (at ruleset eod_time, e.g. 15:55) should have closed all positions
        before market close. If positions are still open here, that is a bug.
        """
        try:
            strategy = self.orchestrator.strategy
            if strategy and strategy.positions:
                symbols = list(strategy.positions.keys())
                self.logger.error(
                    f"[UNCLOSED POSITIONS] {len(symbols)} position(s) still open at market close: "
                    f"{symbols}. EOD exit at ruleset eod_time should have closed these. "
                    f"These positions will carry over as stale state — restart the bot to clear."
                )
                for symbol in symbols:
                    pos = strategy.get_position(symbol)
                    if pos:
                        self.logger.error(
                            f"  {symbol}: {pos['side']} @ ${pos['entry_price']:.2f}, "
                            f"SL=${pos['stop_loss']:.2f}"
                        )
        except Exception as e:
            self.logger.error(f"Error checking open positions at cooldown: {e}", exc_info=True)

    async def _flush_dashboard_publisher(self) -> None:
        """Run a bounded dashboard publication flush at cooldown."""
        publisher = getattr(self.orchestrator, "remote_data_publisher", None)
        if publisher is None:
            return
        try:
            result = await publisher.flush_pending(timeout_seconds=30.0, max_batches=5)
            summary = publisher.publish_cooldown_summary()
            self.logger.info(
                "Dashboard cooldown publish flush: claimed=%s published=%s failed=%s dead_lettered=%s status=%s",
                result.claimed,
                result.published,
                result.failed,
                result.dead_lettered,
                summary,
            )
            await self._send_dashboard_publish_alert(summary)
        except Exception as e:
            self.logger.warning("Dashboard cooldown publish flush failed: %s", e)

    async def _send_dashboard_publish_alert(self, summary: dict) -> None:
        """Send a Discord alert if cooldown leaves unresolved publish rows."""
        unresolved_statuses = ("pending", "failed", "publishing", "dead_letter")
        unresolved = {
            status: int(summary.get(status, 0) or 0)
            for status in unresolved_statuses
            if int(summary.get(status, 0) or 0) > 0
        }
        if not unresolved:
            return

        notifications = getattr(self.config, "notifications", None)
        webhook_url = getattr(notifications, "discord_webhook_url", None)
        notify_on_error = getattr(notifications, "notify_on_error", True)
        if not webhook_url or not notify_on_error:
            self.logger.warning("Dashboard publish rows unresolved at cooldown: %s", unresolved)
            return

        has_dead_letter = unresolved.get("dead_letter", 0) > 0
        payload = SystemAlertPayload(
            event_type="SYSTEM_ERROR" if has_dead_letter else "SYSTEM_WARNING",
            timestamp=get_market_now(self.market_scheduler),
            severity="error" if has_dead_letter else "warning",
            title="Live Dashboard Publication Degraded",
            message="Cooldown completed with unresolved dashboard publication rows.",
            component="RemoteDataPublisher",
            action_required=(
                "Inspect publish_outbox and publish_failures; confirm Supabase connectivity "
                "before pruning local dashboard data."
            ),
            version=BUILD_VERSION,
            details={**summary, "unresolved_total": sum(unresolved.values())},
        )
        try:
            async with discord_notification_context(webhook_url) as notifier:
                await notifier.send_system_alert(payload)
        except Exception as e:
            self.logger.warning("Dashboard publish Discord alert failed: %s", e)

    async def _rotate_tick_logs(self) -> None:
        """Rotate tick log file for next market session."""
        if self.active_provider and hasattr(self.active_provider, 'rotate_tick_log_for_new_session'):
            try:
                self.active_provider.rotate_tick_log_for_new_session()
                self.logger.info("[OK] Rotated tick log file for next market session")
            except Exception as e:
                self.logger.warning(f"Failed to rotate tick log file: {e}")

    async def _disconnect_provider(self) -> None:
        """Disconnect from real-time data provider."""
        if self.active_provider and self.active_provider.connected:
            await self.active_provider.disconnect()
            self.logger.info(f"[OK] Disconnected from {self.active_provider.provider_name} after {self.COOLDOWN_DURATION}s cooldown")

    def calculate_sleep_until_warmup(self) -> float:
        """Calculate seconds to sleep until next warm-up time.

        Returns:
            Sleep duration in seconds
        """
        now = get_market_now(self.market_scheduler)

        # Get next warm-up or market open time
        next_warmup = self.market_scheduler.get_warmup_time()
        next_open = self.market_scheduler.next_market_open()
        target_time = next_warmup if next_warmup else next_open

        sleep_seconds = (target_time - now).total_seconds()
        return max(0, sleep_seconds)

    def should_log_sleep_message(self) -> bool:
        """Check if should log the sleep message (avoid spam).

        Returns:
            True if message hasn't been logged yet
        """
        return not self._market_closed_logged
