"""
Position data model for tracking open positions.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class Position(BaseModel):
    """
    Represents an open trading position.
    """

    symbol: str = Field(..., description="Trading symbol")
    side: str = Field(..., description="Position side: 'long' or 'short'")
    quantity: float = Field(..., description="Position quantity")
    entry_price: float = Field(..., description="Entry price")
    current_price: float = Field(..., description="Current market price")
    opened_at: datetime = Field(default_factory=datetime.now, description="Position open time")
    pnl: float = Field(default=0.0, description="Unrealized P&L")
    pnl_pct: float = Field(default=0.0, description="Unrealized P&L percentage")

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
