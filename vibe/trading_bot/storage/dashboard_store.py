"""SQLite stores for live dashboard read-model rows and publish outbox."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class _SQLiteStore:
    """Small local SQLite helper matching the existing storage pattern."""

    def __init__(self, db_path: str, timeout: float = 30.0):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                f"file:{self.db_path}?mode=rwc",
                uri=True,
                timeout=self.timeout,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA busy_timeout=5000")
        return self._local.connection

    def _init_schema(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    timeframe: str
    bar_start: datetime | str
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str
    ingestion_time: datetime | str
    is_complete: bool = True


class PriceBarStore(_SQLiteStore):
    """Local idempotent OHLCV bar store for dashboard charts."""

    def __init__(self, db_path: str = "./data/market_data.db"):
        super().__init__(db_path)

    def _init_schema(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_start TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                provider TEXT NOT NULL,
                ingestion_time TEXT NOT NULL,
                is_complete INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, timeframe, bar_start)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_time
            ON price_bars(symbol, timeframe, bar_start DESC)
        """)
        conn.commit()

    def upsert_bar(self, bar: PriceBar) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO price_bars (
                    symbol, timeframe, bar_start, open, high, low, close, volume,
                    provider, ingestion_time, is_complete, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, bar_start) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    provider = excluded.provider,
                    ingestion_time = excluded.ingestion_time,
                    is_complete = excluded.is_complete,
                    updated_at = excluded.updated_at
            """, (
                bar.symbol,
                bar.timeframe,
                _iso(bar.bar_start),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.provider,
                _iso(bar.ingestion_time),
                1 if bar.is_complete else 0,
                now,
                now,
            ))
            conn.commit()

    def get_bar(self, symbol: str, timeframe: str, bar_start: datetime | str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM price_bars
            WHERE symbol = ? AND timeframe = ? AND bar_start = ?
        """, (symbol, timeframe, _iso(bar_start)))
        row = cursor.fetchone()
        return dict(row) if row else None

    def count_bars(self) -> int:
        conn = self._get_connection()
        return conn.execute("SELECT COUNT(*) FROM price_bars").fetchone()[0]


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    broker: str
    display_name: str
    currency: str = "USD"
    mode: str = "paper"


@dataclass(frozen=True)
class EquitySnapshot:
    snapshot_id: str
    account_id: str
    timestamp: datetime | str
    net_liquidation: Optional[float] = None
    cash: Optional[float] = None
    buying_power: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    source: str = "broker"


@dataclass(frozen=True)
class PositionSnapshot:
    position_id: str
    account_id: str
    symbol: str
    quantity: float
    side: str
    avg_cost: Optional[float]
    market_price: Optional[float]
    unrealized_pnl: Optional[float]
    updated_at: datetime | str


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    account_id: str
    broker: str
    broker_order_id: str
    event_type: str
    symbol: str
    side: str
    quantity: float
    occurred_at: datetime | str
    strategy_order_id: Optional[str] = None
    trade_id: Optional[str] = None
    price: Optional[float] = None
    expected_price: Optional[float] = None
    slippage_bps: Optional[float] = None
    latency_ms: Optional[float] = None
    raw_status: Optional[str] = None


