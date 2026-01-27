"""
Trade Store Module - SQLite-based trade execution storage

This module provides a thread-safe SQLite database interface for storing
and querying trade execution data. Designed for real-time trading bot use.

Features:
- Thread-safe operations with connection pooling
- Automatic schema creation and migration
- Efficient querying with indexes
- WAL mode for better concurrency
- Comprehensive error handling
"""

import sqlite3
import json
import threading
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from contextlib import contextmanager

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TradeStore:
    """
    Thread-safe SQLite database for trade execution storage.

    Usage:
        store = TradeStore('data/trades.db')
        store.record_trade(
            symbol='AAPL',
            side='BUY',
            quantity=100,
            price=150.25,
            strategy='ORB',
            metadata={'signal': 'breakout', 'stop_loss': 148.50}
        )

        recent = store.get_recent_trades(limit=10)
        summary = store.get_daily_summary()
    """

    def __init__(self, db_path: str = 'data/trades.db'):
        """
        Initialize TradeStore with database path.

        Args:
            db_path: Path to SQLite database file (created if doesn't exist)
        """
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

        # Create database directory if it doesn't exist
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database schema
        self._init_database()

        logger.info(f"TradeStore initialized at {db_path}")

    @contextmanager
    def _get_connection(self):
        """
        Get thread-local database connection with automatic cleanup.

        Yields:
            sqlite3.Connection: Thread-safe database connection
        """
        # Each thread gets its own connection
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            # Enable WAL mode for better concurrency
            self._local.conn.execute('PRAGMA journal_mode=WAL')
            # Enable foreign keys
            self._local.conn.execute('PRAGMA foreign_keys=ON')
            # Row factory for dict-like access
            self._local.conn.row_factory = sqlite3.Row

        try:
            yield self._local.conn
        except Exception as e:
            self._local.conn.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise

    def _init_database(self):
        """Create database schema if it doesn't exist."""
        with self._get_connection() as conn:
            # Create trades table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    quantity REAL NOT NULL CHECK(quantity > 0),
                    price REAL NOT NULL CHECK(price > 0),
                    strategy TEXT,
                    pnl REAL,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create indexes for common queries
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp
                ON trades(timestamp DESC)
            ''')

            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_trades_symbol
                ON trades(symbol)
            ''')

            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_trades_strategy
                ON trades(strategy)
            ''')

            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_trades_date
                ON trades(date(timestamp))
            ''')

            conn.commit()
            logger.info("Database schema initialized successfully")

    def record_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy: Optional[str] = None,
        pnl: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """
        Record a trade execution to the database.

        Args:
            symbol: Trading symbol (e.g., 'AAPL', 'TSLA')
            side: Trade side ('BUY' or 'SELL')
            quantity: Number of shares/contracts
            price: Execution price
            strategy: Strategy name (e.g., 'ORB', 'MOMENTUM')
            pnl: Profit/loss for this trade (optional)
            metadata: Additional trade metadata as dict (optional)
            timestamp: Trade timestamp (defaults to now)

        Returns:
            int: Trade ID (primary key)

        Raises:
            ValueError: If invalid parameters provided
            sqlite3.Error: If database operation fails
        """
        # Validate inputs
        side = side.upper()
        if side not in ('BUY', 'SELL'):
            raise ValueError(f"Invalid side: {side}. Must be 'BUY' or 'SELL'")

        if quantity <= 0:
            raise ValueError(f"Invalid quantity: {quantity}. Must be > 0")

        if price <= 0:
            raise ValueError(f"Invalid price: {price}. Must be > 0")

        # Use current time if not provided
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Convert metadata to JSON string
        metadata_json = json.dumps(metadata) if metadata else None

        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO trades (timestamp, symbol, side, quantity, price, strategy, pnl, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp.isoformat(),
                    symbol,
                    side,
                    quantity,
                    price,
                    strategy,
                    pnl,
                    metadata_json
                ))
                conn.commit()
                trade_id = cursor.lastrowid

                logger.info(
                    f"Trade recorded: {side} {quantity} {symbol} @ ${price:.2f}",
                    extra={'trade_id': trade_id, 'symbol': symbol, 'strategy': strategy}
                )

                return trade_id

        except sqlite3.Error as e:
            logger.error(f"Failed to record trade: {e}", exc_info=True)
            raise

    def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get most recent trades.

        Args:
            limit: Maximum number of trades to return

        Returns:
            List of trade dictionaries, newest first
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, timestamp, symbol, side, quantity, price, strategy, pnl, metadata
                FROM trades
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))

            trades = []
            for row in cursor:
                trade = dict(row)
                # Parse metadata JSON
                if trade['metadata']:
                    try:
                        trade['metadata'] = json.loads(trade['metadata'])
                    except json.JSONDecodeError:
                        trade['metadata'] = None
                trades.append(trade)

            return trades

    def get_trades_by_date(self, target_date: date) -> List[Dict[str, Any]]:
        """
        Get all trades for a specific date.

        Args:
            target_date: Date to query (datetime.date object)

        Returns:
            List of trade dictionaries for that date
        """
        date_str = target_date.isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, timestamp, symbol, side, quantity, price, strategy, pnl, metadata
                FROM trades
                WHERE date(timestamp) = ?
                ORDER BY timestamp ASC
            ''', (date_str,))

            trades = []
            for row in cursor:
                trade = dict(row)
                if trade['metadata']:
                    try:
                        trade['metadata'] = json.loads(trade['metadata'])
                    except json.JSONDecodeError:
                        trade['metadata'] = None
                trades.append(trade)

            return trades

    def get_daily_summary(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Get daily trading summary statistics.

        Args:
            target_date: Date to summarize (defaults to today)

        Returns:
            Dictionary with summary statistics:
            - total_trades: Number of trades
            - total_pnl: Sum of P&L
            - gross_profit: Sum of profitable trades
            - gross_loss: Sum of losing trades
            - win_rate: Percentage of winning trades
            - total_volume: Total shares/contracts traded
            - symbols_traded: List of unique symbols
            - best_trade: Trade with highest P&L
            - worst_trade: Trade with lowest P&L
        """
        if target_date is None:
            target_date = date.today()

        date_str = target_date.isoformat()

        with self._get_connection() as conn:
            # Get aggregate statistics
            cursor = conn.execute('''
                SELECT
                    COUNT(*) as total_trades,
                    SUM(pnl) as total_pnl,
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as gross_loss,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(quantity) as total_volume,
                    MAX(pnl) as best_pnl,
                    MIN(pnl) as worst_pnl
                FROM trades
                WHERE date(timestamp) = ?
            ''', (date_str,))

            row = cursor.fetchone()

            total_trades = row['total_trades'] or 0
            winning_trades = row['winning_trades'] or 0

            summary = {
                'date': date_str,
                'total_trades': total_trades,
                'total_pnl': row['total_pnl'] or 0.0,
                'gross_profit': row['gross_profit'] or 0.0,
                'gross_loss': row['gross_loss'] or 0.0,
                'win_rate': (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
                'total_volume': row['total_volume'] or 0.0,
                'best_pnl': row['best_pnl'] or 0.0,
                'worst_pnl': row['worst_pnl'] or 0.0,
            }

            # Get unique symbols traded
            cursor = conn.execute('''
                SELECT DISTINCT symbol
                FROM trades
                WHERE date(timestamp) = ?
                ORDER BY symbol
            ''', (date_str,))

            summary['symbols_traded'] = [row['symbol'] for row in cursor]

            # Get best and worst trades (full records)
            if total_trades > 0:
                cursor = conn.execute('''
                    SELECT id, timestamp, symbol, side, quantity, price, pnl
                    FROM trades
                    WHERE date(timestamp) = ? AND pnl = (
                        SELECT MAX(pnl) FROM trades WHERE date(timestamp) = ?
                    )
                    LIMIT 1
                ''', (date_str, date_str))

                best_trade = cursor.fetchone()
                summary['best_trade'] = dict(best_trade) if best_trade else None

                cursor = conn.execute('''
                    SELECT id, timestamp, symbol, side, quantity, price, pnl
                    FROM trades
                    WHERE date(timestamp) = ? AND pnl = (
                        SELECT MIN(pnl) FROM trades WHERE date(timestamp) = ?
                    )
                    LIMIT 1
                ''', (date_str, date_str))

                worst_trade = cursor.fetchone()
                summary['worst_trade'] = dict(worst_trade) if worst_trade else None
            else:
                summary['best_trade'] = None
                summary['worst_trade'] = None

            return summary

    def get_trades_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get trades for a specific symbol.

        Args:
            symbol: Trading symbol
            limit: Maximum trades to return

        Returns:
            List of trades for that symbol
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, timestamp, symbol, side, quantity, price, strategy, pnl, metadata
                FROM trades
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (symbol, limit))

            trades = []
            for row in cursor:
                trade = dict(row)
                if trade['metadata']:
                    try:
                        trade['metadata'] = json.loads(trade['metadata'])
                    except json.JSONDecodeError:
                        trade['metadata'] = None
                trades.append(trade)

            return trades

    def get_trades_by_strategy(self, strategy: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get trades for a specific strategy.

        Args:
            strategy: Strategy name
            limit: Maximum trades to return

        Returns:
            List of trades for that strategy
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT id, timestamp, symbol, side, quantity, price, strategy, pnl, metadata
                FROM trades
                WHERE strategy = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (strategy, limit))

            trades = []
            for row in cursor:
                trade = dict(row)
                if trade['metadata']:
                    try:
                        trade['metadata'] = json.loads(trade['metadata'])
                    except json.JSONDecodeError:
                        trade['metadata'] = None
                trades.append(trade)

            return trades

    def get_total_pnl(self) -> float:
        """
        Get total P&L across all trades.

        Returns:
            Total profit/loss
        """
        with self._get_connection() as conn:
            cursor = conn.execute('SELECT SUM(pnl) as total FROM trades')
            result = cursor.fetchone()
            return result['total'] or 0.0

    def close(self):
        """Close database connections. Called on shutdown."""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            logger.info("TradeStore connection closed")
