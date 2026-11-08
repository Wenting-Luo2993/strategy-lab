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

    requested_size: float = 0.0
    """Size implied by risk alone, before any cap was applied."""

    capped_by: Optional[str] = None
    """Which limit reduced the position, if any.

    One of ``"max_position_size"``, ``"max_position_pct"``,
    ``"buying_power"``, or ``None``. Recorded so a clamp is a measurable
    event rather than a silent change of risk profile: a position cut by
    buying power is no longer risking the configured percentage.
    """

    @property
    def was_capped(self) -> bool:
        return self.capped_by is not None


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
        max_position_pct: Optional[float] = None,
    ):
        """
        Initialize position sizer with risk parameters.

        Args:
            risk_per_trade: Fixed dollar amount to risk per trade (e.g., $100)
            risk_pct: Risk as percentage of account (e.g., 0.01 for 1%)
            max_position_size: Maximum position size in shares
            max_position_pct: Maximum notional position size as percentage of account value
        """
        self.risk_per_trade = risk_per_trade
        self.risk_pct = risk_pct
        self.max_position_size = max_position_size
        self.max_position_pct = max_position_pct

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

        if max_position_pct is not None and (
            max_position_pct <= 0 or max_position_pct > 1
        ):
            raise ValueError("max_position_pct must be between 0 and 1")

    def calculate(
        self,
        entry_price: float,
        stop_price: float,
        account_value: float,
        existing_position_size: float = 0.0,
        buying_power: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size based on risk parameters.

        Uses the specified risk method (fixed dollar or percentage) and
        the stop-loss distance to determine optimal position size.

        Risk-based sizing is unbounded by construction: as the stop tightens,
        the implied size grows without limit. Every cap below exists to bound
        it, and ``buying_power`` is the only one that reflects what the
        account can actually fund.

        Args:
            entry_price: Entry price for the trade
            stop_price: Stop-loss price
            account_value: Current account value
            existing_position_size: Existing position size in shares. Accepted
                for call compatibility but deliberately NOT subtracted from
                buying power: a broker's reported buying power is already net
                of open positions, so doing so would double-count them.
            buying_power: Funds actually available to open the position. When
                supplied, the position is clamped so its notional never
                exceeds it. When ``None``, no affordability check is made and
                the caller is asserting that funding is guaranteed elsewhere.

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
        if buying_power is not None and buying_power < 0:
            raise ValueError("buying_power must be non-negative")

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
        requested_size = position_size
        capped_by: Optional[str] = None

        # Apply maximum position size limit if specified
        if self.max_position_size is not None:
            if position_size > self.max_position_size:
                position_size = self.max_position_size
                capped_by = "max_position_size"
                sizing_method += f" (capped at max {self.max_position_size:.0f} shares)"

        if self.max_position_pct is not None:
            max_notional = account_value * self.max_position_pct
            max_notional_size = max_notional / entry_price
            if position_size > max_notional_size:
                position_size = max_notional_size
                capped_by = "max_position_pct"
                sizing_method += f" (capped at {self.max_position_pct * 100:.0f}% capital)"

        # Affordability is the last and most binding cap: the others express
        # policy, this one expresses what the account can actually fund.
        if buying_power is not None:
            affordable_size = buying_power / entry_price
            if position_size > affordable_size:
                position_size = affordable_size
                capped_by = "buying_power"
                sizing_method += f" (capped by ${buying_power:,.2f} buying power)"

        # Round down to whole shares (no fractional shares)
        position_size = int(position_size)

        # Ensure we have a position
        if position_size < 1:
            position_size = 0

        return PositionSizeResult(
            size=position_size,
            risk_amount=risk_amount,
            requested_size=requested_size,
            capped_by=capped_by,
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