class DashboardStore(_SQLiteStore):
    """Local account, position, equity, and order-event store."""

    def __init__(self, db_path: str = "./data/dashboard.db"):
        super().__init__(db_path)

    def _init_schema(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                broker TEXT NOT NULL,
                display_name TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                net_liquidation REAL,
                cash REAL,
                buying_power REAL,
                realized_pnl REAL,
                unrealized_pnl REAL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                side TEXT NOT NULL,
                avg_cost REAL,
                market_price REAL,
                unrealized_pnl REAL,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                stored_updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_events (
                event_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                broker TEXT NOT NULL,
                broker_order_id TEXT NOT NULL,
                strategy_order_id TEXT,
                trade_id TEXT,
                event_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL,
                expected_price REAL,
                slippage_bps REAL,
                latency_ms REAL,
                occurred_at TEXT NOT NULL,
                raw_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_equity_account_time ON equity_snapshots(account_id, timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_symbol ON positions(account_id, symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_events_account_time ON order_events(account_id, occurred_at DESC)")
        conn.commit()

    def upsert_account(self, account: AccountRecord) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO accounts (account_id, broker, display_name, currency, mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    broker = excluded.broker,
                    display_name = excluded.display_name,
                    currency = excluded.currency,
                    mode = excluded.mode,
                    updated_at = excluded.updated_at
            """, (account.account_id, account.broker, account.display_name, account.currency, account.mode, now, now))
            conn.commit()

    def upsert_equity_snapshot(self, snapshot: EquitySnapshot) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO equity_snapshots (
                    snapshot_id, account_id, timestamp, net_liquidation, cash, buying_power,
                    realized_pnl, unrealized_pnl, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    timestamp = excluded.timestamp,
                    net_liquidation = excluded.net_liquidation,
                    cash = excluded.cash,
                    buying_power = excluded.buying_power,
                    realized_pnl = excluded.realized_pnl,
                    unrealized_pnl = excluded.unrealized_pnl,
                    source = excluded.source,
                    updated_at = excluded.updated_at
            """, (
                snapshot.snapshot_id,
                snapshot.account_id,
                _iso(snapshot.timestamp),
                snapshot.net_liquidation,
                snapshot.cash,
                snapshot.buying_power,
                snapshot.realized_pnl,
                snapshot.unrealized_pnl,
                snapshot.source,
                now,
                now,
            ))
            conn.commit()

    def upsert_position(self, position: PositionSnapshot) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO positions (
                    position_id, account_id, symbol, quantity, side, avg_cost,
                    market_price, unrealized_pnl, updated_at, created_at, stored_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    symbol = excluded.symbol,
                    quantity = excluded.quantity,
                    side = excluded.side,
                    avg_cost = excluded.avg_cost,
                    market_price = excluded.market_price,
                    unrealized_pnl = excluded.unrealized_pnl,
                    updated_at = excluded.updated_at,
                    stored_updated_at = excluded.stored_updated_at
            """, (
                position.position_id,
                position.account_id,
                position.symbol,
                position.quantity,
                position.side,
                position.avg_cost,
                position.market_price,
                position.unrealized_pnl,
                _iso(position.updated_at),
                now,
                now,
            ))
            conn.commit()

    def upsert_order_event(self, event: OrderEvent) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO order_events (
                    event_id, account_id, broker, broker_order_id, strategy_order_id, trade_id,
                    event_type, symbol, side, quantity, price, expected_price, slippage_bps,
                    latency_ms, occurred_at, raw_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    broker = excluded.broker,
                    broker_order_id = excluded.broker_order_id,
                    strategy_order_id = excluded.strategy_order_id,
                    trade_id = excluded.trade_id,
                    event_type = excluded.event_type,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    expected_price = excluded.expected_price,
                    slippage_bps = excluded.slippage_bps,
                    latency_ms = excluded.latency_ms,
                    occurred_at = excluded.occurred_at,
                    raw_status = excluded.raw_status,
                    updated_at = excluded.updated_at
            """, (
                event.event_id,
                event.account_id,
                event.broker,
                event.broker_order_id,
                event.strategy_order_id,
                event.trade_id,
                event.event_type,
                event.symbol,
                event.side,
                event.quantity,
                event.price,
                event.expected_price,
                event.slippage_bps,
                event.latency_ms,
                _iso(event.occurred_at),
                event.raw_status,
                now,
                now,
            ))
            conn.commit()

    def get_row(self, table: str, key_column: str, key_value: str) -> Optional[Dict[str, Any]]:
        allowed = {
            "accounts": "account_id",
            "equity_snapshots": "snapshot_id",
            "positions": "position_id",
            "order_events": "event_id",
        }
        if allowed.get(table) != key_column:
            raise ValueError(f"Unsupported lookup: {table}.{key_column}")

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key_value,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def count_rows(self, table: str) -> int:
        if table not in {"accounts", "equity_snapshots", "positions", "order_events"}:
            raise ValueError(f"Unsupported table: {table}")
        return self._get_connection().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@dataclass(frozen=True)
class PublishOutboxEvent:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    destination: str
    payload: Dict[str, Any]
    original_event_timestamp: datetime | str
    next_retry_at: Optional[datetime | str] = None


class PublishOutboxStore(_SQLiteStore):
    """Durable queue of dashboard remote-publication events."""

    def __init__(self, db_path: str = "./data/local/publish_outbox.db"):
        super().__init__(db_path, timeout=5.0)

    def _init_schema(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publish_outbox (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT NOT NULL,
                last_error TEXT,
                original_event_timestamp TEXT NOT NULL,
                claimed_by TEXT,
                claimed_at TEXT,
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_outbox_claim
            ON publish_outbox(status, next_retry_at, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_outbox_aggregate
            ON publish_outbox(aggregate_type, aggregate_id, destination)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publish_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                error TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_failures_event
            ON publish_failures(event_id, created_at DESC)
        """)
        conn.commit()

    def enqueue_event(self, event: PublishOutboxEvent) -> None:
        now = _utc_now_iso()
        next_retry_at = _iso(event.next_retry_at) if event.next_retry_at else now
        payload_json = json.dumps(event.payload, sort_keys=True)
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO publish_outbox (
                    event_id, event_type, aggregate_type, aggregate_id, destination,
                    payload_json, status, attempts, next_retry_at, original_event_timestamp,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    aggregate_type = excluded.aggregate_type,
                    aggregate_id = excluded.aggregate_id,
                    destination = excluded.destination,
                    payload_json = excluded.payload_json,
                    original_event_timestamp = excluded.original_event_timestamp,
                    updated_at = excluded.updated_at
                WHERE publish_outbox.status IN ('pending', 'failed')
            """, (
                event.event_id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.destination,
                payload_json,
                next_retry_at,
                _iso(event.original_event_timestamp),
                now,
                now,
            ))
            conn.commit()

    def claim_pending(self, limit: int, claimed_by: str, now: Optional[datetime | str] = None) -> List[Dict[str, Any]]:
        claim_time = _iso(now) if now else _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                SELECT event_id FROM publish_outbox
                WHERE status IN ('pending', 'failed') AND next_retry_at <= ?
                ORDER BY created_at
                LIMIT ?
            """, (claim_time, limit))
            event_ids = [row[0] for row in cursor.fetchall()]
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                cursor.execute(f"""
                    UPDATE publish_outbox
                    SET status = 'publishing', claimed_by = ?, claimed_at = ?, updated_at = ?
                    WHERE event_id IN ({placeholders}) AND status IN ('pending', 'failed')
                """, [claimed_by, claim_time, claim_time, *event_ids])
            conn.commit()

        return [self.get_event(event_id) for event_id in event_ids if self.get_event(event_id) is not None]

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM publish_outbox WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def mark_published(self, event_id: str, published_at: Optional[datetime | str] = None) -> bool:
        now = _iso(published_at) if published_at else _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'published', published_at = ?, claimed_by = NULL,
                    claimed_at = NULL, updated_at = ?
                WHERE event_id = ?
            """, (now, now, event_id))
            conn.commit()
            return cursor.rowcount > 0

    def mark_failed(self, event_id: str, error: str, next_retry_at: datetime | str) -> bool:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'failed', attempts = attempts + 1, last_error = ?,
                    next_retry_at = ?, claimed_by = NULL, claimed_at = NULL, updated_at = ?
                WHERE event_id = ?
            """, (error, _iso(next_retry_at), now, event_id))
            conn.commit()
            return cursor.rowcount > 0

    def mark_dead_letter(self, event_id: str, error: str) -> bool:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'dead_letter', last_error = ?, claimed_by = NULL,
                    claimed_at = NULL, updated_at = ?
                WHERE event_id = ?
            """, (error, now, event_id))
            conn.commit()
            return cursor.rowcount > 0

    def reset_stale_publishing(self, claimed_before: datetime | str) -> int:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'pending', claimed_by = NULL, claimed_at = NULL, updated_at = ?
                WHERE status = 'publishing' AND claimed_at < ?
            """, (now, _iso(claimed_before)))
            conn.commit()
            return cursor.rowcount

    def count_by_status(self, status: str) -> int:
        conn = self._get_connection()
        return conn.execute("SELECT COUNT(*) FROM publish_outbox WHERE status = ?", (status,)).fetchone()[0]

    def status_counts(self) -> Dict[str, int]:
        conn = self._get_connection()
        rows = conn.execute("""
            SELECT status, COUNT(*) AS count
            FROM publish_outbox
            GROUP BY status
        """).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def record_failure(self, event: Dict[str, Any], error: str) -> int:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                INSERT INTO publish_failures (
                    event_id, aggregate_type, aggregate_id, destination,
                    attempts, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                event["event_id"],
                event["aggregate_type"],
                event["aggregate_id"],
                event["destination"],
                int(event.get("attempts") or 0) + 1,
                error,
                now,
            ))
            conn.commit()
            return cursor.lastrowid

    def get_failures(self, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        if event_id:
            cursor.execute("""
                SELECT * FROM publish_failures
                WHERE event_id = ?
                ORDER BY created_at DESC
            """, (event_id,))
        else:
            cursor.execute("SELECT * FROM publish_failures ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def prune_published_before(self, cutoff_timestamp: datetime | str) -> int:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                DELETE FROM publish_outbox
                WHERE status = 'published' AND published_at < ?
            """, (_iso(cutoff_timestamp),))
            conn.commit()
            return cursor.rowcount

    def enqueue_many(self, events: Sequence[PublishOutboxEvent]) -> None:
        for event in events:
            self.enqueue_event(event)