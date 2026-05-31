"""
Unit tests for slippage models.
"""

import pytest
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from vibe.backtester.core.execution.slippage import FixedTickSlippage, SqrtVolumeSlippage, TICK_SIZE
from vibe.common.models.bar import Bar

ET = ZoneInfo("America/New_York")


def _bar(close=100.0, open_=None, high=None, low=None, volume=1_000_000):
    """Helper to create a test bar."""
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) + 1.0
    if low is None:
        low = min(open_, close) - 1.0
    return Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class TestFixedTickSlippage:
    """Test FixedTickSlippage model."""
    
    def test_fixed_tick_buy_adds_slippage(self):
        """Test that buy orders add positive slippage."""
        model = FixedTickSlippage(ticks=5, tick_size=0.01)
        bar = _bar(close=100.0)
        
        price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        
        assert price == pytest.approx(100.05)
    
    def test_fixed_tick_sell_subtracts_slippage(self):
        """Test that sell orders subtract slippage."""
        model = FixedTickSlippage(ticks=5, tick_size=0.01)
        bar = _bar(close=100.0)
        
        price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        
        assert price == pytest.approx(99.95)
    
    def test_fixed_tick_zero_slippage(self):
        """Test with zero ticks (no slippage)."""
        model = FixedTickSlippage(ticks=0)
        bar = _bar(close=100.0)
        
        buy_price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        sell_price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        
        assert buy_price == 100.0
        assert sell_price == 100.0
    
    def test_fixed_tick_custom_tick_size(self):
        """Test with custom tick size."""
        model = FixedTickSlippage(ticks=10, tick_size=0.001)  # Crypto-like 0.001 tick
        bar = _bar(close=100.0)
        
        price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        
        assert price == pytest.approx(100.010)
    
    def test_fixed_tick_matches_legacy_fill_simulator(self):
        """Test that FixedTickSlippage matches current FillSimulator behavior."""
        # This is important for backward compatibility
        model = FixedTickSlippage(ticks=5, tick_size=0.01)
        bar = _bar(close=100.0, volume=1_000_000)
        
        # Buy should add 5 ticks
        buy_price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        assert buy_price == pytest.approx(100.05)
        
        # Sell should subtract 5 ticks
        sell_price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        assert sell_price == pytest.approx(99.95)
    
    def test_fixed_tick_initialization_rejects_negative_ticks(self):
        """Test that negative ticks raises ValueError."""
        with pytest.raises(ValueError, match="ticks must be non-negative"):
            FixedTickSlippage(ticks=-1)
    
    def test_fixed_tick_initialization_rejects_invalid_tick_size(self):
        """Test that invalid tick_size raises ValueError."""
        with pytest.raises(ValueError, match="tick_size must be positive"):
            FixedTickSlippage(tick_size=0)
        
        with pytest.raises(ValueError, match="tick_size must be positive"):
            FixedTickSlippage(tick_size=-0.01)
    
    def test_fixed_tick_rejects_invalid_side(self):
        """Test that invalid side raises ValueError."""
        model = FixedTickSlippage(ticks=5)
        bar = _bar(close=100.0)
        
        with pytest.raises(ValueError, match="Invalid side"):
            model.calculate(base_price=100.0, side="short", order_size=100, bar=bar)


