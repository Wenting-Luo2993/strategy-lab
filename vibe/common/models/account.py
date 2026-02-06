"""
Account state data model for tracking account information.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class AccountState(BaseModel):
    """
    Represents the state of a trading account.
    Validates all account values and enforces business rules.
    """

    cash: float = Field(..., description="Available cash balance")
    equity: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Buying power (available for trades)")
    portfolio_value: float = Field(..., description="Total portfolio value")
    total_trades: int = Field(default=0, description="Total number of trades")
    winning_trades: int = Field(default=0, description="Number of winning trades")
    losing_trades: int = Field(default=0, description="Number of losing trades")
    win_rate: float = Field(default=0.0, description="Win rate percentage (0-100)")
    total_pnl: float = Field(default=0.0, description="Total realized P&L")
    timestamp: datetime = Field(default_factory=datetime.now, description="Account state timestamp")

    @field_validator("cash")
    @classmethod
    def validate_cash(cls, v):
        """Validate that cash is non-negative."""
        if v < 0:
            raise ValueError("Cash balance must be non-negative")
        return v

    @field_validator("equity")
    @classmethod
    def validate_equity(cls, v):
        """Validate that equity is non-negative."""
        if v < 0:
            raise ValueError("Equity must be non-negative")
        return v

    @field_validator("buying_power")
    @classmethod
    def validate_buying_power(cls, v):
        """Validate that buying_power is non-negative."""
        if v < 0:
            raise ValueError("Buying power must be non-negative")
        return v

    @field_validator("portfolio_value")
    @classmethod
    def validate_portfolio_value(cls, v):
        """Validate that portfolio_value is non-negative."""
        if v < 0:
            raise ValueError("Portfolio value must be non-negative")
        return v

    @field_validator("total_trades")
    @classmethod
    def validate_total_trades(cls, v):
        """Validate that total_trades is non-negative."""
        if v < 0:
            raise ValueError("Total trades must be non-negative")
        return v

    @field_validator("winning_trades")
    @classmethod
    def validate_winning_trades(cls, v):
        """Validate that winning_trades is non-negative."""
        if v < 0:
            raise ValueError("Winning trades must be non-negative")
        return v

    @field_validator("losing_trades")
    @classmethod
    def validate_losing_trades(cls, v):
        """Validate that losing_trades is non-negative."""
        if v < 0:
            raise ValueError("Losing trades must be non-negative")
        return v

    @field_validator("win_rate")
    @classmethod
    def validate_win_rate(cls, v):
        """Validate that win_rate is between 0 and 100."""
        if v < 0 or v > 100:
            raise ValueError("Win rate must be between 0 and 100 percent")
        return v

    @model_validator(mode="after")
    def validate_trade_consistency(self):
        """Validate that winning_trades and losing_trades sum <= total_trades."""
        if self.winning_trades + self.losing_trades > self.total_trades:
            raise ValueError(
                "Sum of winning and losing trades cannot exceed total trades"
            )
        return self

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
