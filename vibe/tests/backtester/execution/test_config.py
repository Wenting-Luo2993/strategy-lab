"""
Unit tests for ExecutionConfig.
"""

import pytest

from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.slippage import FixedTickSlippage, SqrtVolumeSlippage
from vibe.backtester.core.execution.volume import UnlimitedVolume, ParticipationRateVolume
from vibe.backtester.core.execution.impact import NoImpact, SqrtImpact


class TestExecutionConfigLegacy:
    """Test ExecutionConfig.legacy() factory."""
    
    def test_legacy_config_defaults(self):
        """Test that legacy config has correct defaults."""
        config = ExecutionConfig.legacy()
        
        assert isinstance(config.slippage_model, FixedTickSlippage)
        assert isinstance(config.volume_model, UnlimitedVolume)
        assert isinstance(config.impact_model, NoImpact)
        assert config.latency_bars == 0
        assert config.adv_window == 20
    
    def test_legacy_config_slippage_ticks_default(self):
        """Test that legacy uses 5 ticks by default."""
        config = ExecutionConfig.legacy()
        
        assert config.slippage_model.ticks == 5
    
    def test_legacy_config_slippage_ticks_custom(self):
        """Test that legacy accepts custom slippage_ticks."""
        config = ExecutionConfig.legacy(slippage_ticks=10)
        
        assert config.slippage_model.ticks == 10
    
    def test_legacy_matches_current_behavior(self):
        """Test that legacy config produces no volume/impact constraints."""
        config = ExecutionConfig.legacy()
        
        # Unlimited volume: fills everything
        max_qty = config.volume_model.max_fill_qty(100, 1000)
        assert max_qty == 100
        
        # No impact: zero cost
        impact = config.impact_model.price_impact(100, 1000, "buy")
        assert impact == 0.0


class TestExecutionConfigRealistic:
    """Test ExecutionConfig.realistic() factory."""
    
    def test_realistic_config_defaults(self):
        """Test that realistic config has correct defaults."""
        config = ExecutionConfig.realistic()
        
        assert isinstance(config.slippage_model, SqrtVolumeSlippage)
        assert isinstance(config.volume_model, ParticipationRateVolume)
        assert isinstance(config.impact_model, SqrtImpact)
        assert config.latency_bars == 0
        assert config.adv_window == 20
    
    def test_realistic_config_default_parameters(self):
        """Test that realistic uses expected default parameters."""
        config = ExecutionConfig.realistic()
        
        assert config.slippage_model.k == 0.1
        assert config.volume_model.rate == 0.10
        assert config.impact_model.k == 0.1
    
    def test_realistic_config_custom_slippage(self):
        """Test that realistic accepts custom slippage_k."""
        config = ExecutionConfig.realistic(slippage_k=0.2)
        
        assert config.slippage_model.k == 0.2
    
    def test_realistic_config_custom_participation(self):
        """Test that realistic accepts custom participation_rate."""
        config = ExecutionConfig.realistic(participation_rate=0.20)
        
        assert config.volume_model.rate == 0.20
    
    def test_realistic_config_custom_impact(self):
        """Test that realistic accepts custom impact_k."""
        config = ExecutionConfig.realistic(impact_k=0.2)
        
        assert config.impact_model.k == 0.2
    
    def test_realistic_config_custom_latency(self):
        """Test that realistic accepts custom latency_bars."""
        config = ExecutionConfig.realistic(latency_bars=2)
        
        assert config.latency_bars == 2
    
    def test_realistic_config_custom_adv_window(self):
        """Test that realistic accepts custom adv_window."""
        config = ExecutionConfig.realistic(adv_window=30)
        
        assert config.adv_window == 30
    
    def test_realistic_has_realistic_constraints(self):
        """Test that realistic config actually imposes constraints."""
        config = ExecutionConfig.realistic()
        
        # Participation rate limits fills
        max_qty = config.volume_model.max_fill_qty(200_000, 1_000_000)
        assert max_qty < 200_000  # 10% participation = 100k max
        
        # Impact is non-zero
        impact = config.impact_model.price_impact(10_000, 1_000_000, "buy", adv=1_000_000)
        assert impact > 0.0


class TestExecutionConfigValidation:
    """Test ExecutionConfig validation."""
    
    def test_config_rejects_negative_latency_bars(self):
        """Test that negative latency_bars raises ValueError."""
        with pytest.raises(ValueError, match="latency_bars must be non-negative"):
            ExecutionConfig(
                slippage_model=FixedTickSlippage(),
                volume_model=UnlimitedVolume(),
                impact_model=NoImpact(),
                latency_bars=-1,
            )
    
    def test_config_rejects_zero_adv_window(self):
        """Test that zero adv_window raises ValueError."""
        with pytest.raises(ValueError, match="adv_window must be positive"):
            ExecutionConfig(
                slippage_model=FixedTickSlippage(),
                volume_model=UnlimitedVolume(),
                impact_model=NoImpact(),
                adv_window=0,
            )
    
    def test_config_rejects_negative_adv_window(self):
        """Test that negative adv_window raises ValueError."""
        with pytest.raises(ValueError, match="adv_window must be positive"):
            ExecutionConfig(
                slippage_model=FixedTickSlippage(),
                volume_model=UnlimitedVolume(),
                impact_model=NoImpact(),
                adv_window=-20,
            )
    
    def test_config_accepts_zero_latency(self):
        """Test that zero latency_bars is valid."""
        config = ExecutionConfig(
            slippage_model=FixedTickSlippage(),
            volume_model=UnlimitedVolume(),
            impact_model=NoImpact(),
            latency_bars=0,
        )
        assert config.latency_bars == 0
    
    def test_config_accepts_large_latency(self):
        """Test that large latency_bars is valid."""
        config = ExecutionConfig(
            slippage_model=FixedTickSlippage(),
            volume_model=UnlimitedVolume(),
            impact_model=NoImpact(),
            latency_bars=100,
        )
        assert config.latency_bars == 100


class TestExecutionConfigComparison:
    """Test differences between legacy and realistic configs."""
    
    def test_legacy_vs_realistic_slippage(self):
        """Test that legacy and realistic have different slippage models."""
        legacy = ExecutionConfig.legacy()
        realistic = ExecutionConfig.realistic()
        
        assert type(legacy.slippage_model) == FixedTickSlippage
        assert type(realistic.slippage_model) == SqrtVolumeSlippage
    
    def test_legacy_vs_realistic_volume(self):
        """Test that legacy and realistic have different volume models."""
        legacy = ExecutionConfig.legacy()
        realistic = ExecutionConfig.realistic()
        
        assert type(legacy.volume_model) == UnlimitedVolume
        assert type(realistic.volume_model) == ParticipationRateVolume
    
    def test_legacy_vs_realistic_impact(self):
        """Test that legacy and realistic have different impact models."""
        legacy = ExecutionConfig.legacy()
        realistic = ExecutionConfig.realistic()
        
        assert type(legacy.impact_model) == NoImpact
        assert type(realistic.impact_model) == SqrtImpact
