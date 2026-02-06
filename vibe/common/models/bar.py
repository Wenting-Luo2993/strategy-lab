"""
OHLCV Bar data model for candlestick data.
"""

from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator


class Bar(BaseModel):
    """
    Represents a candlestick bar (OHLCV - Open, High, Low, Close, Volume).
    """

    timestamp: datetime = Field(..., description="Bar timestamp")
    open: float = Field(..., description="Opening price")
    high: float = Field(..., description="Highest price in bar")
    low: float = Field(..., description="Lowest price in bar")
    close: float = Field(..., description="Closing price")
    volume: float = Field(..., description="Trading volume")

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_prices(cls, v):
        """Validate that prices are positive."""
        if v <= 0:
            raise ValueError("Prices must be positive")
        return v

    @field_validator("volume")
    @classmethod
    def validate_volume(cls, v):
        """Validate that volume is non-negative."""
        if v < 0:
            raise ValueError("Volume must be non-negative")
        return v

    @model_validator(mode="after")
    def validate_ohlc_relationships(self):
        """Validate OHLC relationships: high >= max(open,close) and low <= min(open,close)."""
        # High must be >= both open and close
        if self.high < self.open:
            raise ValueError("High must be >= Open")
        if self.high < self.close:
            raise ValueError("High must be >= Close")

        # Low must be <= both open and close
        if self.low > self.open:
            raise ValueError("Low must be <= Open")
        if self.low > self.close:
            raise ValueError("Low must be <= Close")

        # High must be >= Low
        if self.high < self.low:
            raise ValueError("High must be >= Low")

        return self

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "timestamp": "2024-01-15T10:00:00",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 102.0,
                "volume": 1000000,
            }
        }
