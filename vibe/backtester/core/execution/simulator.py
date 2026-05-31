"""
Core execution simulator for order fills.
Handles market/limit orders with configurable slippage, volume, and impact models.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging

from vibe.backtester.core.execution.models import Order, Fill
from vibe.backtester.core.execution.config import ExecutionConfig


logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """Market data for a single bar."""
    symbol: str
    timestamp: datetime
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float


class ExecutionSimulator:
    """
    Simulates order execution with configurable models.
    
    Handles:
    - Market order fills at calculated prices
    - Limit order fills when price crosses level
    - Slippage and market impact calculations
    - Volume constraints (partial fills)
    - Price overrides for special cases (e.g., ORB entries)
    """
    
    def __init__(self, config: ExecutionConfig) -> None:
        """
        Initialize simulator with execution configuration.
        
        Args:
            config: ExecutionConfig with models and parameters
        """
        if config is None:
            raise ValueError("config cannot be None")
        
        self.config = config
        self.slippage_model = config.slippage_model
        self.volume_model = config.volume_model
        self.impact_model = config.impact_model
    
    def execute_market_order(
        self,
        order: Order,
        bar: Bar,
        adv: Optional[float] = None,
    ) -> Fill:
        """
        Execute a market order at current bar prices.
        
        Applies slippage, impact, and volume constraints. If price_override is set,
        uses that price and skips slippage/impact calculations.
        
        Args:
            order: Market order to execute (order_type must be "market")
            bar: Current market bar data
            adv: Average Daily Volume (optional, used by impact model)
            
        Returns:
            Fill with execution price and actual quantity filled
            
        Raises:
            ValueError: If order type is not "market", side is invalid, or prices are invalid
        """
        if order is None:
            raise ValueError("order cannot be None")
        if bar is None:
            raise ValueError("bar cannot be None")
        
        if order.order_type != "market":
            raise ValueError(f"execute_market_order expects market orders, got {order.order_type}")
        
        if order.side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {order.side}")
        
        if bar.close_price <= 0:
            raise ValueError(f"Invalid close price: {bar.close_price}")
        
        # Check for price override (skip slippage/impact for special entries)
        if order.price_override is not None:
            if order.price_override <= 0:
                raise ValueError(f"Invalid price_override: {order.price_override}")
            
            # Use override price directly, limited by volume
            max_qty = self.volume_model.max_fill_qty(order.size, bar.volume)
            filled_qty = min(order.size, max_qty)
            
            return Fill(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                price=order.price_override,
                qty=filled_qty,
                timestamp=bar.timestamp,
                slippage=0.0,
                impact=0.0,
            )
        
        # Calculate execution price with slippage (returns final price)
        base_price = bar.close_price
        slippage_price = self.slippage_model.calculate(
            base_price,
            order.side,
            order.size,
            bar
        )
        slippage_amount = abs(slippage_price - base_price)
        
        # Apply market impact (returns percentage)
        impact_pct = self.impact_model.price_impact(
            order.size,
            bar.volume,
            order.side,
            adv=adv
        )
        
        # Calculate final execution price
        # Start with slippage price, then add impact
        if order.side == "buy":
            execution_price = slippage_price + (impact_pct * base_price)
        else:  # sell
            execution_price = slippage_price - (impact_pct * base_price)
        
        # Check volume constraints (partial fills)
        max_qty = self.volume_model.max_fill_qty(order.size, bar.volume)
        filled_qty = min(order.size, max_qty)
        
        return Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            price=execution_price,
            qty=filled_qty,
            timestamp=bar.timestamp,
            slippage=slippage_amount,
            impact=impact_pct,
        )
    
    def execute_order(
        self,
        order: Order,
        bar: Bar,
        adv: Optional[float] = None,
    ) -> Optional[Fill]:
        """
        Execute an order (market or limit).
        
        Market orders fill immediately. Limit orders only fill if price is favorable.
        
        Args:
            order: Order to execute (market or limit)
            bar: Current market bar data
            adv: Average Daily Volume (optional, used by impact model)
            
        Returns:
            Fill if executed, None if limit order not filled
            
        Raises:
            ValueError: If order type is invalid or order data is malformed
        """
        if order is None:
            raise ValueError("order cannot be None")
        
        if order.order_type == "market":
            return self.execute_market_order(order, bar, adv)
        
        elif order.order_type == "limit":
            # Limit order only fills if price is favorable
            if order.limit_price is None or order.limit_price <= 0:
                raise ValueError(f"Limit order requires valid limit_price, got {order.limit_price}")
            
            # Check if limit price is hit
            if order.side == "buy":
                # Buy limit: only fill if market price <= limit price
                if bar.low_price <= order.limit_price:
                    # Execute at the limit price or better
                    execution_price = min(order.limit_price, bar.close_price)
                    max_qty = self.volume_model.max_fill_qty(order.size, bar.volume)
                    filled_qty = min(order.size, max_qty)
                    
                    return Fill(
                        order_id=order.id,
                        symbol=order.symbol,
                        side=order.side,
                        price=execution_price,
                        qty=filled_qty,
                        timestamp=bar.timestamp,
                        slippage=0.0,
                        impact=0.0,
                    )
            else:  # sell
                # Sell limit: only fill if market price >= limit price
                if bar.high_price >= order.limit_price:
                    # Execute at the limit price or better
                    execution_price = max(order.limit_price, bar.close_price)
                    max_qty = self.volume_model.max_fill_qty(order.size, bar.volume)
                    filled_qty = min(order.size, max_qty)
                    
                    return Fill(
                        order_id=order.id,
                        symbol=order.symbol,
                        side=order.side,
                        price=execution_price,
                        qty=filled_qty,
                        timestamp=bar.timestamp,
                        slippage=0.0,
                        impact=0.0,
                    )
            
            # Limit price not hit
            return None
        
        else:
            raise ValueError(f"Invalid order_type: {order.order_type}. Must be 'market' or 'limit'.")
