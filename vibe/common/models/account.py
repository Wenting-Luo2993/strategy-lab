"""
Account state data model for tracking account information.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AccountState(BaseModel):
    """
    Represents the state of a trading account.
    """

    cash: float = Field(..., description="Available cash balance")
    equity: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Buying power (available for trades)")
    portfolio_value: float = Field(..., description="Total portfolio value")
    total_trades: int = Field(default=0, description="Total number of trades")
    winning_trades: int = Field(default=0, description="Number of winning trades")
    losing_trades: int = Field(default=0, description="Number of losing trades")
    win_rate: float = Field(default=0.0, description="Win rate percentage")
    total_pnl: float = Field(default=0.0, description="Total realized P&L")
    timestamp: datetime = Field(default_factory=datetime.now, description="Account state timestamp")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "cash": 5000.0,
                "equity": 15000.0,
                "buying_power": 30000.0,
                "portfolio_value": 15000.0,
                "total_trades": 42,
                "winning_trades": 28,
                "losing_trades": 14,
                "win_rate": 66.67,
                "total_pnl": 5000.0,
                "timestamp": "2024-01-15T16:00:00",
            }
        }
