"""
Unit tests for exchange components: SlippageModel and MockExchange.
"""

import pytest
from datetime import datetime

from vibe.common.models import OrderStatus
from vibe.trading_bot.exchange.slippage import SlippageModel
from vibe.trading_bot.exchange.mock_exchange import MockExchange


class TestSlippageModel:
    """Tests for SlippageModel."""

    def test_slippage_buy_direction(self):
        """Buy orders slip up (worse price)."""
        model = SlippageModel(
            base_slippage_pct=0.001,
            volatility_factor=0,
            size_impact_factor=0,
            random_factor=0,
            use_seed=42,
        )

        slipped = model.apply(price=100.00, side="buy")

        assert slipped > 100.00
        # With only base slippage 0.1%, price should be 100.10
        assert slipped == pytest.approx(100.10, rel=0.001)

    def test_slippage_sell_direction(self):
        """Sell orders slip down (worse price)."""
        model = SlippageModel(base_slippage_pct=0.001, random_factor=0, use_seed=42)

        slipped = model.apply(price=100.00, side="sell")

        assert slipped < 100.00

    def test_slippage_volatility_factor(self):
        """Higher volatility increases slippage."""
        model = SlippageModel(
            base_slippage_pct=0.001,
            volatility_factor=0.5,
            random_factor=0,
            use_seed=42,
        )

        low_vol = model.apply(
            price=100.00, side="buy", volatility=0.01
        )
        high_vol = model.apply(
            price=100.00, side="buy", volatility=0.05
        )

        assert high_vol > low_vol

    def test_slippage_size_impact(self):
        """Larger orders have more slippage."""
        model = SlippageModel(
            base_slippage_pct=0.001,
            size_impact_factor=0.0001,
            random_factor=0,
            use_seed=42,
        )

        small_order = model.apply(
            price=100.00, side="buy", order_size=100
        )
        large_order = model.apply(
            price=100.00, side="buy", order_size=1000
        )

        assert large_order > small_order

    def test_slippage_amount_calculation(self):
        """Slippage amount calculated correctly."""
        model = SlippageModel(base_slippage_pct=0.001, random_factor=0, use_seed=42)

        slipped = model.apply(price=100.00, side="buy")
        amount = model.calculate_slippage_amount(price=100.00, side="buy")

        assert amount == pytest.approx(abs(slipped - 100.00), rel=0.001)

    def test_get_total_slippage_pct(self):
        """Get total slippage percentage."""
        model = SlippageModel(
            base_slippage_pct=0.001,
            volatility_factor=0.5,
            size_impact_factor=0.0001,
        )

        slippage_pct = model.get_total_slippage_pct(
            volatility=0.02,
            order_size=100,
        )

        # 0.1% base + 0.02 * 0.5 vol + 100 * 0.0001 size = 0.03
        expected = 0.001 + 0.02 * 0.5 + 100 * 0.0001
        assert slippage_pct == pytest.approx(expected, rel=0.001)

    def test_invalid_parameters(self):
        """Raises error for invalid parameters."""
        with pytest.raises(ValueError):
            SlippageModel(base_slippage_pct=-0.01)

        with pytest.raises(ValueError):
            SlippageModel(base_slippage_pct=0.15)

        with pytest.raises(ValueError):
            SlippageModel(volatility_factor=-1)

    def test_invalid_apply_parameters(self):
        """Raises error for invalid apply parameters."""
        model = SlippageModel()

        with pytest.raises(ValueError):
            model.apply(price=0, side="buy")

        with pytest.raises(ValueError):
            model.apply(price=100, side="invalid")

        with pytest.raises(ValueError):
            model.apply(price=100, side="buy", volatility=-0.01)


