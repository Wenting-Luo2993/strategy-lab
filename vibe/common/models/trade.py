"""
Trade data model for completed trades with P&L calculations.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class Trade(BaseModel):
    """
    Represents a completed trade with entry and exit details.
    Handles both long (buy) and short (sell) positions with automatic P&L calculation.
    """

    trade_id: Optional[str] = Field(default=None, description="Unique trade identifier")
    symbol: str = Field(..., description="Trading symbol")
    side: str = Field(..., description="Trade side: 'buy' or 'sell'")
    quantity: float = Field(..., description="Trade quantity")
    entry_price: float = Field(..., description="Entry price")
    exit_price: Optional[float] = Field(default=None, description="Exit price (None if still open)")
    entry_time: datetime = Field(default_factory=datetime.now, description="Entry time")
    exit_time: Optional[datetime] = Field(default=None, description="Exit time (None if still open)")
    pnl: Optional[float] = Field(default=None, description="Realized P&L")
    pnl_pct: Optional[float] = Field(default=None, description="Realized P&L percentage")
    commission: float = Field(default=0.0, description="Total commission")
    strategy: Optional[str] = Field(default=None, description="Strategy name")

    @field_validator("side")
    @classmethod
    def validate_side(cls, v):
        """Validate that side is either 'buy' or 'sell'."""
        if v not in ("buy", "sell"):
            raise ValueError("Side must be 'buy' or 'sell'")
        return v

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        """Validate that quantity is positive."""
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v

    @field_validator("entry_price")
    @classmethod
    def validate_entry_price(cls, v):
        """Validate that entry_price is positive."""
        if v <= 0:
            raise ValueError("Entry price must be positive")
        return v

    @field_validator("exit_price")
    @classmethod
    def validate_exit_price(cls, v):
        """Validate that exit_price is positive when provided."""
        if v is not None and v <= 0:
            raise ValueError("Exit price must be positive")
        return v

    @model_validator(mode="after")
    def calculate_pnl(self):
        """Calculate P&L and P&L% if exit_price is provided."""
        if self.exit_price is not None:
            # Calculate P&L based on side (buy or sell)
            if self.side == "buy":
                # Long position: profit if exit_price > entry_price
                self.pnl = (self.exit_price - self.entry_price) * self.quantity
            else:  # side == "sell"
                # Short position: profit if exit_price < entry_price
                self.pnl = (self.entry_price - self.exit_price) * self.quantity

            # Calculate P&L percentage
            if self.entry_price != 0:
                if self.side == "buy":
                    self.pnl_pct = ((self.exit_price - self.entry_price) / self.entry_price) * 100
                else:  # side == "sell"
                    self.pnl_pct = ((self.entry_price - self.exit_price) / self.entry_price) * 100

        return self

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "trade_id": "trade_001",
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 100,
                "entry_price": 150.0,
                "exit_price": 160.0,
                "entry_time": "2024-01-15T10:00:00",
                "exit_time": "2024-01-15T11:30:00",
                "pnl": 1000.0,
                "pnl_pct": 6.67,
                "commission": 10.0,
                "strategy": "orb",
            }
        }
