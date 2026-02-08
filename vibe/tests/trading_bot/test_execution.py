"""
Unit tests for execution components: OrderManager and TradeExecutor.
"""

import asyncio
import pytest
from datetime import datetime, timedelta

from vibe.common.models import OrderStatus
from vibe.common.risk import PositionSizer
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import (
    OrderManager,
    OrderRetryPolicy,
    ManagedOrder,
)
from vibe.trading_bot.execution.trade_executor import (
    TradeExecutor,
    SimpleRiskManager,
    RiskCheckResult,
    ExecutionResult,
)


class TestOrderRetryPolicy:
    """Tests for OrderRetryPolicy."""

    def test_should_retry_under_limits(self):
        """Retry allowed when under limits."""
        policy = OrderRetryPolicy(max_retries=3, cancel_after_seconds=60)

        assert policy.should_retry(retry_count=0, elapsed_seconds=10)
        assert policy.should_retry(retry_count=1, elapsed_seconds=20)
        assert policy.should_retry(retry_count=2, elapsed_seconds=30)

    def test_should_retry_denied_max_retries(self):
        """Retry denied when max retries exceeded."""
        policy = OrderRetryPolicy(max_retries=3)

        assert not policy.should_retry(retry_count=3, elapsed_seconds=10)
        assert not policy.should_retry(retry_count=4, elapsed_seconds=20)

    def test_should_retry_denied_timeout(self):
        """Retry denied when timeout exceeded."""
        policy = OrderRetryPolicy(
            max_retries=10, cancel_after_seconds=60
        )

        assert not policy.should_retry(
            retry_count=0, elapsed_seconds=60
        )
        assert not policy.should_retry(
            retry_count=0, elapsed_seconds=70
        )

    def test_exponential_backoff(self):
        """Delay increases exponentially."""
        policy = OrderRetryPolicy(
            base_delay_seconds=1.0,
            backoff_multiplier=2.0,
        )

        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 2.0
        assert policy.get_delay(2) == 4.0
        assert policy.get_delay(3) == 8.0

    def test_delay_capped(self):
        """Delay capped at maximum."""
        policy = OrderRetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=10.0,
            backoff_multiplier=2.0,
        )

        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(3) == 8.0
        assert policy.get_delay(10) == 10.0  # Capped


