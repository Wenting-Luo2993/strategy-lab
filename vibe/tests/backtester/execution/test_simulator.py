"""
Unit tests for ExecutionSimulator.
"""

from datetime import datetime, timezone
import pytest

from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.simulator import ExecutionSimulator, Bar
from vibe.backtester.core.execution.config import ExecutionConfig


@pytest.fixture
def legacy_config():
    """Legacy configuration (no slippage/impact)."""
    return ExecutionConfig.legacy()


@pytest.fixture
def realistic_config():
    """Realistic configuration with all models."""
    return ExecutionConfig.realistic()


@pytest.fixture
def bar():
    """Sample market bar."""
    return Bar(
        symbol="QQQ",
        timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        open_price=100.0,
        close_price=100.5,
        high_price=101.0,
        low_price=100.0,
        volume=1_000_000,
    )


@pytest.fixture
def market_buy_order():
    """Sample market buy order."""
    return Order(
        id="order_1",
        symbol="QQQ",
        side="buy",
        size=1000,
        order_type="market",
        limit_price=None,
        timestamp=datetime(2024, 1, 15, 9, 55, 0, tzinfo=timezone.utc),
        signal_bar_index=100,
    )


@pytest.fixture
def market_sell_order():
    """Sample market sell order."""
    return Order(
        id="order_2",
        symbol="QQQ",
        side="sell",
        size=1000,
        order_type="market",
        limit_price=None,
        timestamp=datetime(2024, 1, 15, 9, 55, 0, tzinfo=timezone.utc),
        signal_bar_index=100,
    )


