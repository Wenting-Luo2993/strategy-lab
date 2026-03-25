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
from vibe.trading_bot.core.phases import WarmupPhaseManager, CooldownPhaseManager
from vibe.common.ruleset import StrategyRuleSet, RuleSetLoader, OrbLevelStopLoss, OrbRangeMultipleTakeProfit


logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main orchestrator coordinating all trading bot components.

    Manages initialization, main trading loop, graceful shutdown, and component
    integration for complete trading system lifecycle.
    """

    def __init__(
        self,
        config: Optional[AppSettings] = None,
        ruleset: Optional[StrategyRuleSet] = None,
        market_scheduler: Optional[BaseMarketScheduler] = None,
        testing_mode: bool = False,
        bar_interval: str = "5m",
    ):
        """Initialize trading orchestrator.

        Args:
            config: Application settings (uses get_settings() if None)
            ruleset: Strategy ruleset (loads from config.active_ruleset if None)
            market_scheduler: Market scheduler (creates default if None)
            testing_mode: If True, use shorter sleep intervals for faster testing
            bar_interval: Bar aggregation interval (e.g., "1m", "5m"). Default "5m" for production.
                         Use "1m" for integration testing to see bars complete faster.
        """
        self.config = config or get_settings()
        self.logger = logging.getLogger(__name__)
        self._testing_mode = testing_mode
        self._bar_interval = bar_interval

        # Load ruleset if not provided
        if ruleset is None:
            try:
                ruleset = RuleSetLoader.from_name(self.config.active_ruleset)
            except Exception as e:
                self.logger.error(f"Failed to load ruleset '{self.config.active_ruleset}': {e}")
                raise
        self.ruleset = ruleset

        # Component initialization order matters
        # Allow dependency injection for testing (defaults to real scheduler for production)
        if market_scheduler is None:
            self.market_scheduler: BaseMarketScheduler = create_scheduler(
                market_type=self.config.trading.market_type,
                exchange=self.config.trading.exchange,
            )
        else:
            self.market_scheduler: BaseMarketScheduler = market_scheduler
        self.health_monitor = HealthMonitor()
        self.trade_store = TradeStore(db_path=self.config.database_path)

        # Retry/backoff state
        self._consecutive_failures = 0
        self._max_consecutive_failures = 10

        # Sleep intervals: shorter in testing mode for faster test execution
        if testing_mode:
            self._base_cycle_interval = 1   # 1 second when monitoring positions (testing)
            self._idle_cycle_interval = 2   # 2 seconds when no positions (testing)
            self._max_backoff_seconds = 5   # 5 seconds max backoff (testing)
        else:
            self._base_cycle_interval = 60   # 60 seconds when monitoring positions (production)
            self._idle_cycle_interval = 300  # 5 minutes when no positions (production)
            self._max_backoff_seconds = 900  # 15 minutes max backoff (production)
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

        # Market closed state tracking (to avoid log spam after cooldown completes)
        # Note: Cooldown manager has its own internal state
        self._market_closed_logged: bool = False

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

        # Phase managers (warmup, cooldown)
        self.warmup_manager: Optional[WarmupPhaseManager] = None
        self.cooldown_manager: Optional[CooldownPhaseManager] = None

        # Log active ruleset at initialization
        self.logger.info(
            f"Active ruleset: {self.ruleset.name} (v{self.ruleset.version}) — "
            f"Symbols: {', '.join(self.ruleset.instruments.symbols)} | "
            f"Timeframe: {self.ruleset.instruments.timeframe}"
        )

    @property
    def active_symbols(self) -> List[str]:
        """Trading symbols driven by ruleset if available, else fall back to config."""
        if self.ruleset and self.ruleset.instruments.symbols:
            return list(self.ruleset.instruments.symbols)
        return list(self.config.trading.symbols)

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
                # Use configured interval (default 5m, but 1m for integration testing)
                aggregator = BarAggregator(bar_interval=self._bar_interval)

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
                # Risk pct driven by ruleset if available, else default 1%
                risk_pct = 0.01
                if self.ruleset and self.ruleset.position_size.method == "max_loss_pct":
                    risk_pct = self.ruleset.position_size.value
                position_sizer = PositionSizer(risk_pct=risk_pct)

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

            # 5. Initialize strategy — driven by ruleset if available, else fall back to .env
            try:
                if self.ruleset:
                    orb_params = self.ruleset.strategy
                    # Take profit multiplier: 0.0 means disabled (no TP target)
                    tp_multiplier = 0.0
                    if self.ruleset.exit.take_profit is not None:
                        tp_multiplier = getattr(self.ruleset.exit.take_profit, "multiplier", 0.0)
                    # Stop loss: True if ORB level stop, False if ATR-based
                    stop_at_level = isinstance(self.ruleset.exit.stop_loss, OrbLevelStopLoss)
                    strategy_config = ORBStrategyConfig(
                        name="ORB",
                        orb_start_time=orb_params.orb_start_time,
                        orb_duration_minutes=orb_params.orb_duration_minutes,
                        orb_body_pct_filter=orb_params.orb_body_pct_filter,
                        entry_cutoff_time=orb_params.entry_cutoff_time,
                        take_profit_multiplier=tp_multiplier,
                        stop_loss_at_level=stop_at_level,
                        use_volume_filter=self.ruleset.trade_filter.volume_confirmation,
                        volume_threshold=self.ruleset.trade_filter.volume_threshold,
                        market_close_time=self.config.strategy.market_close_time,
                    )
                    self.logger.info(
                        f"Strategy config from ruleset '{self.ruleset.name}': "
                        f"ORB {orb_params.orb_start_time}+{orb_params.orb_duration_minutes}m, "
                        f"body_filter={orb_params.orb_body_pct_filter:.0%}, "
                        f"tp={'disabled' if tp_multiplier == 0 else f'{tp_multiplier}x'}, "
                        f"sl={'orb_level' if stop_at_level else 'atr'}"
                    )
                else:
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
                    self.logger.info(
                        f"Strategy config from .env: ORB window={self.config.strategy.orb_start_time} "
                        f"duration={self.config.strategy.orb_duration_minutes}m"
                    )
                self.strategy = ORBStrategy(config=strategy_config)
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

                # Create bar aggregators for all active symbols (driven by ruleset)
                for symbol in self.active_symbols:
                    aggregator = BarAggregator(
                        bar_interval=self._bar_interval,
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
                # NOTE: Do NOT clear bar_aggregators here!
                # Bar aggregators have callbacks registered - clearing them destroys callbacks
                # and prevents real-time bars even if provider reconnects later.
                # Keep aggregators alive - they'll be reset in warmup phase.

            # 7. Register health checks
            self._register_health_checks()

            # 8. Initialize phase managers
            self.warmup_manager = WarmupPhaseManager(self)
            self.cooldown_manager = CooldownPhaseManager(self)

            # 9. Provider connection now handled in warm-up phase (Step 2)
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

        # Safety check: Reset stats if new day (warmup phase should handle this proactively)
        current_date = datetime.now(self.market_scheduler.timezone).date().isoformat()
        if self._daily_stats["date"] != current_date:
            self.logger.debug(f"Late daily stats reset during trading (expected in warmup)")
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
            self.logger.info(
                f"[ORB STORED] {symbol}: High=${metadata['orb_high']:.2f}, "
                f"Low=${metadata['orb_low']:.2f}, Range=${metadata['orb_range']:.2f}"
            )

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
        expected_symbols = set(self.active_symbols)
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
        from vibe.trading_bot.utils.datetime_utils import get_market_now

        # Use market scheduler's time (supports both real and mock schedulers)
        now = get_market_now(self.market_scheduler)
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
            from vibe.trading_bot.utils.datetime_utils import get_market_now
            from vibe.trading_bot.notifications.payloads import DailySummaryPayload
            from vibe.trading_bot.notifications.helper import discord_notification_context
            from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter
            from vibe.trading_bot.version import BUILD_VERSION
            import aiohttp

            # Get current time in market timezone
            now = get_market_now(self.market_scheduler)

            # Get account equity
            account = await self.exchange.get_account()
            account_value = account.equity
            initial_capital = self.config.trading.initial_capital
            pnl_pct = ((account_value - initial_capital) / initial_capital) * 100

            # Build ORB levels dict
            orb_levels = {}
            for symbol, levels in self._daily_stats["orb_levels"].items():
                orb_levels[symbol] = {
                    "high": levels["high"],
                    "low": levels["low"],
                    "range": levels["range"]
                }

            # Create payload
            payload = DailySummaryPayload(
                event_type="DAILY_SUMMARY",
                timestamp=now,
                date=self._daily_stats["date"],
                account_equity=account_value,
                initial_capital=initial_capital,
                pnl_pct=pnl_pct,
                orb_levels=orb_levels,
                breakouts_detected=self._daily_stats["breakouts_detected"],
                signals_generated=self._daily_stats["signals_generated"],
                trades_executed=self._daily_stats["trades_executed"],
                signals_by_symbol=self._daily_stats["signals_by_symbol"].copy(),
                breakouts_rejected=self._daily_stats["breakouts_rejected"].copy(),
                version=BUILD_VERSION
            )

            # Use formatter to convert payload to webhook format
            formatter = DiscordNotificationFormatter()
            webhook_payload = formatter.format_daily_summary(payload)

            # Send directly using aiohttp (notifier doesn't have send_daily_summary method yet)
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self.config.notifications.discord_webhook_url,
                    json=webhook_payload,
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

    async def _flush_elapsed_bars(self) -> None:
        """
        Flush bars that have crossed time boundaries (quiet market handling).

        This is the TIME-TRIGGERED completion path that complements the existing
        TRADE-TRIGGERED completion. Called periodically from trading loop.

        Why needed:
        - Trade-triggered: Bar completes when first trade of NEXT minute arrives (fast)
        - Time-triggered: Bar completes after time boundary even if no trades (safety net)

        Example scenario:
        - 9:32:00 bar is building with trades at 9:32:05, 9:32:15, 9:32:30
        - Market goes quiet - NO trades arrive at 9:33:00+
        - Without this method: 9:32:00 bar never completes!
        - With this method: 9:32:00 bar completes within 1-60 seconds (guaranteed)

        Called every iteration of trading loop (1-60 seconds depending on mode).
        """
        from vibe.trading_bot.utils.datetime_utils import get_market_now

        try:
            current_time = get_market_now(self.market_scheduler)

            for symbol, aggregator in self.bar_aggregators.items():
                # Check if this aggregator has a bar that crossed time boundary
                # NOTE: flush_if_elapsed() already calls the callback if bar completes,
                # so we don't need to call _handle_completed_bar() here
                aggregator.flush_if_elapsed(current_time)

        except Exception as e:
            self.logger.error(f"Error flushing elapsed bars: {e}", exc_info=True)

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
                        symbols=self.active_symbols,
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
                    for symbol in self.active_symbols:
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
                    from vibe.trading_bot.utils.datetime_utils import get_market_now

                    if not self.market_scheduler.should_bot_be_active():
                        now = get_market_now(self.market_scheduler)

                        # If provider already disconnected, we've completed cooldown - just sleep until morning
                        if self.active_provider and not self.active_provider.connected:
                            # Calculate sleep time until next warm-up
                            # Use next_market_open() and subtract 5 min to ensure FUTURE time
                            next_open = self.market_scheduler.next_market_open()
                            target_time = next_open - timedelta(minutes=5)  # Warmup is 5 min before open

                            # Log once
                            if not self._market_closed_logged:
                                current_time = get_market_now(self.market_scheduler)
                                hours_until_warmup = (target_time - current_time).total_seconds() / 3600
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                                    f"({hours_until_warmup:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )
                                self._market_closed_logged = True

                            try:
                                sleep_seconds = (target_time - get_market_now(
                                    self.market_scheduler
                                )).total_seconds()
                                # Use shorter sleep in testing mode
                                max_sleep = 1 if self._testing_mode else 300
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, max_sleep)
                                )
                            except asyncio.TimeoutError:
                                pass
                            continue

                        # Run cooldown phase (process final data, disconnect provider)
                        await self.cooldown_manager.execute()

                        # If cooldown complete, sleep until next warmup
                        if self.cooldown_manager.is_cooldown_complete():
                            sleep_seconds = self.cooldown_manager.calculate_sleep_until_warmup()
                            # Use next_market_open() and subtract 5 min to ensure FUTURE time
                            next_open = self.market_scheduler.next_market_open()
                            target_time = next_open - timedelta(minutes=5)  # Warmup is 5 min before open

                            # Log sleep message once (avoid spam)
                            if sleep_seconds > 0 and self.cooldown_manager.should_log_sleep_message():
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                                    f"({sleep_seconds/3600:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )

                            try:
                                # Check for shutdown every 5 minutes (or 1s in testing mode)
                                max_sleep = 1 if self._testing_mode else 300
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, max_sleep)
                                )
                            except asyncio.TimeoutError:
                                pass

                        continue

                    # Bot is active - check if warm-up phase or trading
                    if self.market_scheduler.is_warmup_phase():
                        # Pre-market warm-up phase (9:25-9:30 AM)
                        self.logger.info("Entering pre-market warm-up phase...")

                        # Reset cooldown from previous day
                        self.cooldown_manager.reset()

                        await self.warmup_manager.execute()

                        # Sleep until market actually opens
                        market_open = self.market_scheduler.get_open_time()
                        if market_open:
                            now = get_market_now(self.market_scheduler)
                            sleep_until_open = (market_open - now).total_seconds()

                            if sleep_until_open > 0:
                                self.logger.info(
                                    f"Warm-up complete. Waiting {sleep_until_open:.0f}s "
                                    f"until market open at {market_open.strftime('%H:%M:%S')}..."
                                )
                                # Use shorter sleep in testing mode
                                max_sleep = 1 if self._testing_mode else sleep_until_open
                                await asyncio.sleep(max_sleep)

                        continue

                    elif self.market_scheduler.is_market_open():
                        # If bot started during market hours, run warmup (without Discord notification)
                        # Note: Warmup phase handles all state reset (bars, flags, stats, etc.)
                        if self.primary_provider and not self.primary_provider.connected:
                            self.logger.info("Bot started during market hours - running warmup phase...")
                            await self.warmup_manager.execute(send_notification=False)

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
                "weak_breakout_candle",
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

            for symbol in self.active_symbols:
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

                            # CRITICAL: Ensure timestamp is datetime (fixes dtype issues from stale cache)
                            if not pd.api.types.is_datetime64_any_dtype(bars["timestamp"]):
                                self.logger.warning(
                                    f"[DTYPE FIX] {symbol}: timestamp column is {bars['timestamp'].dtype}, converting to datetime"
                                )
                                bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)

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
                        # Log no-signal reasons at info level for visibility
                        self.logger.info(
                            f"[STRATEGY] {symbol}: No signal - {reason}"
                        )

                    # Smart logging based on metadata
                    self._log_strategy_events(symbol, signal_value, signal_metadata)

                    # Update daily statistics
                    self._update_daily_stats(symbol, signal_value, signal_metadata)

                    # 4. Execute trade if we have a signal
                    if signal_value != 0:  # 1=long, -1=short, 0=no signal
                        tp = signal_metadata.get('take_profit')
                        rr = signal_metadata.get('risk_reward')
                        tp_str = f"${tp:.2f}" if tp is not None else "none"
                        rr_str = f"{rr:.1f}" if rr is not None else "n/a"
                        self.logger.info(
                            f"[SIGNAL] {symbol}: {signal_metadata.get('signal', 'unknown').upper()} at "
                            f"${signal_metadata.get('current_price', 0):.2f} "
                            f"(ORB: ${signal_metadata.get('orb_low', 0):.2f}-${signal_metadata.get('orb_high', 0):.2f}, "
                            f"TP: {tp_str}, "
                            f"SL: ${signal_metadata.get('stop_loss', 0):.2f}, "
                            f"R/R: {rr_str})"
                        )
                        # Execute trade via TradeExecutor
                        try:
                            entry_price = signal_metadata.get('current_price', 0.0)
                            stop_price = signal_metadata.get('stop_loss', 0.0)
                            result = await self.trade_executor.execute_signal(
                                symbol=symbol,
                                signal=signal_value,
                                entry_price=entry_price,
                                stop_price=stop_price,
                                take_profit=signal_metadata.get('take_profit'),
                                strategy_name=self.strategy.config.name,
                            )

                            if result.success:
                                self.logger.info(
                                    f"[TRADE] {symbol}: {result.reason} "
                                    f"({int(result.position_size)} shares @ ${entry_price:.2f})"
                                )
                                self._daily_stats["trades_executed"] += 1

                                # Send Discord ORDER_SENT notification
                                if self.config.notifications.discord_webhook_url:
                                    from vibe.trading_bot.notifications.payloads import OrderNotificationPayload
                                    from vibe.trading_bot.notifications.helper import discord_notification_context
                                    from vibe.trading_bot.utils.datetime_utils import get_market_now
                                    side = "buy" if signal_value == 1 else "sell"
                                    payload = OrderNotificationPayload(
                                        event_type="ORDER_SENT",
                                        timestamp=get_market_now(self.market_scheduler),
                                        order_id=result.order_id or "unknown",
                                        symbol=symbol,
                                        side=side,
                                        order_type="market",
                                        quantity=result.position_size,
                                        strategy_name=self.strategy.config.name,
                                        signal_reason=signal_metadata.get('signal'),
                                        order_price=entry_price,
                                        exchange="PAPER",
                                    )
                                    async with discord_notification_context(
                                        self.config.notifications.discord_webhook_url
                                    ) as notifier:
                                        await notifier.send_order_event(payload)
                            else:
                                self.logger.warning(
                                    f"[TRADE] {symbol}: Execution failed — {result.reason}"
                                )

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

            # Flush any bars that crossed time boundaries (quiet market handling)
            # This is the TIME-TRIGGERED completion path (complements trade-triggered)
            await self._flush_elapsed_bars()

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
