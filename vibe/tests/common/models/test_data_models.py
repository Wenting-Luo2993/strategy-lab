"""Tests for Task 0.2: Shared Data Models."""

import json
from datetime import datetime, timedelta
import pytest
from pydantic import ValidationError

from vibe.common.models import (
    Bar,
    Order,
    OrderStatus,
    Position,
    Trade,
    Signal,
    AccountState,
)


class TestBarModel:
    """Tests for Bar model."""

    def test_bar_creation(self):
        """Test basic Bar creation."""
        bar = Bar(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000000,
        )
        assert bar.open == 100.0
        assert bar.high == 105.0
        assert bar.low == 99.0
        assert bar.close == 102.0
        assert bar.volume == 1000000

    def test_bar_high_low_validation(self):
        """Test that Bar validates high >= low."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=100.0,
                high=99.0,  # Invalid: high < low
                low=100.0,
                close=102.0,
                volume=1000000,
            )

    def test_bar_positive_prices(self):
        """Test that Bar validates prices are positive."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=-100.0,  # Invalid: negative price
                high=105.0,
                low=99.0,
                close=102.0,
                volume=1000000,
            )

    def test_bar_non_negative_volume(self):
        """Test that Bar validates volume is non-negative."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=100.0,
                high=105.0,
                low=99.0,
                close=102.0,
                volume=-1000,  # Invalid: negative volume
            )

    def test_bar_serialization(self):
        """Test Bar serialization to/from JSON."""
        bar = Bar(
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000000,
        )

        # Serialize
        json_str = bar.model_dump_json()
        assert isinstance(json_str, str)

        # Deserialize
        bar_loaded = Bar.model_validate_json(json_str)
        assert bar_loaded.open == bar.open
        assert bar_loaded.close == bar.close


class TestOrderModel:
    """Tests for Order model."""

    def test_order_creation(self):
        """Test basic Order creation."""
        order = Order(
            order_id="123",
            symbol="AAPL",
            side="buy",
            quantity=100,
            price=150.0,
        )
        assert order.order_id == "123"
        assert order.symbol == "AAPL"
        assert order.status == OrderStatus.CREATED

    def test_order_status_enum(self):
        """Test OrderStatus enum has all required states."""
        assert OrderStatus.CREATED.value == 1
        assert OrderStatus.PENDING.value == 2
        assert OrderStatus.SUBMITTED.value == 3
        assert OrderStatus.PARTIAL.value == 4
        assert OrderStatus.FILLED.value == 5
        assert OrderStatus.CANCELLED.value == 6
        assert OrderStatus.REJECTED.value == 7

    def test_order_status_ordering(self):
        """Test OrderStatus values maintain order."""
        assert OrderStatus.PENDING.value < OrderStatus.FILLED.value
        assert OrderStatus.CREATED.value < OrderStatus.CANCELLED.value

    def test_order_with_filled_qty(self):
        """Test Order with filled quantity."""
        order = Order(
            order_id="456",
            symbol="MSFT",
            side="sell",
            quantity=50,
            price=300.0,
            status=OrderStatus.PARTIAL,
            filled_qty=25,
            avg_price=299.5,
        )
        assert order.filled_qty == 25
        assert order.avg_price == 299.5
        assert order.status == OrderStatus.PARTIAL


class TestOrderStatusEnum:
    """Tests for OrderStatus enum."""

    def test_all_statuses_present(self):
        """Test all required order statuses exist."""
        required_statuses = [
            "CREATED",
            "PENDING",
            "SUBMITTED",
            "PARTIAL",
            "FILLED",
            "CANCELLED",
            "REJECTED",
        ]
        for status in required_statuses:
            assert hasattr(OrderStatus, status)


class TestPositionModel:
    """Tests for Position model."""

    def test_position_creation(self):
        """Test basic Position creation."""
        position = Position(
            symbol="AAPL",
            side="long",
            quantity=100,
            entry_price=150.0,
            current_price=152.5,
        )
        assert position.symbol == "AAPL"
        assert position.side == "long"
        assert position.quantity == 100

    def test_position_with_pnl(self):
        """Test Position with P&L calculation."""
        position = Position(
            symbol="AAPL",
            side="long",
            quantity=100,
            entry_price=150.0,
            current_price=155.0,
            pnl=500.0,
            pnl_pct=3.33,
        )
        assert position.pnl == 500.0
        assert position.pnl_pct == 3.33


class TestTradeModel:
    """Tests for Trade model."""

    def test_trade_pnl_calculation(self):
        """Test Trade calculates P&L correctly."""
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
            exit_price=110,
        )
        assert trade.pnl == 100  # (110-100) * 10
        assert trade.pnl_pct == 10.0  # ((110-100)/100) * 100

    def test_trade_pnl_negative(self):
        """Test Trade with negative P&L."""
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
            exit_price=90,
        )
        assert trade.pnl == -100  # (90-100) * 10
        assert trade.pnl_pct == -10.0

    def test_trade_without_exit(self):
        """Test Trade without exit price (still open)."""
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
        )
        assert trade.exit_price is None
        assert trade.pnl is None
        assert trade.pnl_pct is None

    def test_trade_with_commission(self):
        """Test Trade with commission."""
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
            exit_price=110,
            commission=5.0,
        )
        assert trade.commission == 5.0
        assert trade.pnl == 100  # P&L before commission

    def test_trade_serialization(self):
        """Test Trade serialization to/from JSON."""
        trade = Trade(
            trade_id="trade_001",
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
            exit_price=110,
            strategy="orb",
        )

        # Serialize
        json_str = trade.model_dump_json()
        assert isinstance(json_str, str)

        # Deserialize
        trade_loaded = Trade.model_validate_json(json_str)
        assert trade_loaded.trade_id == trade.trade_id
        assert trade_loaded.pnl == trade.pnl


class TestSignalModel:
    """Tests for Signal model."""

    def test_signal_creation(self):
        """Test basic Signal creation."""
        signal = Signal(
            symbol="AAPL",
            side="buy",
            strategy="orb",
        )
        assert signal.symbol == "AAPL"
        assert signal.side == "buy"
        assert signal.strategy == "orb"
        assert signal.strength == 1.0
        assert signal.confidence == 0.5

    def test_signal_with_metadata(self):
        """Test Signal with metadata."""
        signal = Signal(
            symbol="AAPL",
            side="buy",
            strategy="orb",
            metadata={"rsi": 35, "macd_histogram": 0.5},
        )
        assert signal.metadata["rsi"] == 35
        assert signal.metadata["macd_histogram"] == 0.5

    def test_signal_neutral_side(self):
        """Test Signal with neutral side."""
        signal = Signal(
            symbol="AAPL",
            side="neutral",
            strategy="orb",
        )
        assert signal.side == "neutral"


class TestAccountStateModel:
    """Tests for AccountState model."""

    def test_account_state_creation(self):
        """Test basic AccountState creation."""
        account = AccountState(
            cash=5000.0,
            equity=15000.0,
            buying_power=30000.0,
            portfolio_value=15000.0,
        )
        assert account.cash == 5000.0
        assert account.equity == 15000.0
        assert account.portfolio_value == 15000.0

    def test_account_state_with_trades(self):
        """Test AccountState with trade statistics."""
        account = AccountState(
            cash=5000.0,
            equity=15000.0,
            buying_power=30000.0,
            portfolio_value=15000.0,
            total_trades=42,
            winning_trades=28,
            losing_trades=14,
            win_rate=66.67,
            total_pnl=5000.0,
        )
        assert account.total_trades == 42
        assert account.winning_trades == 28
        assert account.losing_trades == 14
        assert account.win_rate == 66.67
        assert account.total_pnl == 5000.0


class TestModelSerializationFunctional:
    """Functional tests for model serialization."""

    def test_trade_json_roundtrip(self):
        """Test Trade JSON serialization roundtrip."""
        trade = Trade(
            trade_id="trade_001",
            symbol="AAPL",
            side="buy",
            quantity=10,
            entry_price=100,
            exit_price=110,
            strategy="orb",
        )

        # Serialize to JSON
        json_str = trade.model_dump_json()

        # Deserialize from JSON
        trade_loaded = Trade.model_validate_json(json_str)

        # Verify equality
        assert trade_loaded.trade_id == trade.trade_id
        assert trade_loaded.symbol == trade.symbol
        assert trade_loaded.side == trade.side
        assert trade_loaded.quantity == trade.quantity
        assert trade_loaded.entry_price == trade.entry_price
        assert trade_loaded.exit_price == trade.exit_price
        assert trade_loaded.pnl == trade.pnl
        assert trade_loaded.strategy == trade.strategy

    def test_bar_dict_conversion(self):
        """Test Bar dict conversion."""
        bar = Bar(
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000000,
        )

        # Convert to dict
        bar_dict = bar.model_dump()

        # Verify all fields are present
        assert "timestamp" in bar_dict
        assert "open" in bar_dict
        assert "high" in bar_dict
        assert "low" in bar_dict
        assert "close" in bar_dict
        assert "volume" in bar_dict
