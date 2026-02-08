"""SQLite-based storage for trades."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from vibe.common.models import Trade


class TradeStore:
    """Thread-safe SQLite store for trades."""

    # Whitelist of allowed update fields (prevents SQL injection)
    ALLOWED_UPDATE_FIELDS = {
        'exit_price', 'exit_time', 'status', 'pnl', 'pnl_pct',
        'strategy', 'updated_at', 'quantity', 'entry_price'
    }

    def __init__(self, db_path: str = "./data/trades.db"):
        """Initialize trade store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connection pooling using thread-local storage
        self._local = threading.local()
        self._lock = threading.Lock()

        # Initialize schema
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection with WAL mode for better concurrency.

        Returns:
            SQLite connection for current thread
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                f"file:{self.db_path}?mode=rwc",
                uri=True,
                timeout=30.0,
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent read/write performance
            self._local.connection.execute("PRAGMA journal_mode=WAL")
        return self._local.connection

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                pnl REAL,
                pnl_pct REAL,
                strategy TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Create single-column indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy ON trades(strategy)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON trades(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_entry_time ON trades(entry_time)
        """)

        # Create composite indexes for common query patterns (performance optimization)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol_status ON trades(symbol, status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_entry_time ON trades(status, entry_time)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol_created_at ON trades(symbol, created_at DESC)
        """)

        conn.commit()

    def insert_trade(self, trade: Trade) -> int:
        """Insert a new trade.

        Args:
            trade: Trade object to insert

        Returns:
            Trade ID of inserted trade

        Raises:
            sqlite3.Error: If database operation fails
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.utcnow().isoformat()

            cursor.execute("""
                INSERT INTO trades (
                    symbol, side, quantity, entry_price, exit_price,
                    entry_time, exit_time, status, pnl, pnl_pct,
                    strategy, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.symbol,
                trade.side,
                trade.quantity,
                trade.entry_price,
                trade.exit_price,
                trade.entry_time.isoformat() if trade.entry_time else now,
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.status if hasattr(trade, 'status') else 'open',
                trade.pnl,
                trade.pnl_pct,
                trade.strategy if hasattr(trade, 'strategy') else None,
                now,
                now,
            ))

            conn.commit()
            return cursor.lastrowid

    def update_trade(self, trade_id: int, **updates: Any) -> bool:
        """Update a trade.

        Args:
            trade_id: ID of trade to update
            **updates: Fields to update (e.g., exit_price=150.0, status='closed')

        Returns:
            True if trade was updated, False if not found

        Raises:
            ValueError: If invalid field names provided
            sqlite3.Error: If database operation fails
        """
        if not updates:
            return False

        # Validate field names against whitelist (prevents SQL injection)
        invalid_fields = set(updates.keys()) - self.ALLOWED_UPDATE_FIELDS
        if invalid_fields:
            raise ValueError(f"Invalid update fields: {invalid_fields}. "
                           f"Allowed fields: {self.ALLOWED_UPDATE_FIELDS}")

        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Add updated_at timestamp
            updates['updated_at'] = datetime.utcnow().isoformat()

            # Build SET clause (safe now - all keys are whitelisted)
            set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [trade_id]

            cursor.execute(f"""
                UPDATE trades SET {set_clause} WHERE id = ?
            """, values)

            conn.commit()
            return cursor.rowcount > 0

    def get_trade_by_id(self, trade_id: int) -> Optional[Dict[str, Any]]:
        """Get a trade by ID.

        Args:
            trade_id: Trade ID

        Returns:
            Trade data as dictionary, or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()

        return dict(row) if row else None

    def get_trades(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get trades with optional filtering.

        Args:
            symbol: Filter by symbol (optional)
            status: Filter by status (optional)
            strategy: Filter by strategy (optional)
            limit: Maximum number of trades to return
            offset: Offset for pagination

        Returns:
            List of trade dictionaries
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM trades WHERE 1=1"
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        if status:
            query += " AND status = ?"
            params.append(status)

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_trades_by_symbol(
        self,
        symbol: str,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get trades for a symbol with optional pagination.

        Args:
            symbol: Symbol to query
            limit: Maximum number of trades to return (default: 100)
            offset: Offset for pagination

        Returns:
            List of trade dictionaries
        """
        return self.get_trades(
            symbol=symbol,
            limit=limit or 100,  # Sensible default instead of 10000
            offset=offset
        )

    def count_trades(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """Count trades matching criteria.

        Args:
            symbol: Filter by symbol (optional)
            status: Filter by status (optional)

        Returns:
            Number of matching trades
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT COUNT(*) FROM trades WHERE 1=1"
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        if status:
            query += " AND status = ?"
            params.append(status)

        cursor.execute(query, params)
        result = cursor.fetchone()

        return result[0] if result else 0

    def get_pnl_stats(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get P&L statistics for trades.

        Args:
            symbol: Optional symbol filter

        Returns:
            Dictionary with P&L stats (total_pnl, win_rate, trade_count)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT
                COUNT(*) as total_trades,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl,
                COUNT(CASE WHEN pnl > 0 THEN 1 END) as winning_trades,
                COUNT(CASE WHEN pnl <= 0 THEN 1 END) as losing_trades
            FROM trades WHERE status = 'closed'
        """
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row or row[0] == 0:
            return {
                'total_trades': 0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
            }

        total_trades, total_pnl, avg_pnl, winning, losing = row
        win_rate = (winning / total_trades) if total_trades > 0 else 0.0

        return {
            'total_trades': total_trades,
            'total_pnl': total_pnl or 0.0,
            'avg_pnl': avg_pnl or 0.0,
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': win_rate,
        }

    def delete_trade(self, trade_id: int) -> bool:
        """Delete a trade.

        Args:
            trade_id: ID of trade to delete

        Returns:
            True if trade was deleted, False if not found
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            conn.commit()

            return cursor.rowcount > 0

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
