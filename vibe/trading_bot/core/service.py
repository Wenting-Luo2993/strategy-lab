"""Trading service with graceful shutdown."""

import asyncio
import signal
import sys
from typing import Optional, Callable, List, Any
from vibe.trading_bot.utils.logger import get_logger
from vibe.trading_bot.config.settings import AppSettings, get_settings


logger = get_logger(__name__)


class TradingService:
    """Main trading service with graceful shutdown support."""

    def __init__(self, config: Optional[AppSettings] = None):
        """Initialize trading service.

        Args:
            config: AppSettings configuration (uses defaults if not provided)
        """
        self.config = config or get_settings()
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._shutdown_handlers: List[Callable] = []
        self._is_running = False

    def register_shutdown_handler(self, handler: Callable[[], Any]) -> None:
        """Register a handler to be called during shutdown.

        Args:
            handler: Callable to execute during shutdown
        """
        self._shutdown_handlers.append(handler)

    def _setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Setup signal handlers for graceful shutdown.

        Args:
            loop: The event loop to use for scheduling shutdown
        """
        def _signal_handler(signum: int, frame: Any) -> None:
            logger.info(f"Received signal {signum}, initiating graceful shutdown")
            # Schedule shutdown in the correct event loop
            if loop.is_running():
                loop.create_task(self._handle_shutdown(signum))
            else:
                logger.warning("Event loop not running, cannot schedule shutdown")

        # Register signal handlers
        if sys.platform != "win32":  # Unix-like systems
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
        else:  # Windows
            signal.signal(signal.SIGINT, _signal_handler)

    async def _handle_shutdown(self, signum: Optional[int] = None) -> None:
        """Handle graceful shutdown sequence.

        Args:
            signum: Signal number that triggered shutdown
        """
        if self._shutdown_event.is_set():
            logger.warning("Shutdown already in progress, ignoring")
            return

        self._shutdown_event.set()
        self._is_running = False

        logger.info("Starting graceful shutdown sequence")

        try:
            # Step 1: Call registered shutdown handlers
            logger.info(f"Executing {len(self._shutdown_handlers)} shutdown handlers")
            for i, handler in enumerate(self._shutdown_handlers, 1):
                try:
                    logger.debug(f"Executing shutdown handler {i}/{len(self._shutdown_handlers)}")
                    if asyncio.iscoroutinefunction(handler):
                        await asyncio.wait_for(
                            handler(),
                            timeout=self.config.shutdown_timeout_seconds,
                        )
                    else:
                        handler()
                except asyncio.TimeoutError:
                    logger.error(f"Shutdown handler {i} timed out")
                except Exception as e:
                    logger.error(f"Error in shutdown handler {i}: {e}")

            logger.info("Graceful shutdown completed successfully")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def run(self) -> None:
        """Run the trading service.

        This method sets up signal handlers and runs until shutdown is triggered.
        """
        self._is_running = True
        logger.info(f"Starting trading service (environment: {self.config.environment})")

        # Get the current event loop and setup signal handlers
        loop = asyncio.get_running_loop()
        self._setup_signal_handlers(loop)

        try:
            # Wait for shutdown signal
            while self._is_running:
                await asyncio.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
            await self._handle_shutdown(signal.SIGINT)
        except Exception as e:
            logger.error(f"Error in service main loop: {e}")
            await self._handle_shutdown()

    def start(self) -> None:
        """Start the trading service synchronously.

        Creates and runs an asyncio event loop.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self.run())
        finally:
            loop.close()

    def is_running(self) -> bool:
        """Check if service is running.

        Returns:
            True if service is running, False otherwise
        """
        return self._is_running

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested.

        Returns:
            True if shutdown event is set
        """
        return self._shutdown_event.is_set()


class ServiceConfig:
    """Configuration for service behavior."""

    def __init__(
        self,
        shutdown_timeout: int = 30,
        environment: str = "development",
        log_level: str = "INFO",
    ):
        """Initialize service config.

        Args:
            shutdown_timeout: Seconds to wait for graceful shutdown
            environment: Environment name (development, production)
            log_level: Logging level
        """
        self.shutdown_timeout = shutdown_timeout
        self.environment = environment
        self.log_level = log_level
