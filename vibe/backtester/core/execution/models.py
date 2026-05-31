"""
Data models for the execution simulator: Order and Fill.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Order:
    """
    Represents a pending order to be executed.
    
    Attributes:
        id: Unique order identifier
        symbol: Trading symbol (e.g., "QQQ")
        side: "buy" or "sell"
        size: Order size in shares
        order_type: "market" or "limit"
        limit_price: Limit price for limit orders (None for market orders)
        timestamp: Order creation timestamp
        signal_bar_index: Bar index when signal was generated (for latency calculation)
        price_override: If set, use this price directly, skip slippage/impact (for ORB entries)
    """
    
    id: str
    symbol: str
    side: str  # "buy" | "sell"
    size: float
    order_type: str  # "market" | "limit"
    limit_price: Optional[float]
    timestamp: datetime
    signal_bar_index: int
    price_override: Optional[float] = None
    
    def __post_init__(self) -> None:
        """Validate order fields."""
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {self.side}. Must be 'buy' or 'sell'.")
        
        if self.order_type not in ("market", "limit"):
            raise ValueError(f"Invalid order_type: {self.order_type}. Must be 'market' or 'limit'.")
        
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("Limit orders must have a limit_price.")
        
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("Market orders cannot have a limit_price.")
        
        if self.size <= 0:
            raise ValueError(f"Order size must be positive, got {self.size}.")
        
        if self.signal_bar_index < 0:
            raise ValueError(f"signal_bar_index must be non-negative, got {self.signal_bar_index}.")
    
    def remaining(self, filled_qty: float) -> "Order":
        """
        Return new Order representing the unfilled remainder after a partial fill.
        
        Args:
            filled_qty: Quantity that was filled in this bar.
            
        Returns:
            New Order with same ID, reduced size (self.size - filled_qty), all other fields preserved.
            
        Raises:
            ValueError: If filled_qty > self.size.
        """
        if filled_qty > self.size:
            raise ValueError(
                f"filled_qty {filled_qty} exceeds order size {self.size}. "
                f"Cannot create remainder."
            )
        
        return Order(
            id=self.id,
            symbol=self.symbol,
            side=self.side,
            size=self.size - filled_qty,
            order_type=self.order_type,
            limit_price=self.limit_price,
            timestamp=self.timestamp,
            signal_bar_index=self.signal_bar_index,
            price_override=self.price_override,
        )


@dataclass
class Fill:
    """
    Represents an executed fill (partial or full).
    
    Attributes:
        order_id: ID of the order this fill is for
        symbol: Trading symbol
        side: "buy" or "sell"
        price: Execution price
        qty: Quantity executed in this fill
        timestamp: Execution timestamp
        slippage: Price deviation from base price due to slippage (can be positive or negative)
        impact: Price deviation due to market impact (always positive, sign applied by caller)
    """
    
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    timestamp: datetime
    slippage: float = 0.0
    impact: float = 0.0
    
    def __post_init__(self) -> None:
        """Validate fill fields."""
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {self.side}. Must be 'buy' or 'sell'.")
        
        if self.price <= 0:
            raise ValueError(f"Fill price must be positive, got {self.price}.")
        
        if self.qty <= 0:
            raise ValueError(f"Fill quantity must be positive, got {self.qty}.")
