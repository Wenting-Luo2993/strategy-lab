"""
Stop-loss management with support for fixed and trailing stops.
Tracks stop levels per position and detects trigger conditions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class StopLossConfig:
    """Configuration for a stop loss."""

    entry_price: float
    """Entry price for the position."""

    initial_stop: float
    """Initial stop-loss price."""

    stop_type: str = "fixed"
    """Stop type: 'fixed' or 'trailing'."""

    trailing_distance: Optional[float] = None
    """Distance for trailing stops (in dollars)."""

    is_long: bool = True
    """True for long positions, False for short."""

    created_at: datetime = field(default_factory=datetime.now)
    """When the stop was created."""


@dataclass
class StopLossStatus:
    """Current status of a stop loss."""

    current_stop: float
    """Current stop price."""

    triggered: bool
    """Whether stop has been triggered."""

    trigger_price: Optional[float] = None
    """Price at which stop was triggered."""

    trigger_time: Optional[datetime] = None
    """Time when stop was triggered."""


class StopLossManager:
    """
    Manages stop-loss levels for open positions.

    Supports:
    - Fixed stops at a specific price
    - Trailing stops that move with price
    - Multiple positions with independent stops
    """

    def __init__(self):
        """Initialize the stop-loss manager."""
        self._stops: Dict[str, StopLossConfig] = {}
        self._status: Dict[str, StopLossStatus] = {}

    def set_stop(
        self,
        position_id: str,
        entry_price: float,
        stop_price: float,
        is_long: bool = True,
        trailing: bool = False,
        trailing_distance: Optional[float] = None,
    ) -> None:
        """
        Set a stop-loss level for a position.

        Args:
            position_id: Unique identifier for the position
            entry_price: Entry price for the position
            stop_price: Initial stop-loss price
            is_long: True for long positions, False for short
            trailing: Whether this is a trailing stop
            trailing_distance: Distance for trailing stop (in dollars)

        Raises:
            ValueError: If parameters are invalid
        """
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")

        if trailing and trailing_distance is None:
            raise ValueError(
                "trailing_distance required for trailing stops"
            )
        if trailing_distance is not None and trailing_distance <= 0:
            raise ValueError("trailing_distance must be positive")

        # Validate stop relative to entry
        if is_long and stop_price >= entry_price:
            raise ValueError(
                "For long positions, stop_price must be below entry_price"
            )
        if not is_long and stop_price <= entry_price:
            raise ValueError(
                "For short positions, stop_price must be above entry_price"
            )

        # For trailing stops, derive trailing_distance from entry and initial stop
        if trailing and trailing_distance is None:
            trailing_distance = abs(entry_price - stop_price)

        config = StopLossConfig(
            entry_price=entry_price,
            initial_stop=stop_price,
            stop_type="trailing" if trailing else "fixed",
            trailing_distance=trailing_distance,
            is_long=is_long,
        )

        self._stops[position_id] = config
        self._status[position_id] = StopLossStatus(
            current_stop=stop_price,
            triggered=False,
        )

    def update_price(
        self,
        position_id: str,
        current_price: float,
    ) -> Optional[StopLossStatus]:
        """
        Update current price and adjust trailing stops.

        Args:
            position_id: Position identifier
            current_price: Current market price

        Returns:
            Updated StopLossStatus, or None if position not found

        Raises:
            ValueError: If price is invalid
        """
        if position_id not in self._stops:
            return None

        if current_price <= 0:
            raise ValueError("current_price must be positive")

        config = self._stops[position_id]
        status = self._status[position_id]

        # Skip if already triggered
        if status.triggered:
            return status

        # Update trailing stop if applicable
        if config.stop_type == "trailing":
            if config.is_long:
                # For long: stop can only move up
                new_stop = current_price - config.trailing_distance
                if new_stop > status.current_stop:
                    status.current_stop = new_stop
            else:
                # For short: stop can only move down
                new_stop = current_price + config.trailing_distance
                if new_stop < status.current_stop:
                    status.current_stop = new_stop

        return status

    def check_trigger(
        self,
        position_id: str,
        current_price: float,
    ) -> bool:
        """
        Check if stop-loss has been triggered.

        Args:
            position_id: Position identifier
            current_price: Current market price

        Returns:
            True if stop has been triggered, False otherwise

        Raises:
            ValueError: If price is invalid or position not found
        """
        if position_id not in self._stops:
            raise ValueError(f"Unknown position: {position_id}")

        if current_price <= 0:
            raise ValueError("current_price must be positive")

        config = self._stops[position_id]
        status = self._status[position_id]

        # Already triggered
        if status.triggered:
            return True

        # Check trigger condition
        triggered = False
        if config.is_long:
            # For long: triggered if price <= stop
            triggered = current_price <= status.current_stop
        else:
            # For short: triggered if price >= stop
            triggered = current_price >= status.current_stop

        if triggered:
            status.triggered = True
            status.trigger_price = current_price
            status.trigger_time = datetime.now()

        return triggered

    def get_stop(self, position_id: str) -> Optional[float]:
        """
        Get current stop-loss price for a position.

        Args:
            position_id: Position identifier

        Returns:
            Current stop price, or None if position not found
        """
        if position_id not in self._status:
            return None
        return self._status[position_id].current_stop

    def get_status(
        self,
        position_id: str,
    ) -> Optional[StopLossStatus]:
        """
        Get current stop-loss status for a position.

        Args:
            position_id: Position identifier

        Returns:
            StopLossStatus, or None if position not found
        """
        return self._status.get(position_id)

    def remove_stop(self, position_id: str) -> bool:
        """
        Remove a stop-loss (e.g., when position is closed).

        Args:
            position_id: Position identifier

        Returns:
            True if stop was removed, False if not found
        """
        if position_id in self._stops:
            del self._stops[position_id]
            del self._status[position_id]
            return True
        return False

    def get_all_triggered(self) -> Dict[str, StopLossStatus]:
        """
        Get all positions with triggered stops.

        Returns:
            Dictionary of position_id -> StopLossStatus for triggered positions
        """
        return {
            pos_id: status
            for pos_id, status in self._status.items()
            if status.triggered
        }

    def get_all_active(self) -> Dict[str, StopLossStatus]:
        """
        Get all positions with active (not triggered) stops.

        Returns:
            Dictionary of position_id -> StopLossStatus for active positions
        """
        return {
            pos_id: status
            for pos_id, status in self._status.items()
            if not status.triggered
        }
