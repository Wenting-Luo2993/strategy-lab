"""
Execution configuration for the execution simulator.
Bundles together slippage, volume, and impact models with factory methods.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.backtester.core.execution.slippage import SlippageModel
    from vibe.backtester.core.execution.volume import VolumeModel
    from vibe.backtester.core.execution.impact import ImpactModel


@dataclass
class ExecutionConfig:
    """
    Configuration for the ExecutionSimulator.
    
    Bundles slippage, volume, and impact models along with other execution parameters.
    Provides factory methods for common configurations (legacy, realistic).
    """
    
    slippage_model: "SlippageModel"
    volume_model: "VolumeModel"
    impact_model: "ImpactModel"
    latency_bars: int = 0  # Execution delay in bars (0 = immediate)
    adv_window: int = 20  # Days for rolling ADV calculation
    
    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.latency_bars < 0:
            raise ValueError(f"latency_bars must be non-negative, got {self.latency_bars}")
        
        if self.adv_window <= 0:
            raise ValueError(f"adv_window must be positive, got {self.adv_window}")
    
    @staticmethod
    def legacy(slippage_ticks: int = 5) -> "ExecutionConfig":
        """
        Create legacy configuration matching current backtester behavior.
        
        - Fixed tick slippage (no volume/impact effects)
        - Unlimited volume (no liquidity constraints)
        - No market impact
        - Immediate execution (latency_bars=0)
        
        Args:
            slippage_ticks: Number of ticks of slippage (default 5)
            
        Returns:
            ExecutionConfig with legacy settings
        """
        from vibe.backtester.core.execution.slippage import FixedTickSlippage
        from vibe.backtester.core.execution.volume import UnlimitedVolume
        from vibe.backtester.core.execution.impact import NoImpact
        
        return ExecutionConfig(
            slippage_model=FixedTickSlippage(ticks=slippage_ticks),
            volume_model=UnlimitedVolume(),
            impact_model=NoImpact(),
            latency_bars=0,
            adv_window=20,
        )
    
    @staticmethod
    def realistic(
        slippage_k: float = 0.1,
        participation_rate: float = 0.10,
        impact_k: float = 0.1,
        latency_bars: int = 0,
        adv_window: int = 20,
    ) -> "ExecutionConfig":
        """
        Create realistic configuration with all models enabled.
        
        - Square-root volume slippage (k=0.1, capped at 5%)
        - Participation rate volume constraint (10% per bar)
        - Square-root market impact (k=0.1, capped at 5%)
        - Optional latency
        - 20-day rolling ADV window
        
        Args:
            slippage_k: Slippage coefficient (default 0.1)
            participation_rate: Volume participation rate (default 0.10 = 10%)
            impact_k: Impact coefficient (default 0.1)
            latency_bars: Execution delay in bars (default 0)
            adv_window: Days for rolling ADV (default 20)
            
        Returns:
            ExecutionConfig with realistic settings
        """
        from vibe.backtester.core.execution.slippage import SqrtVolumeSlippage
        from vibe.backtester.core.execution.volume import ParticipationRateVolume
        from vibe.backtester.core.execution.impact import SqrtImpact
        
        return ExecutionConfig(
            slippage_model=SqrtVolumeSlippage(k=slippage_k),
            volume_model=ParticipationRateVolume(rate=participation_rate),
            impact_model=SqrtImpact(k=impact_k),
            latency_bars=latency_bars,
            adv_window=adv_window,
        )
