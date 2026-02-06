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

    def test_bar_high_greater_than_open(self):
        """Test that Bar validates high >= open."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=105.0,
                high=100.0,  # Invalid: high < open
                low=99.0,
                close=102.0,
                volume=1000000,
            )

    def test_bar_high_greater_than_close(self):
        """Test that Bar validates high >= close."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=100.0,
                high=101.0,  # Invalid: high < close
                low=99.0,
                close=102.0,
                volume=1000000,
            )

    def test_bar_low_less_than_open(self):
        """Test that Bar validates low <= open."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=100.0,
                high=105.0,
                low=101.0,  # Invalid: low > open
                close=102.0,
                volume=1000000,
            )

    def test_bar_low_less_than_close(self):
        """Test that Bar validates low <= close."""
        with pytest.raises(ValidationError):
            Bar(
                timestamp=datetime.now(),
                open=100.0,
                high=105.0,
                low=103.0,  # Invalid: low > close
                close=102.0,
                volume=1000000,
            )

    def test_bar_zero_volume(self):
        """Test that Bar accepts zero volume."""
        bar = Bar(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=0,
        )
        assert bar.volume == 0


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

    def test_order_invalid_side(self):
        """Test that Order validates side is 'buy' or 'sell'."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="invalid",
                quantity=100,
                price=150.0,
            )

    def test_order_zero_quantity(self):
        """Test that Order rejects zero quantity."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=0,
                price=150.0,
            )

    def test_order_negative_quantity(self):
        """Test that Order rejects negative quantity."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=-10,
                price=150.0,
            )

    def test_order_invalid_order_type(self):
        """Test that Order validates order_type is valid."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=150.0,
                order_type="invalid_type",
            )

    def test_order_valid_order_types(self):
        """Test that Order accepts valid order types."""
        for order_type in ("limit", "market", "stop"):
            order = Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=150.0,
                order_type=order_type,
            )
            assert order.order_type == order_type

    def test_order_zero_price(self):
        """Test that Order rejects zero price."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=0,
            )

    def test_order_negative_price(self):
        """Test that Order rejects negative price."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=-150.0,
            )

    def test_order_filled_qty_exceeds_quantity(self):
        """Test that filled_qty cannot exceed quantity."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=150.0,
                filled_qty=150,
            )

    def test_order_negative_filled_qty(self):
        """Test that Order rejects negative filled_qty."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=150.0,
                filled_qty=-10,
            )

    def test_order_negative_commission(self):
        """Test that Order rejects negative commission."""
        with pytest.raises(ValidationError):
            Order(
                order_id="789",
                symbol="AAPL",
                side="buy",
                quantity=100,
                price=150.0,
                commission=-5.0,
            )


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
        assert position.pnl_pct == pytest.approx(3.33, abs=0.01)

    def test_position_long_pnl_calculation(self):
        """Test that Position calculates long P&L automatically."""
        position = Position(
            symbol="AAPL",
            side="long",
            quantity=100,
            entry_price=150.0,
            current_price=155.0,
        )
        # Long: profit when current > entry
        assert position.unrealized_pnl == 500.0  # (155-150) * 100
        assert position.unrealized_pnl_pct == pytest.approx(3.33, abs=0.01)
        # Check deprecated fields
        assert position.pnl == position.unrealized_pnl
        assert position.pnl_pct == position.unrealized_pnl_pct

    def test_position_long_loss(self):
        """Test long position loss calculation."""
        position = Position(
            symbol="AAPL",
            side="long",
            quantity=100,
            entry_price=150.0,
            current_price=145.0,
        )
        # Long: loss when current < entry
        assert position.unrealized_pnl == -500.0  # (145-150) * 100
        assert position.unrealized_pnl_pct == pytest.approx(-3.33, abs=0.01)

    def test_position_short_pnl_calculation(self):
        """Test that Position calculates short P&L automatically."""
        position = Position(
            symbol="AAPL",
            side="short",
            quantity=100,
            entry_price=150.0,
            current_price=145.0,
        )
        # Short: profit when current < entry
        assert position.unrealized_pnl == 500.0  # (150-145) * 100
        assert position.unrealized_pnl_pct == pytest.approx(3.33, abs=0.01)

    def test_position_short_loss(self):
        """Test short position loss calculation."""
        position = Position(
            symbol="AAPL",
            side="short",
            quantity=100,
            entry_price=150.0,
            current_price=155.0,
        )
        # Short: loss when current > entry
        assert position.unrealized_pnl == -500.0  # (150-155) * 100
        assert position.unrealized_pnl_pct == pytest.approx(-3.33, abs=0.01)

    def test_position_invalid_side(self):
        """Test that Position validates side is 'long' or 'short'."""
        with pytest.raises(ValidationError):
            Position(
                symbol="AAPL",
                side="invalid",
                quantity=100,
                entry_price=150.0,
                current_price=152.5,
            )

    def test_position_zero_quantity(self):
        """Test that Position rejects zero quantity."""
        with pytest.raises(ValidationError):
            Position(
                symbol="AAPL",
                side="long",
                quantity=0,
                entry_price=150.0,
                current_price=152.5,
            )

    def test_position_negative_quantity(self):
        """Test that Position rejects negative quantity."""
        with pytest.raises(ValidationError):
            Position(
                symbol="AAPL",
                side="long",
                quantity=-100,
                entry_price=150.0,
                current_price=152.5,
            )

    def test_position_zero_entry_price(self):
        """Test that Position rejects zero entry price."""
        with pytest.raises(ValidationError):
            Position(
                symbol="AAPL",
                side="long",
                quantity=100,
                entry_price=0,
                current_price=152.5,
            )

    def test_position_zero_current_price(self):
        """Test that Position rejects zero current price."""
        with pytest.raises(ValidationError):
            Position(
                symbol="AAPL",
                side="long",
                quantity=100,
                entry_price=150.0,
                current_price=0,
            )


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

    def test_trade_short_position_pnl(self):
        """Test Trade P&L calculation for short positions."""
        trade = Trade(
            symbol="AAPL",
            side="sell",
            quantity=10,
            entry_price=100,
            exit_price=90,
        )
        # Short: profit when exit < entry
        assert trade.pnl == 100  # (100-90) * 10
        assert trade.pnl_pct == 10.0  # ((100-90)/100) * 100

    def test_trade_short_position_loss(self):
        """Test Trade loss for short position."""
        trade = Trade(
            symbol="AAPL",
            side="sell",
            quantity=10,
            entry_price=100,
            exit_price=110,
        )
        # Short: loss when exit > entry
        assert trade.pnl == -100  # (100-110) * 10
        assert trade.pnl_pct == -10.0  # ((100-110)/100) * 100

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

    def test_trade_invalid_side(self):
        """Test that Trade validates side is 'buy' or 'sell'."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="invalid",
                quantity=10,
                entry_price=100,
                exit_price=110,
            )

    def test_trade_zero_quantity(self):
        """Test that Trade rejects zero quantity."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=0,
                entry_price=100,
                exit_price=110,
            )

    def test_trade_negative_quantity(self):
        """Test that Trade rejects negative quantity."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=-10,
                entry_price=100,
                exit_price=110,
            )

    def test_trade_zero_entry_price(self):
        """Test that Trade rejects zero entry price."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=10,
                entry_price=0,
                exit_price=110,
            )

    def test_trade_negative_entry_price(self):
        """Test that Trade rejects negative entry price."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=10,
                entry_price=-100,
                exit_price=110,
            )

    def test_trade_zero_exit_price(self):
        """Test that Trade rejects zero exit price."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=10,
                entry_price=100,
                exit_price=0,
            )

    def test_trade_negative_exit_price(self):
        """Test that Trade rejects negative exit price."""
        with pytest.raises(ValidationError):
            Trade(
                symbol="AAPL",
                side="buy",
                quantity=10,
                entry_price=100,
                exit_price=-110,
            )


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

    def test_signal_invalid_side(self):
        """Test that Signal validates side."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="invalid",
                strategy="orb",
            )

    def test_signal_strength_range(self):
        """Test that Signal validates strength is in -1.0 to 1.0 range."""
        # Valid strengths
        for strength in (-1.0, -0.5, 0, 0.5, 1.0):
            signal = Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                strength=strength,
            )
            assert signal.strength == strength

    def test_signal_strength_too_high(self):
        """Test that Signal rejects strength > 1.0."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                strength=1.1,
            )

    def test_signal_strength_too_low(self):
        """Test that Signal rejects strength < -1.0."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                strength=-1.1,
            )

    def test_signal_confidence_range(self):
        """Test that Signal validates confidence is in 0.0 to 1.0 range."""
        # Valid confidences
        for confidence in (0.0, 0.25, 0.5, 0.75, 1.0):
            signal = Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                confidence=confidence,
            )
            assert signal.confidence == confidence

    def test_signal_confidence_too_high(self):
        """Test that Signal rejects confidence > 1.0."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                confidence=1.1,
            )

    def test_signal_confidence_negative(self):
        """Test that Signal rejects negative confidence."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                confidence=-0.1,
            )

    def test_signal_price_validation(self):
        """Test that Signal validates price is positive."""
        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                price=0,
            )

        with pytest.raises(ValidationError):
            Signal(
                symbol="AAPL",
                side="buy",
                strategy="orb",
                price=-100,
            )


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

    def test_account_state_negative_cash(self):
        """Test that AccountState rejects negative cash."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=-1000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
            )

    def test_account_state_negative_equity(self):
        """Test that AccountState rejects negative equity."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=-15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
            )

    def test_account_state_negative_buying_power(self):
        """Test that AccountState rejects negative buying_power."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=-30000.0,
                portfolio_value=15000.0,
            )

    def test_account_state_negative_portfolio_value(self):
        """Test that AccountState rejects negative portfolio_value."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=-15000.0,
            )

    def test_account_state_negative_total_trades(self):
        """Test that AccountState rejects negative total_trades."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                total_trades=-5,
            )

    def test_account_state_negative_winning_trades(self):
        """Test that AccountState rejects negative winning_trades."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                winning_trades=-10,
            )

    def test_account_state_negative_losing_trades(self):
        """Test that AccountState rejects negative losing_trades."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                losing_trades=-5,
            )

    def test_account_state_win_rate_range(self):
        """Test that AccountState validates win_rate is 0-100."""
        # Valid win rates
        for wr in (0.0, 25.0, 50.0, 75.0, 100.0):
            account = AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                win_rate=wr,
            )
            assert account.win_rate == wr

    def test_account_state_win_rate_too_high(self):
        """Test that AccountState rejects win_rate > 100."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                win_rate=101.0,
            )

    def test_account_state_win_rate_negative(self):
        """Test that AccountState rejects negative win_rate."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                win_rate=-1.0,
            )

    def test_account_state_trades_exceed_total(self):
        """Test that winning + losing trades cannot exceed total."""
        with pytest.raises(ValidationError):
            AccountState(
                cash=5000.0,
                equity=15000.0,
                buying_power=30000.0,
                portfolio_value=15000.0,
                total_trades=10,
                winning_trades=7,
                losing_trades=5,  # 7+5=12 > 10
            )


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
