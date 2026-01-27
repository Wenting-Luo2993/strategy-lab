"""
Unit tests for TradeStore module
"""

import pytest
import os
import tempfile
from datetime import datetime, date
from pathlib import Path

from src.data.trade_store import TradeStore


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db') as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def trade_store(temp_db):
    """Create a TradeStore instance with temporary database."""
    store = TradeStore(temp_db)
    yield store
    store.close()
    # Give Windows time to release file handle
    import time
    time.sleep(0.1)


class TestTradeStoreInitialization:
    """Test database initialization and schema creation."""

    def test_creates_database_file(self, temp_db):
        """Test that database file is created."""
        store = TradeStore(temp_db)
        assert os.path.exists(temp_db)
        store.close()

    def test_creates_trades_table(self, trade_store):
        """Test that trades table is created with correct schema."""
        with trade_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
            )
            assert cursor.fetchone() is not None

    def test_creates_indexes(self, trade_store):
        """Test that indexes are created."""
        with trade_store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_trades_%'"
            )
            indexes = [row[0] for row in cursor.fetchall()]

            assert 'idx_trades_timestamp' in indexes
            assert 'idx_trades_symbol' in indexes
            assert 'idx_trades_strategy' in indexes
            assert 'idx_trades_date' in indexes


class TestRecordTrade:
    """Test trade recording functionality."""

    def test_record_simple_trade(self, trade_store):
        """Test recording a basic trade."""
        trade_id = trade_store.record_trade(
            symbol='AAPL',
            side='BUY',
            quantity=100,
            price=150.25
        )

        assert trade_id > 0

        # Verify trade was recorded
        trades = trade_store.get_recent_trades(limit=1)
        assert len(trades) == 1
        assert trades[0]['symbol'] == 'AAPL'
        assert trades[0]['side'] == 'BUY'
        assert trades[0]['quantity'] == 100
        assert trades[0]['price'] == 150.25

    def test_record_trade_with_all_fields(self, trade_store):
        """Test recording a trade with all optional fields."""
        metadata = {'signal': 'breakout', 'stop_loss': 148.50}

        trade_id = trade_store.record_trade(
            symbol='TSLA',
            side='SELL',
            quantity=50,
            price=200.75,
            strategy='ORB',
            pnl=125.50,
            metadata=metadata
        )

        assert trade_id > 0

        # Verify all fields
        trades = trade_store.get_recent_trades(limit=1)
        trade = trades[0]

        assert trade['symbol'] == 'TSLA'
        assert trade['side'] == 'SELL'
        assert trade['quantity'] == 50
        assert trade['price'] == 200.75
        assert trade['strategy'] == 'ORB'
        assert trade['pnl'] == 125.50
        assert trade['metadata'] == metadata

    def test_record_trade_with_custom_timestamp(self, trade_store):
        """Test recording a trade with custom timestamp."""
        custom_time = datetime(2026, 1, 15, 10, 30, 0)

        trade_store.record_trade(
            symbol='AAPL',
            side='BUY',
            quantity=100,
            price=150.00,
            timestamp=custom_time
        )

        trades = trade_store.get_recent_trades(limit=1)
        assert trades[0]['timestamp'] == custom_time.isoformat()

    def test_invalid_side_raises_error(self, trade_store):
        """Test that invalid trade side raises ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            trade_store.record_trade(
                symbol='AAPL',
                side='INVALID',
                quantity=100,
                price=150.00
            )

    def test_negative_quantity_raises_error(self, trade_store):
        """Test that negative quantity raises ValueError."""
        with pytest.raises(ValueError, match="Invalid quantity"):
            trade_store.record_trade(
                symbol='AAPL',
                side='BUY',
                quantity=-100,
                price=150.00
            )

    def test_negative_price_raises_error(self, trade_store):
        """Test that negative price raises ValueError."""
        with pytest.raises(ValueError, match="Invalid price"):
            trade_store.record_trade(
                symbol='AAPL',
                side='BUY',
                quantity=100,
                price=-150.00
            )

    def test_case_insensitive_side(self, trade_store):
        """Test that side is case-insensitive."""
        trade_store.record_trade(
            symbol='AAPL',
            side='buy',  # lowercase
            quantity=100,
            price=150.00
        )

        trades = trade_store.get_recent_trades(limit=1)
        assert trades[0]['side'] == 'BUY'


class TestQueryTrades:
    """Test trade querying functionality."""

    @pytest.fixture
    def populated_store(self, trade_store):
        """Create a store with sample trades."""
        # Add trades for today
        today = datetime.now().date()

        trade_store.record_trade('AAPL', 'BUY', 100, 150.00, 'ORB', 50.00,
                                timestamp=datetime.combine(today, datetime.min.time()))
        trade_store.record_trade('TSLA', 'SELL', 50, 200.00, 'ORB', -25.00,
                                timestamp=datetime.combine(today, datetime.min.time()))
        trade_store.record_trade('AAPL', 'SELL', 100, 151.00, 'ORB', 100.00,
                                timestamp=datetime.combine(today, datetime.min.time()))

        # Add trade for yesterday
        yesterday = datetime.now().date().replace(day=datetime.now().day - 1)
        trade_store.record_trade('MSFT', 'BUY', 200, 300.00, 'MOMENTUM', 75.00,
                                timestamp=datetime.combine(yesterday, datetime.min.time()))

        return trade_store

    def test_get_recent_trades(self, populated_store):
        """Test getting recent trades."""
        trades = populated_store.get_recent_trades(limit=10)

        assert len(trades) == 4
        # Should be in reverse chronological order
        assert trades[0]['symbol'] in ['AAPL', 'TSLA', 'MSFT']

    def test_get_recent_trades_with_limit(self, populated_store):
        """Test limit parameter works."""
        trades = populated_store.get_recent_trades(limit=2)
        assert len(trades) == 2

    def test_get_trades_by_date(self, populated_store):
        """Test getting trades for specific date."""
        today = datetime.now().date()
        trades = populated_store.get_trades_by_date(today)

        assert len(trades) == 3
        assert all(trade['symbol'] in ['AAPL', 'TSLA'] for trade in trades)

    def test_get_trades_by_symbol(self, populated_store):
        """Test getting trades for specific symbol."""
        trades = populated_store.get_trades_by_symbol('AAPL')

        assert len(trades) == 2
        assert all(trade['symbol'] == 'AAPL' for trade in trades)

    def test_get_trades_by_strategy(self, populated_store):
        """Test getting trades for specific strategy."""
        trades = populated_store.get_trades_by_strategy('ORB')

        assert len(trades) == 3
        assert all(trade['strategy'] == 'ORB' for trade in trades)

    def test_get_total_pnl(self, populated_store):
        """Test calculating total P&L."""
        total_pnl = populated_store.get_total_pnl()

        # 50.00 - 25.00 + 100.00 + 75.00 = 200.00
        assert total_pnl == 200.00


class TestDailySummary:
    """Test daily summary statistics."""

    @pytest.fixture
    def summary_store(self, trade_store):
        """Create a store with trades for summary testing."""
        today = datetime.now().date()
        base_time = datetime.combine(today, datetime.min.time())

        # 3 winning trades
        trade_store.record_trade('AAPL', 'BUY', 100, 150.00, 'ORB', 100.00, timestamp=base_time)
        trade_store.record_trade('TSLA', 'BUY', 50, 200.00, 'ORB', 50.00, timestamp=base_time)
        trade_store.record_trade('MSFT', 'BUY', 200, 300.00, 'MOMENTUM', 75.00, timestamp=base_time)

        # 2 losing trades
        trade_store.record_trade('GOOGL', 'SELL', 30, 100.00, 'ORB', -25.00, timestamp=base_time)
        trade_store.record_trade('AMZN', 'SELL', 20, 150.00, 'ORB', -50.00, timestamp=base_time)

        return trade_store

    def test_daily_summary_calculations(self, summary_store):
        """Test daily summary calculates correct statistics."""
        today = datetime.now().date()
        summary = summary_store.get_daily_summary(today)

        assert summary['total_trades'] == 5
        assert summary['total_pnl'] == 150.00  # 100 + 50 + 75 - 25 - 50
        assert summary['gross_profit'] == 225.00  # 100 + 50 + 75
        assert summary['gross_loss'] == -75.00   # -25 + -50
        assert summary['win_rate'] == 60.0        # 3/5 = 60%
        assert summary['total_volume'] == 400.0   # 100 + 50 + 200 + 30 + 20
        assert summary['best_pnl'] == 100.00
        assert summary['worst_pnl'] == -50.00

    def test_daily_summary_symbols(self, summary_store):
        """Test daily summary includes traded symbols."""
        today = datetime.now().date()
        summary = summary_store.get_daily_summary(today)

        assert len(summary['symbols_traded']) == 5
        assert set(summary['symbols_traded']) == {'AAPL', 'TSLA', 'MSFT', 'GOOGL', 'AMZN'}

    def test_daily_summary_best_worst_trades(self, summary_store):
        """Test daily summary includes best and worst trades."""
        today = datetime.now().date()
        summary = summary_store.get_daily_summary(today)

        assert summary['best_trade'] is not None
        assert summary['best_trade']['symbol'] == 'AAPL'
        assert summary['best_trade']['pnl'] == 100.00

        assert summary['worst_trade'] is not None
        assert summary['worst_trade']['symbol'] == 'AMZN'
        assert summary['worst_trade']['pnl'] == -50.00

    def test_daily_summary_no_trades(self, trade_store):
        """Test daily summary with no trades."""
        future_date = date(2026, 12, 31)
        summary = trade_store.get_daily_summary(future_date)

        assert summary['total_trades'] == 0
        assert summary['total_pnl'] == 0.0
        assert summary['win_rate'] == 0.0
        assert summary['symbols_traded'] == []
        assert summary['best_trade'] is None
        assert summary['worst_trade'] is None


class TestThreadSafety:
    """Test thread-safety of TradeStore."""

    def test_concurrent_writes(self, trade_store):
        """Test multiple threads can write simultaneously."""
        import threading

        def write_trades(store, symbol, count):
            for i in range(count):
                store.record_trade(symbol, 'BUY', 100, 150.00 + i)

        threads = []
        for i in range(5):
            t = threading.Thread(target=write_trades, args=(trade_store, f'SYM{i}', 10))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Should have 50 trades total (5 threads × 10 trades each)
        trades = trade_store.get_recent_trades(limit=100)
        assert len(trades) == 50


class TestMetadataHandling:
    """Test metadata storage and retrieval."""

    def test_metadata_storage(self, trade_store):
        """Test storing complex metadata."""
        metadata = {
            'signal': 'breakout',
            'stop_loss': 148.50,
            'take_profit': 155.00,
            'indicators': {
                'rsi': 65.5,
                'macd': 1.2
            },
            'tags': ['high-confidence', 'momentum']
        }

        trade_store.record_trade(
            symbol='AAPL',
            side='BUY',
            quantity=100,
            price=150.00,
            metadata=metadata
        )

        trades = trade_store.get_recent_trades(limit=1)
        assert trades[0]['metadata'] == metadata

    def test_null_metadata(self, trade_store):
        """Test trades without metadata."""
        trade_store.record_trade('AAPL', 'BUY', 100, 150.00)

        trades = trade_store.get_recent_trades(limit=1)
        assert trades[0]['metadata'] is None
