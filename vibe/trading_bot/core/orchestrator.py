"""Main trading orchestrator coordinating all components."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from vibe.trading_bot.config.settings import AppSettings, get_settings
from vibe.trading_bot.core.market_schedulers import create_scheduler, BaseMarketScheduler
from vibe.trading_bot.core.health_monitor import HealthMonitor
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider
from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
from vibe.trading_bot.storage.trade_store import TradeStore
import pandas as pd
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager, OrderRetryPolicy
from vibe.trading_bot.execution.trade_executor import TradeExecutor
from vibe.common.risk import PositionSizer
from vibe.common.strategies import ORBStrategy
from vibe.common.strategies.orb import ORBStrategyConfig
from vibe.common.indicators.engine import IncrementalIndicatorEngine


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

        # Strategy logging state tracking (to avoid duplicate logs)
        self._orb_logged_today: Dict[str, str] = {}  # symbol -> date_str
        self._last_approach_logged: Dict[str, float] = {}  # symbol -> timestamp

        # Daily statistics tracking for end-of-day summary
        self._daily_stats: Dict[str, Any] = self._initialize_daily_stats()
        self._last_summary_date: Optional[str] = None

        # Market closed state tracking (to avoid log spam)
        self._market_closed_logged: bool = False

        # Finnhub websocket for real-time intraday data
        self.finnhub_ws: Optional[FinnhubWebSocketClient] = None
        self.bar_aggregators: Dict[str, BarAggregator] = {}  # One aggregator per symbol

        # Real-time bars storage (symbol -> DataFrame with today's bars)
        self._realtime_bars: Dict[str, pd.DataFrame] = {}

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

            # 6. Initialize Finnhub websocket for real-time intraday data
            try:
                finnhub_api_key = getattr(self.config.data, 'finnhub_api_key', None) or self.config.data.api_key

                if finnhub_api_key and finnhub_api_key != "your_finnhub_api_key_here":
                    self.logger.info("Initializing Finnhub WebSocket for real-time intraday data...")

                    # Create websocket client
                    self.finnhub_ws = FinnhubWebSocketClient(api_key=finnhub_api_key)

                    # Create one bar aggregator per symbol
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

                    # Set up trade callback to feed aggregators
                    self.finnhub_ws.on_trade(self._handle_realtime_trade)

                    self.logger.info("Finnhub WebSocket configured (will connect at market open)")
                else:
                    self.logger.warning(
                        "Finnhub API key not configured - using Yahoo Finance only (15-min delay). "
                        "Set FINNHUB_API_KEY in .env for real-time intraday data."
                    )
            except Exception as e:
                self.logger.error(f"Failed to initialize Finnhub WebSocket: {e}")
                self.logger.warning("Falling back to Yahoo Finance only (15-min delay)")
                self.finnhub_ws = None
                self.bar_aggregators = {}

            # 7. Register health checks
            self._register_health_checks()

            # 8. Connect Finnhub websocket if market is already open
            market_is_open = self.market_scheduler.is_market_open()

            if self.finnhub_ws and market_is_open:
                try:
                    await self._connect_finnhub_websocket()
                    self.logger.info("Finnhub WebSocket connected (market already open)")
                except Exception as e:
                    self.logger.error(f"Failed to connect Finnhub WebSocket during initialization: {e}")
                    self.logger.warning("Will continue with Yahoo Finance only (15-min delay)")

            # Log data source configuration
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

        # Record ORB levels
        if "orb_high" in metadata and symbol not in self._daily_stats["orb_levels"]:
            self._daily_stats["orb_levels"][symbol] = {
                "high": metadata["orb_high"],
                "low": metadata["orb_low"],
                "range": metadata["orb_range"],
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

    async def _connect_finnhub_websocket(self) -> None:
        """Connect to Finnhub websocket and subscribe to symbols."""
        if not self.finnhub_ws:
            return

        try:
            self.logger.info("Connecting to Finnhub WebSocket...")
            await self.finnhub_ws.connect()

            # Subscribe to all symbols
            for symbol in self.config.trading.symbols:
                await self.finnhub_ws.subscribe(symbol)
                self.logger.info(f"Subscribed to real-time data for {symbol}")

            self.logger.info("Finnhub WebSocket connected and subscribed")
            self.logger.info(
                "Note: Finnhub streams real-time trades from NOW forward. "
                "If bot restarted during market hours, there will be a ~15min gap "
                "between last yfinance bar (delayed) and first Finnhub bar (real-time). "
                "This gap will be logged when detected during data combination."
            )

        except Exception as e:
            self.logger.error(f"Error connecting to Finnhub WebSocket: {e}", exc_info=True)
            self.logger.warning("Will continue with Yahoo Finance only (15-min delay)")

    async def _disconnect_finnhub_websocket(self) -> None:
        """Disconnect from Finnhub websocket."""
        if not self.finnhub_ws or not self.finnhub_ws.connected:
            return

        try:
            self.logger.info("Disconnecting from Finnhub WebSocket...")
            await self.finnhub_ws.disconnect()
            self.logger.info("Finnhub WebSocket disconnected")

        except Exception as e:
            self.logger.error(f"Error disconnecting from Finnhub WebSocket: {e}", exc_info=True)

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

        # Step 2: Connect Finnhub websocket
        self.logger.info("Step 2/4: Connecting to Finnhub WebSocket...")
        try:
            if self.finnhub_ws and not self.finnhub_ws.connected:
                await self._connect_finnhub_websocket()
                self.logger.info("Finnhub WebSocket ready!")
            elif self.finnhub_ws:
                self.logger.info("Finnhub WebSocket already connected")
            else:
                self.logger.info("Finnhub not configured (will use Yahoo Finance only)")
        except Exception as e:
            self.logger.error(f"Error connecting Finnhub during warm-up: {e}", exc_info=True)
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

        return warmup_success

    async def run(self) -> None:
        """Run main trading loop.

        Continuously checks market hours, fetches data, generates signals,
        and executes trades until shutdown is triggered.
        """
        await self.initialize()

        self._running = True
        self.logger.info("Trading loop started")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Check if we should send end-of-day summary
                    await self._check_and_send_daily_summary()

                    # Check if bot should be active (warm-up OR market open)
                    if not self.market_scheduler.should_bot_be_active():
                        # Market closed, sleep until warm-up time (5 min before open)
                        next_warmup = self.market_scheduler.get_warmup_time()
                        next_open = self.market_scheduler.next_market_open()

                        # Use warm-up time if available, otherwise market open
                        target_time = next_warmup if next_warmup else next_open

                        sleep_seconds = (target_time - datetime.now(
                            self.market_scheduler.timezone
                        )).total_seconds()

                        if sleep_seconds > 0:
                            # Log only once when market first closes (avoid log spam)
                            if not self._market_closed_logged:
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time} "
                                    f"({sleep_seconds/3600:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )
                                self._market_closed_logged = True

                                # Disconnect from Finnhub websocket when market closes
                                if self.finnhub_ws and self.finnhub_ws.connected:
                                    await self._disconnect_finnhub_websocket()

                            try:
                                # Check for shutdown every 5 minutes
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
                        # Market is open - reset closed flag if needed
                        if self._market_closed_logged:
                            self._market_closed_logged = False
                            self.logger.info("Market is now open - starting trading cycle")

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
            # 1. Fetch fresh data for all symbols
            for symbol in self.config.trading.symbols:
                try:
                    # Fetch historical data from Yahoo (includes yesterday + today with staleness check)
                    bars = await self.data_manager.get_data(
                        symbol=symbol,
                        timeframe="5m",
                        days=1,
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
                            if gap_minutes > 10:
                                self.logger.warning(
                                    f"[DATA GAP] {symbol}: {gap_minutes:.1f} minute gap between "
                                    f"yfinance (last: {last_yf_bar.strftime('%H:%M:%S')}) and "
                                    f"Finnhub (first: {first_rt_bar.strftime('%H:%M:%S')}). "
                                    f"This happens when bot restarts during market hours due to "
                                    f"yfinance 15-min delay. Consider keeping bot running or using "
                                    f"Finnhub REST API for backfill."
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
                        # Calculate ATR_14 for the DataFrame
                        bars_with_indicators = bars.copy()
                        atr_values = []

                        for idx, row in bars.iterrows():
                            bar_dict = {
                                'high': row['high'],
                                'low': row['low'],
                                'close': row['close'],
                            }
                            atr = self.indicator_engine.calculate_atr(
                                symbol=symbol,
                                timeframe="5m",
                                bar=bar_dict,
                                length=14,
                            )
                            atr_values.append(atr)

                        bars_with_indicators['ATR_14'] = atr_values
                        bars = bars_with_indicators

                    except Exception as e:
                        self.logger.debug(f"Error calculating indicators for {symbol}: {e}")
                        # Continue without indicators - strategy will handle missing ATR

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

                    # Debug: Log strategy response for first few cycles
                    if self._consecutive_failures < 2:  # Only log for first couple cycles
                        self.logger.debug(
                            f"Strategy evaluation for {symbol}: signal={signal_value}, "
                            f"reason={signal_metadata.get('reason', 'N/A')}, "
                            f"has_orb={('orb_high' in signal_metadata)}"
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
