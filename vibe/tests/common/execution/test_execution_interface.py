"""Tests for Task 0.3: Abstract Execution Interface."""

import pytest

from vibe.common.execution import ExecutionEngine, OrderResponse
from vibe.common.models import OrderStatus


def test_execution_engine_abstract():
    """ExecutionEngine cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ExecutionEngine()


def test_order_response_fields():
    """OrderResponse contains all required fields."""
    response = OrderResponse(
        order_id="123",
        status=OrderStatus.FILLED,
        filled_qty=100,
        avg_price=150.0,
        remaining_qty=0,
    )
    assert response.order_id == "123"
    assert response.status == OrderStatus.FILLED
    assert response.filled_qty == 100
    assert response.avg_price == 150.0
    assert response.remaining_qty == 0


def test_order_response_partial_fill():
    """OrderResponse can represent partial fills."""
    response = OrderResponse(
        order_id="456",
        status=OrderStatus.PARTIAL,
        filled_qty=50,
        avg_price=100.5,
        remaining_qty=50,
    )
    assert response.filled_qty == 50
    assert response.remaining_qty == 50
    assert response.status == OrderStatus.PARTIAL


def test_order_response_pending():
    """OrderResponse can represent pending orders."""
    response = OrderResponse(
        order_id="789",
        status=OrderStatus.PENDING,
        filled_qty=0,
        avg_price=0,
        remaining_qty=100,
    )
    assert response.filled_qty == 0
    assert response.remaining_qty == 100


class ConcreteExecutionEngine(ExecutionEngine):
    """Concrete implementation of ExecutionEngine for testing."""

    async def submit_order(self, symbol, side, quantity, order_type="limit", price=None):
        """Dummy implementation."""
        return OrderResponse(
            order_id="test_order",
            status=OrderStatus.SUBMITTED,
            filled_qty=0,
            avg_price=price or 0,
            remaining_qty=quantity,
        )

    async def cancel_order(self, order_id):
        """Dummy implementation."""
        return OrderResponse(
            order_id=order_id,
            status=OrderStatus.CANCELLED,
            filled_qty=0,
            avg_price=0,
            remaining_qty=0,
        )

    async def get_position(self, symbol):
        """Dummy implementation."""
        return None

    async def get_account(self):
        """Dummy implementation."""
        pass

    async def get_order(self, order_id):
        """Dummy implementation."""
        return None


def test_execution_engine_concrete_implementation():
    """ExecutionEngine can be subclassed and instantiated."""
    engine = ConcreteExecutionEngine()
    assert engine is not None


def test_execution_engine_has_abstract_methods():
    """ExecutionEngine has all required abstract methods."""
    required_methods = [
        "submit_order",
        "cancel_order",
        "get_position",
        "get_account",
        "get_order",
    ]

    for method_name in required_methods:
        assert hasattr(ExecutionEngine, method_name)
