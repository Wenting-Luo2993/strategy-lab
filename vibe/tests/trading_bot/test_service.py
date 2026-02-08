"""Tests for trading service."""

import pytest
import asyncio
import signal
from vibe.trading_bot.core.service import TradingService, ServiceConfig
from vibe.trading_bot.config.settings import AppSettings


class TestTradingService:
    """Tests for TradingService."""

    @pytest.fixture
    def service(self):
        """Create a trading service."""
        config = AppSettings(
            shutdown_timeout_seconds=5,
            environment="test",
        )
        return TradingService(config=config)

    def test_service_initialization(self, service):
        """Test service initialization."""
        assert service is not None
        assert service.config.environment == "test"
        assert not service.is_running()
        assert not service.is_shutdown_requested()

    def test_default_settings(self):
        """Test service with default settings."""
        service = TradingService()
        assert service.config is not None
        assert service.config.environment in ["development", "production"]

    def test_register_shutdown_handler(self, service):
        """Test registering shutdown handlers."""
        handler1 = lambda: None
        handler2 = lambda: None

        service.register_shutdown_handler(handler1)
        service.register_shutdown_handler(handler2)

        assert len(service._shutdown_handlers) == 2

    @pytest.mark.asyncio
    async def test_shutdown_sequence(self, service):
        """Test shutdown sequence executes handlers."""
        call_order = []

        def handler1():
            call_order.append(1)

        def handler2():
            call_order.append(2)

        service.register_shutdown_handler(handler1)
        service.register_shutdown_handler(handler2)

        await service._handle_shutdown()

        assert call_order == [1, 2]
        assert service.is_shutdown_requested()

    @pytest.mark.asyncio
    async def test_async_shutdown_handler(self, service):
        """Test async shutdown handlers."""
        called = []

        async def async_handler():
            called.append(True)

        service.register_shutdown_handler(async_handler)
        await service._handle_shutdown()

        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_shutdown_handler_exception(self, service):
        """Test that exceptions in handlers don't stop shutdown."""
        call_order = []

        def handler1():
            call_order.append(1)
            raise ValueError("Test error")

        def handler2():
            call_order.append(2)

        service.register_shutdown_handler(handler1)
        service.register_shutdown_handler(handler2)

        await service._handle_shutdown()

        # Both handlers should be called despite exception in handler1
        assert call_order == [1, 2]

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, service):
        """Test that shutdown can be called multiple times safely."""
        call_count = [0]

        def handler():
            call_count[0] += 1

        service.register_shutdown_handler(handler)

        await service._handle_shutdown()
        await service._handle_shutdown()  # Second call should be no-op

        # Handler should only be called once
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_is_running_flag(self, service):
        """Test is_running flag."""
        assert not service.is_running()

        service._is_running = True
        assert service.is_running()

        await service._handle_shutdown()
        assert not service.is_running()

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self, service):
        """Test shutdown timeout for slow handlers."""
        service.config.shutdown_timeout_seconds = 1

        async def slow_handler():
            await asyncio.sleep(5)  # Longer than timeout

        service.register_shutdown_handler(slow_handler)

        # Should complete without hanging due to timeout
        await service._handle_shutdown()
        assert service.is_shutdown_requested()

    @pytest.mark.asyncio
    async def test_run_until_shutdown(self, service):
        """Test run method with immediate shutdown."""
        service.register_shutdown_handler(lambda: None)

        # Create a task to trigger shutdown after a short delay
        async def trigger_shutdown():
            await asyncio.sleep(0.1)
            await service._handle_shutdown()

        shutdown_task = asyncio.create_task(trigger_shutdown())

        # This should complete after shutdown is triggered
        await asyncio.wait_for(service.run(), timeout=2.0)

        assert service.is_shutdown_requested()

    @pytest.mark.asyncio
    async def test_multiple_handlers_order(self, service):
        """Test that handlers are called in registration order."""
        execution_order = []

        for i in range(5):
            service.register_shutdown_handler(
                lambda idx=i: execution_order.append(idx)
            )

        await service._handle_shutdown()

        # Handlers should be called in order of registration
        assert execution_order == [0, 1, 2, 3, 4]


class TestServiceConfig:
    """Tests for ServiceConfig."""

    def test_default_config(self):
        """Test default service config."""
        config = ServiceConfig()
        assert config.shutdown_timeout == 30
        assert config.environment == "development"
        assert config.log_level == "INFO"

    def test_custom_config(self):
        """Test custom service config."""
        config = ServiceConfig(
            shutdown_timeout=60,
            environment="production",
            log_level="ERROR",
        )
        assert config.shutdown_timeout == 60
        assert config.environment == "production"
        assert config.log_level == "ERROR"


class TestSignalHandling:
    """Tests for signal handling."""

    @pytest.fixture
    def service(self):
        """Create a service for signal tests."""
        config = AppSettings(shutdown_timeout_seconds=5)
        return TradingService(config=config)

    @pytest.mark.asyncio
    async def test_signal_handlers_setup(self, service):
        """Test that signal handlers are registered."""
        # Get a running event loop to pass to signal handler setup
        loop = asyncio.get_running_loop()
        service._setup_signal_handlers(loop)
        # On non-Windows systems, SIGTERM should be registered
        # This is a basic check that setup doesn't error
        assert service is not None

    @pytest.mark.asyncio
    async def test_signal_handler_creates_task(self, service):
        """Test that signal handler can create shutdown task."""
        service._is_running = True

        # Manually trigger shutdown
        await service._handle_shutdown(signal.SIGINT)

        assert service.is_shutdown_requested()
        assert not service.is_running()
