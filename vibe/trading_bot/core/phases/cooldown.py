"""Post-market cooldown phase manager.

This module handles the post-market cooldown phase (5 minutes after market close)
which processes final data, disconnects providers, and prepares for the next trading day.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

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

        # Parity report before the publisher flush, so its metrics go out today.
        try:
            await self._run_parity_report()
        except Exception as exc:
            self.logger.error("Daily parity report failed: %s", exc, exc_info=True)

        try:
            await self._flush_dashboard_publisher()
            retention_maintenance = getattr(
                self.orchestrator, "run_dashboard_retention_maintenance", None
            )
            if retention_maintenance is not None:
                try:
                    retention_maintenance()
                except Exception as exc:
                    self.logger.exception(
                        "Dashboard retention maintenance failed during cooldown: %s",
                        exc,
                    )
        finally:
            # Provider cleanup and rotation are mandatory even when optional
            # publication/retention maintenance fails.
            try:
                await self._disconnect_provider()
            except Exception as exc:
                self.logger.exception("Provider disconnect failed during cooldown: %s", exc)
            finally:
                try:
                    await self._rotate_tick_logs()
                except Exception as exc:
                    self.logger.exception("Tick log rotation failed during cooldown: %s", exc)

    async def _run_parity_report(self) -> Optional[Any]:
        """Replay today's session through the backtester and diff it with live.

        Runs for resting-stop rulesets only. For every active symbol the
        session's 1-minute bars are fetched from the broker and replayed with
        the live ruleset and session-start equity; live trades are rebuilt
        from the durable execution journal. The report is persisted (local
        JSON + metrics published to the dashboard) and sent to Discord.
        """
        ruleset = getattr(self.orchestrator, "ruleset", None)
        policy = getattr(getattr(ruleset, "strategy", None), "entry_order", None)
        if policy is None or policy.type != "resting_stop":
            return None
        exchange = self.orchestrator.exchange
        list_executions = getattr(exchange, "list_durable_executions", None)
        fetch_bars = getattr(exchange, "fetch_session_minute_bars", None)
        if list_executions is None or fetch_bars is None:
            self.logger.info("Parity report skipped: execution engine has no broker history")
            return None

        from vibe.common.risk.portfolio_sizing import net_liquidation
        from vibe.trading_bot.parity.report import (
            backtest_session,
            build_report,
            live_trades_from_executions,
        )

        now = get_market_now(self.market_scheduler)
        session_date = now.date()
        errors: dict[str, str] = {}
        risk_manager = getattr(self.orchestrator, "portfolio_risk_manager", None)
        session_equity = risk_manager.session_equity if risk_manager is not None else None
        if session_equity is None:
            session_equity = net_liquidation(await exchange.get_account())
            errors["session_equity"] = "no session-start snapshot; replayed with current NLV"
        if session_equity is None:
            self.logger.error("Parity report skipped: no equity to replay with")
            return None

        live = live_trades_from_executions(
            list_executions(), session_date, self.market_scheduler.timezone
        )
        backtests = {}
        for symbol in self.orchestrator.active_symbols:
            try:
                bars = await fetch_bars(symbol, session_date)
                self._save_session_bars(symbol, session_date, bars)
                backtests[symbol] = await asyncio.to_thread(
                    backtest_session, ruleset, symbol, bars, session_equity, session_date
                )
            except Exception as exc:
                self.logger.error("Parity replay failed for %s: %s", symbol, exc, exc_info=True)
                errors[symbol] = str(exc)

        report = build_report(
            session_date,
            self.orchestrator.active_symbols,
            live,
            backtests,
            errors={k: v for k, v in errors.items() if k != "session_equity"},
            ruleset=ruleset,
            session_equity=session_equity,
        )
        report.errors.update(errors)
        self.logger.info("[PARITY] %s", report.to_dict())

        record = getattr(self.orchestrator, "record_parity_report", None)
        if record is not None:
            record(report)
        await self._send_parity_report(report, len(live))
        return report

    def _save_session_bars(self, symbol: str, session_date: Any, bars: Any) -> None:
        """Keep the IBKR bars the report replayed, for the offline script.

        Written as ``<data>/parity_reports/bars/{SYMBOL}_{date}.csv`` so a
        later offline recomputation replays exactly the same bars.
        """
        database_path = getattr(getattr(self.orchestrator, "config", None), "database_path", None)
        if not database_path or bars is None or getattr(bars, "empty", True):
            return
        try:
            directory = Path(database_path).parent / "parity_reports" / "bars"
            directory.mkdir(parents=True, exist_ok=True)
            frame = bars.copy()
            frame.index.name = "timestamp"
            frame.to_csv(directory / f"{symbol}_{session_date.isoformat()}.csv")
        except Exception as exc:
            self.logger.error("Failed to save parity bars for %s: %s", symbol, exc, exc_info=True)

    async def _send_parity_report(self, report: Any, entry_fills: int) -> None:
        webhook_url = getattr(self.config.notifications, "discord_webhook_url", None)
        if not webhook_url:
            return
        from vibe.trading_bot.notifications.payloads import ParityReportPayload

        lines = []
        for item in report.mismatched:
            d = item.details
            lines.append(
                f"{item.symbol}: {', '.join(item.mismatches)} "
                f"(live {d.get('live_side', '-')} {d.get('live_qty', '-')} @ "
                f"{d.get('live_entry_price', '-')} [{d.get('live_capped_by') or 'risk'}], "
                f"backtest {d.get('backtest_side', '-')} "
                f"{d.get('backtest_qty', '-')} @ {d.get('backtest_entry_price', '-')} "
                f"[{','.join(d.get('backtest_sizing_caps') or {}) or 'risk'}])"
            )
        payload = ParityReportPayload(
            event_type="PARITY_REPORT",
            timestamp=get_market_now(self.market_scheduler),
            session_date=report.session_date.isoformat(),
            matched=report.matched,
            mismatched=len(report.mismatched),
            mismatch_counts=report.mismatch_counts(),
            mismatch_lines=lines,
            entry_fills=entry_fills,
            mean_slippage_ticks=report.mean_slippage_ticks,
            median_slippage_ticks=report.median_slippage_ticks,
            modelled_slippage_ticks=report.modelled_slippage_ticks,
            gap_fills=report.gap_fills,
            errors=report.errors,
            version=BUILD_VERSION,
            normal_fills=len(report.entry_slippage_ticks),
            late_placements=report.late_placements,
            mean_gap_slippage_ticks=report.mean_gap_slippage_ticks,
        )
        try:
            async with discord_notification_context(webhook_url) as notifier:
                await notifier.send_parity_report(payload)
        except Exception as exc:
            self.logger.error("Failed to send parity report: %s", exc, exc_info=True)

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
                    "Broker exposure may persist overnight. Reconcile positions and open orders; "
                    "restarting the bot does not close positions or provide protection."
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
            dashboard_store = getattr(self.orchestrator, "dashboard_store", None)
            source_stores = [
                store
                for store in (
                    dashboard_store,
                    getattr(self.orchestrator, "dashboard_price_store", None),
                    getattr(self.orchestrator, "trade_store", None),
                )
                if store is not None and hasattr(store, "iter_publish_events")
            ]
            if source_stores:
                publisher.reconcile_sources(
                    source_stores,
                    get_market_now(self.market_scheduler).date().isoformat(),
                )
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
            await self._send_dashboard_publish_alert(summary, result)
        except Exception as e:
            self.logger.warning("Dashboard cooldown publish flush failed: %s", e)

    async def _send_dashboard_publish_alert(self, summary: dict, result: Any = None) -> None:
        """Send a Discord alert if cooldown leaves unresolved publish rows."""
        unresolved_statuses = ("failed", "publishing", "dead_letter")
        unresolved = {
            status: int(summary.get(status, 0) or 0)
            for status in unresolved_statuses
            if int(summary.get(status, 0) or 0) > 0
        }
        pending = int(summary.get("pending", 0) or 0)
        made_progress = int(getattr(result, "published", 0) or 0) > 0
        if pending > 0 and not made_progress:
            unresolved["pending"] = pending
        if not unresolved:
            if pending > 0:
                self.logger.info(
                    "Dashboard publication backlog remains after bounded cooldown flush: pending=%s",
                    pending,
                )
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
