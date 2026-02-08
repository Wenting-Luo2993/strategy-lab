"""Tests for storage module (trade store, metrics store, log store)."""

import tempfile
import logging
from pathlib import Path
from datetime import datetime
import pytest
from vibe.common.models import Trade
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.trading_bot.storage.metrics_store import MetricsStore, MetricType
from vibe.trading_bot.storage.log_store import LogStore, DatabaseLogHandler


class TestTradeStore:
    """Tests for TradeStore."""

    @pytest.fixture
    def trade_store(self, tmp_path):
        """Create a trade store in temporary directory."""
        db_path = str(tmp_path / "trades.db")
        store = TradeStore(db_path=db_path)
        yield store
        store.close()

    @pytest.fixture
    def sample_trade(self):
        """Create a sample trade."""
        return Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=150.0,
            exit_price=155.0,
            entry_time=datetime.now(),
        )

    def test_database_creation(self, tmp_path):
        """Test that database is created on first access."""
        db_path = str(tmp_path / "new_trades.db")
        store = TradeStore(db_path=db_path)

        assert Path(db_path).exists()
        store.close()

    def test_schema_creation(self, trade_store):
        """Test that schema is created correctly."""
        conn = trade_store._get_connection()
        cursor = conn.cursor()

        # Check trades table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='trades'
        """)
        assert cursor.fetchone() is not None

        # Check indexes exist
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'
        """)
        indexes = cursor.fetchall()
        assert len(indexes) >= 4  # symbol, strategy, status, entry_time

    def test_insert_trade(self, trade_store, sample_trade):
        """Test inserting a trade."""
        trade_id = trade_store.insert_trade(sample_trade)

        assert trade_id > 0
        assert isinstance(trade_id, int)

    def test_get_trade_by_id(self, trade_store, sample_trade):
        """Test retrieving a trade by ID."""
        trade_id = trade_store.insert_trade(sample_trade)
        retrieved = trade_store.get_trade_by_id(trade_id)

        assert retrieved is not None
        assert retrieved['symbol'] == "AAPL"
        assert retrieved['side'] == "buy"
        assert retrieved['quantity'] == 100

    def test_get_nonexistent_trade(self, trade_store):
        """Test getting a trade that doesn't exist."""
        result = trade_store.get_trade_by_id(999)
        assert result is None

    def test_update_trade(self, trade_store, sample_trade):
        """Test updating a trade."""
        trade_id = trade_store.insert_trade(sample_trade)

        success = trade_store.update_trade(
            trade_id,
            exit_price=160.0,
            status='closed',
        )

        assert success is True

        updated = trade_store.get_trade_by_id(trade_id)
        assert updated['exit_price'] == 160.0
        assert updated['status'] == 'closed'

    def test_update_nonexistent_trade(self, trade_store):
        """Test updating a trade that doesn't exist."""
        success = trade_store.update_trade(999, exit_price=160.0)
        assert success is False

    def test_delete_trade(self, trade_store, sample_trade):
        """Test deleting a trade."""
        trade_id = trade_store.insert_trade(sample_trade)

        success = trade_store.delete_trade(trade_id)
        assert success is True

        retrieved = trade_store.get_trade_by_id(trade_id)
        assert retrieved is None

    def test_get_trades_with_limit(self, trade_store):
        """Test getting trades with limit."""
        # Insert multiple trades
        for i in range(10):
            trade = Trade(
                symbol=f"TEST{i}",
                side="buy",
                quantity=100,
                entry_price=100.0 + i,
            )
            trade_store.insert_trade(trade)

        trades = trade_store.get_trades(limit=5)
        assert len(trades) == 5

    def test_get_trades_with_offset(self, trade_store):
        """Test getting trades with offset."""
        for i in range(10):
            trade = Trade(
                symbol=f"TEST{i}",
                side="buy",
                quantity=100,
                entry_price=100.0 + i,
            )
            trade_store.insert_trade(trade)

        trades1 = trade_store.get_trades(limit=5, offset=0)
        trades2 = trade_store.get_trades(limit=5, offset=5)

        assert len(trades1) == 5
        assert len(trades2) == 5
        # First trade of second batch should be different from first batch
        assert trades1[0]['id'] != trades2[0]['id']

    def test_filter_by_symbol(self, trade_store):
        """Test filtering trades by symbol."""
        trade1 = Trade(symbol="AAPL", side="buy", quantity=100, entry_price=150.0)
        trade2 = Trade(symbol="GOOGL", side="buy", quantity=50, entry_price=200.0)

        trade_store.insert_trade(trade1)
        trade_store.insert_trade(trade2)

        aapl_trades = trade_store.get_trades(symbol="AAPL")
        assert len(aapl_trades) == 1
        assert aapl_trades[0]['symbol'] == "AAPL"

    def test_filter_by_status(self, trade_store):
        """Test filtering trades by status."""
        trade1 = Trade(symbol="AAPL", side="buy", quantity=100, entry_price=150.0)
        id1 = trade_store.insert_trade(trade1)

        trade2 = Trade(symbol="GOOGL", side="buy", quantity=50, entry_price=200.0, exit_price=205.0)
        id2 = trade_store.insert_trade(trade2)

        # Update statuses
        trade_store.update_trade(id1, status="open")
        trade_store.update_trade(id2, status="closed")

        open_trades = trade_store.get_trades(status="open")
        assert len(open_trades) == 1
        assert open_trades[0]['status'] == "open"

    def test_get_trades_by_symbol(self, trade_store):
        """Test getting all trades for a symbol."""
        for i in range(5):
            trade = Trade(
                symbol="AAPL",
                side="buy" if i % 2 == 0 else "sell",
                quantity=100 + i,
                entry_price=150.0 + i,
            )
            trade_store.insert_trade(trade)

        trades = trade_store.get_trades_by_symbol("AAPL")
        assert len(trades) == 5
        assert all(t['symbol'] == "AAPL" for t in trades)

    def test_count_trades(self, trade_store):
        """Test counting trades."""
        for i in range(5):
            trade = Trade(
                symbol="AAPL",
                side="buy",
                quantity=100,
                entry_price=150.0 + i,
            )
            trade_store.insert_trade(trade)

        count = trade_store.count_trades()
        assert count == 5

    def test_count_trades_by_symbol(self, trade_store):
        """Test counting trades filtered by symbol."""
        for i in range(3):
            trade1 = Trade(symbol="AAPL", side="buy", quantity=100, entry_price=150.0)
            trade2 = Trade(symbol="GOOGL", side="buy", quantity=50, entry_price=200.0)
            trade_store.insert_trade(trade1)
            trade_store.insert_trade(trade2)

        aapl_count = trade_store.count_trades(symbol="AAPL")
        assert aapl_count == 3

    def test_pnl_stats_no_trades(self, trade_store):
        """Test P&L stats with no trades."""
        stats = trade_store.get_pnl_stats()

        assert stats['total_trades'] == 0
        assert stats['total_pnl'] == 0.0
        assert stats['win_rate'] == 0.0

    def test_pnl_stats_with_trades(self, trade_store):
        """Test P&L stats with trades."""
        # Create winning trade
        trade1 = Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=150.0,
            exit_price=155.0,
        )
        id1 = trade_store.insert_trade(trade1)

        # Create losing trade
        trade2 = Trade(
            symbol="GOOGL",
            side="buy",
            quantity=50,
            entry_price=200.0,
            exit_price=195.0,
        )
        id2 = trade_store.insert_trade(trade2)

        # Mark as closed
        trade_store.update_trade(id1, status="closed")
        trade_store.update_trade(id2, status="closed")

        stats = trade_store.get_pnl_stats()

        assert stats['total_trades'] == 2
        # PnL should be: (155-150)*100 + (195-200)*50 = 500 - 250 = 250
        assert stats['total_pnl'] == 250.0
        assert stats['winning_trades'] == 1
        assert stats['losing_trades'] == 1
        assert stats['win_rate'] == 0.5

    def test_thread_safety(self, trade_store):
        """Test that multiple threads can use the store safely."""
        import threading

        def insert_trades(symbol_prefix):
            for i in range(10):
                trade = Trade(
                    symbol=f"{symbol_prefix}{i}",
                    side="buy",
                    quantity=100,
                    entry_price=100.0 + i,
                )
                trade_store.insert_trade(trade)

        threads = [
            threading.Thread(target=insert_trades, args=(f"THREAD{i}",))
            for i in range(3)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        total_count = trade_store.count_trades()
        assert total_count == 30  # 3 threads * 10 trades each

    def test_concurrent_reads(self, trade_store):
        """Test that concurrent reads work correctly."""
        import threading

        # Insert a trade
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=150.0,
        )
        trade_id = trade_store.insert_trade(trade)

        results = []

        def read_trade():
            result = trade_store.get_trade_by_id(trade_id)
            results.append(result)

        threads = [threading.Thread(target=read_trade) for _ in range(5)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(results) == 5
        assert all(r['symbol'] == "AAPL" for r in results)


class TestMetricsStore:
    """Tests for MetricsStore."""

    @pytest.fixture
    def metrics_store(self, tmp_path):
        """Create a metrics store in temporary directory."""
        db_path = str(tmp_path / "metrics.db")
        store = MetricsStore(db_path=db_path)
        yield store
        store.close()

    def test_database_creation(self, tmp_path):
        """Test that metrics database is created."""
        db_path = str(tmp_path / "new_metrics.db")
        store = MetricsStore(db_path=db_path)
        assert Path(db_path).exists()
        store.close()

    def test_record_metric(self, metrics_store):
        """Test recording a metric."""
        metric_id = metrics_store.record_metric(
            metric_type="trade",
            metric_name="pnl",
            metric_value=150.0,
            dimensions={"symbol": "AAPL", "strategy": "orb"},
        )
        assert metric_id > 0

    def test_record_metric_without_dimensions(self, metrics_store):
        """Test recording a metric without dimensions."""
        metric_id = metrics_store.record_metric(
            metric_type="health",
            metric_name="heartbeat",
            metric_value=1.0,
        )
        assert metric_id > 0

    def test_get_metrics(self, metrics_store):
        """Test retrieving metrics."""
        metrics_store.record_metric(
            metric_type="trade",
            metric_name="pnl",
            metric_value=100.0,
            dimensions={"symbol": "AAPL"},
        )
        metrics_store.record_metric(
            metric_type="trade",
            metric_name="pnl",
            metric_value=200.0,
            dimensions={"symbol": "GOOGL"},
        )

        metrics = metrics_store.get_metrics(metric_type="trade", metric_name="pnl")
        assert len(metrics) == 2

    def test_get_metrics_with_time_filter(self, metrics_store):
        """Test getting metrics with time filtering."""
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        old_time = (now - timedelta(hours=2)).isoformat()
        current_time = now.isoformat()

        metrics_store.record_metric(
            metric_type="performance",
            metric_name="return",
            metric_value=0.01,
            timestamp=old_time,
        )
        metrics_store.record_metric(
            metric_type="performance",
            metric_name="return",
            metric_value=0.02,
            timestamp=current_time,
        )

        # Get only recent metrics
        recent = metrics_store.get_metrics(
            metric_type="performance",
            metric_name="return",
            start_time=current_time,
        )
        assert len(recent) == 1
        assert recent[0]['metric_value'] == 0.02

    def test_aggregate_sum(self, metrics_store):
        """Test summing metrics."""
        for value in [10.0, 20.0, 30.0]:
            metrics_store.record_metric(
                metric_type="trade",
                metric_name="volume",
                metric_value=value,
            )

        total = metrics_store.aggregate_metrics(
            metric_type="trade",
            metric_name="volume",
            aggregation="sum",
        )
        assert total == 60.0

    def test_aggregate_avg(self, metrics_store):
        """Test averaging metrics."""
        for value in [10.0, 20.0, 30.0]:
            metrics_store.record_metric(
                metric_type="trade",
                metric_name="price",
                metric_value=value,
            )

        avg = metrics_store.aggregate_metrics(
            metric_type="trade",
            metric_name="price",
            aggregation="avg",
        )
        assert avg == 20.0

    def test_aggregate_count(self, metrics_store):
        """Test counting metrics."""
        for i in range(5):
            metrics_store.record_metric(
                metric_type="health",
                metric_name="check",
                metric_value=float(i),
            )

        count = metrics_store.aggregate_metrics(
            metric_type="health",
            metric_name="check",
            aggregation="count",
        )
        assert count == 5

    def test_aggregate_min_max(self, metrics_store):
        """Test min/max aggregation."""
        for value in [5.0, 15.0, 25.0]:
            metrics_store.record_metric(
                metric_type="trade",
                metric_name="pnl",
                metric_value=value,
            )

        min_val = metrics_store.aggregate_metrics(
            metric_type="trade",
            metric_name="pnl",
            aggregation="min",
        )
        max_val = metrics_store.aggregate_metrics(
            metric_type="trade",
            metric_name="pnl",
            aggregation="max",
        )

        assert min_val == 5.0
        assert max_val == 25.0

    def test_get_metric_stats(self, metrics_store):
        """Test getting comprehensive metric statistics."""
        for value in [10.0, 20.0, 30.0]:
            metrics_store.record_metric(
                metric_type="trade",
                metric_name="pnl",
                metric_value=value,
            )

        stats = metrics_store.get_metric_stats(
            metric_type="trade",
            metric_name="pnl",
        )

        assert stats['count'] == 3
        assert stats['sum'] == 60.0
        assert stats['avg'] == 20.0
        assert stats['min'] == 10.0
        assert stats['max'] == 30.0

    def test_get_metric_stats_empty(self, metrics_store):
        """Test stats for non-existent metrics."""
        stats = metrics_store.get_metric_stats(
            metric_type="trade",
            metric_name="nonexistent",
        )

        assert stats['count'] == 0
        assert stats['sum'] == 0.0

    def test_delete_metrics(self, metrics_store):
        """Test deleting metrics."""
        metrics_store.record_metric(
            metric_type="trade",
            metric_name="pnl",
            metric_value=100.0,
        )
        metrics_store.record_metric(
            metric_type="health",
            metric_name="check",
            metric_value=1.0,
        )

        # Delete trade metrics
        deleted = metrics_store.delete_metrics(metric_type="trade")
        assert deleted == 1

        # Verify health metric still exists
        remaining = metrics_store.get_metrics(metric_type="health")
        assert len(remaining) == 1

    def test_metric_type_enum(self):
        """Test MetricType enum."""
        assert MetricType.TRADE.value == "trade"
        assert MetricType.PERFORMANCE.value == "performance"
        assert MetricType.HEALTH.value == "health"


class TestLogStore:
    """Tests for LogStore."""

    @pytest.fixture
    def log_store(self, tmp_path):
        """Create a log store in temporary directory."""
        db_path = str(tmp_path / "logs.db")
        store = LogStore(db_path=db_path, retention_days=3)
        yield store
        store.close()

    def test_database_creation(self, tmp_path):
        """Test that log database is created."""
        db_path = str(tmp_path / "new_logs.db")
        store = LogStore(db_path=db_path)
        assert Path(db_path).exists()
        store.close()

    def test_insert_log(self, log_store):
        """Test inserting a log."""
        log_id = log_store.insert_log(
            level="INFO",
            logger="test.module",
            message="Test log message",
        )
        assert log_id > 0

    def test_insert_log_with_extra_data(self, log_store):
        """Test inserting a log with extra data."""
        log_id = log_store.insert_log(
            level="WARNING",
            logger="test.module",
            message="Warning message",
            extra_data={"user_id": 123, "action": "trade"},
        )
        assert log_id > 0

        logs = log_store.get_logs(level="WARNING")
        assert len(logs) == 1
        assert logs[0]['extra_data']['user_id'] == 123

    def test_get_logs(self, log_store):
        """Test retrieving logs."""
        log_store.insert_log(level="INFO", logger="app", message="Info 1")
        log_store.insert_log(level="WARNING", logger="app", message="Warning 1")
        log_store.insert_log(level="ERROR", logger="app", message="Error 1")

        logs = log_store.get_logs()
        assert len(logs) == 3

    def test_filter_logs_by_level(self, log_store):
        """Test filtering logs by level."""
        log_store.insert_log(level="INFO", logger="app", message="Info")
        log_store.insert_log(level="ERROR", logger="app", message="Error")

        error_logs = log_store.get_logs(level="ERROR")
        assert len(error_logs) == 1
        assert error_logs[0]['level'] == "ERROR"

    def test_filter_logs_by_logger(self, log_store):
        """Test filtering logs by logger name."""
        log_store.insert_log(level="INFO", logger="module1", message="Msg 1")
        log_store.insert_log(level="INFO", logger="module2", message="Msg 2")

        logs = log_store.get_logs(logger="module1")
        assert len(logs) == 1
        assert logs[0]['logger'] == "module1"

    def test_get_logs_with_time_filter(self, log_store):
        """Test filtering logs by time range."""
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        old_time = (now - timedelta(hours=2)).isoformat()
        current_time = now.isoformat()

        log_store.insert_log(
            timestamp=old_time,
            level="INFO",
            logger="app",
            message="Old log",
        )
        log_store.insert_log(
            timestamp=current_time,
            level="INFO",
            logger="app",
            message="Recent log",
        )

        recent = log_store.get_logs(start_time=current_time)
        assert len(recent) == 1
        assert recent[0]['message'] == "Recent log"

    def test_count_logs(self, log_store):
        """Test counting logs."""
        for i in range(5):
            log_store.insert_log(level="INFO", logger="app", message=f"Message {i}")

        count = log_store.count_logs()
        assert count == 5

    def test_count_logs_by_level(self, log_store):
        """Test counting logs filtered by level."""
        for i in range(3):
            log_store.insert_log(level="INFO", logger="app", message=f"Info {i}")

        for i in range(2):
            log_store.insert_log(level="ERROR", logger="app", message=f"Error {i}")

        info_count = log_store.count_logs(level="INFO")
        assert info_count == 3

    def test_cleanup_old_logs(self, log_store):
        """Test cleaning up old logs."""
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        old_time = (now - timedelta(days=5)).isoformat()
        current_time = now.isoformat()

        # Insert old log
        log_store.insert_log(
            timestamp=old_time,
            level="INFO",
            logger="app",
            message="Old log",
        )
        # Insert recent log
        log_store.insert_log(
            timestamp=current_time,
            level="INFO",
            logger="app",
            message="Recent log",
        )

        # Cleanup with 3-day retention
        deleted = log_store.cleanup_old_logs(retention_days=3)
        assert deleted == 1

        # Verify recent log still exists
        remaining = log_store.get_logs()
        assert len(remaining) == 1
        assert remaining[0]['message'] == "Recent log"

    def test_get_log_stats(self, log_store):
        """Test getting log statistics."""
        for i in range(3):
            log_store.insert_log(level="INFO", logger="app", message=f"Info {i}")

        for i in range(2):
            log_store.insert_log(level="ERROR", logger="app", message=f"Error {i}")

        stats = log_store.get_log_stats()
        assert stats['total'] == 5
        assert stats['by_level']['INFO'] == 3
        assert stats['by_level']['ERROR'] == 2

    def test_delete_logs_before_time(self, log_store):
        """Test deleting logs before a specific time."""
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        old_time = (now - timedelta(hours=1)).isoformat()
        cutoff_time = now.isoformat()

        log_store.insert_log(
            timestamp=old_time,
            level="INFO",
            logger="app",
            message="Old",
        )
        log_store.insert_log(
            timestamp=cutoff_time,
            level="INFO",
            logger="app",
            message="Recent",
        )

        deleted = log_store.delete_logs(cutoff_time)
        assert deleted == 1

        remaining = log_store.get_logs()
        assert len(remaining) == 1

    def test_database_log_handler(self, log_store):
        """Test DatabaseLogHandler."""
        handler = DatabaseLogHandler(log_store)
        logger = logging.getLogger("test.handler")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message from handler")

        logs = log_store.get_logs(logger="test.handler")
        assert len(logs) == 1
        assert logs[0]['message'] == "Test message from handler"

    def test_database_log_handler_with_meta(self, log_store):
        """Test DatabaseLogHandler with metadata."""
        handler = DatabaseLogHandler(log_store)
        logger = logging.getLogger("test.meta")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Create a log record with metadata
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            "test.py",
            1,
            "Test with meta",
            (),
            None,
        )
        record.meta = {"key": "value"}
        logger.handle(record)

        logs = log_store.get_logs(logger="test.meta")
        assert len(logs) == 1
        if logs[0]['extra_data']:
            assert logs[0]['extra_data'].get('key') == 'value'
