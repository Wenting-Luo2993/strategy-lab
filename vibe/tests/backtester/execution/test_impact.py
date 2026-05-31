"""
Unit tests for market impact models.
"""

import pytest
import math

from vibe.backtester.core.execution.impact import NoImpact, SqrtImpact


class TestNoImpact:
    """Test NoImpact model."""
    
    def test_no_impact_returns_zero(self):
        """Test that no impact always returns 0."""
        model = NoImpact()
        
        assert model.price_impact(order_size=100, bar_volume=1_000_000, side="buy") == 0.0
        assert model.price_impact(order_size=10_000, bar_volume=1_000_000, side="sell") == 0.0
        assert model.price_impact(order_size=1, bar_volume=1, side="buy") == 0.0
    
    def test_no_impact_ignores_all_parameters(self):
        """Test that no impact ignores all parameters."""
        model = NoImpact()
        
        result1 = model.price_impact(order_size=100, bar_volume=1000, side="buy", adv=10000)
        result2 = model.price_impact(order_size=1000, bar_volume=100, side="sell", adv=None)
        
        assert result1 == 0.0
        assert result2 == 0.0


class TestSqrtImpact:
    """Test SqrtImpact model."""
    
    def test_sqrt_impact_increases_with_order_size(self):
        """Test that impact increases with larger orders."""
        model = SqrtImpact(k=0.1)
        
        small_impact = model.price_impact(order_size=100, bar_volume=1_000_000, side="buy", adv=1_000_000)
        large_impact = model.price_impact(order_size=10_000, bar_volume=1_000_000, side="buy", adv=1_000_000)
        
        assert large_impact > small_impact
    
    def test_sqrt_impact_decreases_with_higher_adv(self):
        """Test that impact decreases with higher ADV."""
        model = SqrtImpact(k=0.1)
        order_size = 1000
        
        low_adv_impact = model.price_impact(order_size=order_size, bar_volume=1000, side="buy", adv=10_000)
        high_adv_impact = model.price_impact(order_size=order_size, bar_volume=1000, side="buy", adv=1_000_000)
        
        assert high_adv_impact < low_adv_impact
    
    def test_sqrt_impact_uses_bar_volume_when_no_adv(self):
        """Test that bar_volume is used as denominator when adv is None."""
        model = SqrtImpact(k=0.1)
        
        # With adv=None, should use bar_volume
        impact_no_adv = model.price_impact(order_size=1000, bar_volume=1_000_000, side="buy", adv=None)
        # With adv=bar_volume, should be identical
        impact_with_adv = model.price_impact(order_size=1000, bar_volume=1_000_000, side="buy", adv=1_000_000)
        
        assert impact_no_adv == pytest.approx(impact_with_adv)
    
    def test_sqrt_impact_zero_adv_raises(self):
        """Test that zero ADV raises ValueError."""
        model = SqrtImpact(k=0.1)
        
        with pytest.raises(ValueError, match="Cannot calculate sqrt impact with zero ADV"):
            model.price_impact(order_size=1000, bar_volume=1000, side="buy", adv=0)
    
    def test_sqrt_impact_zero_volume_no_adv_raises(self):
        """Test that zero bar_volume with no adv raises ValueError."""
        model = SqrtImpact(k=0.1)
        
        with pytest.raises(ValueError, match="Cannot calculate sqrt impact with zero ADV"):
            model.price_impact(order_size=1000, bar_volume=0, side="buy", adv=None)
    
    def test_sqrt_impact_capped_at_max_pct(self):
        """Test that impact is capped at max_impact_pct."""
        model = SqrtImpact(k=0.5, max_impact_pct=0.05)  # 5% cap
        
        # Without cap: 0.5 * sqrt(100_000 / 100_000) = 0.5 = 50% (absurd)
        # With cap: 0.05 = 5%
        impact = model.price_impact(order_size=100_000, bar_volume=100_000, side="buy", adv=100_000)
        
        assert impact == pytest.approx(0.05)
    
    def test_sqrt_impact_zero_k(self):
        """Test with k=0 (no impact)."""
        model = SqrtImpact(k=0.0)
        
        impact = model.price_impact(order_size=10_000, bar_volume=1_000_000, side="buy")
        assert impact == 0.0
    
    def test_sqrt_impact_initialization_rejects_negative_k(self):
        """Test that negative k raises ValueError."""
        with pytest.raises(ValueError, match="k must be non-negative"):
            SqrtImpact(k=-0.1)
    
    def test_sqrt_impact_initialization_rejects_invalid_max_pct(self):
        """Test that invalid max_impact_pct raises ValueError."""
        with pytest.raises(ValueError, match="max_impact_pct must be in"):
            SqrtImpact(max_impact_pct=-0.01)
        
        with pytest.raises(ValueError, match="max_impact_pct must be in"):
            SqrtImpact(max_impact_pct=1.5)
    
    def test_sqrt_impact_rejects_invalid_side(self):
        """Test that invalid side raises ValueError."""
        model = SqrtImpact(k=0.1)
        
        with pytest.raises(ValueError, match="Invalid side"):
            model.price_impact(order_size=1000, bar_volume=1_000_000, side="long")
    
    def test_sqrt_impact_rejects_zero_order_size(self):
        """Test that zero order size raises ValueError."""
        model = SqrtImpact(k=0.1)
        
        with pytest.raises(ValueError, match="order_size must be positive"):
            model.price_impact(order_size=0, bar_volume=1_000_000, side="buy")
    
    def test_sqrt_impact_rejects_negative_order_size(self):
        """Test that negative order size raises ValueError."""
        model = SqrtImpact(k=0.1)
        
        with pytest.raises(ValueError, match="order_size must be positive"):
            model.price_impact(order_size=-1000, bar_volume=1_000_000, side="buy")
    
    def test_sqrt_impact_always_positive(self):
        """Test that impact is always non-negative."""
        model = SqrtImpact(k=0.1)
        
        impact_buy = model.price_impact(order_size=1000, bar_volume=1_000_000, side="buy")
        impact_sell = model.price_impact(order_size=1000, bar_volume=1_000_000, side="sell")
        
        assert impact_buy >= 0.0
        assert impact_sell >= 0.0
        # Both should be identical (side doesn't affect magnitude)
        assert impact_buy == impact_sell
    
    def test_sqrt_impact_formula(self):
        """Test the mathematical formula: impact % = k * sqrt(order_size / adv)."""
        model = SqrtImpact(k=0.2)
        order_size = 2500
        adv = 50_000
        
        # impact_pct = 0.2 * sqrt(2500 / 50000) = 0.2 * sqrt(0.05) = 0.2 * 0.2236 = 0.04472
        expected = 0.2 * math.sqrt(2500 / 50_000)
        actual = model.price_impact(order_size=order_size, bar_volume=1000, side="buy", adv=adv)
        
        assert actual == pytest.approx(expected)
