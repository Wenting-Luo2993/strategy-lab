"""
Volume models for the execution simulator.
Determines the maximum quantity that can be filled in a single bar.
"""

from typing import Protocol


class VolumeModel(Protocol):
    """
    Protocol for volume models.
    Volume models determine liquidity constraints on order fills.
    """
    
    def max_fill_qty(self, order_size: float, bar_volume: float) -> float:
        """
        Calculate maximum fillable quantity for this bar.
        
        Args:
            order_size: Desired order size in shares
            bar_volume: Total bar volume in shares
            
        Returns:
            Maximum quantity that can be filled (0 to order_size)
        """
        ...


class UnlimitedVolume:
    """
    Unlimited volume model — no liquidity constraints.
    Orders always fill completely (current behavior).
    """
    
    def max_fill_qty(self, order_size: float, bar_volume: float) -> float:
        """
        Return full order size (no volume constraint).
        
        Args:
            order_size: Desired order size
            bar_volume: Total bar volume (unused)
            
        Returns:
            Full order_size
        """
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")
        return order_size


class ParticipationRateVolume:
    """
    Participation rate volume model.
    Orders can fill up to a fraction of the bar's total volume.
    
    Max fill = participation_rate * bar_volume
    
    This represents realistic liquidity constraints where we can participate
    in at most rate% of the bar's volume (e.g., 10% = 0.10).
    """
    
    def __init__(self, rate: float = 0.10) -> None:
        """
        Initialize participation rate volume model.
        
        Args:
            rate: Participation rate (0.0 to 1.0, default 0.10 = 10%)
        """
        if rate < 0 or rate > 1.0:
            raise ValueError(f"rate must be in [0, 1], got {rate}")
        self.rate = rate
    
    def max_fill_qty(self, order_size: float, bar_volume: float) -> float:
        """
        Calculate maximum fillable quantity based on participation rate.
        
        Args:
            order_size: Desired order size
            bar_volume: Total bar volume
            
        Returns:
            Minimum of order_size and (rate * bar_volume)
        """
        if order_size <= 0:
            raise ValueError(f"order_size must be positive, got {order_size}")
        if bar_volume < 0:
            raise ValueError(f"bar_volume must be non-negative, got {bar_volume}")
        
        max_available = self.rate * bar_volume
        return min(order_size, max_available)
