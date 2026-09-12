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
        'strategy', 'updated_at', 'quantity', 'entry_price',
        'account_id', 'broker_order_id', 'exit_reason', 'pnl_currency',
        'closed_quantity'
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
                trade_id TEXT,
                account_id TEXT,
                broker_order_id TEXT,
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
                pnl_currency TEXT,
                closed_quantity REAL NOT NULL DEFAULT 0,
                strategy TEXT,
                exit_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        self._ensure_dashboard_columns(cursor)

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
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_account_id ON trades(account_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_broker_order_id ON trades(broker_order_id)
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_lifecycle_projections (
                order_id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL,
                trade_row_id INTEGER NOT NULL,
                applied_quantity REAL NOT NULL,
                applied_notional REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_lifecycle_watermarks (
                execution_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                trade_row_id INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)

        conn.commit()

    def apply_entry_execution_projection(
        self,
        *,
        execution_id: str,
        broker_order_id: str,
        account_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        entry_time: datetime,
        pnl_currency: Optional[str],
        strategy: str,
    ) -> tuple[Dict[str, Any], bool]:
        """Atomically apply one durable entry execution exactly once."""
        now = datetime.utcnow().isoformat()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                watermark = cursor.execute(
                    """
                    SELECT trade_row_id FROM execution_lifecycle_watermarks
                    WHERE execution_id = ?
                    """,
                    (str(execution_id),),
                ).fetchone()
                if watermark is not None:
                    row = cursor.execute(
                        "SELECT * FROM trades WHERE id = ?",
                        (watermark["trade_row_id"],),
                    ).fetchone()
                    conn.rollback()
                    if row is None:
                        raise RuntimeError(
                            f"Lifecycle watermark {execution_id} references a missing trade"
                        )
                    return dict(row), False

                row = cursor.execute(
                    """
                    SELECT * FROM trades
                    WHERE account_id = ? AND symbol = ? AND status = 'open'
                      AND side = ?
                    ORDER BY entry_time DESC, id DESC
                    LIMIT 1
                    """,
                    (account_id, symbol, side),
                ).fetchone()
                if row is None:
                    trade_id = f"{account_id}:{broker_order_id}"
                    cursor.execute(
                        """
                        INSERT INTO trades (
                            trade_id, account_id, broker_order_id, symbol, side,
                            quantity, entry_price, entry_time, status,
                            pnl_currency, closed_quantity, strategy,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0, ?, ?, ?)
                        """,
                        (
                            trade_id,
                            account_id,
                            str(broker_order_id),
                            symbol,
                            side,
                            float(quantity),
                            float(price),
                            entry_time.isoformat(),
                            pnl_currency,
                            strategy,
                            now,
                            now,
                        ),
                    )
                    trade_row_id = int(cursor.lastrowid)
                else:
                    trade_id = str(row["trade_id"] or f"{account_id}:{broker_order_id}")
                    previous_quantity = float(row["quantity"])
                    total_quantity = previous_quantity + float(quantity)
                    weighted_price = (
                        float(row["entry_price"]) * previous_quantity
                        + float(price) * float(quantity)
                    ) / total_quantity
                    trade_row_id = int(row["id"])
                    cursor.execute(
                        """
                        UPDATE trades
                        SET trade_id = ?, quantity = ?, entry_price = ?,
                            broker_order_id = ?, pnl_currency = COALESCE(?, pnl_currency),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            trade_id,
                            total_quantity,
                            weighted_price,
                            str(broker_order_id),
                            pnl_currency,
                            now,
                            trade_row_id,
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO execution_lifecycle_watermarks (
                        execution_id, action, trade_id, trade_row_id, applied_at
                    ) VALUES (?, 'entry', ?, ?, ?)
                    """,
                    (str(execution_id), trade_id, trade_row_id, now),
                )
                conn.commit()
                applied = conn.execute(
                    "SELECT * FROM trades WHERE id = ?",
                    (trade_row_id,),
                ).fetchone()
                return dict(applied), True
            except Exception:
                conn.rollback()
                raise

    def mark_execution_lifecycle_projection(
        self,
        *,
        execution_id: str,
        action: str,
        trade_id: str,
        trade_row_id: int,
    ) -> bool:
        """Persist a durable execution watermark after an idempotent close."""
        with self._lock:
            cursor = self._get_connection().execute(
                """
                INSERT OR IGNORE INTO execution_lifecycle_watermarks (
                    execution_id, action, trade_id, trade_row_id, applied_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(execution_id),
                    action,
                    str(trade_id),
                    int(trade_row_id),
                    datetime.utcnow().isoformat(),
                ),
            )
            self._get_connection().commit()
            return cursor.rowcount > 0

    def has_execution_lifecycle_projection(self, execution_id: str) -> bool:
        return self._get_connection().execute(
            """
            SELECT 1 FROM execution_lifecycle_watermarks
            WHERE execution_id = ?
            """,
            (str(execution_id),),
        ).fetchone() is not None

    def _ensure_dashboard_columns(self, cursor: sqlite3.Cursor) -> None:
        """Add dashboard columns to existing trade DBs without data loss."""
        cursor.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cursor.fetchall()}
        migrations = {
            "trade_id": "ALTER TABLE trades ADD COLUMN trade_id TEXT",
            "account_id": "ALTER TABLE trades ADD COLUMN account_id TEXT",
            "broker_order_id": "ALTER TABLE trades ADD COLUMN broker_order_id TEXT",
            "exit_reason": "ALTER TABLE trades ADD COLUMN exit_reason TEXT",
            "pnl_currency": "ALTER TABLE trades ADD COLUMN pnl_currency TEXT",
            "closed_quantity": (
                "ALTER TABLE trades ADD COLUMN closed_quantity REAL NOT NULL DEFAULT 0"
            ),
        }
        for column, statement in migrations.items():
            if column not in columns:
                cursor.execute(statement)

    def backfill_dashboard_account_id(self, account_id: str) -> int:
        """Backfill missing account IDs for existing Phase 1 dashboard rows."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades
                SET account_id = ?, updated_at = ?
                WHERE account_id IS NULL OR account_id = ''
            """, (account_id, datetime.utcnow().isoformat()))
            conn.commit()
            return cursor.rowcount

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
                    trade_id, account_id, symbol, side, quantity, entry_price, exit_price,
                    entry_time, exit_time, status, pnl, pnl_pct, pnl_currency,
                    strategy, exit_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id,
                getattr(trade, 'account_id', None),
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
                trade.pnl_currency,
                trade.strategy if hasattr(trade, 'strategy') else None,
                trade.exit_reason if hasattr(trade, 'exit_reason') else None,
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

    def apply_exit_projection(
        self,
        *,
        trade_row_id: int,
        trade_id: str,
        order_id: str,
        cumulative_quantity: float,
        cumulative_avg_price: float,
        remaining_quantity: float,
        exit_time: datetime,
        exit_reason: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically apply new cumulative close progress for one broker order.

        The durable per-order watermark prevents a restart or a late commission
        callback from applying already persisted partial-close quantity again.
        """
        cumulative_quantity = max(float(cumulative_quantity), 0.0)
        cumulative_notional = cumulative_quantity * float(cumulative_avg_price)
        now = datetime.utcnow().isoformat()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    "SELECT * FROM trades WHERE id = ?",
                    (trade_row_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                projection = cursor.execute(
                    """
                    SELECT applied_quantity, applied_notional
                    FROM trade_lifecycle_projections
                    WHERE order_id = ?
                    """,
                    (str(order_id),),
                ).fetchone()
                seeded_legacy_projection = False
                if (
                    projection is None
                    and str(row["broker_order_id"] or "") == str(order_id)
                    and float(row["closed_quantity"] or 0.0) > 0
                ):
                    # Migration/recovery for a partial close persisted by the
                    # pre-watermark implementation. The trade's latest broker
                    # order already owns this cumulative close, so establish
                    # its durable watermark without applying quantity again.
                    seeded_quantity = min(
                        float(row["closed_quantity"] or 0.0),
                        cumulative_quantity,
                    )
                    seeded_notional = (
                        float(row["exit_price"] or cumulative_avg_price)
                        * seeded_quantity
                    )
                    cursor.execute(
                        """
                        INSERT INTO trade_lifecycle_projections (
                            order_id, trade_id, trade_row_id, applied_quantity,
                            applied_notional, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(order_id),
                            str(trade_id),
                            trade_row_id,
                            seeded_quantity,
                            seeded_notional,
                            now,
                            now,
                        ),
                    )
                    previous_quantity = seeded_quantity
                    previous_notional = seeded_notional
                    seeded_legacy_projection = True
                else:
                    previous_quantity = (
                        float(projection["applied_quantity"]) if projection else 0.0
                    )
                    previous_notional = (
                        float(projection["applied_notional"]) if projection else 0.0
                    )
                if cumulative_quantity <= previous_quantity:
                    if seeded_legacy_projection:
                        conn.commit()
                    else:
                        conn.rollback()
                    return None

                fill_quantity = cumulative_quantity - previous_quantity
                total_trade_quantity = (
                    float(row["quantity"])
                    + float(row["closed_quantity"] or 0.0)
                )
                unapplied_quantity = max(
                    total_trade_quantity - float(row["closed_quantity"] or 0.0),
                    0.0,
                )
                fill_quantity = min(fill_quantity, unapplied_quantity)
                if fill_quantity <= 0:
                    conn.rollback()
                    return None
                incremental_notional = cumulative_notional - previous_notional
                incremental_exit_price = incremental_notional / (
                    cumulative_quantity - previous_quantity
                )
                closed_quantity = float(row["closed_quantity"] or 0.0)
                new_closed_quantity = closed_quantity + fill_quantity
                previous_exit_price = float(row["exit_price"] or 0.0)
                weighted_exit_price = (
                    previous_exit_price * closed_quantity
                    + incremental_exit_price * fill_quantity
                ) / new_closed_quantity
                entry_price = float(row["entry_price"])
                is_flat = float(remaining_quantity) <= 0
                if row["side"] == "buy":
                    pnl = (
                        weighted_exit_price - entry_price
                    ) * new_closed_quantity
                    pnl_pct = (
                        (weighted_exit_price - entry_price) / entry_price * 100
                        if entry_price
                        else 0.0
                    )
                else:
                    pnl = (
                        entry_price - weighted_exit_price
                    ) * new_closed_quantity
                    pnl_pct = (
                        (entry_price - weighted_exit_price) / entry_price * 100
                        if entry_price
                        else 0.0
                    )

                cursor.execute(
                    """
                    UPDATE trades
                    SET quantity = ?, closed_quantity = ?, exit_price = ?,
                        exit_time = ?, status = ?, pnl = ?, pnl_pct = ?,
                        exit_reason = ?, broker_order_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_closed_quantity if is_flat else float(remaining_quantity),
                        new_closed_quantity,
                        weighted_exit_price,
                        exit_time.isoformat() if is_flat else None,
                        "closed" if is_flat else "open",
                        pnl,
                        pnl_pct,
                        exit_reason if is_flat else None,
                        str(order_id),
                        now,
                        trade_row_id,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO trade_lifecycle_projections (
                        order_id, trade_id, trade_row_id, applied_quantity,
                        applied_notional, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        trade_id = excluded.trade_id,
                        trade_row_id = excluded.trade_row_id,
                        applied_quantity = excluded.applied_quantity,
                        applied_notional = excluded.applied_notional,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(order_id),
                        str(trade_id),
                        trade_row_id,
                        cumulative_quantity,
                        cumulative_notional,
                        now,
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM trades WHERE id = ?",
                    (trade_row_id,),
                ).fetchone()
                return dict(updated) if updated else None
            except sqlite3.Error:
                conn.rollback()
                raise

    def get_exit_projection(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Return the durable close watermark for diagnostics and recovery."""
        row = self._get_connection().execute(
            "SELECT * FROM trade_lifecycle_projections WHERE order_id = ?",
            (str(order_id),),
        ).fetchone()
        return dict(row) if row else None

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
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get trades with optional filtering.

        Args:
            symbol: Filter by symbol (optional)
            status: Filter by status (optional)
            strategy: Filter by strategy (optional)
            account_id: Filter by dashboard/broker account (optional)
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

        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)

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

    def iter_publish_events(self, trading_day: Any = None):
        """Reconstruct durable trade publications after an outbox enqueue gap."""
        from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent

        rows = self._get_connection().execute(
            "SELECT * FROM trades ORDER BY entry_time"
        ).fetchall()
        events = []
        for row in rows:
            values = dict(row)
            trade_id = values.get("trade_id") or str(values["id"])
            payload = {
                "trade_id": trade_id,
                "account_id": values.get("account_id"),
                "symbol": values.get("symbol"),
                "side": "long" if values.get("side") == "buy" else "short",
                "quantity": values.get("quantity"),
                "entry_price": values.get("entry_price"),
                "entry_time": values.get("entry_time"),
                "exit_price": values.get("exit_price"),
                "exit_time": values.get("exit_time"),
                "status": values.get("status"),
                "pnl": values.get("pnl"),
                "pnl_pct": values.get("pnl_pct"),
                "pnl_currency": values.get("pnl_currency"),
                "strategy": values.get("strategy"),
                "exit_reason": values.get("exit_reason"),
                "broker_order_id": values.get("broker_order_id"),
                "created_at": values.get("created_at"),
                "updated_at": values.get("updated_at"),
            }
            events.append(PublishOutboxEvent(
                event_id=f"trade:{trade_id}",
                event_type="upsert",
                aggregate_type="trade",
                aggregate_id=trade_id,
                destination="supabase",
                payload=payload,
                original_event_timestamp=values.get("exit_time") or values["entry_time"],
            ))
        return events

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
