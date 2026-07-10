"""Entry point and CLI for trading bot."""

import asyncio
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

from vibe.trading_bot.config.logging_config import setup_logging
from vibe.trading_bot.config.settings import get_settings
from vibe.trading_bot.brokers.interactive_brokers import IBConnectionFailed, IBOperatorActionRequired
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.notifications.helper import discord_notification_context
from vibe.trading_bot.notifications.payloads import SystemAlertPayload
from vibe.trading_bot.version import BUILD_VERSION, BUILD_INFO


logger = logging.getLogger(__name__)


class TradingBotCLI:
    """CLI interface for trading bot."""

    def __init__(self):
        """Initialize CLI."""
        self.settings = get_settings()
        self.orchestrator: Optional[TradingOrchestrator] = None
        self._shutdown_event = asyncio.Event()

    async def run(self, dry_run: bool = False) -> int:
        """Run the trading bot.

        Args:
            dry_run: If True, don't execute real orders

        Returns:
            Exit code
        """
        setup_logging(
            level=self.settings.log_level,
            environment=self.settings.environment
        )

        # Print version information (helps verify correct build is running)
        logger.info("=" * 80)
        logger.info(f"Trading Bot {BUILD_VERSION}")
        logger.info(f"Build Time: {BUILD_INFO['build_time']}")
        logger.info("=" * 80)

        logger.info(f"Starting trading bot (environment={self.settings.environment})")
        logger.info(f"Symbols: {self.settings.trading.symbols}")
        logger.info(f"Initial capital: ${self.settings.trading.initial_capital:,.2f}")

        if dry_run:
            logger.warning("DRY RUN MODE: No real orders will be executed")

        try:
            # Create orchestrator
            self.orchestrator = TradingOrchestrator(config=self.settings)

            # Setup signal handlers for graceful shutdown
            self._setup_signal_handlers()

            # Run trading loop
            await self.orchestrator.run()

            return 0

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            return 1
        except IBOperatorActionRequired as e:
            logger.error("Operator action required: %s", e)
            logger.error("Trading bot stopped fail-closed; restart the service after resolving the Gateway prompt.")
            await self._send_error_alert(
                title="Trading Bot Warning - Operator Action Required",
                message=str(e),
                severity="warning",
                component="Interactive Brokers Gateway",
                action_required="Accept the IB paper trading disclaimer in Gateway, then restart trading-bot-phase2.service.",
            )
            return 0
        except IBConnectionFailed as e:
            logger.error("IB connection failed after bounded retries: %s", e)
            logger.error("Trading bot stopped fail-closed; restart the service after resolving IB connectivity.")
            await self._send_error_alert(
                title="Trading Bot Error - IB Connection Failed",
                message=str(e),
                severity="error",
                component="Interactive Brokers Gateway",
                action_required="Check IB Gateway/API connectivity, then restart trading-bot-phase2.service.",
            )
            return 0
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            await self._send_error_alert(
                title="Trading Bot Fatal Error",
                message=str(e) or e.__class__.__name__,
                severity="critical",
                component="Trading Bot",
                action_required="Check service logs and restart only after the root cause is understood.",
            )
            return 1

    async def _send_error_alert(
        self,
        title: str,
        message: str,
        severity: str,
        component: str,
        action_required: str,
    ) -> None:
        """Send a Discord warning/error alert if notification config is available."""
        notifications = self.settings.notifications
        if not notifications.notify_on_error:
            logger.info("Error notifications disabled by config")
            return
        if not notifications.discord_webhook_url:
            logger.warning("Discord webhook URL not configured; cannot send error notification")
            return

        try:
            payload = SystemAlertPayload(
                event_type="SYSTEM_WARNING" if severity == "warning" else "SYSTEM_ERROR",
                timestamp=datetime.now(),
                severity=severity,
                title=title,
                message=message,
                component=component,
                action_required=action_required,
                version=BUILD_VERSION,
                details={"environment": self.settings.environment},
            )
            async with discord_notification_context(notifications.discord_webhook_url) as notifier:
                await notifier.send_system_alert(payload)
        except Exception as notify_error:
            logger.error("Failed to send Discord error notification: %s", notify_error, exc_info=True)

    async def backtest(
        self,
        start_date: str,
        end_date: str,
    ) -> int:
        """Run backtest for date range.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            Exit code
        """
        setup_logging(
            level=self.settings.log_level,
            environment="backtest"
        )

        logger.info(f"Starting backtest: {start_date} to {end_date}")

        try:
            self.orchestrator = TradingOrchestrator(config=self.settings)
            result = await self.orchestrator.run_backtest(start_date, end_date)

            logger.info(f"Backtest completed: {result['status']}")
            return 0 if result['status'] == 'completed' else 1

        except Exception as e:
            logger.error(f"Backtest failed: {e}", exc_info=True)
            return 1

    async def validate_config(self) -> int:
        """Validate configuration.

        Returns:
            Exit code (0 if valid)
        """
        setup_logging(level="INFO")

        logger.info("Validating configuration...")

        try:
            # Check all required settings
            if not self.settings.trading.symbols:
                logger.error("No trading symbols configured")
                return 1

            if self.settings.trading.initial_capital <= 0:
                logger.error("Initial capital must be positive")
                return 1

            if self.settings.trading.stop_loss_pct < 0 or self.settings.trading.stop_loss_pct > 1:
                logger.error("Stop loss percentage must be between 0 and 1")
                return 1

            logger.info("Configuration is valid")
            logger.info(f"  Symbols: {self.settings.trading.symbols}")
            logger.info(f"  Initial capital: ${self.settings.trading.initial_capital:,.2f}")
            logger.info(f"  Stop loss: {self.settings.trading.stop_loss_pct * 100:.1f}%")
            logger.info(f"  Take profit: {self.settings.trading.take_profit_pct * 100:.1f}%")

            return 0

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}", exc_info=True)
            return 1

    async def show_status(self) -> int:
        """Show trading bot status.

        Returns:
            Exit code
        """
        setup_logging(level="INFO")

        try:
            # Create orchestrator to get status
            orchestrator = TradingOrchestrator(config=self.settings)
            health = orchestrator.get_health()

            logger.info("Trading Bot Status")
            logger.info(f"  Overall: {health['overall']}")
            logger.info(f"  Uptime: {health['uptime_seconds']:.0f}s")
            logger.info(f"  Total errors: {health['total_errors']}")

            for component, status in health['components'].items():
                logger.info(f"  {component}: {status.get('status', 'unknown')}")

            return 0

        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return 1

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(self.orchestrator.shutdown())

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)


@click.group()
def cli():
    """Trading bot CLI."""
    pass


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Don't execute real orders",
)
def run(dry_run: bool):
    """Start trading bot."""
    cli_app = TradingBotCLI()
    exit_code = asyncio.run(cli_app.run(dry_run=dry_run))
    sys.exit(exit_code)


@cli.command()
@click.option(
    "--start",
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end",
    required=True,
    help="End date (YYYY-MM-DD)",
)
def backtest(start: str, end: str):
    """Run backtest for date range."""
    cli_app = TradingBotCLI()
    exit_code = asyncio.run(cli_app.backtest(start, end))
    sys.exit(exit_code)


@cli.command()
def validate_config():
    """Validate configuration."""
    cli_app = TradingBotCLI()
    exit_code = asyncio.run(cli_app.validate_config())
    sys.exit(exit_code)


@cli.command()
def show_status():
    """Show trading bot status."""
    cli_app = TradingBotCLI()
    exit_code = asyncio.run(cli_app.show_status())
    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
