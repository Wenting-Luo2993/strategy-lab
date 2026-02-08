"""
Slippage model for realistic market execution simulation.
Implements base slippage with volatility and size impact factors.
"""

import random
from typing import Optional


class SlippageModel:
    """
    Simulates realistic market slippage based on various factors.

    Slippage = base_slippage + volatility_adjustment + size_impact + random_component

    Factors:
    - Base slippage: Fixed percentage (e.g., 0.05%)
    - Volatility: Higher volatility increases slippage
    - Size impact: Larger orders have worse fills
    - Direction: Buy orders slip up, sell orders slip down
    - Random component: Adds realism
    """

    def __init__(
        self,
        base_slippage_pct: float = 0.0005,
        volatility_factor: float = 1.0,
        size_impact_factor: float = 0.00001,
        random_factor: float = 0.0001,
        use_seed: Optional[int] = None,
    ):
        """
        Initialize slippage model with configuration.

        Args:
            base_slippage_pct: Base slippage percentage (default 0.05%)
            volatility_factor: Multiplier for volatility impact
            size_impact_factor: Impact per share (e.g., 0.00001 = 0.001% per 100 shares)
            random_factor: Range for random component
            use_seed: Optional seed for reproducibility

        Raises:
            ValueError: If parameters are invalid
        """
        if base_slippage_pct < 0 or base_slippage_pct > 0.1:
            raise ValueError(
                "base_slippage_pct must be between 0 and 0.1 (0-10%)"
            )
        if volatility_factor < 0:
            raise ValueError("volatility_factor must be non-negative")
        if size_impact_factor < 0:
            raise ValueError("size_impact_factor must be non-negative")
        if random_factor < 0:
            raise ValueError("random_factor must be non-negative")

        self.base_slippage_pct = base_slippage_pct
        self.volatility_factor = volatility_factor
        self.size_impact_factor = size_impact_factor
        self.random_factor = random_factor

        if use_seed is not None:
            random.seed(use_seed)

    def apply(
        self,
        price: float,
        side: str,
        volatility: float = 0.02,
        order_size: float = 100,
    ) -> float:
        """
        Apply slippage to a price based on market conditions.

        Args:
            price: Current market price
            side: Order side ('buy' or 'sell')
            volatility: Current volatility (annualized, e.g., 0.02 = 2%)
            order_size: Order size in shares

        Returns:
            Slipped price (worse for the trader)

        Raises:
            ValueError: If parameters are invalid
        """
        if price <= 0:
            raise ValueError("price must be positive")
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if volatility < 0:
            raise ValueError("volatility must be non-negative")
        if order_size <= 0:
            raise ValueError("order_size must be positive")

        # Calculate total slippage as percentage
        slippage_pct = self.base_slippage_pct

        # Add volatility component
        if volatility > 0:
            vol_adjustment = volatility * self.volatility_factor
            slippage_pct += vol_adjustment

        # Add size impact component
        size_impact = order_size * self.size_impact_factor
        slippage_pct += size_impact

        # Add random component
        if self.random_factor > 0:
            random_adjustment = random.uniform(
                -self.random_factor, self.random_factor
            )
            slippage_pct += random_adjustment

        # Ensure slippage doesn't go negative
        slippage_pct = max(0, slippage_pct)

        # Calculate slipped price based on side
        if side == "buy":
            # Buy orders slip up (worse price - higher)
            slipped_price = price * (1 + slippage_pct)
        else:  # sell
            # Sell orders slip down (worse price - lower)
            slipped_price = price * (1 - slippage_pct)

        return slipped_price

    def calculate_slippage_amount(
        self,
        price: float,
        side: str,
        volatility: float = 0.02,
        order_size: float = 100,
    ) -> float:
        """
        Calculate absolute slippage amount in dollars.

        Args:
            price: Current market price
            side: Order side ('buy' or 'sell')
            volatility: Current volatility
            order_size: Order size in shares

        Returns:
            Absolute slippage amount in dollars
        """
        slipped_price = self.apply(
            price=price,
            side=side,
            volatility=volatility,
            order_size=order_size,
        )
        return abs(slipped_price - price)

    def get_total_slippage_pct(
        self,
        volatility: float = 0.02,
        order_size: float = 100,
    ) -> float:
        """
        Get total slippage percentage (excluding random component).

        Args:
            volatility: Current volatility
            order_size: Order size in shares

        Returns:
            Total slippage percentage
        """
        slippage_pct = self.base_slippage_pct

        if volatility > 0:
            vol_adjustment = volatility * self.volatility_factor
            slippage_pct += vol_adjustment

        size_impact = order_size * self.size_impact_factor
        slippage_pct += size_impact

        return max(0, slippage_pct)
