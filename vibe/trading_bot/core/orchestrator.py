"""Main trading orchestrator coordinating all components."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from vibe.trading_bot.config.settings import AppSettings, get_settings
from vibe.trading_bot.core.market_schedulers import create_scheduler, BaseMarketScheduler
from vibe.trading_bot.core.health_monitor import HealthMonitor
from vibe.trading_bot.api.health import start_health_server_task, set_health_state
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider
from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
from vibe.trading_bot.data.providers.factory import DataProviderFactory
from vibe.trading_bot.data.providers.types import RealtimeDataProvider, WebSocketDataProvider, RESTDataProvider
from vibe.trading_bot.storage.trade_store import TradeStore
import pandas as pd
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager, OrderRetryPolicy
from vibe.trading_bot.execution.trade_executor import TradeExecutor
from vibe.common.risk import PositionSizer
from vibe.common.strategies import ORBStrategy
from vibe.common.strategies.orb import ORBStrategyConfig
from vibe.common.indicators.engine import IncrementalIndicatorEngine
from vibe.trading_bot.notifications.discord import DiscordNotifier
from vibe.trading_bot.notifications.payloads import SystemStatusPayload
from vibe.trading_bot.version import BUILD_VERSION


logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main orchestrator coordinating all trading bot components.

    Manages initialization, main trading loop, graceful shutdown, and component
    integration for complete trading system lifecycle.
    """

    def __init__(self, config: Optional[AppSettings] = None):
        """Initialize trading orchestrator.

        Args:
            config: Application settings (uses get_settings() if None)
        """
        self.config = config or get_settings()
        self.logger = logging.getLogger(__name__)

        # Component initialization order matters
        self.market_scheduler: BaseMarketScheduler = create_scheduler(
            market_type=self.config.trading.market_type,
            exchange=self.config.trading.exchange,
        )
        self.health_monitor = HealthMonitor()
        self.trade_store = TradeStore(db_path=self.config.database_path)

        # Retry/backoff state
        self._consecutive_failures = 0
        self._max_consecutive_failures = 10
        self._base_cycle_interval = 60  # Base interval: 60 seconds when monitoring positions
        self._idle_cycle_interval = 300  # Idle interval: 5 minutes when no positions
        self._max_backoff_seconds = 900  # Max backoff: 15 minutes
        self.data_manager: Optional[DataManager] = None
        self.exchange = MockExchange()
        self.trade_executor: Optional[TradeExecutor] = None
        self.strategy: Optional[ORBStrategy] = None
        self.indicator_engine: Optional[IncrementalIndicatorEngine] = None

        # Trading loop control
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Main loop task
        self._main_task: Optional[asyncio.Task] = None

        # Health API server task
        self._health_server_task: Optional[asyncio.Task] = None

        # Strategy logging state tracking (to avoid duplicate logs)
        self._orb_logged_today: Dict[str, str] = {}  # symbol -> date_str
        self._last_approach_logged: Dict[str, float] = {}  # symbol -> timestamp

        # Daily statistics tracking for end-of-day summary
        self._daily_stats: Dict[str, Any] = self._initialize_daily_stats()
        self._last_summary_date: Optional[str] = None
        self._orb_notification_sent_date: Optional[str] = None  # Track ORB Discord notification

        # Market closed state tracking (to avoid log spam)
        self._market_closed_logged: bool = False
        self._cooldown_start_time: Optional[datetime] = None
        self._cooldown_duration_seconds: int = 300  # 5 minutes cooldown after market close

        # Real-time data providers (configurable)
        self.primary_provider: Optional[RealtimeDataProvider] = None
        self.secondary_provider: Optional[RealtimeDataProvider] = None
        self.active_provider: Optional[RealtimeDataProvider] = None

        # Finnhub websocket for real-time intraday data (backward compatibility)
        self.finnhub_ws: Optional[FinnhubWebSocketClient] = None
        self.bar_aggregators: Dict[str, BarAggregator] = {}  # One aggregator per symbol

        # Real-time bars storage (symbol -> DataFrame with today's bars)
        self._realtime_bars: Dict[str, pd.DataFrame] = {}

        # Polling task for REST providers
        self._polling_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Initialize all components in correct order.

        Returns:
            True if all components initialized successfully

        Raises:
            Exception if initialization fails critically
        """
        try:
            self.logger.info("Starting component initialization...")

            # 1. Initialize data manager
            try:
                # Create data provider
                data_provider = YahooDataProvider()

                # Create cache directory
                cache_dir = Path(self.config.data.cache_dir) if hasattr(self.config.data, 'cache_dir') else Path("./data/cache")
                cache_dir.mkdir(parents=True, exist_ok=True)

                # Create aggregator for real-time data
                aggregator = BarAggregator(bar_interval="5m")

                # Initialize data manager
                self.data_manager = DataManager(
                    provider=data_provider,
                    cache_dir=cache_dir,
                    aggregator=aggregator,
                    cache_ttl_seconds=self.config.data.data_cache_ttl_seconds,
                )
                self.logger.info("Data manager initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize data manager: {e}")
                raise

            # 2. Initialize exchange
            try:
                await self.exchange.initialize()
                self.logger.info("Exchange initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize exchange: {e}")
                raise

            # 3. Initialize trading components
            try:
                # Create position sizer with percentage-based risk
                # Risk amount is calculated dynamically based on current account value
                position_sizer = PositionSizer(
                    risk_pct=0.01,  # 1% risk per trade (scales with account growth)
                )

                # Create order manager with retry policy
                retry_policy = OrderRetryPolicy(
                    max_retries=3,
                    base_delay_seconds=1.0,
                    cancel_after_seconds=60,
                )
                order_manager = OrderManager(
                    exchange=self.exchange,
                    retry_policy=retry_policy,
                )

                # Create trade executor
                self.trade_executor = TradeExecutor(
                    exchange=self.exchange,
                    order_manager=order_manager,
                    position_sizer=position_sizer,
                )
                self.logger.info("Trade executor initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize trade executor: {e}")
                raise

            # 4. Initialize indicator engine
            try:
                indicator_state_dir = Path(self.config.database_path).parent / "indicator_state"
                self.indicator_engine = IncrementalIndicatorEngine(state_dir=indicator_state_dir)
                self.logger.info("Indicator engine initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize indicator engine: {e}")
                raise

            # 5. Initialize strategy
            try:
                # Create strategy config from settings
                strategy_config = ORBStrategyConfig(
                    name="ORB",
                    orb_start_time=self.config.strategy.orb_start_time,
                    orb_duration_minutes=self.config.strategy.orb_duration_minutes,
                    orb_body_pct_filter=self.config.strategy.orb_body_pct_filter,
                    entry_cutoff_time=self.config.strategy.entry_cutoff_time,
                    take_profit_multiplier=self.config.strategy.take_profit_multiplier,
                    stop_loss_at_level=self.config.strategy.stop_loss_at_level,
                    use_volume_filter=self.config.strategy.use_volume_filter,
                    volume_threshold=self.config.strategy.volume_threshold,
                    market_close_time=self.config.strategy.market_close_time,
                )
                self.strategy = ORBStrategy(config=strategy_config)
                self.logger.info(
                    f"Strategy initialized: ORB window={self.config.strategy.orb_start_time} "
                    f"duration={self.config.strategy.orb_duration_minutes}m"
                )
            except Exception as e:
                self.logger.error(f"Failed to initialize strategy: {e}")
                raise

            # 6. Initialize real-time data providers (primary + secondary)
            try:
                # Get API keys from config
                finnhub_key = getattr(self.config.data, 'finnhub_api_key', None)
                polygon_key = getattr(self.config.data, 'polygon_api_key', None)

                # Create primary provider (MANDATORY)
                primary_type = getattr(self.config.data, 'primary_provider', 'polygon')
                self.logger.info(f"Initializing primary data provider: {primary_type}")

                self.primary_provider = DataProviderFactory.create_realtime_provider(
                    provider_type=primary_type,
                    finnhub_api_key=finnhub_key,
                    polygon_api_key=polygon_key
                )

                if not self.primary_provider:
                    raise ValueError(f"Failed to create primary provider: {primary_type}")

                self.active_provider = self.primary_provider
                self.logger.info(
                    f"[OK] Primary provider: {self.primary_provider.provider_name} "
                    f"(type={self.primary_provider.provider_type.value}, "
                    f"real_time={self.primary_provider.is_real_time})"
                )

                # Create secondary provider (OPTIONAL fallback)
                secondary_type = getattr(self.config.data, 'secondary_provider', None)
                if secondary_type:
                    self.logger.info(f"Initializing secondary data provider: {secondary_type}")
                    try:
                        self.secondary_provider = DataProviderFactory.create_realtime_provider(
                            provider_type=secondary_type,
                            finnhub_api_key=finnhub_key,
                            polygon_api_key=polygon_key
                        )
                        if self.secondary_provider:
                            self.logger.info(
                                f"[OK] Secondary provider: {self.secondary_provider.provider_name} (fallback)"
                            )
                    except Exception as e:
                        self.logger.warning(f"Failed to create secondary provider: {e}")
                        self.secondary_provider = None

                # Create bar aggregators for all symbols
                for symbol in self.config.trading.symbols:
                    aggregator = BarAggregator(
                        bar_interval="5m",
                        timezone=str(self.market_scheduler.timezone)
                    )
                    # Set up bar completion callback with symbol binding
                    aggregator.on_bar_complete(
                        lambda bar_dict, sym=symbol: self._handle_completed_bar(sym, bar_dict)
                    )
                    self.bar_aggregators[symbol] = aggregator

                # Handle WebSocket provider (callback-based)
                if isinstance(self.primary_provider, WebSocketDataProvider):
                    self.logger.info("Primary provider is WebSocket - setting up callbacks")
                    self.finnhub_ws = self.primary_provider  # For backward compatibility

                    # Set up trade callback to feed aggregators
                    self.primary_provider.on_trade(self._handle_realtime_trade)
                    self.primary_provider.on_error(self._handle_provider_error)

                    self.logger.info("WebSocket callbacks configured (will connect at market open)")

                # Handle REST provider (polling-based)
                elif isinstance(self.primary_provider, RESTDataProvider):
                    self.logger.info("Primary provider is REST - will poll at intervals")
                    poll_with = getattr(self.config.data, 'poll_interval_with_position', 60)
                    poll_without = getattr(self.config.data, 'poll_interval_no_position', 300)
                    self.logger.info(
                        f"Poll interval: {poll_with}s with positions, {poll_without}s without"
                    )

            except Exception as e:
                self.logger.error(f"Failed to initialize data providers: {e}")
                self.logger.warning("Falling back to Yahoo Finance only (15-min delay)")
                self.primary_provider = None
                self.secondary_provider = None
                self.active_provider = None
                self.finnhub_ws = None
                self.bar_aggregators = {}

            # 7. Register health checks
            self._register_health_checks()

            # 8. Provider connection now handled in warm-up phase (Step 2)
            # Removed old duplicate connection code that was causing rate limiting

            # Log data source configuration
            market_is_open = self.market_scheduler.is_market_open()
            self.logger.info("=" * 60)
            self.logger.info("DATA SOURCE CONFIGURATION")
            self.logger.info("=" * 60)
            if self.finnhub_ws:
                if market_is_open:
                    self.logger.info("Market Status: OPEN")
                    self.logger.info("Primary Source: Finnhub WebSocket (real-time)")
                    self.logger.info("Fallback Source: Yahoo Finance (15-min delayed)")
                    self.logger.info("Expected Gap: ~15 minutes between yfinance and Finnhub on restart")
                else:
                    self.logger.info("Market Status: CLOSED")
                    self.logger.info("Data Source: Yahoo Finance only (Finnhub will connect at market open)")
            else:
                self.logger.info("Finnhub: Not configured")
                self.logger.info("Data Source: Yahoo Finance only (15-min delayed)")
            self.logger.info("=" * 60)

            self.logger.info("All components initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Component initialization failed: {e}", exc_info=True)
            raise

    def _register_health_checks(self) -> None:
        """Register health check callbacks for all components."""
        def check_data():
            return {"status": "healthy" if self.data_manager else "unhealthy"}

        def check_exchange():
            return {"status": "healthy" if self.exchange else "unhealthy"}

        def check_strategy():
            return {"status": "healthy" if self.strategy else "unhealthy"}

        self.health_monitor.register_component("data", check_data)
        self.health_monitor.register_component("exchange", check_exchange)
        self.health_monitor.register_component("strategy", check_strategy)

    def _initialize_daily_stats(self) -> Dict[str, Any]:
        """Initialize daily statistics dictionary."""
        from datetime import datetime
        return {
            "date": datetime.now().date().isoformat(),
            "orb_levels": {},  # symbol -> {high, low, range}
            "breakouts_detected": 0,
            "breakouts_rejected": {},  # reason -> count
            "signals_generated": 0,
            "trades_executed": 0,
            "signals_by_symbol": {},  # symbol -> count
        }

    def _update_daily_stats(self, symbol: str, signal_value: int, metadata: Dict[str, Any]) -> None:
        """Update daily statistics based on strategy evaluation."""
        from datetime import datetime

        # Reset stats if new day
        current_date = datetime.now().date().isoformat()
        if self._daily_stats["date"] != current_date:
            self._daily_stats = self._initialize_daily_stats()

        # Record ORB levels (include body_pct from current bar)
        if "orb_high" in metadata and symbol not in self._daily_stats["orb_levels"]:
            # Calculate body percentage of current bar if available
            body_pct = 0.0
            if "current_bar" in metadata:
                bar = metadata["current_bar"]
                if "open" in bar and "close" in bar and "high" in bar and "low" in bar:
                    total_range = bar["high"] - bar["low"]
                    if total_range > 0:
                        body_pct = abs(bar["close"] - bar["open"]) / total_range * 100

            self._daily_stats["orb_levels"][symbol] = {
                "high": metadata["orb_high"],
                "low": metadata["orb_low"],
                "range": metadata["orb_range"],
                "body_pct": body_pct,
            }

        # Count breakouts detected
        price_position = metadata.get("price_position", "")
        if price_position in ["above_high", "below_low"] and signal_value == 0:
            self._daily_stats["breakouts_detected"] += 1

            # Count rejection reasons
            reason = metadata.get("reason", "unknown")
            if reason in self._daily_stats["breakouts_rejected"]:
                self._daily_stats["breakouts_rejected"][reason] += 1
            else:
                self._daily_stats["breakouts_rejected"][reason] = 1

        # Count signals generated
        if signal_value != 0:
            self._daily_stats["signals_generated"] += 1

            # Count by symbol
            if symbol in self._daily_stats["signals_by_symbol"]:
                self._daily_stats["signals_by_symbol"][symbol] += 1
            else:
                self._daily_stats["signals_by_symbol"][symbol] = 1

    async def _check_and_send_orb_notification(self) -> None:
        """Check if ORB levels are ready and send Discord notification once per day.

        Sends notification when:
        1. ORB levels collected for all tracked symbols
        2. Notification not sent yet today
        3. Discord notifications enabled
        """
        from datetime import datetime

        # Check if notifications enabled
        if not self.config.notifications.discord_webhook_url:
            return

        # Use market timezone for date comparison
        now = datetime.now(self.market_scheduler.timezone)
        current_date = now.date().isoformat()

        # Only send once per day
        if self._orb_notification_sent_date == current_date:
            return

        # Check if we have ORB levels for all symbols
        orb_levels = self._daily_stats.get("orb_levels", {})
        expected_symbols = set(self.config.trading.symbols)
        collected_symbols = set(orb_levels.keys())

        if not collected_symbols or not collected_symbols.issuperset(expected_symbols):
            # Not all symbols have ORB levels yet
            return

        # All ORB levels collected - send notification
        try:
            from vibe.trading_bot.notifications.payloads import ORBLevelsPayload
            from vibe.trading_bot.notifications.discord import DiscordNotifier
            from vibe.trading_bot.version import BUILD_VERSION

            self.logger.info(
                f"[ORB NOTIFICATION] Sending Discord notification for {len(orb_levels)} symbols..."
            )

            payload = ORBLevelsPayload(
                event_type="ORB_ESTABLISHED",
                timestamp=now,
                symbols=orb_levels,
                version=BUILD_VERSION,
            )

            # Create notifier temporarily (like _send_daily_summary does)
            notifier = DiscordNotifier(webhook_url=self.config.notifications.discord_webhook_url)
            await notifier.start()

            await notifier.send_orb_notification(payload)

            await notifier.stop()

            self._orb_notification_sent_date = current_date
            self.logger.info("[ORB NOTIFICATION] Discord notification sent successfully")

        except Exception as e:
            self.logger.error(f"Failed to send ORB Discord notification: {e}", exc_info=True)
            # Don't set the flag so we can retry

    async def _check_and_send_daily_summary(self) -> None:
        """Check if it's time to send daily summary and send if needed."""
        from datetime import datetime

        # Use market timezone for date comparison (not system timezone!)
        now = datetime.now(self.market_scheduler.timezone)
        current_date = now.date().isoformat()

        # Only send once per day, after session end
        if self._last_summary_date == current_date:
            return

        # Get session end time for today
        session_end = self.market_scheduler.get_session_end_time()
        if not session_end:
            return

        # Check if we're past session end (already have 'now' in market timezone)
        if now >= session_end:
            await self._send_daily_summary()
            self._last_summary_date = current_date

    async def _send_daily_summary(self) -> None:
        """Generate and send end-of-day summary to Discord."""
        if not self.config.notifications.discord_webhook_url:
            self.logger.debug("Discord webhook not configured, skipping daily summary")
            return

        try:
            from vibe.trading_bot.notifications.discord import DiscordNotifier

            # Get account equity
            account = await self.exchange.get_account()
            account_value = account.equity
            initial_capital = self.config.trading.initial_capital
            pnl_pct = ((account_value - initial_capital) / initial_capital) * 100

            # Build summary message
            summary_lines = [
                f"📊 **Daily Summary - {self._daily_stats['date']}**",
                f"",
                f"**Account:**",
                f"• Equity: ${account_value:,.2f}",
                f"• P/L: ${account_value - initial_capital:,.2f} ({pnl_pct:+.2f}%)",
                f"",
            ]

            # ORB levels
            if self._daily_stats["orb_levels"]:
                summary_lines.append("**Opening Range Levels:**")
                for symbol, levels in self._daily_stats["orb_levels"].items():
                    summary_lines.append(
                        f"• {symbol}: ${levels['low']:.2f}-${levels['high']:.2f} (range: ${levels['range']:.2f})"
                    )
                summary_lines.append("")

            # Activity summary
            summary_lines.extend([
                f"**Activity:**",
                f"• Breakouts Detected: {self._daily_stats['breakouts_detected']}",
                f"• Signals Generated: {self._daily_stats['signals_generated']}",
                f"• Trades Executed: {self._daily_stats['trades_executed']}",
            ])

            # Signals by symbol
            if self._daily_stats["signals_by_symbol"]:
                summary_lines.append("")
                summary_lines.append("**Signals by Symbol:**")
                for symbol, count in self._daily_stats["signals_by_symbol"].items():
                    summary_lines.append(f"• {symbol}: {count}")

            # Rejection reasons
            if self._daily_stats["breakouts_rejected"]:
                summary_lines.append("")
                summary_lines.append("**Breakouts Rejected:**")
                for reason, count in self._daily_stats["breakouts_rejected"].items():
                    summary_lines.append(f"• {reason}: {count}")

            # No activity message
            if self._daily_stats["signals_generated"] == 0 and self._daily_stats["breakouts_detected"] == 0:
                summary_lines.append("")
                summary_lines.append("_No trading signals or breakouts today._")

            message = "\n".join(summary_lines)

            # Send to Discord (simple text message, not using notification payloads)
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self.config.notifications.discord_webhook_url,
                    json={"content": message},
                    timeout=aiohttp.ClientTimeout(total=10)
                )

            self.logger.info(f"Daily summary sent to Discord for {self._daily_stats['date']}")

        except Exception as e:
            self.logger.error(f"Error sending daily summary: {e}", exc_info=True)

    async def _handle_realtime_trade(self, trade: dict) -> None:
        """
        Handle real-time trade from Finnhub websocket.

        Feeds trades to appropriate BarAggregator which builds 5m bars.

        Args:
            trade: Trade dict with {symbol, price, size, timestamp}
        """
        try:
            symbol = trade.get("symbol")
            price = trade.get("price")
            size = trade.get("size", 0)
            timestamp = trade.get("timestamp")

            if not all([symbol, price, timestamp]):
                return

            # Get aggregator for this symbol
            aggregator = self.bar_aggregators.get(symbol)
            if not aggregator:
                return

            # Add trade to aggregator (will trigger _handle_completed_bar when bar completes)
            aggregator.add_trade(
                timestamp=timestamp,
                price=price,
                size=size
            )

        except Exception as e:
            self.logger.error(f"Error handling real-time trade: {e}", exc_info=True)

    def _handle_completed_bar(self, symbol: str, bar_dict: dict) -> None:
        """
        Handle completed 5m bar from aggregator.

        Stores completed bar for use in strategy evaluation.

        Args:
            symbol: Trading symbol
            bar_dict: Completed bar dict with {timestamp, open, high, low, close, volume}
        """
        try:
            self.logger.info(
                f"[REALTIME BAR] {symbol}: "
                f"timestamp={bar_dict.get('timestamp')}, "
                f"O={bar_dict.get('open'):.2f}, "
                f"H={bar_dict.get('high'):.2f}, "
                f"L={bar_dict.get('low'):.2f}, "
                f"C={bar_dict.get('close'):.2f}, "
                f"V={bar_dict.get('volume'):.0f}"
            )

            # Convert to DataFrame row
            bar_row = pd.DataFrame([bar_dict])

            # Append to real-time bars for this symbol
            if symbol in self._realtime_bars:
                self._realtime_bars[symbol] = pd.concat(
                    [self._realtime_bars[symbol], bar_row],
                    ignore_index=True
                )
            else:
                self._realtime_bars[symbol] = bar_row

        except Exception as e:
            self.logger.error(f"Error handling completed bar for {symbol}: {e}", exc_info=True)

    # Old Finnhub connection methods removed - now handled by provider system in warm-up phase

    async def _start_rest_polling(self):
        """
        Start polling loop for REST API providers (Polygon-style).

        Polls at different intervals based on whether we have open positions:
        - With positions: poll every 60 seconds (monitor closely)
        - No positions: poll every 300 seconds (reduce API calls)
        """
        if not isinstance(self.active_provider, RESTDataProvider):
            return

        self.logger.info("Starting REST API polling loop")

        try:
            while self._running and self.market_scheduler.is_market_open():
                try:
                    # Determine poll interval based on positions
                    has_positions = len(self.exchange.get_all_positions()) > 0
                    poll_interval = (
                        self.config.data.poll_interval_with_position if has_positions
                        else self.config.data.poll_interval_no_position
                    )

                    self.logger.debug(
                        f"Polling {self.active_provider.provider_name} "
                        f"(positions={has_positions}, interval={poll_interval}s)"
                    )

                    # Fetch latest bars for all symbols
                    bars = await self.active_provider.get_multiple_latest_bars(
                        symbols=self.config.trading.symbols,
                        timeframe="5"  # 5-minute bars (Massive free tier supports 5min, not 1min)
                    )

                    # Process each bar
                    for symbol, bar in bars.items():
                        if bar:
                            # Feed to bar aggregator (same as WebSocket flow)
                            aggregator = self.bar_aggregators.get(symbol)
                            if aggregator:
                                # Convert bar to trade format for aggregator
                                aggregator.add_trade(
                                    timestamp=bar['timestamp'],
                                    price=bar['close'],
                                    size=bar['volume']
                                )
                        else:
                            self.logger.warning(f"No bar data received for {symbol}")

                    # Wait before next poll
                    await asyncio.sleep(poll_interval)

                except Exception as e:
                    self.logger.error(f"Error during REST polling: {e}", exc_info=True)

                    # Try fallback to secondary provider
                    if self.secondary_provider and self.active_provider != self.secondary_provider:
                        await self._switch_to_secondary_provider()

                    # Wait before retry
                    await asyncio.sleep(30)

        except asyncio.CancelledError:
            self.logger.info("REST polling task cancelled")
        except Exception as e:
            self.logger.error(f"Fatal error in REST polling: {e}", exc_info=True)

    async def _switch_to_secondary_provider(self):
        """Switch from primary to secondary provider on failure."""
        if not self.secondary_provider:
            self.logger.error("No secondary provider available for fallback")
            return

        self.logger.warning(
            f"Switching from {self.active_provider.provider_name} "
            f"to {self.secondary_provider.provider_name}"
        )

        # Disconnect primary
        try:
            await self.active_provider.disconnect()
        except Exception as e:
            self.logger.error(f"Error disconnecting primary provider: {e}")

        # Switch to secondary
        self.active_provider = self.secondary_provider

        # Connect secondary
        try:
            await self.active_provider.connect()

            if self.active_provider.connected:
                self.logger.info(f"[OK] Successfully switched to {self.active_provider.provider_name}")

                # If WebSocket, subscribe to symbols
                if isinstance(self.active_provider, WebSocketDataProvider):
                    for symbol in self.config.trading.symbols:
                        await self.active_provider.subscribe(symbol)
                    self.active_provider.on_trade(self._handle_realtime_trade)
                    self.active_provider.on_error(self._handle_provider_error)

                # If REST, polling loop will handle it automatically
                elif isinstance(self.active_provider, RESTDataProvider):
                    self.logger.info("REST provider - will continue polling")
            else:
                self.logger.error("Secondary provider connected but status not set")

        except Exception as e:
            self.logger.error(f"Failed to connect to secondary provider: {e}", exc_info=True)

    async def _handle_provider_error(self, error_data: dict):
        """Handle errors from real-time data provider."""
        error_type = error_data.get("type", "unknown")
        message = error_data.get("message", "Unknown error")

        self.logger.error(f"Provider error ({error_type}): {message}")

        # If critical error, try secondary provider
        critical_errors = ["connection_error", "auth_error", "rate_limit", "gap_detected"]
        if error_type in critical_errors and self.secondary_provider:
            await self._switch_to_secondary_provider()

    async def _pre_market_warmup(self) -> bool:
        """
        Pre-market warm-up phase (9:25-9:30 AM EST).

        Prepares the bot for market open by:
        1. Pre-fetching 2 days of historical data (warm cache)
        2. Connecting to Finnhub websocket
        3. Pre-calculating indicators
        4. Running health checks

        Returns:
            True if warm-up successful, False if errors occurred
        """
        self.logger.info("=" * 60)
        self.logger.info("PRE-MARKET WARM-UP PHASE")
        self.logger.info("=" * 60)

        market_open = self.market_scheduler.get_open_time()
        if market_open:
            self.logger.info(f"Market opens at: {market_open.strftime('%H:%M:%S %Z')}")

        warmup_success = True

        # Step 1: Pre-fetch historical data (warm cache)
        self.logger.info("Step 1/4: Warming cache with historical data...")
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
                    warmup_success = False

            self.logger.info("Cache warm-up complete!")
        except Exception as e:
            self.logger.error(f"Error during cache warm-up: {e}", exc_info=True)
            warmup_success = False

        # Step 2: Connect to real-time data provider
        self.logger.info("Step 2/4: Connecting to real-time data provider...")
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
                        max_wait = 70  # Wait up to 70 seconds for first pong (Finnhub pings ~60s)
                        waited = 0
                        while waited < max_wait:
                            if hasattr(self.primary_provider, 'last_pong_time') and self.primary_provider.last_pong_time:
                                self.logger.info(f"   [OK] WebSocket ping/pong verified after {waited:.1f}s")
                                break
                            await asyncio.sleep(1)
                            waited += 1

                        if not (hasattr(self.primary_provider, 'last_pong_time') and self.primary_provider.last_pong_time):
                            self.logger.warning("   [!] WebSocket ping/pong not received within 70s timeout")
                            self.logger.warning("   [!] Connection may be unstable (continuing anyway)")
                else:
                    raise Exception("Connection failed - provider not connected")
            else:
                self.logger.info("No real-time provider configured (will use Yahoo Finance only)")
        except Exception as e:
            self.logger.error(f"   ❌ Failed to connect: {e}", exc_info=True)
            if self.secondary_provider:
                await self._switch_to_secondary_provider()
            self.logger.warning("Continuing without Finnhub (Yahoo Finance fallback)")
            warmup_success = False

        # Step 3: Pre-calculate indicators (optional, for future optimization)
        self.logger.info("Step 3/4: Pre-calculating indicators...")
        try:
            # For now, just verify indicator engine is ready
            if self.indicator_engine:
                self.logger.info("Indicator engine ready!")
            else:
                self.logger.warning("Indicator engine not initialized")
        except Exception as e:
            self.logger.error(f"Error checking indicators: {e}")

        # Step 4: Health checks
        self.logger.info("Step 4/4: Running health checks...")
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

        # Summary
        self.logger.info("=" * 60)
        if warmup_success and all_healthy:
            self.logger.info("WARM-UP COMPLETE - Ready for market open!")
        else:
            self.logger.warning("WARM-UP COMPLETE - Some issues detected (continuing anyway)")
        self.logger.info("=" * 60)

        # Send Discord notification with system health status
        if self.config.notifications.discord_webhook_url:
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
        else:
            self.logger.debug("Discord webhook URL not configured, skipping warm-up notification")

        return warmup_success

    async def run(self) -> None:
        """Run main trading loop.

        Continuously checks market hours, fetches data, generates signals,
        and executes trades until shutdown is triggered.
        """
        await self.initialize()

        # Start health API server for Docker healthcheck
        self._health_server_task = await start_health_server_task(
            host="0.0.0.0",
            port=self.config.health_check_port
        )
        self.logger.info(f"Health API server started on port {self.config.health_check_port}")

        # Mark bot as alive for healthcheck
        set_health_state(is_alive=True)

        self._running = True
        self.logger.info("Trading loop started")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Check if we should send end-of-day summary
                    await self._check_and_send_daily_summary()

                    # Check if bot should be active (warm-up OR market open)
                    if not self.market_scheduler.should_bot_be_active():
                        now = datetime.now(self.market_scheduler.timezone)

                        # If provider already disconnected, we've completed cooldown - just sleep until morning
                        if self.active_provider and not self.active_provider.connected:
                            # Calculate sleep time until next warm-up
                            next_warmup = self.market_scheduler.get_warmup_time()
                            next_open = self.market_scheduler.next_market_open()
                            target_time = next_warmup if next_warmup else next_open

                            # Log once
                            if not self._market_closed_logged:
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                                    f"({(target_time - now).total_seconds()/3600:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )
                                self._market_closed_logged = True

                            try:
                                sleep_seconds = (target_time - datetime.now(
                                    self.market_scheduler.timezone
                                )).total_seconds()
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, 300)  # 5 minutes
                                )
                            except asyncio.TimeoutError:
                                pass
                            continue

                        # Check if we're in cooldown phase (5 minutes after market close)
                        if self._cooldown_start_time is None:
                            # Market just closed, start cooldown phase
                            self._cooldown_start_time = now
                            self.logger.info(
                                "=" * 60 + "\n"
                                "MARKET CLOSE COOLDOWN PHASE STARTED\n"
                                "=" * 60
                            )
                            self.logger.info(
                                f"Market closed at {now.strftime('%H:%M:%S')}. "
                                f"Entering {self._cooldown_duration_seconds}s cooldown phase for cleanup:"
                            )
                            self.logger.info("  - Processing final real-time bars")
                            self.logger.info("  - Completing pending operations")
                            self.logger.info("  - Generating daily summaries")
                            self.logger.info(f"  - Will disconnect from provider at {(now + timedelta(seconds=self._cooldown_duration_seconds)).strftime('%H:%M:%S')}")

                        # Calculate time remaining in cooldown
                        cooldown_elapsed = (now - self._cooldown_start_time).total_seconds()

                        if cooldown_elapsed < self._cooldown_duration_seconds:
                            # Still in cooldown - keep provider connected, process final data
                            remaining = self._cooldown_duration_seconds - cooldown_elapsed
                            self.logger.info(
                                f"Cooldown phase: {remaining:.0f}s remaining. "
                                f"Processing final data..."
                            )

                            # Continue to process final bars/trades during cooldown
                            # Sleep briefly then loop again to process any remaining data
                            await asyncio.sleep(30)  # Check every 30 seconds during cooldown
                            continue

                        # Cooldown complete, disconnect and sleep until next day
                        # Always log completion (not gated by flag)
                        self.logger.info(
                            "=" * 60 + "\n"
                            "COOLDOWN PHASE COMPLETE\n"
                            "=" * 60
                        )

                        # Rotate tick log file for next market session (if enabled)
                        if self.active_provider and hasattr(self.active_provider, 'rotate_tick_log_for_new_session'):
                            try:
                                self.active_provider.rotate_tick_log_for_new_session()
                                self.logger.info("[OK] Rotated tick log file for next market session")
                            except Exception as e:
                                self.logger.warning(f"Failed to rotate tick log file: {e}")

                        # Disconnect from provider after cooldown (idempotent, safe to call multiple times)
                        if self.active_provider and self.active_provider.connected:
                            await self.active_provider.disconnect()
                            self.logger.info(f"[OK] Disconnected from {self.active_provider.provider_name} after {self._cooldown_duration_seconds}s cooldown")

                        # Calculate sleep time until next warm-up (must recalculate each iteration)
                        next_warmup = self.market_scheduler.get_warmup_time()
                        next_open = self.market_scheduler.next_market_open()
                        target_time = next_warmup if next_warmup else next_open

                        sleep_seconds = (target_time - now).total_seconds()

                        # Only log the "sleeping until..." message once (avoid spam)
                        if sleep_seconds > 0 and not self._market_closed_logged:
                            self.logger.info(
                                f"Market closed, sleeping until warm-up at {target_time} "
                                f"({sleep_seconds/3600:.1f} hours). "
                                f"Checking for shutdown every 5 minutes."
                            )
                            self._market_closed_logged = True

                        try:
                            # Check for shutdown every 5 minutes
                            sleep_seconds = (target_time - datetime.now(
                                self.market_scheduler.timezone
                            )).total_seconds()
                            await asyncio.wait_for(
                                self._shutdown_event.wait(),
                                timeout=min(sleep_seconds, 300)  # 5 minutes
                            )
                        except asyncio.TimeoutError:
                            pass
                        continue

                    # Bot is active - check if warm-up phase or trading
                    if self.market_scheduler.is_warmup_phase():
                        # Pre-market warm-up phase (9:25-9:30 AM)
                        self.logger.info("Entering pre-market warm-up phase...")
                        await self._pre_market_warmup()

                        # Sleep until market actually opens
                        market_open = self.market_scheduler.get_open_time()
                        if market_open:
                            now = datetime.now(self.market_scheduler.timezone)
                            sleep_until_open = (market_open - now).total_seconds()

                            if sleep_until_open > 0:
                                self.logger.info(
                                    f"Warm-up complete. Waiting {sleep_until_open:.0f}s "
                                    f"until market open at {market_open.strftime('%H:%M:%S')}..."
                                )
                                await asyncio.sleep(sleep_until_open)

                        continue

                    elif self.market_scheduler.is_market_open():
                        # Market is open - reset closed flag and cooldown timer if needed
                        if self._market_closed_logged:
                            self._market_closed_logged = False
                            self._cooldown_start_time = None
                            self.logger.info("Market is now open - starting trading cycle")

                        # If bot started during market hours (after warm-up), ensure provider is connected
                        if self.primary_provider and not self.primary_provider.connected:
                            self.logger.info("Bot started during market hours - connecting to real-time provider...")
                            try:
                                await self.primary_provider.connect()
                                if self.primary_provider.connected:
                                    self.logger.info(f"   [OK] Connected to {self.primary_provider.provider_name}")
                                    set_health_state(websocket_connected=True, recent_heartbeat=True)

                                    # Subscribe if WebSocket
                                    if isinstance(self.primary_provider, WebSocketDataProvider):
                                        for symbol in self.config.trading.symbols:
                                            await self.primary_provider.subscribe(symbol)
                                        self.logger.info(f"   [OK] Subscribed to {len(self.config.trading.symbols)} symbols")
                                else:
                                    self.logger.error("Failed to connect to real-time provider")
                            except Exception as e:
                                self.logger.error(f"Error connecting to real-time provider: {e}", exc_info=True)

                        # Run trading cycle
                        success = await self._trading_cycle()

                    # Update failure counter
                    if success:
                        self._consecutive_failures = 0
                    else:
                        self._consecutive_failures += 1

                    # Heartbeat
                    self.health_monitor.check_heartbeat()

                    # Calculate sleep interval with exponential backoff
                    sleep_interval = self._calculate_sleep_interval()

                    if self._consecutive_failures > 0:
                        self.logger.warning(
                            f"Consecutive failures: {self._consecutive_failures}, "
                            f"sleeping for {sleep_interval}s before retry"
                        )

                    await asyncio.sleep(sleep_interval)

                except asyncio.CancelledError:
                    self.logger.info("Trading loop cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}", exc_info=True)
                    self.health_monitor.record_error("trading_cycle")
                    self._consecutive_failures += 1
                    # Use exponential backoff on errors too
                    backoff = min(5 * (2 ** min(self._consecutive_failures, 5)), 300)
                    self.logger.info(f"Backing off for {backoff}s after error")
                    await asyncio.sleep(backoff)

        finally:
            await self.shutdown()

    def _has_active_positions(self) -> bool:
        """Check if we have any active positions.

        Returns:
            True if any positions are open, False otherwise
        """
        try:
            positions = self.exchange.get_positions()
            return len(positions) > 0
        except Exception as e:
            self.logger.debug(f"Error checking positions: {e}")
            # On error, assume we have positions (conservative approach)
            return True

    def _log_strategy_events(
        self,
        symbol: str,
        signal_value: int,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Smart event-based logging for strategy evaluation.

        Only logs interesting events to avoid spam:
        - ORB establishment (once per day per symbol)
        - Price approaching breakout levels (within 0.5%)
        - Breakout detected but rejected by filters
        - Signal generated (handled by caller)

        Args:
            symbol: Trading symbol
            signal_value: Signal value (1=long, -1=short, 0=no signal)
            metadata: Strategy metadata dict
        """
        # Check if we have ORB data in metadata
        if "orb_high" not in metadata or "orb_low" not in metadata:
            return

        orb_high = metadata.get("orb_high", 0)
        orb_low = metadata.get("orb_low", 0)
        orb_range = metadata.get("orb_range", 0)
        current_price = metadata.get("current_price", 0)

        # Get current date for tracking
        from datetime import datetime
        current_date = datetime.now().date().isoformat()

        # Event 1: Log ORB establishment once per day per symbol
        if symbol not in self._orb_logged_today or self._orb_logged_today[symbol] != current_date:
            self.logger.info(
                f"[ORB] {symbol}: Opening range established "
                f"${orb_low:.2f}-${orb_high:.2f} (range: ${orb_range:.2f})"
            )
            self._orb_logged_today[symbol] = current_date

        # Event 2: Price approaching breakout (within 0.5%, but don't spam)
        price_position = metadata.get("price_position", "")
        distance_to_high = metadata.get("distance_to_high_pct", 100)
        distance_to_low = metadata.get("distance_to_low_pct", 100)

        # Approaching high breakout
        if price_position == "within_range" and 0 < distance_to_high < 0.5:
            # Only log if we haven't logged in the last 5 minutes (300 seconds)
            last_approach = self._last_approach_logged.get(f"{symbol}_high", 0)
            if datetime.now().timestamp() - last_approach > 300:
                self.logger.info(
                    f"[ORB] {symbol}: Price approaching HIGH breakout - "
                    f"Current: ${current_price:.2f}, Breakout: ${orb_high:.2f} "
                    f"({distance_to_high:.2f}% away)"
                )
                self._last_approach_logged[f"{symbol}_high"] = datetime.now().timestamp()

        # Approaching low breakout
        elif price_position == "within_range" and 0 < distance_to_low < 0.5:
            last_approach = self._last_approach_logged.get(f"{symbol}_low", 0)
            if datetime.now().timestamp() - last_approach > 300:
                self.logger.info(
                    f"[ORB] {symbol}: Price approaching LOW breakout - "
                    f"Current: ${current_price:.2f}, Breakout: ${orb_low:.2f} "
                    f"({distance_to_low:.2f}% away)"
                )
                self._last_approach_logged[f"{symbol}_low"] = datetime.now().timestamp()

        # Event 3: Breakout detected but rejected by filters
        if signal_value == 0 and price_position in ["above_high", "below_low"]:
            reason = metadata.get("reason", "unknown")
            reason_detail = metadata.get("reason_detail", "")

            # Only log interesting rejection reasons (not repetitive ones)
            interesting_reasons = {
                "after_entry_cutoff_time",
                "insufficient_volume",
                "position_already_open",
            }

            if reason in interesting_reasons:
                # Avoid logging the same rejection multiple times
                last_rejection = self._last_approach_logged.get(f"{symbol}_rejection_{reason}", 0)
                if datetime.now().timestamp() - last_rejection > 600:  # 10 minutes
                    breakout_type = "HIGH" if price_position == "above_high" else "LOW"
                    detail_str = f" ({reason_detail})" if reason_detail else ""

                    self.logger.info(
                        f"[ORB] {symbol}: {breakout_type} breakout detected at ${current_price:.2f} "
                        f"but REJECTED - Reason: {reason}{detail_str}"
                    )
                    self._last_approach_logged[f"{symbol}_rejection_{reason}"] = datetime.now().timestamp()

    def _calculate_sleep_interval(self) -> int:
        """Calculate sleep interval with exponential backoff on failures.

        Optimizes interval based on position status:
        - With active positions: 60s (active monitoring)
        - No positions: 300s (idle, less frequent checks)

        Returns:
            Sleep interval in seconds
        """
        # Check if we're in failure/backoff mode
        if self._consecutive_failures > 0:
            # Check if we've exceeded max failures
            if self._consecutive_failures >= self._max_consecutive_failures:
                self.logger.error(
                    f"Exceeded max consecutive failures ({self._max_consecutive_failures}). "
                    f"Pausing for {self._max_backoff_seconds}s. "
                    "This may indicate a persistent issue (e.g., no market data available)."
                )
                return self._max_backoff_seconds

            # Exponential backoff: base * 2^failures, capped at max
            backoff = min(
                self._base_cycle_interval * (2 ** self._consecutive_failures),
                self._max_backoff_seconds
            )
            return int(backoff)

        # No failures - choose interval based on position status
        has_positions = self._has_active_positions()

        if has_positions:
            self.logger.debug(f"Active positions detected, using {self._base_cycle_interval}s interval")
            return self._base_cycle_interval
        else:
            self.logger.info(
                f"No active positions, using idle interval: {self._idle_cycle_interval}s "
                f"(checking for entry signals)"
            )
            return self._idle_cycle_interval

    async def _trading_cycle(self) -> bool:
        """Execute one trading cycle: fetch data, generate signals, execute trades.

        Returns:
            True if cycle completed successfully, False if data fetch failed
        """
        successful_fetches = 0
        try:
            # Start REST polling if needed and market is open
            if isinstance(self.active_provider, RESTDataProvider):
                market_open = self.market_scheduler.is_market_open()
                if market_open and (not self._polling_task or self._polling_task.done()):
                    self._polling_task = asyncio.create_task(self._start_rest_polling())
                    self.logger.info("Started REST API polling task")

            # 1. Fetch fresh data for all symbols
            # During market hours with Finnhub active, don't use Yahoo Finance fallback
            # (Yahoo is 15-min delayed and would interfere with real-time data)
            market_open = self.market_scheduler.is_market_open()
            finnhub_active = self.finnhub_ws and self.finnhub_ws.connected

            allow_yfinance = not (market_open and finnhub_active)

            if market_open and not allow_yfinance:
                self.logger.debug(
                    f"Market is open and Finnhub is active - relying solely on websocket data"
                )

            for symbol in self.config.trading.symbols:
                try:
                    # Fetch historical data from Yahoo (includes yesterday + today with staleness check)
                    # During market hours with Finnhub websocket, disable yfinance fallback
                    bars = await self.data_manager.get_data(
                        symbol=symbol,
                        timeframe="5m",
                        days=1,
                        allow_yfinance_fallback=allow_yfinance,
                    )

                    if bars is None or bars.empty:
                        # Only log on first few failures, then reduce verbosity
                        if self._consecutive_failures < 3:
                            self.logger.warning(f"No bars fetched for {symbol}")
                        continue

                    # If we have real-time bars from Finnhub websocket, append them
                    if symbol in self._realtime_bars and not self._realtime_bars[symbol].empty:
                        realtime_bars = self._realtime_bars[symbol]

                        # Detect data gap between yfinance (delayed) and Finnhub (real-time)
                        if "timestamp" in bars.columns and not bars.empty:
                            import pytz
                            last_yf_bar = pd.to_datetime(bars.iloc[-1]["timestamp"])
                            first_rt_bar = pd.to_datetime(realtime_bars.iloc[0]["timestamp"])

                            # Ensure timezone awareness
                            if last_yf_bar.tzinfo is None:
                                last_yf_bar = pytz.utc.localize(last_yf_bar)
                            if first_rt_bar.tzinfo is None:
                                first_rt_bar = pytz.utc.localize(first_rt_bar)

                            gap_minutes = (first_rt_bar - last_yf_bar).total_seconds() / 60

                            # Expected gap is 5 minutes (one bar interval)
                            # If gap > 10 minutes, we're missing data
                            # Note: This happens when bot restarts during market hours due to yfinance 15-min delay.
                            # Consider keeping bot running or using Finnhub REST API for backfill.
                            if gap_minutes > 10:
                                self.logger.warning(
                                    f"[DATA GAP] {symbol}: {gap_minutes:.1f} minute gap between "
                                    f"yfinance (last: {last_yf_bar.strftime('%H:%M:%S')}) and "
                                    f"Finnhub (first: {first_rt_bar.strftime('%H:%M:%S')})"
                                )

                        # Combine historical (Yahoo) + real-time (Finnhub)
                        bars = pd.concat([bars, realtime_bars], ignore_index=True)

                        # Remove duplicates based on timestamp (prefer real-time data)
                        if "timestamp" in bars.columns:
                            bars = bars.drop_duplicates(subset=["timestamp"], keep="last")
                            bars = bars.sort_values("timestamp").reset_index(drop=True)

                        self.logger.info(
                            f"[HYBRID DATA] {symbol}: Combined {len(bars) - len(realtime_bars)} "
                            f"yfinance bars + {len(realtime_bars)} Finnhub real-time bars"
                        )

                    # Successfully fetched data
                    successful_fetches += 1

                    # 2. Calculate technical indicators (ATR required for strategy)
                    try:
                        # Calculate ATR_14 using the incremental indicator engine
                        self.logger.info(f"Calculating indicators for {symbol} ({len(bars)} bars)...")

                        bars = self.indicator_engine.update(
                            df=bars,
                            start_idx=0,
                            indicators=[{"name": "atr", "params": {"length": 14}}],
                            symbol=symbol,
                            timeframe="5m",
                        )

                        if "ATR_14" in bars.columns:
                            # Count how many bars have valid ATR (non-null)
                            valid_atr_count = bars["ATR_14"].notna().sum()
                            self.logger.info(
                                f"ATR_14 calculated for {symbol}: {valid_atr_count}/{len(bars)} bars have valid values"
                            )
                        else:
                            self.logger.error(
                                f"CRITICAL: ATR_14 column not added to DataFrame for {symbol}! "
                                f"Strategy evaluation will fail."
                            )
                            self.health_monitor.record_error(f"indicators_{symbol}")

                    except Exception as e:
                        # DO NOT CATCH SILENTLY - Log as ERROR with traceback
                        self.logger.error(
                            f"CRITICAL: Failed to calculate indicators for {symbol}: {e}",
                            exc_info=True
                        )
                        self.health_monitor.record_error(f"indicators_{symbol}")
                        # Continue without indicators - strategy will return early with error

                    # 3. Generate signals using incremental method (for real-time trading)
                    if bars.empty or len(bars) == 0:
                        self.logger.debug(f"Empty bars for {symbol}, skipping signal generation")
                        continue

                    # Get the latest bar for incremental signal generation
                    current_bar = bars.iloc[-1].to_dict()

                    # Generate signal for current bar with historical context
                    signal_value, signal_metadata = self.strategy.generate_signal_incremental(
                        symbol=symbol,
                        current_bar=current_bar,
                        df_context=bars,
                    )

                    # Log strategy evaluation issues at INFO level for visibility
                    reason = signal_metadata.get('reason', None)
                    if reason == 'insufficient_data':
                        self.logger.warning(
                            f"[STRATEGY] {symbol}: Insufficient data - "
                            f"missing ATR_14 or empty context. Strategy cannot evaluate."
                        )
                    elif reason and signal_value == 0:
                        # Log other no-signal reasons at debug level
                        self.logger.debug(
                            f"[STRATEGY] {symbol}: No signal - {reason}"
                        )

                    # Smart logging based on metadata
                    self._log_strategy_events(symbol, signal_value, signal_metadata)

                    # Update daily statistics
                    self._update_daily_stats(symbol, signal_value, signal_metadata)

                    # 4. Execute trade if we have a signal
                    if signal_value != 0:  # 1=long, -1=short, 0=no signal
                        self.logger.info(
                            f"[SIGNAL] {symbol}: {signal_metadata.get('signal', 'unknown').upper()} at "
                            f"${signal_metadata.get('current_price', 0):.2f} "
                            f"(ORB: ${signal_metadata.get('orb_low', 0):.2f}-${signal_metadata.get('orb_high', 0):.2f}, "
                            f"TP: ${signal_metadata.get('take_profit', 0):.2f}, "
                            f"SL: ${signal_metadata.get('stop_loss', 0):.2f}, "
                            f"R/R: {signal_metadata.get('risk_reward', 0):.1f})"
                        )
                        # TODO: Convert signal to proper Signal object and execute
                        # For now, just log it
                        try:
                            pass  # Placeholder for execution logic
                        except Exception as e:
                            self.logger.error(
                                f"Failed to execute signal for {symbol}: {e}",
                                exc_info=True
                            )
                            self.health_monitor.record_error("execution")

                except Exception as e:
                    # Only log full traceback on first few failures
                    if self._consecutive_failures < 3:
                        self.logger.error(f"Error processing {symbol}: {e}", exc_info=True)
                    else:
                        self.logger.debug(f"Error processing {symbol}: {e}")
                    self.health_monitor.record_error(f"data_{symbol}")

            # Check if ORB notification should be sent (after all symbols evaluated)
            await self._check_and_send_orb_notification()

            # Return success if we got data for at least one symbol
            return successful_fetches > 0

        except Exception as e:
            self.logger.error(f"Trading cycle error: {e}", exc_info=True)
            return False

    async def _execute_signal(self, signal: Any) -> None:
        """Execute a single trade signal.

        Args:
            signal: Trade signal from strategy
        """
        try:
            # Execute the trade
            result = await self.trade_executor.execute(
                signal=signal,
                account=None,  # Use default
            )

            if result.success:
                self.logger.info(f"Trade executed: {signal.symbol} {result.reason}")
            else:
                self.logger.warning(f"Trade execution failed: {result.reason}")

        except Exception as e:
            self.logger.error(f"Signal execution error: {e}", exc_info=True)

    async def shutdown(self) -> None:
        """Gracefully shutdown all components.

        Closes positions, syncs data, and cleans up resources.
        """
        if not self._running:
            return

        self.logger.info("Initiating graceful shutdown...")
        self._running = False
        self._shutdown_event.set()

        try:
            # Cancel main task if still running
            if self._main_task and not self._main_task.done():
                self._main_task.cancel()
                try:
                    await asyncio.wait_for(
                        self._main_task,
                        timeout=self.config.shutdown_timeout_seconds
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    self.logger.warning("Main task did not complete gracefully")

            # Cancel polling task if running
            if self._polling_task and not self._polling_task.done():
                self._polling_task.cancel()
                try:
                    await self._polling_task
                except asyncio.CancelledError:
                    self.logger.debug("Polling task cancelled")
                except Exception as e:
                    self.logger.error(f"Error cancelling polling task: {e}")

            # Disconnect from data providers
            try:
                if self.active_provider:
                    await self.active_provider.disconnect()
                    self.logger.info(f"Disconnected from {self.active_provider.provider_name}")
            except Exception as e:
                self.logger.error(f"Error disconnecting active provider: {e}")

            try:
                if self.secondary_provider and self.secondary_provider != self.active_provider:
                    await self.secondary_provider.disconnect()
                    self.logger.info(f"Disconnected from {self.secondary_provider.provider_name}")
            except Exception as e:
                self.logger.error(f"Error disconnecting secondary provider: {e}")

            # Update health state before shutdown
            set_health_state(is_alive=False, websocket_connected=False)

            # Stop health server
            if self._health_server_task and not self._health_server_task.done():
                self._health_server_task.cancel()
                try:
                    await self._health_server_task
                except asyncio.CancelledError:
                    self.logger.debug("Health server task cancelled")
                except Exception as e:
                    self.logger.error(f"Error stopping health server: {e}")

            # Close components
            try:
                await self.exchange.close()
            except Exception as e:
                self.logger.error(f"Error closing exchange: {e}")

            if self.trade_store:
                self.trade_store.close()

            self.logger.info("Graceful shutdown complete")

        except Exception as e:
            self.logger.error(f"Shutdown error: {e}", exc_info=True)

    def get_health(self) -> Dict[str, Any]:
        """Get current system health status."""
        return self.health_monitor.get_health()

    def get_status(self) -> Dict[str, Any]:
        """Get trading bot status."""
        return {
            "running": self._running,
            "market_open": self.market_scheduler.is_market_open(),
            "health": self.health_monitor.get_status_summary(),
        }

    async def run_backtest(
        self,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """Run backtest for a date range (placeholder for e2e testing).

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            Backtest results
        """
        await self.initialize()

        self.logger.info(f"Running backtest from {start_date} to {end_date}")

        # This is a placeholder - full backtest would be more complex
        # For now, just run a few iterations of the trading cycle
        try:
            for _ in range(5):
                if self._shutdown_event.is_set():
                    break
                await self._trading_cycle()

            return {
                "start_date": start_date,
                "end_date": end_date,
                "status": "completed",
                "trades": self.trade_store.get_trades(limit=100),
            }

        except Exception as e:
            self.logger.error(f"Backtest error: {e}", exc_info=True)
            return {
                "start_date": start_date,
                "end_date": end_date,
                "status": "failed",
                "error": str(e),
            }
        finally:
            await self.shutdown()