class TestSqrtVolumeSlippage:
    """Test SqrtVolumeSlippage model."""
    
    def test_sqrt_slippage_buy_adds_slippage(self):
        """Test that buy orders add slippage."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=1_000_000)
        
        price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        
        # Slippage = 0.1 * sqrt(100 / 1_000_000) = 0.1 * 0.01 = 0.001 = 0.1%
        # = 100.0 * 0.001 = 0.1
        expected = 100.0 * (1 + 0.1 * math.sqrt(100 / 1_000_000))
        assert price == pytest.approx(expected)
    
    def test_sqrt_slippage_sell_subtracts_slippage(self):
        """Test that sell orders subtract slippage."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=1_000_000)
        
        price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        
        expected = 100.0 * (1 - 0.1 * math.sqrt(100 / 1_000_000))
        assert price == pytest.approx(expected)
    
    def test_sqrt_slippage_increases_with_order_size(self):
        """Test that slippage increases with larger orders."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=1_000_000)
        
        small_price = model.calculate(base_price=100.0, side="buy", order_size=10, bar=bar)
        large_price = model.calculate(base_price=100.0, side="buy", order_size=1000, bar=bar)
        
        # Large order should have worse fill price (higher for buy)
        assert large_price > small_price
    
    def test_sqrt_slippage_decreases_with_higher_volume(self):
        """Test that slippage decreases with higher bar volume."""
        model = SqrtVolumeSlippage(k=0.1)
        order_size = 100
        
        low_vol_bar = _bar(close=100.0, volume=10_000)
        high_vol_bar = _bar(close=100.0, volume=1_000_000)
        
        low_vol_price = model.calculate(base_price=100.0, side="buy", order_size=order_size, bar=low_vol_bar)
        high_vol_price = model.calculate(base_price=100.0, side="buy", order_size=order_size, bar=high_vol_bar)
        
        # Higher volume = lower slippage = better price for buy (lower)
        assert high_vol_price < low_vol_price
    
    def test_sqrt_slippage_zero_volume_raises(self):
        """Test that zero volume raises ValueError."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=0)  # Zero volume
        
        with pytest.raises(ValueError, match="Cannot calculate sqrt slippage with zero volume"):
            model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
    
    def test_sqrt_slippage_capped_at_max_pct(self):
        """Test that slippage is capped at max_slippage_pct."""
        model = SqrtVolumeSlippage(k=0.5, max_slippage_pct=0.05)  # 5% cap
        bar = _bar(close=100.0, volume=100)  # Very low volume
        
        # Without cap: 0.5 * sqrt(100 / 100) = 0.5 = 50% (absurd)
        # With cap: 0.05 = 5%
        price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        
        # Price should be capped: 100 * (1 + 0.05) = 105
        assert price == pytest.approx(105.0)
    
    def test_sqrt_slippage_zero_k(self):
        """Test with k=0 (no slippage)."""
        model = SqrtVolumeSlippage(k=0.0)
        bar = _bar(close=100.0, volume=1_000_000)
        
        buy_price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        sell_price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        
        assert buy_price == 100.0
        assert sell_price == 100.0
    
    def test_sqrt_slippage_initialization_rejects_negative_k(self):
        """Test that negative k raises ValueError."""
        with pytest.raises(ValueError, match="k must be non-negative"):
            SqrtVolumeSlippage(k=-0.1)
    
    def test_sqrt_slippage_initialization_rejects_invalid_max_slippage_pct(self):
        """Test that invalid max_slippage_pct raises ValueError."""
        with pytest.raises(ValueError, match="max_slippage_pct must be in"):
            SqrtVolumeSlippage(max_slippage_pct=-0.01)
        
        with pytest.raises(ValueError, match="max_slippage_pct must be in"):
            SqrtVolumeSlippage(max_slippage_pct=1.5)
    
    def test_sqrt_slippage_rejects_invalid_side(self):
        """Test that invalid side raises ValueError."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=1_000_000)
        
        with pytest.raises(ValueError, match="Invalid side"):
            model.calculate(base_price=100.0, side="long", order_size=100, bar=bar)
    
    def test_sqrt_slippage_buy_vs_sell_direction(self):
        """Test that buy and sell have opposite slippage direction."""
        model = SqrtVolumeSlippage(k=0.1)
        bar = _bar(close=100.0, volume=1_000_000)
        
        buy_price = model.calculate(base_price=100.0, side="buy", order_size=100, bar=bar)
        sell_price = model.calculate(base_price=100.0, side="sell", order_size=100, bar=bar)
        
        # Buy should have worse (higher) price than base
        assert buy_price > 100.0
        # Sell should have worse (lower) price than base
        assert sell_price < 100.0
        # Symmetry: (buy_price - base) should approximately equal (base - sell_price)
        assert pytest.approx(buy_price - 100.0, rel=1e-6) == pytest.approx(100.0 - sell_price, rel=1e-6)
    
    def test_sqrt_slippage_participation_rate_formula(self):
        """Test the mathematical formula: slippage % = k * sqrt(order_size / volume)."""
        model = SqrtVolumeSlippage(k=0.2)
        bar = _bar(close=100.0, volume=50_000)
        order_size = 2_500
        
        # participation = 2500 / 50000 = 0.05
        # slippage_pct = 0.2 * sqrt(0.05) = 0.2 * 0.2236 = 0.04472
        expected_slippage_pct = 0.2 * math.sqrt(2500 / 50_000)
        expected_price = 100.0 * (1 + expected_slippage_pct)
        
        actual_price = model.calculate(base_price=100.0, side="buy", order_size=order_size, bar=bar)
        
        assert actual_price == pytest.approx(expected_price)
