"""
Trade data model for completed trades with P&L calculations.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Trade(BaseModel):
    """
    Represents a completed trade with entry and exit details.
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

    def __init__(self, **data):
        """Initialize Trade and calculate P&L if exit_price is provided."""
        super().__init__(**data)

        # Calculate P&L if not provided and exit_price is set
        if self.pnl is None and self.exit_price is not None:
            self.pnl = (self.exit_price - self.entry_price) * self.quantity

        # Calculate P&L% if not provided and exit_price is set
        if self.pnl_pct is None and self.exit_price is not None:
            if self.entry_price != 0:
                self.pnl_pct = ((self.exit_price - self.entry_price) / self.entry_price) * 100

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
