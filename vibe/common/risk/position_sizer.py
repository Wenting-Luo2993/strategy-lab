"""
Position sizing calculator for risk management.
Implements multiple sizing strategies: fixed dollar, percentage-based, and risk-based.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSizeResult:
    """Result from position sizing calculation."""

    size: float
    """Calculated position size (in shares/units)."""

    risk_amount: float
    """Dollar amount at risk for this position."""

    reasoning: str
    """Explanation of sizing decision."""


class PositionSizer:
    """
    Calculates position sizes based on account and risk parameters.

    Supports multiple sizing strategies:
    - Fixed dollar risk per trade
    - Percentage of account per trade
    - Risk-based sizing from stop loss distance
    """

    def __init__(
        self,
        risk_per_trade: Optional[float] = None,
        risk_pct: Optional[float] = None,
        max_position_size: Optional[float] = None,
    ):
        """
        Initialize position sizer with risk parameters.

        Args:
            risk_per_trade: Fixed dollar amount to risk per trade (e.g., $100)
            risk_pct: Risk as percentage of account (e.g., 0.01 for 1%)
            max_position_size: Maximum position size in shares
        """
        self.risk_per_trade = risk_per_trade
        self.risk_pct = risk_pct
        self.max_position_size = max_position_size

        # Validate that at least one risk method is specified
        if risk_per_trade is None and risk_pct is None:
            raise ValueError(
                "Must specify either risk_per_trade or risk_pct"
            )

        if (
            risk_per_trade is not None
            and risk_pct is not None
        ):
            raise ValueError(
                "Cannot specify both risk_per_trade and risk_pct"
            )

        if risk_per_trade is not None and risk_per_trade <= 0:
            raise ValueError("risk_per_trade must be positive")

        if risk_pct is not None and (
            risk_pct <= 0 or risk_pct > 1
        ):
            raise ValueError("risk_pct must be between 0 and 1")

        if (
            max_position_size is not None
            and max_position_size <= 0
        ):
            raise ValueError("max_position_size must be positive")

    def calculate(
        self,
        entry_price: float,
        stop_price: float,
        account_value: float,
        existing_position_size: float = 0.0,
    ) -> PositionSizeResult:
        """
        Calculate position size based on risk parameters.

        Uses the specified risk method (fixed dollar or percentage) and
        the stop-loss distance to determine optimal position size.

        Args:
            entry_price: Entry price for the trade
            stop_price: Stop-loss price
            account_value: Current account value
            existing_position_size: Existing position size to account for
                (prevents over-leverage)

        Returns:
            PositionSizeResult with calculated size and details

        Raises:
            ValueError: If parameters are invalid
        """
        # Validate inputs
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        if account_value <= 0:
            raise ValueError("account_value must be positive")
        if existing_position_size < 0:
            raise ValueError("existing_position_size must be non-negative")

        # Calculate stop loss distance
        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0:
            raise ValueError(
                "stop_price cannot equal entry_price"
            )

        # Determine risk amount based on selected method
        if self.risk_per_trade is not None:
            risk_amount = self.risk_per_trade
            sizing_method = f"fixed ${risk_amount:.2f}"
        else:  # risk_pct is not None
            risk_amount = account_value * self.risk_pct
            sizing_method = f"{self.risk_pct * 100:.1f}% of account (${risk_amount:.2f})"

        # Calculate position size: position_size = risk_amount / stop_distance
        position_size = risk_amount / stop_distance

        # Apply maximum position size limit if specified
        if self.max_position_size is not None:
            if position_size > self.max_position_size:
                position_size = self.max_position_size
                sizing_method += f" (capped at max {self.max_position_size:.0f} shares)"

        # Round down to whole shares (no fractional shares)
        position_size = int(position_size)

        # Ensure we have a position
        if position_size < 1:
            position_size = 0

        return PositionSizeResult(
            size=position_size,
            risk_amount=risk_amount,
            reasoning=(
                f"Risk: {sizing_method}, "
                f"Stop distance: ${stop_distance:.2f}, "
                f"Position: {position_size:.0f} shares"
            ),
        )

    def calculate_from_risk_amount(
        self,
        risk_amount: float,
        stop_distance: float,
    ) -> PositionSizeResult:
        """
        Calculate position size from explicit risk amount and stop distance.

        Args:
            risk_amount: Dollar amount to risk
            stop_distance: Stop-loss distance in dollars

        Returns:
            PositionSizeResult with calculated size
        """
        if risk_amount <= 0:
            raise ValueError("risk_amount must be positive")
        if stop_distance <= 0:
            raise ValueError("stop_distance must be positive")

        position_size = risk_amount / stop_distance

        # Apply maximum position size limit
        if self.max_position_size is not None:
            position_size = min(position_size, self.max_position_size)

        position_size = int(position_size)

        return PositionSizeResult(
            size=position_size,
            risk_amount=risk_amount,
            reasoning=(
                f"Risk: ${risk_amount:.2f}, "
                f"Stop distance: ${stop_distance:.2f}, "
                f"Position: {position_size:.0f} shares"
            ),
        )
