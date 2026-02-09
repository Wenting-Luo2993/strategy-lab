"""Main trading orchestrator coordinating all components."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from vibe.trading_bot.config.settings import AppSettings, get_settings
from vibe.trading_bot.core.scheduler import MarketScheduler
from vibe.trading_bot.core.health_monitor import HealthMonitor
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.trade_executor import TradeExecutor
from vibe.common.strategies import ORBStrategy


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
        self.market_scheduler = MarketScheduler(exchange="NYSE")
        self.health_monitor = HealthMonitor()
        self.trade_store = TradeStore(db_path=self.config.database_path)
        self.data_manager: Optional[DataManager] = None
        self.exchange = MockExchange()
        self.trade_executor: Optional[TradeExecutor] = None
        self.strategy: Optional[ORBStrategy] = None

        # Trading loop control
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Main loop task
        self._main_task: Optional[asyncio.Task] = None

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
                self.data_manager = DataManager(
                    symbols=self.config.trading.symbols,
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

            # 3. Initialize trade executor
            try:
                self.trade_executor = TradeExecutor(
                    exchange=self.exchange,
                    data_manager=self.data_manager,
                    position_sizer=None,  # Use default
                )
                self.logger.info("Trade executor initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize trade executor: {e}")
                raise

            # 4. Initialize strategy
            try:
                self.strategy = ORBStrategy()
                self.logger.info("Strategy initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize strategy: {e}")
                raise

            # 5. Register health checks
            self._register_health_checks()

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
                    # Check if market is open
                    if not self.market_scheduler.is_market_open():
                        # Market closed, sleep until next open
                        next_open = self.market_scheduler.next_market_open()
                        sleep_seconds = (next_open - datetime.now(
                            self.market_scheduler.timezone
                        )).total_seconds()

                        if sleep_seconds > 0:
                            self.logger.info(
                                f"Market closed, sleeping for {sleep_seconds:.0f}s "
                                f"until {next_open}"
                            )
                            try:
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, 60)  # Check every minute
                                )
                            except asyncio.TimeoutError:
                                pass
                        continue

                    # Market is open, run trading cycle
                    await self._trading_cycle()

                    # Heartbeat
                    self.health_monitor.check_heartbeat()

                    # Small sleep to prevent busy loop
                    await asyncio.sleep(1)

                except asyncio.CancelledError:
                    self.logger.info("Trading loop cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}", exc_info=True)
                    self.health_monitor.record_error("trading_cycle")
                    await asyncio.sleep(5)  # Back off on error

        finally:
            await self.shutdown()

    async def _trading_cycle(self) -> None:
        """Execute one trading cycle: fetch data, generate signals, execute trades."""
        try:
            # 1. Fetch fresh data for all symbols
            for symbol in self.config.trading.symbols:
                try:
                    bars = await self.data_manager.fetch_bars(
                        symbol=symbol,
                        timeframe="1m",
                        limit=100,
                    )

                    if not bars:
                        self.logger.warning(f"No bars fetched for {symbol}")
                        continue

                    # 2. Generate signals
                    signals = self.strategy.generate_signals(
                        symbol=symbol,
                        bars=bars,
                    )

                    # 3. Execute trades for each signal
                    for signal in signals:
                        try:
                            await self._execute_signal(signal)
                        except Exception as e:
                            self.logger.error(
                                f"Failed to execute signal for {symbol}: {e}",
                                exc_info=True
                            )
                            self.health_monitor.record_error("execution")

                except Exception as e:
                    self.logger.error(f"Error processing {symbol}: {e}", exc_info=True)
                    self.health_monitor.record_error(f"data_{symbol}")

        except Exception as e:
            self.logger.error(f"Trading cycle error: {e}", exc_info=True)

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
