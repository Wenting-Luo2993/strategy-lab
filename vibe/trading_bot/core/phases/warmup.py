"""Pre-market warm-up phase manager.

This module handles the pre-market warm-up phase (9:25-9:30 AM EST) which prepares
the trading bot for market open by pre-fetching data, connecting to real-time providers,
and running health checks.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any

from vibe.trading_bot.core.phases.base import BasePhase
from vibe.trading_bot.data.providers.types import WebSocketDataProvider
from vibe.trading_bot.api.health import set_health_state
from vibe.trading_bot.notifications.discord import DiscordNotifier
from vibe.trading_bot.notifications.helper import discord_notification_context
from vibe.trading_bot.notifications.payloads import (
    RiskAlertPayload,
    SystemAlertPayload,
    SystemStatusPayload,
)
from vibe.trading_bot.utils.datetime_utils import get_market_now
from vibe.trading_bot.version import BUILD_VERSION
from vibe.common.models import OrderStatus, Position


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
    CARRYOVER_FLATTEN_TIMEOUT_SECONDS = 360

    def __init__(self, orchestrator) -> None:
        super().__init__(orchestrator)
        self._critical_broker_alert_sent = False

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

        # Reconnect the trading client before any position or equity reads.
        broker_healthy = await self._verify_broker_health()
        warmup_success &= broker_healthy

        if broker_healthy:
            warmup_success &= await self._apply_carryover_position_policy(
                send_notification=send_notification
            )
            # A snapshot failure blocks entries, but not exit management.
            await self._take_session_equity_snapshot()
        else:
            # A transient disconnect must not persist a session-long E0 failure.
            self.logger.error(
                "Skipping carryover handling and equity snapshot until the "
                "execution connection recovers; warm-up will be retried"
            )

        # Step 5: Run health checks
        health_status, all_healthy = await self._run_health_checks()

        # Summary
        self.logger.info("=" * 60)
        if warmup_success and all_healthy:
            if send_notification:
                self.logger.info("WARM-UP COMPLETE - Ready for market open!")
            else:
                self.logger.info("WARM-UP COMPLETE - Ready for trading!")
        else:
            self.logger.warning("WARM-UP COMPLETE - Some checks failed")
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

        # 6. Reset cooldown manager state from previous day
        if hasattr(self.orchestrator, 'cooldown_manager'):
            self.orchestrator.cooldown_manager.reset()
            self.logger.info("  Reset cooldown manager state")

        self.logger.info("Cleanup complete!")

    async def _prefetch_historical_data(self) -> bool:
        """Warm cache by pre-fetching 2 days of historical data.

        Returns:
            True if successful, False if errors occurred
        """
        self.logger.info("Step 1/5: Warming cache with historical data...")

        try:
            for symbol in self.orchestrator.active_symbols:
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
                        for symbol in self.orchestrator.active_symbols:
                            await self.primary_provider.subscribe(symbol)
                        self.logger.info(f"   [OK] Subscribed to {len(self.orchestrator.active_symbols)} symbols")

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
            for symbol in self.orchestrator.active_symbols:
                if symbol not in self.orchestrator.bar_aggregators:
                    missing_symbols.append(symbol)

            if missing_symbols:
                self.logger.warning(f"   [!] Missing bar aggregators for: {missing_symbols}")
                self.logger.info("   [*] Recreating bar aggregators (self-healing)...")

                # Recreate missing aggregators
                for symbol in missing_symbols:
                    aggregator = BarAggregator(
                        bar_interval=self.orchestrator._bar_interval,
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
                self.logger.info(f"   [OK] All {len(self.orchestrator.active_symbols)} bar aggregators present")

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

    async def _verify_broker_health(self) -> bool:
        """Reconnect and verify the persistent IB execution client.

        Returns:
            True if broker health is verified or not required, False otherwise
        """
        broker_config = getattr(self.config, "broker", None)
        broker_type = getattr(broker_config, "broker_type", "mock") if broker_config else "mock"

        if broker_type != "interactive_brokers":
            self.logger.info("Step 4/6: Broker health check skipped (broker_type=%s)", broker_type)
            return True

        self.logger.info("Step 4/6: Connecting and verifying IB execution client...")

        try:
            # A separate diagnostic client can pass while the order client is
            # still disconnected after the gateway's nightly restart.
            broker = self.orchestrator.exchange.broker
            if not await broker.connect():
                raise ConnectionError("IB execution client could not connect")

            if not getattr(broker_config, "health_check_enabled", True):
                self.logger.info(
                    "IB execution client connected; additional health checks disabled"
                )
                self._critical_broker_alert_sent = False
                return True

            account = await broker.get_account_info()
            if broker_config.ib_account_id and account.account_id != broker_config.ib_account_id:
                raise RuntimeError(
                    f"IB account mismatch: expected={broker_config.ib_account_id} "
                    f"actual={account.account_id}"
                )

            quote_symbol = broker_config.health_check_symbol or self.orchestrator.active_symbols[0]
            quote = await broker.get_market_data(quote_symbol)
            positions = await broker.get_positions()

            self.logger.info(
                "   [OK] IB execution client healthy: account=%s net_liq=%s positions=%s quote=%s market_price=%s",
                account.account_id,
                account.net_liquidation,
                len(positions),
                quote.symbol,
                quote.market_price,
            )
            self._critical_broker_alert_sent = False
            return True

        except Exception as e:
            self.logger.error("   [!] Interactive Brokers health check failed: %s", e, exc_info=True)
            await self._send_critical_broker_alert(e)
            return False

    async def _take_session_equity_snapshot(self) -> bool:
        """Capture E0, the account equity every sizing cap uses for the session.

        Only runs when the ruleset has a ``portfolio:`` block. A snapshot
        already persisted for today (bot restarted mid-session) is reused, so
        the caps do not drift with intraday equity. After the open a snapshot
        is only taken from a provably untouched account (flat, no working
        orders, no fills today), whose NLV still equals session-start equity;
        it is flagged ``late`` for audit. Otherwise, and on any failure,
        entries stay blocked for the session and one EQUITY_SNAPSHOT_FAILED
        alert is sent; there is no fallback value. This step never raises.
        """
        risk_manager = getattr(self.orchestrator, "portfolio_risk_manager", None)
        if risk_manager is None:
            return True

        from vibe.common.risk.portfolio_sizing import net_liquidation
        from vibe.trading_bot.execution.portfolio_risk import (
            SNAPSHOT_FAILED,
            SessionEquitySnapshot,
        )

        now = get_market_now(self.market_scheduler)
        session_date = now.date()
        try:
            existing = risk_manager.begin_session(session_date)
            if existing is not None:
                self.logger.info(
                    "Step 4.6/6: Reusing session equity snapshot E0=$%.2f taken at %s",
                    existing.equity,
                    existing.taken_at.isoformat(),
                )
                return True

            if risk_manager.halted_reason == SNAPSHOT_FAILED:
                self.logger.error(
                    "Step 4.6/6: Session equity snapshot already failed for %s; "
                    "entries blocked: %s",
                    session_date,
                    risk_manager.halt_detail,
                )
                await self._alert_snapshot_failure(risk_manager, now)
                return False

            market_open = self.market_scheduler.get_open_time(now)
            late = market_open is not None and now >= market_open
            if late:
                # After the open, NLV equals session-start equity only if the
                # account has not been touched today.
                untouched, detail = await self._session_untouched(session_date)
                if not untouched:
                    raise RuntimeError(
                        "no session-start snapshot and the account traded or holds "
                        f"orders/positions this session ({detail}); a late snapshot "
                        "would size off intraday equity"
                    )

            self.logger.info(
                "Step 4.6/6: Taking session-start equity snapshot%s...",
                " (late: account flat with no fills today)" if late else "",
            )
            account = await self.orchestrator.exchange.get_account()
            equity = net_liquidation(account)
            if equity is None:
                raise ValueError(
                    f"NetLiquidation unavailable or non-positive: {account.broker_equity!r}"
                )
            snapshot = SessionEquitySnapshot(
                session_date=session_date,
                equity=equity,
                taken_at=now,
                account_id=account.account_id,
                late=late,
            )
            risk_manager.record_snapshot(snapshot)
            self.logger.info(
                "   [OK] Session equity E0=$%.2f (account=%s, late=%s)",
                snapshot.equity,
                snapshot.account_id,
                snapshot.late,
            )
        except Exception as e:
            self.logger.error(
                "   [!] Session equity snapshot failed; entries blocked for %s: %s",
                session_date,
                e,
                exc_info=True,
            )
            try:
                risk_manager.record_snapshot_failure(session_date, str(e))
            except Exception as record_error:
                self.logger.error(
                    "Could not record snapshot failure: %s", record_error, exc_info=True
                )
            await self._alert_snapshot_failure(risk_manager, now)
            return False

        persist = getattr(self.orchestrator, "_persist_dashboard_account_and_positions", None)
        if persist is not None:
            await persist(reason="session_start_equity", account=account)
        return True

    async def _session_untouched(self, session_date) -> tuple[bool, str]:
        """Whether the account is flat, has no working orders and no fills today.

        Only then is current NetLiquidation the session-start equity, so a
        snapshot taken after the open is still exact. Any doubt (a source
        that cannot be read) counts as touched.
        """
        exchange = self.orchestrator.exchange
        list_positions = getattr(exchange, "list_positions", None)
        if list_positions is None:
            return False, "positions cannot be listed"
        positions = [p for p in await list_positions() if float(p.quantity) != 0]
        if positions:
            return False, "open positions: " + ", ".join(sorted(p.symbol for p in positions))

        working = []
        list_open_orders = getattr(exchange, "list_open_orders", None)
        if list_open_orders is not None:
            working = [order.order_id for order in list_open_orders()]
        elif hasattr(exchange, "get_all_orders"):
            working = [
                order.order_id
                for order in exchange.get_all_orders().values()
                if order.status in (
                    OrderStatus.CREATED,
                    OrderStatus.PENDING,
                    OrderStatus.SUBMITTED,
                    OrderStatus.PARTIAL,
                )
            ]
        else:
            return False, "open orders cannot be listed"
        trade_executor = getattr(self.orchestrator, "trade_executor", None)
        pending_entries = getattr(trade_executor, "_pending_entries", {}) or {}
        working.extend(pending_entries)
        if working:
            return False, f"working orders: {', '.join(sorted(set(map(str, working))))}"

        reconcile = getattr(exchange, "reconcile_todays_executions", None)
        if reconcile is not None:
            # The local journal misses fills that happened while the bot was
            # down (e.g. a broker-side protective stop). Unreadable history
            # counts as touched.
            try:
                await reconcile()
            except Exception as exc:
                self.logger.error(
                    "Broker execution reconciliation failed: %s", exc, exc_info=True
                )
                return False, f"broker execution history unavailable: {exc}"

        list_executions = getattr(exchange, "list_durable_executions", None)
        if list_executions is not None:
            executions = list_executions()
        elif hasattr(exchange, "get_all_orders"):
            executions = [
                execution
                for order in exchange.get_all_orders().values()
                for execution in order.executions
            ]
        else:
            return False, "executions cannot be listed"
        tz = self.market_scheduler.timezone
        today = [
            execution
            for execution in executions
            if self._execution_market_date(execution.get("filled_at"), tz) in (session_date, None)
        ]
        if today:
            return False, f"{len(today)} execution(s) today"
        return True, "flat, no working orders, no fills today"

    @staticmethod
    def _execution_market_date(value, tz):
        """Market date of an execution timestamp; None when unparseable (counts as today)."""
        if value is None:
            return None
        try:
            filled_at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if filled_at.tzinfo is None:
            filled_at = filled_at.replace(tzinfo=timezone.utc)
        return filled_at.astimezone(tz).date()

    async def _alert_snapshot_failure(self, risk_manager, now) -> None:
        """Send EQUITY_SNAPSHOT_FAILED once per session."""
        if not risk_manager.take_unsent_failure_alert():
            return
        await self._send_risk_alert(
            RiskAlertPayload(
                event_type="EQUITY_SNAPSHOT_FAILED",
                timestamp=now,
                session_date=now.date().isoformat(),
                message=(
                    "No session-start equity snapshot is available, so position "
                    "sizing has no basis. New entries are blocked for this session; "
                    "open positions are still managed."
                ),
                error=risk_manager.halt_detail or risk_manager.snapshot_error,
                version=BUILD_VERSION,
            )
        )

    async def _send_risk_alert(self, payload: RiskAlertPayload) -> None:
        webhook_url = self.config.notifications.discord_webhook_url
        if not webhook_url:
            self.logger.warning("Risk alert not sent to Discord: webhook not configured")
            return
        try:
            async with discord_notification_context(webhook_url) as notifier:
                await notifier.send_risk_alert(payload)
        except Exception as e:
            self.logger.error("Failed to send risk alert: %s", e, exc_info=True)

    async def _send_critical_broker_alert(self, error: Exception) -> None:
        """Notify operators when all IB warm-up connection attempts fail."""
        if self._critical_broker_alert_sent:
            return

        webhook_url = self.config.notifications.discord_webhook_url
        if not webhook_url:
            self.logger.warning(
                "Critical IB warm-up failure could not be sent to Discord: webhook not configured"
            )
            return

        payload = SystemAlertPayload(
            event_type="SYSTEM_ERROR",
            timestamp=get_market_now(self.market_scheduler),
            severity="critical",
            title="Critical: IB Warm-up Failed",
            message=(
                "The trading bot could not complete Interactive Brokers connection "
                "and synchronization after all configured attempts. Trading is disabled."
            ),
            component="interactive_brokers",
            action_required=(
                "Check IB Gateway login, connectivity and market-data access. "
                "The bot will retry warm-up."
            ),
            version=BUILD_VERSION,
            details={
                "attempts": self.config.broker.ib_connect_max_retries,
                "error": str(error),
            },
        )

        try:
            async with discord_notification_context(webhook_url) as notifier:
                sent = await notifier.send_system_alert(payload)
            if not sent:
                self.logger.error("Discord rejected the critical IB warm-up alert")
                return
            self._critical_broker_alert_sent = True
        except Exception as notification_error:
            self.logger.error(
                "Failed to send critical IB warm-up alert: %s",
                notification_error,
                exc_info=True,
            )

    async def _apply_carryover_position_policy(self, send_notification: bool) -> bool:
        """Apply configured policy for broker positions carried into a new trading day.

        ORB is an intraday strategy, so the production default is to flatten carryovers
        during pre-market warmup. Intraday restarts skip automatic flattening to avoid
        accidentally closing a same-day recovery position.
        """
        policy = getattr(self.config.strategy, "carryover_position_policy", "block_new_entries")
        policy = (policy or "block_new_entries").strip().lower()

        if policy in {"block_new_entries", "manual_only"}:
            self.logger.info("Step 4.5/6: Carryover position policy=%s; no warmup flattening", policy)
            return True

        if policy != "flatten_at_market_open":
            self.logger.warning(
                "Step 4.5/6: Unknown carryover_position_policy=%s; blocking entries without flattening",
                policy,
            )
            return True

        if not send_notification:
            self.logger.info(
                "Step 4.5/6: Skipping carryover flattening during intraday restart"
            )
            return True

        exchange = getattr(self.orchestrator, "exchange", None)
        if exchange is None:
            self.logger.info("Step 4.5/6: No exchange available for carryover check")
            return True

        self.logger.info("Step 4.5/6: Flattening broker carryover positions for intraday ORB policy...")
        success = True

        for symbol in self.orchestrator.active_symbols:
            try:
                position = await exchange.get_position(symbol)
                if position is None or position.quantity == 0:
                    self.logger.info("  [OK] %s: no broker carryover position", symbol)
                    continue

                flattened = await self._flatten_carryover_position(symbol, position)
                success &= flattened

            except Exception as e:
                success = False
                self.logger.error("  [!] Failed to apply carryover policy for %s: %s", symbol, e, exc_info=True)

        return success

    async def _flatten_carryover_position(self, symbol: str, position: Position) -> bool:
        """Submit a market order to close one broker carryover position."""
        quantity = abs(int(position.quantity))

        self.logger.warning(
            "  [CARRYOVER FLATTEN] %s: closing %s %s share(s) at market before ORB entries",
            symbol,
            position.side,
            quantity,
        )

        result = await self.orchestrator.trade_executor._close_position(
            symbol,
            cancel_after_seconds=self.CARRYOVER_FLATTEN_TIMEOUT_SECONDS,
        )
        if not result.success:
            self.logger.error(
                "  [!] %s carryover flatten failed: %s",
                symbol,
                result.reason,
            )
            return False

        self.logger.info(
            "  [OK] %s carryover flattened: order_id=%s filled=%s avg_price=%s",
            symbol,
            result.order_id,
            result.position_size,
            result.avg_price,
        )

        strategy = getattr(self.orchestrator, "strategy", None)
        if strategy is not None:
            strategy.close_position(symbol)
            if hasattr(strategy, "clear_traded_today"):
                strategy.clear_traded_today(symbol)

        return True

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
        if (
            not self.config.notifications.discord_webhook_url
            or not getattr(self.config.notifications, "notify_routine", True)
        ):
            self.logger.debug("Routine Discord notifications disabled or webhook not configured, skipping warm-up notification")
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
