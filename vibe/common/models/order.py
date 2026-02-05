"""
Order data model and status enumerations.
"""

from enum import IntEnum
from datetime import datetime
from pydantic import BaseModel, Field


class OrderStatus(IntEnum):
    """Order status enumeration with ordering."""

    CREATED = 1
    PENDING = 2
    SUBMITTED = 3
    PARTIAL = 4
    FILLED = 5
    CANCELLED = 6
    REJECTED = 7


class Order(BaseModel):
    """
    Represents a trading order with all its details.
    """

    order_id: str = Field(..., description="Unique order identifier")
    symbol: str = Field(..., description="Trading symbol (e.g., AAPL)")
    side: str = Field(..., description="Order side: 'buy' or 'sell'")
    quantity: float = Field(..., description="Order quantity")
    price: float = Field(..., description="Order price")
    order_type: str = Field(default="limit", description="Order type: limit, market, stop")
    status: OrderStatus = Field(default=OrderStatus.CREATED, description="Order status")
    created_at: datetime = Field(default_factory=datetime.now, description="Order creation time")
    filled_qty: float = Field(default=0.0, description="Quantity filled")
    avg_price: float = Field(default=0.0, description="Average fill price")
    commission: float = Field(default=0.0, description="Commission paid")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "order_id": "12345",
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 100,
                "price": 150.0,
                "order_type": "limit",
                "status": 2,
                "created_at": "2024-01-15T10:00:00",
                "filled_qty": 0,
                "avg_price": 0,
                "commission": 0,
            }
        }
