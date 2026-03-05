"""Pre-market warm-up phase manager.

This module handles the pre-market warm-up phase (9:25-9:30 AM EST) which prepares
the trading bot for market open by pre-fetching data, connecting to real-time providers,
and running health checks.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from vibe.trading_bot.core.phases.base import BasePhase
from vibe.trading_bot.data.providers.types import WebSocketDataProvider
from vibe.trading_bot.api.health import set_health_state
from vibe.trading_bot.notifications.discord import DiscordNotifier
from vibe.trading_bot.notifications.payloads import SystemStatusPayload
from vibe.trading_bot.version import BUILD_VERSION


class WarmupPhaseManager(BasePhase):
    """Manages warm-up phase for provider connection and health verification.

    The warm-up phase prepares the bot for trading by:
    1. Pre-fetching 2 days of historical data (warm cache)
    2. Connecting to real-time data provider (WebSocket or REST)
    3. Verifying WebSocket ping/pong (confirms connection health)
    4. Pre-calculating indicators (currently just verification)
    5. Running health checks on all components
    6. Sending Discord notification with status (optional, only for pre-market)

    Used for both:
    - Pre-market warmup (9:25-9:30 AM EST) - sends Discord notification
    - Intraday restart - skips Discord notification
    """

    WEBSOCKET_PING_TIMEOUT = 70  # Finnhub pings ~60s, wait up to 70s

    async def execute(self, send_notification: bool = True) -> bool:
        """Execute warm-up phase: prefetch data, connect provider, verify health.

        Args:
            send_notification: If True, send Discord notification (for pre-market).
                             If False, skip notification (for intraday restart).

        Returns:
            True if warm-up successful, False if errors occurred
        """
        self.logger.info("=" * 60)
        if send_notification:
            self.logger.info("PRE-MARKET WARM-UP PHASE")
        else:
            self.logger.info("WARM-UP PHASE (Intraday Restart)")
        self.logger.info("=" * 60)

        market_open = self.market_scheduler.get_open_time()
        if market_open:
            self.logger.info(f"Market opens at: {market_open.strftime('%H:%M:%S %Z')}")

        warmup_success = True

        # Step 0: Clear stale data from previous session
        self._cleanup_stale_data()

        # Step 1: Pre-fetch historical data
        warmup_success &= await self._prefetch_historical_data()

        # Step 2: Connect to real-time provider
        warmup_success &= await self._connect_realtime_provider()

        # Step 2.5: Verify and recreate bar aggregators if missing (CRITICAL for real-time bars)
        warmup_success &= await self._verify_bar_aggregators()

        # Step 3: Pre-calculate indicators (optional)
        await self._precalculate_indicators()

        # Step 4: Run health checks
        health_status, all_healthy = await self._run_health_checks()

        # Summary
        self.logger.info("=" * 60)
        if warmup_success and all_healthy:
            if send_notification:
                self.logger.info("WARM-UP COMPLETE - Ready for market open!")
            else:
                self.logger.info("WARM-UP COMPLETE - Ready for trading!")
        else:
            self.logger.warning("WARM-UP COMPLETE - Some issues detected (continuing anyway)")
        self.logger.info("=" * 60)

        # Step 5: Send Discord notification (only for pre-market)
        if send_notification:
            await self._send_discord_notification(warmup_success, all_healthy, health_status)

        return warmup_success

    def _cleanup_stale_data(self) -> None:
        """Clear stale data and reset state from previous trading session.

        This ensures we start fresh each day:
        - Clear old real-time bars (but keep bar aggregators with callbacks)
        - Reset logging state trackers (ORB logs, approach logs)
        - Reset market closed flags
        - Reset cooldown state
        - Reset daily statistics

        Note: Bar aggregators are kept alive (they have callbacks registered).
        """
        self.logger.info("Step 0/6: Cleaning up stale data from previous session...")

        # 1. Clear stale real-time bars
        if hasattr(self.orchestrator, '_realtime_bars'):
            bars_count = len(self.orchestrator._realtime_bars)
            self.orchestrator._realtime_bars.clear()
            if bars_count > 0:
                self.logger.info(f"  Cleared {bars_count} stale bar series")
            else:
                self.logger.info("  No stale bars to clear")

        # 2. Reset logging state trackers (for fresh daily logs)
        if hasattr(self.orchestrator, '_orb_logged_today'):
            self.orchestrator._orb_logged_today.clear()
            self.logger.info("  Reset ORB logging tracker")

        if hasattr(self.orchestrator, '_last_approach_logged'):
            self.orchestrator._last_approach_logged.clear()
            self.logger.info("  Reset approach logging tracker")

        # 3. Reset market closed flag (used to avoid log spam after cooldown)
        if hasattr(self.orchestrator, '_market_closed_logged'):
            self.orchestrator._market_closed_logged = False
            self.logger.info("  Reset market closed flag")

        # 4. Reset ORB notification flag for new day
        if hasattr(self.orchestrator, '_orb_notification_sent_date'):
            self.orchestrator._orb_notification_sent_date = None
            self.logger.info("  Reset ORB notification flag")

        # 5. Reset daily statistics (proactive instead of reactive)
        from datetime import datetime
        if hasattr(self.orchestrator, '_daily_stats'):
            current_date = datetime.now(self.market_scheduler.timezone).date().isoformat()
            if self.orchestrator._daily_stats.get("date") != current_date:
                self.orchestrator._daily_stats = self.orchestrator._initialize_daily_stats()
                self.logger.info(f"  Reset daily statistics for {current_date}")

        # NOTE: Cooldown reset removed from here - only happens in orchestrator
        # during official pre-market warmup phase (not mid-session reconnects)

        self.logger.info("Cleanup complete!")

    async def _prefetch_historical_data(self) -> bool:
        """Warm cache by pre-fetching 2 days of historical data.

        Returns:
            True if successful, False if errors occurred
        """
        self.logger.info("Step 1/5: Warming cache with historical data...")

        try:
            for symbol in self.config.trading.symbols:
                self.logger.info(f"  Pre-fetching 2 days of data for {symbol}...")
                bars = await self.data_manager.get_data(
                    symbol=symbol,
                    timeframe="5m",
                    days=2,  # Fetch 2 days for indicator context
                )

                if bars is not None and not bars.empty:
                    self.logger.info(f"  OK {symbol}: {len(bars)} bars loaded")
                else:
                    self.logger.warning(f"  WARNING {symbol}: No data fetched")
                    return False

            self.logger.info("Cache warm-up complete!")
            return True

        except Exception as e:
            self.logger.error(f"Error during cache warm-up: {e}", exc_info=True)
            return False

    async def _connect_realtime_provider(self) -> bool:
        """Connect to real-time data provider and verify WebSocket health.

        Returns:
            True if connected successfully, False otherwise
        """
        self.logger.info("Step 2/5: Connecting to real-time data provider...")

        try:
            if self.primary_provider:
                await self.primary_provider.connect()

                if self.primary_provider.connected:
                    self.logger.info(f"   [OK] Connected to {self.primary_provider.provider_name}")

                    # Update health state
                    set_health_state(websocket_connected=True, recent_heartbeat=True)

                    # Subscribe if WebSocket
                    if isinstance(self.primary_provider, WebSocketDataProvider):
                        for symbol in self.config.trading.symbols:
                            await self.primary_provider.subscribe(symbol)
                        self.logger.info(f"   [OK] Subscribed to {len(self.config.trading.symbols)} symbols")

                        # Wait for first ping/pong to verify connection is truly healthy
                        # Finnhub sends pings every ~60s, so we need to wait longer
                        self.logger.info("   [*] Waiting for WebSocket ping/pong verification (up to 70s)...")
                        waited = 0
                        while waited < self.WEBSOCKET_PING_TIMEOUT:
                            if hasattr(self.primary_provider, 'last_pong_time') and self.primary_provider.last_pong_time:
                                self.logger.info(f"   [OK] WebSocket ping/pong verified after {waited:.1f}s")
                                break
                            await asyncio.sleep(1)
                            waited += 1

                        if not (hasattr(self.primary_provider, 'last_pong_time') and self.primary_provider.last_pong_time):
                            self.logger.warning("   [!] WebSocket ping/pong not received within 70s timeout")
                            self.logger.warning("   [!] Connection may be unstable (continuing anyway)")

                    return True
                else:
                    raise Exception("Connection failed - provider not connected")
            else:
                self.logger.info("No real-time provider configured (will use Yahoo Finance only)")
                return True

        except Exception as e:
            self.logger.error(f"   ❌ Failed to connect: {e}", exc_info=True)
            if self.secondary_provider:
                await self._switch_to_secondary_provider()
            self.logger.warning("Continuing without Finnhub (Yahoo Finance fallback)")
            return False

    async def _switch_to_secondary_provider(self) -> None:
        """Switch to secondary provider if primary fails."""
        try:
            self.logger.info("Attempting to switch to secondary provider...")
            await self.secondary_provider.connect()
            if self.secondary_provider.connected:
                self.orchestrator.active_provider = self.secondary_provider
                self.logger.info(f"Switched to secondary provider: {self.secondary_provider.provider_name}")
        except Exception as e:
            self.logger.error(f"Failed to connect to secondary provider: {e}")

    async def _verify_bar_aggregators(self) -> bool:
        """Verify bar aggregators exist and recreate if missing.

        Bar aggregators can be destroyed if an exception occurs during
        provider initialization. This check ensures they're always present
        after warmup completes, making the system self-healing.

        Returns:
            True if aggregators are ready, False if errors occurred
        """
        self.logger.info("Step 2.5/5: Verifying bar aggregators...")

        try:
            from vibe.trading_bot.data.aggregator import BarAggregator
            from vibe.trading_bot.data.providers.types import WebSocketDataProvider

            missing_symbols = []

            # Check if each symbol has an aggregator
            for symbol in self.config.trading.symbols:
                if symbol not in self.orchestrator.bar_aggregators:
                    missing_symbols.append(symbol)

            if missing_symbols:
                self.logger.warning(f"   [!] Missing bar aggregators for: {missing_symbols}")
                self.logger.info("   [*] Recreating bar aggregators (self-healing)...")

                # Recreate missing aggregators
                for symbol in missing_symbols:
                    aggregator = BarAggregator(
                        bar_interval="5m",
                        timezone=str(self.market_scheduler.timezone)
                    )
                    # Set up bar completion callback with symbol binding
                    aggregator.on_bar_complete(
                        lambda bar_dict, sym=symbol: self.orchestrator._handle_completed_bar(sym, bar_dict)
                    )
                    self.orchestrator.bar_aggregators[symbol] = aggregator
                    self.logger.info(f"   [OK] Recreated aggregator for {symbol}")

                # Re-register trade callback if WebSocket
                if self.primary_provider and isinstance(self.primary_provider, WebSocketDataProvider):
                    self.primary_provider.on_trade(self.orchestrator._handle_realtime_trade)
                    self.logger.info("   [OK] Re-registered trade callback")

                self.logger.info("   [✓] Bar aggregators restored!")
            else:
                self.logger.info(f"   [OK] All {len(self.config.trading.symbols)} bar aggregators present")

            return True

        except Exception as e:
            self.logger.error(f"   ❌ Failed to verify bar aggregators: {e}", exc_info=True)
            return False

    async def _precalculate_indicators(self) -> bool:
        """Pre-calculate indicators (optional optimization).

        Currently just verifies indicator engine is ready.

        Returns:
            True if successful
        """
        self.logger.info("Step 3/5: Pre-calculating indicators...")

        try:
            if self.indicator_engine:
                self.logger.info("Indicator engine ready!")
            else:
                self.logger.warning("Indicator engine not initialized")
            return True
        except Exception as e:
            self.logger.error(f"Error checking indicators: {e}")
            return False

    async def _run_health_checks(self) -> tuple[Dict[str, Any], bool]:
        """Run health checks on all components.

        Returns:
            Tuple of (health_status dict, all_healthy bool)
        """
        self.logger.info("Step 4/5: Running health checks...")

        health_result = self.health_monitor.get_health()
        health_status = health_result.get("components", {})

        all_healthy = all(comp["status"] == "healthy" for comp in health_status.values())

        if all_healthy:
            self.logger.info("All systems healthy!")
        else:
            self.logger.warning("Some systems unhealthy:")
            for component, status in health_status.items():
                if status["status"] != "healthy":
                    self.logger.warning(f"  {component}: {status['status']}")

        return health_status, all_healthy

    async def _send_discord_notification(
        self,
        warmup_success: bool,
        all_healthy: bool,
        health_status: Dict[str, Any]
    ) -> None:
        """Send warm-up status to Discord.

        Args:
            warmup_success: Whether warmup completed successfully
            all_healthy: Whether all health checks passed
            health_status: Health status details from health monitor
        """
        if not self.config.notifications.discord_webhook_url:
            self.logger.debug("Discord webhook URL not configured, skipping warm-up notification")
            return

        try:
            # Create Discord notifier for system status
            notifier = DiscordNotifier(webhook_url=self.config.notifications.discord_webhook_url)
            await notifier.start()

            # Determine overall status
            overall_status = "healthy" if warmup_success and all_healthy else "degraded"

            # Get provider status and verify ping/pong
            primary_provider_status = None
            primary_provider_name = None
            websocket_ping_received = False

            if self.primary_provider:
                primary_provider_name = self.primary_provider.provider_name
                primary_provider_status = "connected" if self.primary_provider.connected else "disconnected"

                # Check if WebSocket ping/pong was actually verified
                if hasattr(self.primary_provider, 'last_pong_time') and self.primary_provider.last_pong_time:
                    websocket_ping_received = True

            payload = SystemStatusPayload(
                event_type="MARKET_START",
                timestamp=datetime.now(),
                overall_status=overall_status,
                warmup_completed=warmup_success,
                primary_provider_status=primary_provider_status,
                primary_provider_name=primary_provider_name,
                websocket_ping_received=websocket_ping_received,
                market_status="pre_market",
                version=BUILD_VERSION,
                details={
                    "components": health_status,
                    "all_healthy": all_healthy,
                    "message": "Ready for market open!" if warmup_success and all_healthy
                               else "Some issues detected (continuing anyway)"
                }
            )
            await notifier.send_system_status(payload)
            await notifier.stop()
            self.logger.debug("Warm-up status notification sent to Discord")

        except Exception as e:
            self.logger.warning(f"Failed to send warm-up notification to Discord: {e}")