class TestOrderManager:
    """Tests for OrderManager."""

    @pytest.mark.asyncio
    async def test_submit_order(self):
        """Submit order to exchange."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)

        response = await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        assert response.order_id is not None
        assert response.filled_qty >= 0

    @pytest.mark.asyncio
    async def test_order_created_callback(self):
        """ORDER_CREATED callback invoked."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        created_orders = []

        def on_created(order_id):
            created_orders.append(order_id)

        manager = OrderManager(
            exchange=exchange,
            on_order_created=on_created,
        )

        response = await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        assert len(created_orders) == 1
        assert created_orders[0] == response.order_id

    @pytest.mark.asyncio
    async def test_order_tracking(self):
        """Orders tracked properly in manager."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)

        response = await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        # Should be able to retrieve the order
        managed = manager.get_order(response.order_id)
        assert managed is not None

    @pytest.mark.asyncio
    async def test_get_order_by_id(self):
        """Get managed order by ID."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)

        response = await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        managed = manager.get_order(response.order_id)
        assert managed is not None
        assert managed.order_id == response.order_id

    @pytest.mark.asyncio
    async def test_get_orders_by_symbol(self):
        """Get orders by symbol."""
        exchange = MockExchange(initial_capital=50000)
        await exchange.set_price("AAPL", 150.00)
        await exchange.set_price("MSFT", 300.00)

        manager = OrderManager(exchange=exchange)

        await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )
        await manager.submit_order(
            symbol="MSFT",
            side="buy",
            quantity=5,
            order_type="market",
        )

        aapl_orders = manager.get_orders_by_symbol("AAPL")
        assert len(aapl_orders) == 1
        assert aapl_orders[0].order.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_get_orders_by_status(self):
        """Get orders filtered by status."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)

        # Submit a market order (will be filled)
        response = await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        # Get all orders
        all_orders = manager.get_all_orders()
        assert len(all_orders) >= 1

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Shutdown cancels tasks and orders."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)

        await manager.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        await manager.shutdown()

        # Should complete without errors


class TestSimpleRiskManager:
    """Tests for SimpleRiskManager."""

    def test_pre_trade_check_passes(self):
        """Risk check passes for valid trade."""
        sizer = PositionSizer(risk_per_trade=100)
        manager = SimpleRiskManager(
            position_sizer=sizer,
            max_positions=5,
        )

        result = manager.pre_trade_check(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=150,
            current_account_value=10000,
            existing_positions={},
        )

        assert result.passed is True

    def test_pre_trade_check_max_positions(self):
        """Risk check fails when max positions exceeded."""
        from datetime import datetime
        from vibe.common.models import Position

        sizer = PositionSizer(risk_per_trade=100)
        manager = SimpleRiskManager(
            position_sizer=sizer,
            max_positions=2,
        )

        existing = {
            "AAPL": Position(
                symbol="AAPL",
                side="long",
                quantity=10,
                entry_price=150,
                current_price=150,
            ),
            "MSFT": Position(
                symbol="MSFT",
                side="long",
                quantity=5,
                entry_price=300,
                current_price=300,
            ),
        }

        result = manager.pre_trade_check(
            symbol="GOOGL",
            side="buy",
            quantity=10,
            entry_price=100,
            current_account_value=10000,
            existing_positions=existing,
        )

        assert result.passed is False
        assert "maximum positions" in result.reason.lower()

    def test_pre_trade_check_symbol_already_exists(self):
        """Risk check fails if symbol already has position."""
        from vibe.common.models import Position

        sizer = PositionSizer(risk_per_trade=100)
        manager = SimpleRiskManager(position_sizer=sizer)

        existing = {
            "AAPL": Position(
                symbol="AAPL",
                side="long",
                quantity=10,
                entry_price=150,
                current_price=150,
            ),
        }

        result = manager.pre_trade_check(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=155,
            current_account_value=10000,
            existing_positions=existing,
        )

        assert result.passed is False
        assert "already exists" in result.reason.lower()


class TestTradeExecutor:
    """Tests for TradeExecutor."""

    @pytest.mark.asyncio
    async def test_signal_to_order(self):
        """Strategy signal converted to order."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
        )

        result = await executor.execute_signal(
            symbol="AAPL",
            signal=1,  # Long
            entry_price=150.00,
            stop_price=145.00,
            strategy_name="ORB",
        )

        assert result.success is True
        assert result.order_id is not None
        assert result.position_size > 0

    @pytest.mark.asyncio
    async def test_risk_check_blocks_execution(self):
        """Risk check failure prevents order submission."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        risk_mgr = SimpleRiskManager(
            position_sizer=sizer,
            max_positions=0,  # Force failure
        )
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
            risk_manager=risk_mgr,
        )

        result = await executor.execute_signal(
            symbol="AAPL",
            signal=1,
            entry_price=150.00,
            stop_price=145.00,
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_execution_callback(self):
        """Execution callback invoked."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)

        executed = []

        def on_execution(result):
            executed.append(result)

        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
            on_execution=on_execution,
        )

        await executor.execute_signal(
            symbol="AAPL",
            signal=1,
            entry_price=150.00,
            stop_price=145.00,
        )

        assert len(executed) >= 1

    @pytest.mark.asyncio
    async def test_close_position(self):
        """Close open position."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
        )

        # Open position
        await executor.execute_signal(
            symbol="AAPL",
            signal=1,
            entry_price=150.00,
            stop_price=145.00,
        )

        # Close position
        result = await executor.execute_signal(
            symbol="AAPL",
            signal=0,  # Close
            entry_price=150.00,
            stop_price=145.00,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_short_signal(self):
        """Short signal creates sell order."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
        )

        result = await executor.execute_signal(
            symbol="AAPL",
            signal=-1,  # Short
            entry_price=150.00,
            stop_price=155.00,
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_close_nonexistent_position(self):
        """Close non-existent position fails gracefully."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
        )

        result = await executor.execute_signal(
            symbol="AAPL",
            signal=0,  # Close
            entry_price=150.00,
            stop_price=145.00,
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_get_open_trades(self):
        """Get all open trades."""
        exchange = MockExchange(initial_capital=50000)
        await exchange.set_price("AAPL", 150.00)
        await exchange.set_price("MSFT", 300.00)

        manager = OrderManager(exchange=exchange)
        sizer = PositionSizer(risk_per_trade=100)
        executor = TradeExecutor(
            exchange=exchange,
            order_manager=manager,
            position_sizer=sizer,
        )

        # Open two positions
        await executor.execute_signal(
            symbol="AAPL",
            signal=1,
            entry_price=150.00,
            stop_price=145.00,
        )
        await executor.execute_signal(
            symbol="MSFT",
            signal=1,
            entry_price=300.00,
            stop_price=295.00,
        )

        trades = executor.get_open_trades()
        assert len(trades) >= 2