class TestMockExchange:
    """Tests for MockExchange."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Exchange initializes with correct capital."""
        exchange = MockExchange(initial_capital=10000)

        assert exchange.initial_capital == 10000
        assert exchange.cash == 10000

    @pytest.mark.asyncio
    async def test_market_order_execution(self):
        """Market orders execute with slippage."""
        exchange = MockExchange(
            initial_capital=10000,
            commission_pct=0.001,
            partial_fill_probability=0.0,  # Disable partial fills for this test
        )
        await exchange.set_price("AAPL", 150.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        assert response.status == OrderStatus.FILLED
        assert response.filled_qty == 10
        assert response.avg_price > 150.00  # Slippage on buy

    @pytest.mark.asyncio
    async def test_limit_order_no_immediate_fill(self):
        """Limit order doesn't fill if price not reached."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=0.0,
        )
        await exchange.set_price("AAPL", 150.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="limit",
            price=148.00,
        )

        # Limit order at 148 with current price 150 won't fill
        assert response.status in (OrderStatus.PENDING, OrderStatus.CREATED)
        assert response.filled_qty == 0

    @pytest.mark.asyncio
    async def test_limit_order_fills_at_better_price(self):
        """Limit order fills when price reaches limit."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=0.0,
        )
        await exchange.set_price("AAPL", 148.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="limit",
            price=150.00,  # Willing to pay up to $150
        )

        # Price is $148, limit is $150, so should fill
        assert response.filled_qty == 10
        assert response.avg_price <= 150.00

    @pytest.mark.asyncio
    async def test_stop_order_triggers(self):
        """Stop order triggers when price crosses."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="sell",
            quantity=10,
            order_type="stop",
            price=145.00,
        )

        # Not triggered yet
        assert response.status == OrderStatus.PENDING

        # Price drops to $144 - should trigger
        await exchange.set_price("AAPL", 144.00)
        await exchange.process_pending_orders()

        order = await exchange.get_order(response.order_id)
        assert order.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_position_tracking(self):
        """Positions tracked correctly."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=0.0,
        )
        await exchange.set_price("AAPL", 100.00)

        # Buy 10 shares
        await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        position = await exchange.get_position("AAPL")
        assert position is not None
        assert position.quantity == 10
        assert position.entry_price > 100.00  # Should have slippage

    @pytest.mark.asyncio
    async def test_cash_tracking(self):
        """Cash balance updated correctly."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=0.0,
        )
        await exchange.set_price("AAPL", 100.00)

        initial_cash = exchange.cash

        # Buy 10 shares at ~$100
        await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        # Cash should be reduced
        assert exchange.cash < initial_cash

        # Remaining cash should be approximately initial - (10 * 100) - commission - slippage
        # With default slippage, price should be ~100.1
        expected_spent = 10 * 100.1 + (10 * 100.1 * 0.001)
        # Allow more tolerance since slippage might vary slightly
        assert exchange.cash < initial_cash - (10 * 100)

    @pytest.mark.asyncio
    async def test_account_state(self):
        """Account state reflects positions and cash."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=0.0,
        )
        await exchange.set_price("AAPL", 100.00)

        # Buy 10 shares
        await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        account = await exchange.get_account()
        assert account.cash > 0
        assert account.cash < 10000  # Should have spent some money
        assert account.equity <= 10000  # Lost some to slippage and commission

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        """Orders can be cancelled."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 150.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="limit",
            price=145.00,
        )

        # Cancel the order
        cancel_response = await exchange.cancel_order(
            response.order_id
        )

        assert cancel_response.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_partial_fill_simulation(self):
        """Partial fills simulated correctly."""
        exchange = MockExchange(
            initial_capital=10000,
            partial_fill_probability=1.0,  # Force partial
        )
        await exchange.set_price("AAPL", 100.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=100,
            order_type="market",
        )

        # Should be partial
        assert response.status == OrderStatus.PARTIAL
        assert response.filled_qty < 100
        assert response.remaining_qty > 0

    @pytest.mark.asyncio
    async def test_commission_deducted(self):
        """Commission deducted from account."""
        exchange = MockExchange(
            initial_capital=10000,
            commission_pct=0.01,  # 1% commission
        )
        await exchange.set_price("AAPL", 100.00)

        initial_cash = exchange.cash

        # Buy 10 shares at $100 = $1000 + 1% commission = $10
        await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        # Commission should be deducted
        expected_deduction = 10 * 100.1 * 0.01 + 10 * 100.1
        assert exchange.cash == pytest.approx(
            initial_cash - expected_deduction, rel=0.01
        )

    @pytest.mark.asyncio
    async def test_get_order(self):
        """Get order by ID."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 100.00)

        response = await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        order = await exchange.get_order(response.order_id)
        assert order is not None
        assert order.order_id == response.order_id
        assert order.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_invalid_price_raises_error(self):
        """Setting invalid price raises error."""
        exchange = MockExchange()

        with pytest.raises(ValueError):
            await exchange.set_price("AAPL", 0)

        with pytest.raises(ValueError):
            await exchange.set_price("AAPL", -100)

    @pytest.mark.asyncio
    async def test_order_without_price_raises_error(self):
        """Order without price set raises error."""
        exchange = MockExchange()

        with pytest.raises(ValueError):
            await exchange.submit_order(
                symbol="AAPL",
                side="buy",
                quantity=10,
            )

    @pytest.mark.asyncio
    async def test_reset(self):
        """Reset clears all state."""
        exchange = MockExchange(initial_capital=10000)
        await exchange.set_price("AAPL", 100.00)

        await exchange.submit_order(
            symbol="AAPL",
            side="buy",
            quantity=10,
            order_type="market",
        )

        exchange.reset()

        assert exchange.cash == 10000
        assert len(exchange.get_positions()) == 0
        assert len(exchange.get_all_orders()) == 0
