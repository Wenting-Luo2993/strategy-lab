"""
Slippage models for the execution simulator.
Determines how execution price deviates from base price based on order size and liquidity.
"""

from typing import Protocol
import math

from vibe.common.models.bar import Bar

TICK_SIZE = 0.01  # US equity minimum price increment


class SlippageModel(Protocol):
    """
    Protocol for slippage models.
    Slippage is the price deviation between the intended price and actual execution price.
    """
    
    def calculate(self, base_price: float, side: str, order_size: float, bar: Bar) -> float:
        """
        Calculate execution price after applying slippage.
        
        Args:
            base_price: Initial reference price (typically bar close or open)
            side: "buy" or "sell"
            order_size: Order quantity in shares
            bar: Current OHLCV bar
            
        Returns:
            Adjusted price after slippage (for buy: higher than base; for sell: lower than base)
        """
        ...


class FixedTickSlippage:
    """
    Fixed tick-based slippage model.
    Slippage is a constant number of ticks, replicating current behavior.
    
    Example: FixedTickSlippage(ticks=5) means 5 cents on QQQ (tick_size=0.01).
    """
    
    def __init__(self, ticks: int = 5, tick_size: float = 0.01) -> None:
        """
        Initialize fixed tick slippage model.
        
        Args:
            ticks: Number of ticks of slippage (default 5)
            tick_size: Value per tick (default 0.01 for US equities)
        """
        if ticks < 0:
            raise ValueError(f"ticks must be non-negative, got {ticks}")
        if tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {tick_size}")
        
        self.ticks = ticks
        self.tick_size = tick_size
    
    def calculate(self, base_price: float, side: str, order_size: float, bar: Bar) -> float:
        """
        Calculate execution price with fixed tick slippage.
        
        Args:
            base_price: Initial reference price
            side: "buy" or "sell"
            order_size: Order quantity (unused for fixed slippage)
            bar: Current OHLCV bar (unused for fixed slippage)
            
        Returns:
            base_price + slippage for buy, base_price - slippage for sell
        """
        slippage_amount = self.ticks * self.tick_size
        
        if side == "buy":
            return base_price + slippage_amount
        elif side == "sell":
            return base_price - slippage_amount
        else:
            raise ValueError(f"Invalid side: {side}")


class SqrtVolumeSlippage:
    """
    Square-root volume-based slippage model.
    Slippage = k * sqrt(order_size / volume)
    
    This represents that execution price moves away from fair value proportionally
    to the square root of participation rate (order_size / bar_volume).
    
    Capped at max_slippage_pct to prevent unrealistic extreme slippage on low-volume bars.
    """
    
    def __init__(self, k: float = 0.1, max_slippage_pct: float = 0.05) -> None:
        """
        Initialize sqrt volume slippage model.
        
        Args:
            k: Slippage coefficient (0.1 means 10% slippage for participation=100%)
            max_slippage_pct: Maximum slippage as % of base_price (default 5%)
        """
        if k < 0:
            raise ValueError(f"k must be non-negative, got {k}")
        if max_slippage_pct < 0 or max_slippage_pct > 1:
            raise ValueError(f"max_slippage_pct must be in [0, 1], got {max_slippage_pct}")
        
        self.k = k
        self.max_slippage_pct = max_slippage_pct
    
    def calculate(self, base_price: float, side: str, order_size: float, bar: Bar) -> float:
        """
        Calculate execution price with sqrt volume slippage.
        
        Args:
            base_price: Initial reference price
            side: "buy" or "sell"
            order_size: Order quantity
            bar: Current OHLCV bar
            
        Returns:
            base_price adjusted for slippage
            
        Raises:
            ValueError: If bar.volume is zero or negative
        """
        if bar.volume <= 0:
            raise ValueError(
                f"Cannot calculate sqrt slippage with zero volume. "
                f"bar.volume={bar.volume}"
            )
        
        # Calculate slippage as percentage: k * sqrt(order_size / volume)
        participation_sqrt = math.sqrt(order_size / bar.volume)
        slippage_pct = min(self.k * participation_sqrt, self.max_slippage_pct)
        slippage_amount = base_price * slippage_pct
        
        if side == "buy":
            return base_price + slippage_amount
        elif side == "sell":
            return base_price - slippage_amount
        else:
            raise ValueError(f"Invalid side: {side}")
