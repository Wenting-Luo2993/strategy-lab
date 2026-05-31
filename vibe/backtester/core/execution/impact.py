"""
Market impact models for the execution simulator.
Determines how large orders move the market price against the trader.
"""

from typing import Protocol, Optional
import math


class ImpactModel(Protocol):
    """
    Protocol for market impact models.
    Impact is the price movement caused by the large order itself.
    """
    
    def price_impact(self, order_size: float, bar_volume: float, side: str, 
                     adv: Optional[float] = None) -> float:
        """
        Calculate price impact (always positive; sign applied by caller).
        
        Args:
            order_size: Order quantity in shares
            bar_volume: Current bar volume in shares
            side: "buy" or "sell" (for validation only; impact is always positive)
            adv: Average Daily Volume over trailing window (optional)
            
        Returns:
            Price impact as an absolute price adjustment (always >= 0)
        """
        ...


class NoImpact:
    """
    No market impact model — large orders don't move price.
    Current behavior (baseline).
    """
    
    def price_impact(self, order_size: float, bar_volume: float, side: str,
                     adv: Optional[float] = None) -> float:
        """
        Return zero impact.
        
        Args:
            order_size: Order quantity (unused)
            bar_volume: Current bar volume (unused)
            side: "buy" or "sell" (unused)
            adv: ADV (unused)
            
        Returns:
            0.0 (no impact)
        """
        return 0.0


class SqrtImpact:
    """
    Square-root market impact model.
    Impact = k * sqrt(order_size / ADV)
    
    This represents that price impact is proportional to sqrt of the order size
    relative to typical daily volume. Large orders have convex impact costs.
    
    Capped at max_impact_pct to prevent unrealistic extreme values.
    """
    
    def __init__(self, k: float = 0.1, max_impact_pct: float = 0.05) -> None:
        """
        Initialize sqrt impact model.
        
        Args:
            k: Impact coefficient (0.1 means 10% impact for participation=100%)
            max_impact_pct: Maximum impact as % of price (default 5%)
        """
        if k < 0:
            raise ValueError(f"k must be non-negative, got {k}")
        if max_impact_pct < 0 or max_impact_pct > 1:
            raise ValueError(f"max_impact_pct must be in [0, 1], got {max_impact_pct}")
        
        self.k = k
        self.max_impact_pct = max_impact_pct
    
    def price_impact(self, order_size: float, bar_volume: float, side: str,
                     adv: Optional[float] = None) -> float:
        """
        Calculate square-root impact on price.
        
        Args:
            order_size: Order quantity
            bar_volume: Current bar volume
            side: "buy" or "sell" (for validation)
            adv: Average Daily Volume (default: use bar_volume)
            
        Returns:
            Price impact as absolute dollar amount (always >= 0)
            
        Raises:
            ValueError: If side is invalid or volumes are invalid
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}. Must be 'buy' or 'sell'.")
        
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")
        
        # Use provided ADV, otherwise fall back to bar volume
        denominator = adv if adv is not None else bar_volume
        
        if denominator <= 0:
            raise ValueError(
                f"Cannot calculate sqrt impact with zero ADV/volume. "
                f"adv={adv}, bar_volume={bar_volume}"
            )
        
        # Calculate impact as percentage: k * sqrt(order_size / denominator)
        participation_sqrt = math.sqrt(order_size / denominator)
        impact_pct = min(self.k * participation_sqrt, self.max_impact_pct)
        
        # Impact is always positive (sign applied by caller)
        return impact_pct