class TestExecutionSimulatorMarketOrders:
    """Test market order execution."""
    
    def test_market_buy_order_basic_fill(self, legacy_config, bar, market_buy_order):
        """Test basic market buy order execution."""
        sim = ExecutionSimulator(legacy_config)
        
        fill = sim.execute_market_order(market_buy_order, bar)
        
        assert fill is not None
        assert fill.order_id == market_buy_order.id
        assert fill.symbol == "QQQ"
        assert fill.side == "buy"
        assert fill.qty == 1000
        assert fill.timestamp == bar.timestamp
    
    def test_market_sell_order_basic_fill(self, legacy_config, bar, market_sell_order):
        """Test basic market sell order execution."""
        sim = ExecutionSimulator(legacy_config)
        
        fill = sim.execute_market_order(market_sell_order, bar)
        
        assert fill is not None
        assert fill.order_id == market_sell_order.id
        assert fill.side == "sell"
        assert fill.qty == 1000
    
    def test_market_order_with_slippage(self, legacy_config, bar, market_buy_order):
        """Test that market orders use configured slippage."""
        sim = ExecutionSimulator(legacy_config)
        
        fill = sim.execute_market_order(market_buy_order, bar)
        
        # Legacy config: 5 tick slippage = 0.05 dollars
        expected_price = bar.close_price + 0.05
        assert fill.price == pytest.approx(expected_price)
    
    def test_market_buy_adds_slippage(self, legacy_config, bar, market_buy_order):
        """Test that buy orders add slippage (price moves against)."""
        sim = ExecutionSimulator(legacy_config)
        
        fill = sim.execute_market_order(market_buy_order, bar)
        
        # Buy: close_price + slippage
        assert fill.price > bar.close_price
    
    def test_market_sell_subtracts_slippage(self, legacy_config, bar, market_sell_order):
        """Test that sell orders subtract slippage (price moves against)."""
        sim = ExecutionSimulator(legacy_config)
        
        fill = sim.execute_market_order(market_sell_order, bar)
        
        # Sell: close_price - slippage
        assert fill.price < bar.close_price
    
    def test_market_order_with_volume_constraint(self, realistic_config, bar, market_buy_order):
        """Test that volume constraints limit fills."""
        sim = ExecutionSimulator(realistic_config)
        
        # Realistic: 10% participation rate
        # bar.volume = 1M, so max fill = 100k
        # order.size = 1000, so full fill
        large_order = Order(
            id="large_order",
            symbol="QQQ",
            side="buy",
            size=200_000,  # More than 10% of volume
            order_type="market",
            limit_price=None,
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        fill = sim.execute_market_order(large_order, bar)
        
        # Should fill at 10% = 100k, not 200k
        assert fill.qty < large_order.size
        assert fill.qty == pytest.approx(100_000)
    
    def test_market_order_full_fill_small_size(self, realistic_config, bar, market_buy_order):
        """Test that small orders fill completely despite realistic config."""
        sim = ExecutionSimulator(realistic_config)
        
        # Order size 1000 is small relative to 1M volume and 10% participation
        fill = sim.execute_market_order(market_buy_order, bar)
        
        assert fill.qty == market_buy_order.size


class TestExecutionSimulatorPriceOverride:
    """Test price override functionality."""
    
    def test_price_override_skips_slippage(self, legacy_config, bar, market_buy_order):
        """Test that price_override bypasses slippage calculation."""
        sim = ExecutionSimulator(legacy_config)
        
        # Create order with override price
        override_order = market_buy_order
        override_order.price_override = 100.0  # Exact close price
        
        fill = sim.execute_market_order(override_order, bar)
        
        # Should use override price (no slippage added)
        assert fill.price == pytest.approx(100.0)
        assert fill.slippage == 0.0
        assert fill.impact == 0.0
    
    def test_price_override_skips_impact(self, realistic_config, bar, market_buy_order):
        """Test that price_override skips market impact."""
        sim = ExecutionSimulator(realistic_config)
        
        override_order = market_buy_order
        override_order.price_override = 100.0
        
        fill = sim.execute_market_order(override_order, bar)
        
        # With realistic config, would have impact
        # But override should skip it
        assert fill.slippage == 0.0
        assert fill.impact == 0.0
    
    def test_price_override_with_volume_constraint(self, realistic_config, bar, market_buy_order):
        """Test that price_override still respects volume constraints."""
        sim = ExecutionSimulator(realistic_config)
        
        large_order = Order(
            id="large_order",
            symbol="QQQ",
            side="buy",
            size=200_000,
            order_type="market",
            limit_price=None,
            timestamp=bar.timestamp,
            signal_bar_index=100,
            price_override=100.0,
        )
        
        fill = sim.execute_market_order(large_order, bar)
        
        # Should still be limited by volume (10% = 100k)
        assert fill.qty < large_order.size
        assert fill.qty == pytest.approx(100_000)


class TestExecutionSimulatorLimitOrders:
    """Test limit order execution."""
    
    def test_limit_buy_order_fills_when_price_below(self, legacy_config, bar):
        """Test limit buy order fills when market price <= limit."""
        sim = ExecutionSimulator(legacy_config)
        
        # Bar low: 100.0, close: 100.5
        limit_buy = Order(
            id="limit_buy",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="limit",
            limit_price=100.3,  # Between low and close
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        fill = sim.execute_order(limit_buy, bar)
        
        assert fill is not None
        assert fill.qty == 1000
        # Should fill at limit price (trader's limit)
        assert fill.price == pytest.approx(limit_buy.limit_price)
    
    def test_limit_buy_order_no_fill_when_above(self, legacy_config, bar):
        """Test limit buy order doesn't fill when market too high."""
        sim = ExecutionSimulator(legacy_config)
        
        # Limit above high price
        limit_buy = Order(
            id="limit_buy",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="limit",
            limit_price=99.0,  # Below all prices in bar
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        fill = sim.execute_order(limit_buy, bar)
        
        assert fill is None
    
    def test_limit_sell_order_fills_when_price_above(self, legacy_config, bar):
        """Test limit sell order fills when market price >= limit."""
        sim = ExecutionSimulator(legacy_config)
        
        # Bar high: 101.0, close: 100.5
        limit_sell = Order(
            id="limit_sell",
            symbol="QQQ",
            side="sell",
            size=1000,
            order_type="limit",
            limit_price=100.7,  # Between close and high
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        fill = sim.execute_order(limit_sell, bar)
        
        assert fill is not None
        assert fill.qty == 1000
        # Should fill at limit price (trader's limit)
        assert fill.price == pytest.approx(limit_sell.limit_price)
    
    def test_limit_sell_order_no_fill_when_below(self, legacy_config, bar):
        """Test limit sell order doesn't fill when market too low."""
        sim = ExecutionSimulator(legacy_config)
        
        # Limit below low price
        limit_sell = Order(
            id="limit_sell",
            symbol="QQQ",
            side="sell",
            size=1000,
            order_type="limit",
            limit_price=102.0,  # Above all prices in bar
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        fill = sim.execute_order(limit_sell, bar)
        
        assert fill is None


class TestExecutionSimulatorErrors:
    """Test error handling."""
    
    def test_none_config_raises(self):
        """Test that None config raises ValueError."""
        with pytest.raises(ValueError, match="config cannot be None"):
            ExecutionSimulator(None)
    
    def test_none_order_raises(self, legacy_config, bar):
        """Test that None order raises ValueError."""
        sim = ExecutionSimulator(legacy_config)
        
        with pytest.raises(ValueError, match="order cannot be None"):
            sim.execute_market_order(None, bar)
    
    def test_none_bar_raises(self, legacy_config, market_buy_order):
        """Test that None bar raises ValueError."""
        sim = ExecutionSimulator(legacy_config)
        
        with pytest.raises(ValueError, match="bar cannot be None"):
            sim.execute_market_order(market_buy_order, None)
    
    def test_invalid_order_type_for_market_execution(self, legacy_config, bar):
        """Test that limit order raises when passed to execute_market_order."""
        sim = ExecutionSimulator(legacy_config)
        
        limit_order = Order(
            id="limit",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="limit",
            limit_price=100.0,
            timestamp=bar.timestamp,
            signal_bar_index=100,
        )
        
        with pytest.raises(ValueError, match="execute_market_order expects market orders"):
            sim.execute_market_order(limit_order, bar)
    
    def test_invalid_side_raises(self, legacy_config, bar):
        """Test that invalid side raises ValueError."""
        sim = ExecutionSimulator(legacy_config)
        
        # Order creation should raise for invalid side
        with pytest.raises(ValueError, match="Invalid side"):
            Order(
                id="bad",
                symbol="QQQ",
                side="long",  # Invalid
                size=1000,
                order_type="market",
                limit_price=None,
                timestamp=bar.timestamp,
                signal_bar_index=100,
            )
    
    def test_invalid_order_type_for_execute_order(self, legacy_config, bar):
        """Test that invalid order_type raises ValueError."""
        # Order creation should raise for invalid order_type
        with pytest.raises(ValueError, match="Invalid order_type"):
            Order(
                id="bad",
                symbol="QQQ",
                side="buy",
                size=1000,
                order_type="stop",  # Invalid
                limit_price=None,
                timestamp=bar.timestamp,
                signal_bar_index=100,
            )
