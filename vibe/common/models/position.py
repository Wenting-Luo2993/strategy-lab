"""
Position data model for tracking open positions.
"""

from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator


class Position(BaseModel):
    """
    Represents an open trading position with automatic P&L calculation.
    Handles both long and short positions.
    """

    symbol: str = Field(..., description="Trading symbol")
    side: str = Field(..., description="Position side: 'long' or 'short'")
    quantity: float = Field(..., description="Position quantity")
    entry_price: float = Field(..., description="Entry price")
    current_price: float = Field(..., description="Current market price")
    opened_at: datetime = Field(default_factory=datetime.now, description="Position open time")
    unrealized_pnl: float = Field(default=0.0, description="Unrealized P&L")
    unrealized_pnl_pct: float = Field(default=0.0, description="Unrealized P&L percentage")

    # Keep these for backward compatibility but deprecated
    pnl: float = Field(default=0.0, description="Unrealized P&L (deprecated: use unrealized_pnl)")
    pnl_pct: float = Field(default=0.0, description="Unrealized P&L percentage (deprecated: use unrealized_pnl_pct)")

    @field_validator("side")
    @classmethod
    def validate_side(cls, v):
        """Validate that side is either 'long' or 'short'."""
        if v not in ("long", "short"):
            raise ValueError("Side must be 'long' or 'short'")
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

    @field_validator("current_price")
    @classmethod
    def validate_current_price(cls, v):
        """Validate that current_price is positive."""
        if v <= 0:
            raise ValueError("Current price must be positive")
        return v

    @model_validator(mode="after")
    def calculate_pnl(self):
        """Calculate unrealized P&L and P&L% based on position side."""
        if self.side == "long":
            # Long position: profit if current_price > entry_price
            self.unrealized_pnl = (self.current_price - self.entry_price) * self.quantity
            if self.entry_price != 0:
                self.unrealized_pnl_pct = ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:  # side == "short"
            # Short position: profit if current_price < entry_price
            self.unrealized_pnl = (self.entry_price - self.current_price) * self.quantity
            if self.entry_price != 0:
                self.unrealized_pnl_pct = ((self.entry_price - self.current_price) / self.entry_price) * 100

        # Update deprecated fields for backward compatibility
        self.pnl = self.unrealized_pnl
        self.pnl_pct = self.unrealized_pnl_pct

        return self

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "symbol": "AAPL",
                "side": "long",
                "quantity": 100,
                "entry_price": 150.0,
                "current_price": 152.5,
                "opened_at": "2024-01-15T10:00:00",
                "pnl": 250.0,
                "pnl_pct": 1.67,
            }
        }
