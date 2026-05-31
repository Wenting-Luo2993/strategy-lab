"""
Unit tests for volume models.
"""

import pytest

from vibe.backtester.core.execution.volume import UnlimitedVolume, ParticipationRateVolume


class TestUnlimitedVolume:
    """Test UnlimitedVolume model."""
    
    def test_unlimited_returns_full_size(self):
        """Test that unlimited always returns order size."""
        model = UnlimitedVolume()
        
        assert model.max_fill_qty(order_size=100, bar_volume=1_000_000) == 100
        assert model.max_fill_qty(order_size=1000, bar_volume=1_000_000) == 1000
    
    def test_unlimited_ignores_volume(self):
        """Test that unlimited ignores bar volume."""
        model = UnlimitedVolume()
        
        # Should return order size regardless of volume
        assert model.max_fill_qty(order_size=100, bar_volume=50) == 100
        assert model.max_fill_qty(order_size=100, bar_volume=50_000) == 100
    
    def test_unlimited_rejects_zero_order_size(self):
        """Test that zero order size raises ValueError."""
        model = UnlimitedVolume()
        
        with pytest.raises(ValueError, match="order_size must be positive"):
            model.max_fill_qty(order_size=0, bar_volume=1_000_000)
    
    def test_unlimited_rejects_negative_order_size(self):
        """Test that negative order size raises ValueError."""
        model = UnlimitedVolume()
        
        with pytest.raises(ValueError, match="order_size must be positive"):
            model.max_fill_qty(order_size=-100, bar_volume=1_000_000)


class TestParticipationRateVolume:
    """Test ParticipationRateVolume model."""
    
    def test_participation_caps_at_rate_times_volume(self):
        """Test that fills are capped at rate * volume."""
        model = ParticipationRateVolume(rate=0.10)  # 10%
        
        # With 1M volume, max fill is 100k
        max_qty = model.max_fill_qty(order_size=200_000, bar_volume=1_000_000)
        assert max_qty == 100_000
    
    def test_participation_fills_full_when_volume_sufficient(self):
        """Test that small orders fill completely."""
        model = ParticipationRateVolume(rate=0.10)
        
        # Order size is smaller than 10% of volume
        max_qty = model.max_fill_qty(order_size=50_000, bar_volume=1_000_000)
        assert max_qty == 50_000
    
    def test_participation_returns_minimum(self):
        """Test that max_fill is min(order_size, rate*volume)."""
        model = ParticipationRateVolume(rate=0.10)
        
        # Scenario 1: order_size < participation limit
        assert model.max_fill_qty(order_size=30_000, bar_volume=1_000_000) == 30_000
        
        # Scenario 2: order_size > participation limit
        assert model.max_fill_qty(order_size=200_000, bar_volume=1_000_000) == 100_000
    
    def test_participation_zero_volume_returns_zero(self):
        """Test that zero volume returns zero fill."""
        model = ParticipationRateVolume(rate=0.10)
        
        max_qty = model.max_fill_qty(order_size=100, bar_volume=0)
        assert max_qty == 0
    
    def test_participation_custom_rate(self):
        """Test custom participation rate."""
        model = ParticipationRateVolume(rate=0.20)  # 20%
        
        max_qty = model.max_fill_qty(order_size=300_000, bar_volume=1_000_000)
        assert max_qty == 200_000  # 20% of 1M
    
    def test_participation_zero_rate(self):
        """Test with zero participation rate (no fills)."""
        model = ParticipationRateVolume(rate=0.0)
        
        max_qty = model.max_fill_qty(order_size=100, bar_volume=1_000_000)
        assert max_qty == 0
    
    def test_participation_full_rate(self):
        """Test with 100% participation rate."""
        model = ParticipationRateVolume(rate=1.0)
        
        max_qty = model.max_fill_qty(order_size=200_000, bar_volume=1_000_000)
        assert max_qty == 200_000  # Entire order fills
    
    def test_participation_initialization_rejects_negative_rate(self):
        """Test that negative rate raises ValueError."""
        with pytest.raises(ValueError, match="rate must be in"):
            ParticipationRateVolume(rate=-0.1)
    
    def test_participation_initialization_rejects_rate_over_100(self):
        """Test that rate > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="rate must be in"):
            ParticipationRateVolume(rate=1.5)
    
    def test_participation_rejects_negative_volume(self):
        """Test that negative bar volume raises ValueError."""
        model = ParticipationRateVolume(rate=0.10)
        
        with pytest.raises(ValueError, match="bar_volume must be non-negative"):
            model.max_fill_qty(order_size=100, bar_volume=-1000)
    
    def test_participation_rejects_zero_order_size(self):
        """Test that zero order size raises ValueError."""
        model = ParticipationRateVolume(rate=0.10)
        
        with pytest.raises(ValueError, match="order_size must be positive"):
            model.max_fill_qty(order_size=0, bar_volume=1_000_000)
