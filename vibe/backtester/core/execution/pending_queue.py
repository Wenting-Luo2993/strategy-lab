"""
Pending order queue for handling latency in order execution (Task 7: Engine Integration).
Manages orders waiting for execution with configurable latency support.
"""

from dataclasses import dataclass
from typing import Optional
from collections import deque

from vibe.backtester.core.execution.models import Order


@dataclass
class PendingOrderEntry:
    """Wrapper for an order in the pending queue."""
    order: Order
    signal_bar_index: int
    
    def is_eligible(self, current_bar_index: int, latency_bars: int) -> bool:
        """
        Check if order is eligible for execution.
        
        Eligible when: signal_bar_index + latency_bars <= current_bar_index
        
        Args:
            current_bar_index: Current bar index in the event loop
            latency_bars: Configured latency (default 0 for immediate execution)
            
        Returns:
            True if eligible, False otherwise
        """
        return self.signal_bar_index + latency_bars <= current_bar_index
    
    def is_expired(self, current_bar_index: int) -> bool:
        """
        Check if order has expired (not filled within 1 day).
        
        Expires at: signal_bar_index + 1440 (1 day = 1440 bars in 5-min timeframe)
        
        Args:
            current_bar_index: Current bar index in the event loop
            
        Returns:
            True if expired, False otherwise
        """
        expiry_bar = self.signal_bar_index + 1440
        return current_bar_index >= expiry_bar


class PendingOrderQueue:
    """
    Queue for managing orders with latency support.
    
    Handles:
    - Adding orders with signal_bar_index
    - Getting eligible orders based on latency
    - Expiring orders at EOD (bar_index >= 1440)
    - Removing filled orders
    - FIFO processing order
    """
    
    def __init__(self) -> None:
        """Initialize empty pending order queue."""
        self._orders: deque[PendingOrderEntry] = deque()
    
    def add(self, order: Order) -> None:
        """
        Add an order to the queue.
        
        Args:
            order: Order to add (must have signal_bar_index set)
        """
        if order.signal_bar_index < 0:
            raise ValueError(f"signal_bar_index must be non-negative, got {order.signal_bar_index}")
        
        entry = PendingOrderEntry(
            order=order,
            signal_bar_index=order.signal_bar_index,
        )
        self._orders.append(entry)
    
    def get_eligible_orders(self, current_bar_index: int, latency_bars: int = 0) -> list[Order]:
        """
        Get all orders eligible for execution at current bar.
        
        Returns orders where: signal_bar_index + latency_bars <= current_bar_index
        Removes expired orders before returning eligible ones.
        
        Args:
            current_bar_index: Current bar index in the event loop
            latency_bars: Configured latency (default 0 for immediate execution)
            
        Returns:
            List of eligible orders in FIFO order (may be empty)
        """
        # Remove expired orders first
        self.remove_expired_orders(current_bar_index)
        
        # Return orders that are eligible and not expired
        eligible = []
        for entry in self._orders:
            if entry.is_eligible(current_bar_index, latency_bars) and not entry.is_expired(current_bar_index):
                eligible.append(entry.order)
        
        return eligible
    
    def remove_expired_orders(self, current_bar_index: int) -> None:
        """
        Remove all expired orders from queue.
        
        Orders expire at: bar_index >= 1440 (EOD)
        
        Args:
            current_bar_index: Current bar index in the event loop
        """
        # Filter out expired orders
        self._orders = deque(
            entry for entry in self._orders
            if not entry.is_expired(current_bar_index)
        )
    
    def mark_filled(self, order_id: str) -> None:
        """
        Remove an order from queue after it's been filled.
        
        Args:
            order_id: ID of the filled order
        """
        # Remove the order with matching ID
        self._orders = deque(
            entry for entry in self._orders
            if entry.order.id != order_id
        )
    
    def is_empty(self) -> bool:
        """
        Check if queue is empty.
        
        Returns:
            True if no orders in queue, False otherwise
        """
        return len(self._orders) == 0
    
    def __len__(self) -> int:
        """Return number of orders in queue."""
        return len(self._orders)
    
    def __repr__(self) -> str:
        """String representation of queue."""
        return f"PendingOrderQueue(orders={len(self._orders)})"
