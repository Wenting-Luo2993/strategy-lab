"""
Order data model and status enumerations.
"""

from enum import IntEnum
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


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
    Validates order parameters and enforces business rules.
    """

    order_id: str = Field(..., description="Unique order identifier")
    symbol: str = Field(..., description="Trading symbol (e.g., AAPL)")
    side: str = Field(..., description="Order side: 'buy' or 'sell'")
    quantity: float = Field(..., description="Order quantity")
    price: float = Field(..., description="Order price")
    order_type: str = Field(default="limit", description="Order type: limit, market, stop")
    status: OrderStatus = Field(default=OrderStatus.CREATED, description="Order status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Order creation time",
    )
    filled_qty: float = Field(default=0.0, description="Quantity filled")
    avg_price: float = Field(default=0.0, description="Average fill price")
    commission: float = Field(default=0.0, description="Commission paid")
    execution_id: Optional[str] = Field(default=None, description="Primary broker execution ID")
    execution_ids: List[str] = Field(default_factory=list, description="Distinct broker execution IDs")
    permanent_order_id: Optional[str] = Field(default=None, description="Broker permanent order ID")
    account_id: Optional[str] = Field(default=None, description="Broker account ID")
    trade_currency: Optional[str] = Field(default=None, description="Instrument/trade currency")
    commission_currency: Optional[str] = Field(default=None, description="Broker-reported commission currency")
    decision_at: Optional[datetime] = Field(default=None, description="Immutable order decision timestamp")
    submitted_at: Optional[datetime] = Field(default=None, description="Broker submission timestamp")
    filled_at: Optional[datetime] = Field(default=None, description="Latest fill timestamp")
    benchmark_type: Optional[str] = Field(default=None, description="Execution benchmark type")
    benchmark_price: Optional[float] = Field(default=None, description="Execution benchmark price")
    quote_bid: Optional[float] = None
    quote_ask: Optional[float] = None
    quote_midpoint: Optional[float] = None
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None
    benchmark_version: int = Field(default=1, description="Slippage benchmark definition version")
    benchmark_valid: bool = Field(default=False, description="Whether slippage is valid for analysis")
    executions: List[dict] = Field(default_factory=list, description="One item per distinct broker execution")

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

    @field_validator("order_type")
    @classmethod
    def validate_order_type(cls, v):
        """Validate that order_type is a valid type."""
        valid_types = ("limit", "market", "stop")
        if v not in valid_types:
            raise ValueError(f"Order type must be one of {valid_types}")
        return v

    @field_validator("price")
    @classmethod
    def validate_price(cls, v):
        """Validate that price is positive."""
        if v <= 0:
            raise ValueError("Price must be positive")
        return v

    @field_validator("filled_qty")
    @classmethod
    def validate_filled_qty(cls, v):
        """Validate that filled_qty is non-negative."""
        if v < 0:
            raise ValueError("Filled quantity must be non-negative")
        return v

    @field_validator("avg_price")
    @classmethod
    def validate_avg_price(cls, v):
        """Validate that avg_price is non-negative."""
        if v < 0:
            raise ValueError("Average price must be non-negative")
        return v

    @field_validator("commission")
    @classmethod
    def validate_commission(cls, v):
        """Validate that commission is non-negative."""
        if v < 0:
            raise ValueError("Commission must be non-negative")
        return v

    @model_validator(mode="after")
    def validate_filled_qty_not_exceeds_quantity(self):
        """Validate that filled_qty does not exceed quantity."""
        if self.filled_qty > self.quantity:
            raise ValueError("Filled quantity cannot exceed order quantity")
        return self

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
