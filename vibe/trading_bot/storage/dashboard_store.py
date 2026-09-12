"""SQLite stores for live dashboard read-model rows and publish outbox."""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def iter_publish_events(self, trading_day: Any = None) -> List["PublishOutboxEvent"]:
        rows = self._get_connection().execute(
            "SELECT * FROM price_bars ORDER BY bar_start"
        ).fetchall()
        events = []
        for row in rows:
            payload = {
                key: value
                for key, value in dict(row).items()
                if key not in {"created_at", "updated_at"}
            }
            payload["is_complete"] = bool(payload["is_complete"])
            aggregate_id = f"{row['symbol']}|{row['timeframe']}|{row['bar_start']}"
            events.append(PublishOutboxEvent(
                event_id=f"price_bar:{aggregate_id}",
                event_type="upsert",
                aggregate_type="price_bar",
                aggregate_id=aggregate_id,
                destination="supabase",
                payload=payload,
                original_event_timestamp=row["bar_start"],
            ))
        return events


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    broker: str
    display_name: str
    currency: Optional[str] = None
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
    base_currency: Optional[str] = None
    net_liquidation_currency: Optional[str] = None
    cash_currency: Optional[str] = None
    buying_power_currency: Optional[str] = None
    realized_pnl_currency: Optional[str] = None
    unrealized_pnl_currency: Optional[str] = None
    pnl_provenance: Optional[str] = None
    realized_pnl_provenance: Optional[str] = None
    unrealized_pnl_provenance: Optional[str] = None
    pnl_version: Optional[int] = None
    local_realized_pnl: Optional[float] = None
    local_realized_pnl_currency: Optional[str] = None
    granularity: str = "raw"
    period_start: Optional[datetime | str] = None
    event_type: Optional[str] = None
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
    instrument_currency: Optional[str] = None
    unrealized_pnl_currency: Optional[str] = None


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
    execution_id: Optional[str] = None
    permanent_order_id: Optional[str] = None
    trade_currency: Optional[str] = None
    commission: Optional[float] = None
    commission_currency: Optional[str] = None
    decision_at: Optional[datetime | str] = None
    submitted_at: Optional[datetime | str] = None
    filled_at: Optional[datetime | str] = None
    benchmark_type: Optional[str] = None
    benchmark_price: Optional[float] = None
    quote_bid: Optional[float] = None
    quote_ask: Optional[float] = None
    quote_midpoint: Optional[float] = None
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None
    decision_to_submission_latency_ms: Optional[float] = None
    submission_to_fill_latency_ms: Optional[float] = None
    slippage_amount: Optional[float] = None
    slippage_version: int = 2
    slippage_valid: bool = True


@dataclass(frozen=True)
class EquityDownsampleResult:
    aggregated: int = 0
    retained: int = 0
    removed: int = 0


@dataclass(frozen=True)
class StrategyAnnotation:
    annotation_id: str
    account_id: str
    symbol: str
    strategy: str
    trading_day: datetime | str
    annotation_type: str
    key: str
    value_json: Dict[str, Any]
    enabled: bool = True


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
                currency TEXT,
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
                base_currency TEXT,
                net_liquidation_currency TEXT,
                cash_currency TEXT,
                buying_power_currency TEXT,
                realized_pnl_currency TEXT,
                unrealized_pnl_currency TEXT,
                pnl_provenance TEXT,
                realized_pnl_provenance TEXT,
                unrealized_pnl_provenance TEXT,
                pnl_version INTEGER,
                local_realized_pnl REAL,
                local_realized_pnl_currency TEXT,
                granularity TEXT NOT NULL DEFAULT 'raw',
                period_start TEXT,
                event_type TEXT,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        self._migrate_accounts_nullable_currency(conn)
        cursor = conn.cursor()
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
                instrument_currency TEXT,
                unrealized_pnl_currency TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                stored_updated_at TEXT NOT NULL,
                publication_pending INTEGER NOT NULL DEFAULT 1
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
                execution_id TEXT,
                permanent_order_id TEXT,
                trade_currency TEXT,
                commission REAL,
                commission_currency TEXT,
                decision_at TEXT,
                submitted_at TEXT,
                filled_at TEXT,
                benchmark_type TEXT,
                benchmark_price REAL,
                quote_bid REAL,
                quote_ask REAL,
                quote_midpoint REAL,
                stop_price REAL,
                limit_price REAL,
                decision_to_submission_latency_ms REAL,
                submission_to_fill_latency_ms REAL,
                slippage_amount REAL,
                slippage_version INTEGER NOT NULL DEFAULT 1,
                slippage_valid INTEGER NOT NULL DEFAULT 0,
                occurred_at TEXT NOT NULL,
                raw_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategy_annotations (
                annotation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                trading_day TEXT NOT NULL,
                annotation_type TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS equity_retention_jobs (
                job_id TEXT PRIMARY KEY,
                aggregate_snapshot_id TEXT NOT NULL,
                source_snapshot_ids_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'aggregate_pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self._ensure_columns(conn, "equity_snapshots", {
            "base_currency": "TEXT",
            "net_liquidation_currency": "TEXT",
            "cash_currency": "TEXT",
            "buying_power_currency": "TEXT",
            "realized_pnl_currency": "TEXT",
            "unrealized_pnl_currency": "TEXT",
            "pnl_provenance": "TEXT",
            "realized_pnl_provenance": "TEXT",
            "unrealized_pnl_provenance": "TEXT",
            "pnl_version": "INTEGER",
            "local_realized_pnl": "REAL",
            "local_realized_pnl_currency": "TEXT",
            "granularity": "TEXT NOT NULL DEFAULT 'raw'",
            "period_start": "TEXT",
            "event_type": "TEXT",
        })
        self._ensure_columns(conn, "positions", {
            "instrument_currency": "TEXT",
            "unrealized_pnl_currency": "TEXT",
            "publication_pending": "INTEGER NOT NULL DEFAULT 1",
        })
        self._ensure_columns(conn, "order_events", {
            "execution_id": "TEXT",
            "permanent_order_id": "TEXT",
            "trade_currency": "TEXT",
            "commission": "REAL",
            "commission_currency": "TEXT",
            "decision_at": "TEXT",
            "submitted_at": "TEXT",
            "filled_at": "TEXT",
            "benchmark_type": "TEXT",
            "benchmark_price": "REAL",
            "quote_bid": "REAL",
            "quote_ask": "REAL",
            "quote_midpoint": "REAL",
            "stop_price": "REAL",
            "limit_price": "REAL",
            "decision_to_submission_latency_ms": "REAL",
            "submission_to_fill_latency_ms": "REAL",
            "slippage_amount": "REAL",
            "slippage_version": "INTEGER NOT NULL DEFAULT 1",
            "slippage_valid": "INTEGER NOT NULL DEFAULT 0",
        })
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_equity_account_time ON equity_snapshots(account_id, timestamp DESC)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_equity_retention "
            "ON equity_snapshots(account_id, granularity, timestamp)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_symbol ON positions(account_id, symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_events_account_time ON order_events(account_id, occurred_at DESC)")
        duplicate_execution = cursor.execute(
            """
            SELECT execution_id FROM order_events
            WHERE execution_id IS NOT NULL
            GROUP BY execution_id HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_execution is not None:
            raise RuntimeError(
                "Cannot create execution uniqueness constraint: duplicate execution_id "
                f"{duplicate_execution['execution_id']!r} requires reviewed reconciliation"
            )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_order_events_execution "
            "ON order_events(execution_id) WHERE execution_id IS NOT NULL"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategy_annotations_symbol_day ON strategy_annotations(symbol, trading_day)")
        conn.commit()

    @staticmethod
    def _migrate_accounts_nullable_currency(conn: sqlite3.Connection) -> None:
        """Transactionally recover/rebuild the nullable account currency schema."""
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                row["name"]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            account_currency = next(
                (
                    row
                    for row in cursor.execute("PRAGMA table_info(accounts)")
                    if row["name"] == "currency"
                ),
                None,
            )
            legacy_exists = "accounts_legacy_currency" in tables
            needs_rebuild = bool(
                account_currency is not None and account_currency["notnull"]
            )

            if needs_rebuild and not legacy_exists:
                cursor.execute(
                    "ALTER TABLE accounts RENAME TO accounts_legacy_currency"
                )
                legacy_exists = True
                tables.discard("accounts")
                tables.add("accounts_legacy_currency")

            if needs_rebuild:
                # This branch is possible when an older non-transactional
                # migration left both tables behind.
                cursor.execute("""
                    CREATE TABLE accounts_currency_rebuild (
                        account_id TEXT PRIMARY KEY,
                        broker TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        currency TEXT,
                        mode TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    INSERT INTO accounts_currency_rebuild
                    SELECT account_id, broker, display_name, currency, mode,
                           created_at, updated_at
                    FROM accounts_legacy_currency
                """)
                if "accounts" in tables:
                    cursor.execute("""
                        INSERT INTO accounts_currency_rebuild
                        SELECT account_id, broker, display_name, currency, mode,
                               created_at, updated_at
                        FROM accounts
                        WHERE true
                        ON CONFLICT(account_id) DO UPDATE SET
                            broker = excluded.broker,
                            display_name = excluded.display_name,
                            currency = excluded.currency,
                            mode = excluded.mode,
                            created_at = excluded.created_at,
                            updated_at = excluded.updated_at
                        WHERE excluded.updated_at
                              > accounts_currency_rebuild.updated_at
                    """)
                    cursor.execute("DROP TABLE accounts")
                cursor.execute("DROP TABLE accounts_legacy_currency")
                cursor.execute(
                    "ALTER TABLE accounts_currency_rebuild RENAME TO accounts"
                )
            elif legacy_exists:
                cursor.execute("""
                    INSERT INTO accounts (
                        account_id, broker, display_name, currency, mode,
                        created_at, updated_at
                    )
                    SELECT account_id, broker, display_name, currency, mode,
                           created_at, updated_at
                    FROM accounts_legacy_currency
                    WHERE true
                    ON CONFLICT(account_id) DO UPDATE SET
                        broker = excluded.broker,
                        display_name = excluded.display_name,
                        currency = excluded.currency,
                        mode = excluded.mode,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at
                    WHERE excluded.updated_at > accounts.updated_at
                """)
                cursor.execute("DROP TABLE accounts_legacy_currency")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
                    realized_pnl, unrealized_pnl, base_currency, net_liquidation_currency,
                    cash_currency, buying_power_currency, realized_pnl_currency,
                    unrealized_pnl_currency, pnl_provenance,
                    realized_pnl_provenance, unrealized_pnl_provenance, pnl_version,
                    local_realized_pnl, local_realized_pnl_currency, granularity,
                    period_start, event_type, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    timestamp = excluded.timestamp,
                    net_liquidation = excluded.net_liquidation,
                    cash = excluded.cash,
                    buying_power = excluded.buying_power,
                    realized_pnl = excluded.realized_pnl,
                    unrealized_pnl = excluded.unrealized_pnl,
                    base_currency = excluded.base_currency,
                    net_liquidation_currency = excluded.net_liquidation_currency,
                    cash_currency = excluded.cash_currency,
                    buying_power_currency = excluded.buying_power_currency,
                    realized_pnl_currency = excluded.realized_pnl_currency,
                    unrealized_pnl_currency = excluded.unrealized_pnl_currency,
                    pnl_provenance = excluded.pnl_provenance,
                    realized_pnl_provenance = excluded.realized_pnl_provenance,
                    unrealized_pnl_provenance = excluded.unrealized_pnl_provenance,
                    pnl_version = excluded.pnl_version,
                    local_realized_pnl = excluded.local_realized_pnl,
                    local_realized_pnl_currency = excluded.local_realized_pnl_currency,
                    granularity = excluded.granularity,
                    period_start = excluded.period_start,
                    event_type = excluded.event_type,
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
                snapshot.base_currency,
                snapshot.net_liquidation_currency,
                snapshot.cash_currency,
                snapshot.buying_power_currency,
                snapshot.realized_pnl_currency,
                snapshot.unrealized_pnl_currency,
                snapshot.pnl_provenance,
                snapshot.realized_pnl_provenance,
                snapshot.unrealized_pnl_provenance,
                snapshot.pnl_version,
                snapshot.local_realized_pnl,
                snapshot.local_realized_pnl_currency,
                snapshot.granularity,
                _iso(snapshot.period_start) if snapshot.period_start is not None else None,
                snapshot.event_type,
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
                    market_price, unrealized_pnl, instrument_currency, unrealized_pnl_currency,
                    updated_at, created_at, stored_updated_at, publication_pending
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(position_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    symbol = excluded.symbol,
                    quantity = excluded.quantity,
                    side = excluded.side,
                    avg_cost = excluded.avg_cost,
                    market_price = excluded.market_price,
                    unrealized_pnl = excluded.unrealized_pnl,
                    instrument_currency = excluded.instrument_currency,
                    unrealized_pnl_currency = excluded.unrealized_pnl_currency,
                    updated_at = excluded.updated_at,
                    stored_updated_at = excluded.stored_updated_at,
                    publication_pending = 1
            """, (
                position.position_id,
                position.account_id,
                position.symbol,
                position.quantity,
                position.side,
                position.avg_cost,
                position.market_price,
                position.unrealized_pnl,
                position.instrument_currency,
                position.unrealized_pnl_currency,
                _iso(position.updated_at),
                now,
                now,
            ))
            conn.commit()

    def position_needs_publication(self, position_id: str) -> bool:
        row = self._get_connection().execute(
            "SELECT publication_pending FROM positions WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        return bool(row and row["publication_pending"])

    def mark_position_enqueued(self, position_id: str) -> None:
        with self._lock:
            self._get_connection().execute(
                "UPDATE positions SET publication_pending = 0 WHERE position_id = ?",
                (position_id,),
            )
            self._get_connection().commit()

    def upsert_position_if_changed(
        self,
        position: PositionSnapshot,
        *,
        market_price_threshold: float = 0.01,
        unrealized_pnl_threshold: float = 0.01,
    ) -> bool:
        """Persist a position only when normalized business state changed."""
        existing = self.get_row("positions", "position_id", position.position_id)
        if existing is not None:
            unchanged = (
                round(float(existing["quantity"]), 8) == round(float(position.quantity), 8)
                and existing["side"] == position.side
                and self._within_threshold(existing["avg_cost"], position.avg_cost, 1e-8)
                and self._within_threshold(
                    existing["market_price"], position.market_price, market_price_threshold
                )
                and self._within_threshold(
                    existing["unrealized_pnl"], position.unrealized_pnl, unrealized_pnl_threshold
                )
                and existing["instrument_currency"] == position.instrument_currency
                and existing["unrealized_pnl_currency"] == position.unrealized_pnl_currency
            )
            if unchanged:
                return False
        self.upsert_position(position)
        return True

    @staticmethod
    def _within_threshold(left: Optional[float], right: Optional[float], threshold: float) -> bool:
        if left is None or right is None:
            return left is None and right is None
        return abs(round(float(left), 8) - round(float(right), 8)) < threshold

    def downsample_equity_snapshots(
        self,
        *,
        now: datetime,
        raw_retention_days: int = 14,
        five_minute_retention_days: int = 90,
        five_minute_bucket_minutes: int = 5,
        market_timezone: str = "America/New_York",
    ) -> EquityDownsampleResult:
        """Transactionally replace old raw snapshots with final-in-bucket snapshots.

        Non-poll snapshots are retained as event-associated raw observations. If
        the transaction is interrupted, SQLite rolls back both aggregate writes
        and raw-row deletes.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if raw_retention_days < 0 or five_minute_retention_days <= raw_retention_days:
            raise ValueError("five-minute retention must exceed raw retention")
        if five_minute_bucket_minutes <= 0:
            raise ValueError("five-minute bucket size must be positive")

        market_tz = ZoneInfo(market_timezone)
        raw_cutoff = now - timedelta(days=raw_retention_days)
        daily_cutoff = now - timedelta(days=five_minute_retention_days)
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                rows = cursor.execute(
                    """
                    SELECT * FROM equity_snapshots
                    WHERE granularity <> '1d'
                    ORDER BY account_id, timestamp
                    """
                ).fetchall()
                retained = 0
                buckets: Dict[tuple[str, str, str], sqlite3.Row] = {}
                bucket_observed_at: Dict[tuple[str, str, str], datetime] = {}
                bucket_sources: Dict[tuple[str, str, str], List[str]] = {}
                for row in rows:
                    if row["granularity"] == "raw" and row["event_type"] not in (None, "", "poll"):
                        retained += 1
                        continue
                    observed = datetime.fromisoformat(row["timestamp"])
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    if row["granularity"] == "raw" and observed >= raw_cutoff:
                        continue
                    if row["granularity"] != "raw" and observed >= daily_cutoff:
                        continue
                    local = observed.astimezone(market_tz)
                    if row["granularity"] == "raw" and observed >= daily_cutoff:
                        minute = local.minute - (local.minute % five_minute_bucket_minutes)
                        bucket_start = local.replace(minute=minute, second=0, microsecond=0)
                        granularity = f"{five_minute_bucket_minutes}m"
                    else:
                        bucket_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                        granularity = "1d"
                    key = (row["account_id"], granularity, bucket_start.isoformat())
                    if (
                        key not in bucket_observed_at
                        or observed > bucket_observed_at[key]
                    ):
                        buckets[key] = row
                        bucket_observed_at[key] = observed
                    bucket_sources.setdefault(key, []).append(row["snapshot_id"])

                now_iso = _utc_now_iso()
                for (account_id, granularity, period_start), row in buckets.items():
                    period_utc = datetime.fromisoformat(period_start).astimezone(timezone.utc)
                    snapshot_id = f"{account_id}:{granularity}:{period_utc.isoformat()}"
                    cursor.execute(
                        """
                        INSERT INTO equity_snapshots (
                            snapshot_id, account_id, timestamp, net_liquidation, cash, buying_power,
                            realized_pnl, unrealized_pnl, base_currency, net_liquidation_currency,
                            cash_currency, buying_power_currency, realized_pnl_currency,
                            unrealized_pnl_currency, pnl_provenance,
                            realized_pnl_provenance, unrealized_pnl_provenance, pnl_version,
                            local_realized_pnl, local_realized_pnl_currency,
                            granularity, period_start, event_type, source, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                        ON CONFLICT(snapshot_id) DO UPDATE SET
                            timestamp = excluded.timestamp,
                            net_liquidation = excluded.net_liquidation,
                            cash = excluded.cash,
                            buying_power = excluded.buying_power,
                            realized_pnl = excluded.realized_pnl,
                            unrealized_pnl = excluded.unrealized_pnl,
                            base_currency = excluded.base_currency,
                            net_liquidation_currency = excluded.net_liquidation_currency,
                            cash_currency = excluded.cash_currency,
                            buying_power_currency = excluded.buying_power_currency,
                            realized_pnl_currency = excluded.realized_pnl_currency,
                            unrealized_pnl_currency = excluded.unrealized_pnl_currency,
                            pnl_provenance = excluded.pnl_provenance,
                            realized_pnl_provenance = excluded.realized_pnl_provenance,
                            unrealized_pnl_provenance = excluded.unrealized_pnl_provenance,
                            pnl_version = excluded.pnl_version,
                            local_realized_pnl = excluded.local_realized_pnl,
                            local_realized_pnl_currency = excluded.local_realized_pnl_currency,
                            granularity = excluded.granularity,
                            period_start = excluded.period_start,
                            source = excluded.source,
                            updated_at = excluded.updated_at
                        WHERE julianday(excluded.timestamp)
                              > julianday(equity_snapshots.timestamp)
                        """,
                        (
                            snapshot_id,
                            account_id,
                            row["timestamp"],
                            row["net_liquidation"],
                            row["cash"],
                            row["buying_power"],
                            row["realized_pnl"],
                            row["unrealized_pnl"],
                            row["base_currency"],
                            row["net_liquidation_currency"],
                            row["cash_currency"],
                            row["buying_power_currency"],
                            row["realized_pnl_currency"],
                            row["unrealized_pnl_currency"],
                            row["pnl_provenance"],
                            row["realized_pnl_provenance"],
                            row["unrealized_pnl_provenance"],
                            row["pnl_version"],
                            row["local_realized_pnl"],
                            row["local_realized_pnl_currency"],
                            granularity,
                            period_start,
                            "downsampled",
                            now_iso,
                            now_iso,
                        ),
                    )
                    source_ids = sorted(set(bucket_sources[(account_id, granularity, period_start)]))
                    cursor.execute(
                        """
                        INSERT INTO equity_retention_jobs (
                            job_id, aggregate_snapshot_id, source_snapshot_ids_json,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, 'aggregate_pending', ?, ?)
                        ON CONFLICT(job_id) DO UPDATE SET
                            source_snapshot_ids_json = excluded.source_snapshot_ids_json,
                            status = CASE
                                WHEN equity_retention_jobs.source_snapshot_ids_json
                                     <> excluded.source_snapshot_ids_json
                                THEN 'aggregate_pending'
                                ELSE equity_retention_jobs.status
                            END,
                            updated_at = excluded.updated_at
                        """,
                        (
                            f"equity-retention:{snapshot_id}",
                            snapshot_id,
                            json.dumps(source_ids),
                            now_iso,
                            now_iso,
                        ),
                    )
                conn.commit()
                return EquityDownsampleResult(
                    aggregated=len(buckets),
                    retained=retained,
                    removed=0,
                )
            except (sqlite3.Error, ValueError):
                conn.rollback()
                raise

    def pending_equity_retention_jobs(self) -> List[Dict[str, Any]]:
        rows = self._get_connection().execute(
            """
            SELECT * FROM equity_retention_jobs
            WHERE status <> 'complete'
            ORDER BY created_at, job_id
            """
        ).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            job["source_snapshot_ids"] = json.loads(job.pop("source_snapshot_ids_json"))
            jobs.append(job)
        return jobs

    def equity_snapshot_payload(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        row = self.get_row("equity_snapshots", "snapshot_id", snapshot_id)
        if row is None:
            return None
        return {
            key: value
            for key, value in row.items()
            if key not in {"created_at", "updated_at"}
        }

    def complete_equity_retention_job(self, job_id: str) -> int:
        """Delete local source rows only after remote deletion was confirmed."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    """
                    SELECT aggregate_snapshot_id, source_snapshot_ids_json
                    FROM equity_retention_jobs WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return 0
                source_ids = [
                    item
                    for item in json.loads(row["source_snapshot_ids_json"])
                    if item != row["aggregate_snapshot_id"]
                ]
                removed = 0
                for start in range(0, len(source_ids), 500):
                    batch = source_ids[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    removed += cursor.execute(
                        f"DELETE FROM equity_snapshots WHERE snapshot_id IN ({placeholders})",
                        batch,
                    ).rowcount
                cursor.execute(
                    """
                    UPDATE equity_retention_jobs
                    SET status = 'complete', updated_at = ?
                    WHERE job_id = ?
                    """,
                    (_utc_now_iso(), job_id),
                )
                conn.commit()
                return removed
            except (sqlite3.Error, ValueError, json.JSONDecodeError):
                conn.rollback()
                raise

    def upsert_order_event(self, event: OrderEvent) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO order_events (
                    event_id, account_id, broker, broker_order_id, strategy_order_id, trade_id,
                    event_type, symbol, side, quantity, price, expected_price, slippage_bps,
                    latency_ms, execution_id, permanent_order_id, trade_currency, commission,
                    commission_currency, decision_at, submitted_at, filled_at, benchmark_type,
                    benchmark_price, quote_bid, quote_ask, quote_midpoint, stop_price, limit_price,
                    decision_to_submission_latency_ms, submission_to_fill_latency_ms,
                    slippage_amount, slippage_version, slippage_valid, occurred_at, raw_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    broker = excluded.broker,
                    broker_order_id = excluded.broker_order_id,
                    strategy_order_id = excluded.strategy_order_id,
                    trade_id = COALESCE(excluded.trade_id, order_events.trade_id),
                    event_type = excluded.event_type,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    expected_price = excluded.expected_price,
                    slippage_bps = excluded.slippage_bps,
                    latency_ms = excluded.latency_ms,
                    execution_id = excluded.execution_id,
                    permanent_order_id = excluded.permanent_order_id,
                    trade_currency = excluded.trade_currency,
                    commission = excluded.commission,
                    commission_currency = excluded.commission_currency,
                    decision_at = excluded.decision_at,
                    submitted_at = excluded.submitted_at,
                    filled_at = excluded.filled_at,
                    benchmark_type = excluded.benchmark_type,
                    benchmark_price = excluded.benchmark_price,
                    quote_bid = excluded.quote_bid,
                    quote_ask = excluded.quote_ask,
                    quote_midpoint = excluded.quote_midpoint,
                    stop_price = excluded.stop_price,
                    limit_price = excluded.limit_price,
                    decision_to_submission_latency_ms = excluded.decision_to_submission_latency_ms,
                    submission_to_fill_latency_ms = excluded.submission_to_fill_latency_ms,
                    slippage_amount = excluded.slippage_amount,
                    slippage_version = excluded.slippage_version,
                    slippage_valid = excluded.slippage_valid,
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
                event.execution_id,
                event.permanent_order_id,
                event.trade_currency,
                event.commission,
                event.commission_currency,
                _iso(event.decision_at) if event.decision_at is not None else None,
                _iso(event.submitted_at) if event.submitted_at is not None else None,
                _iso(event.filled_at) if event.filled_at is not None else None,
                event.benchmark_type,
                event.benchmark_price,
                event.quote_bid,
                event.quote_ask,
                event.quote_midpoint,
                event.stop_price,
                event.limit_price,
                event.decision_to_submission_latency_ms,
                event.submission_to_fill_latency_ms,
                event.slippage_amount,
                event.slippage_version,
                1 if event.slippage_valid else 0,
                _iso(event.occurred_at),
                event.raw_status,
                now,
                now,
            ))
            conn.commit()

    def upsert_strategy_annotation(self, annotation: StrategyAnnotation) -> None:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO strategy_annotations (
                    annotation_id, account_id, symbol, strategy, trading_day,
                    annotation_type, key, value_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(annotation_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    symbol = excluded.symbol,
                    strategy = excluded.strategy,
                    trading_day = excluded.trading_day,
                    annotation_type = excluded.annotation_type,
                    key = excluded.key,
                    value_json = excluded.value_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
            """, (
                annotation.annotation_id,
                annotation.account_id,
                annotation.symbol,
                annotation.strategy,
                _iso(annotation.trading_day),
                annotation.annotation_type,
                annotation.key,
                json.dumps(annotation.value_json, sort_keys=True),
                1 if annotation.enabled else 0,
                now,
                now,
            ))
            conn.commit()

    def iter_publish_events(self, trading_day: Any = None) -> List["PublishOutboxEvent"]:
        """Reconstruct all durable read-model events after an enqueue gap."""
        events: List[PublishOutboxEvent] = []
        conn = self._get_connection()
        specifications = (
            ("accounts", "account_id", "account", "account_id", "updated_at"),
            ("equity_snapshots", "snapshot_id", "equity_snapshot", "snapshot_id", "timestamp"),
            ("positions", "position_id", "position", "position_id", "updated_at"),
            ("order_events", "event_id", "order_event", "event_id", "occurred_at"),
            (
                "strategy_annotations",
                "annotation_id",
                "strategy_annotation",
                "annotation_id",
                "updated_at",
            ),
        )
        for table, key_column, aggregate_type, id_column, timestamp_column in specifications:
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY {timestamp_column}"
            ).fetchall()
            for row in rows:
                excluded = {"created_at", "stored_updated_at", "publication_pending"}
                if aggregate_type not in {"account", "position", "strategy_annotation"}:
                    excluded.add("updated_at")
                payload = {
                    key: value
                    for key, value in dict(row).items()
                    if key not in excluded
                }
                if aggregate_type == "strategy_annotation":
                    payload["value_json"] = json.loads(payload["value_json"])
                    payload["enabled"] = bool(payload["enabled"])
                if aggregate_type == "order_event":
                    payload["slippage_valid"] = bool(payload["slippage_valid"])
                if aggregate_type == "position":
                    event_id = f"position:{row[id_column]}"
                else:
                    event_id = f"{aggregate_type}:{row[id_column]}"
                events.append(PublishOutboxEvent(
                    event_id=event_id,
                    event_type="upsert",
                    aggregate_type=aggregate_type,
                    aggregate_id=row[id_column],
                    destination="supabase",
                    payload=payload,
                    original_event_timestamp=row[timestamp_column],
                ))
        return events

    def iter_execution_order_events(self) -> List[OrderEvent]:
        """Return durable executions for idempotent metric reconstruction."""
        rows = self._get_connection().execute(
            "SELECT * FROM order_events WHERE execution_id IS NOT NULL ORDER BY occurred_at"
        ).fetchall()
        events: List[OrderEvent] = []
        field_names = set(OrderEvent.__dataclass_fields__)
        for row in rows:
            values = {
                key: value
                for key, value in dict(row).items()
                if key in field_names
            }
            values["slippage_valid"] = bool(values.get("slippage_valid"))
            events.append(OrderEvent(**values))
        return events

    def get_row(self, table: str, key_column: str, key_value: str) -> Optional[Dict[str, Any]]:
        allowed = {
            "accounts": "account_id",
            "equity_snapshots": "snapshot_id",
            "positions": "position_id",
            "order_events": "event_id",
            "strategy_annotations": "annotation_id",
        }
        if allowed.get(table) != key_column:
            raise ValueError(f"Unsupported lookup: {table}.{key_column}")

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key_value,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        if table == "strategy_annotations":
            result["value_json"] = json.loads(result["value_json"])
        return result

    def count_rows(self, table: str) -> int:
        if table not in {"accounts", "equity_snapshots", "positions", "order_events", "strategy_annotations"}:
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
                payload_version INTEGER NOT NULL DEFAULT 1,
                publication_version INTEGER NOT NULL DEFAULT 1,
                logical_event_id TEXT,
                successor_version INTEGER NOT NULL DEFAULT 0,
                predecessor_event_id TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT NOT NULL,
                last_error TEXT,
                original_event_timestamp TEXT NOT NULL,
                claimed_by TEXT,
                claimed_at TEXT,
                claimed_payload_version INTEGER,
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        existing_outbox_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(publish_outbox)")
        }
        if "payload_version" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox "
                "ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 1"
            )
        if "publication_version" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox "
                "ADD COLUMN publication_version INTEGER NOT NULL DEFAULT 1"
            )
        if "claimed_payload_version" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox "
                "ADD COLUMN claimed_payload_version INTEGER"
            )
        if "logical_event_id" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox ADD COLUMN logical_event_id TEXT"
            )
        if "successor_version" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox "
                "ADD COLUMN successor_version INTEGER NOT NULL DEFAULT 0"
            )
        if "predecessor_event_id" not in existing_outbox_columns:
            cursor.execute(
                "ALTER TABLE publish_outbox ADD COLUMN predecessor_event_id TEXT"
            )
        cursor.execute(
            """
            UPDATE publish_outbox
            SET logical_event_id = event_id
            WHERE logical_event_id IS NULL
            """
        )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_outbox_claim
            ON publish_outbox(status, next_retry_at, created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_outbox_aggregate
            ON publish_outbox(aggregate_type, aggregate_id, destination)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_outbox_successor
            ON publish_outbox(logical_event_id, successor_version)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publish_outbox_published
            ON publish_outbox(status, published_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publication_ledger (
                event_id TEXT PRIMARY KEY,
                logical_event_id TEXT,
                successor_version INTEGER NOT NULL DEFAULT 0,
                payload_hash TEXT NOT NULL,
                published_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publication_version_watermarks (
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                high_water_mark INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (aggregate_type, aggregate_id, destination)
            )
        """)
        # Seed upgrades from all still-present rows. This table is deliberately
        # outside outbox retention and is never pruned.
        cursor.execute("""
            INSERT INTO publication_version_watermarks (
                aggregate_type, aggregate_id, destination, high_water_mark, updated_at
            )
            SELECT aggregate_type, aggregate_id, destination,
                   MAX(publication_version), ?
            FROM publish_outbox
            GROUP BY aggregate_type, aggregate_id, destination
            ON CONFLICT(aggregate_type, aggregate_id, destination) DO UPDATE SET
                high_water_mark = MAX(
                    publication_version_watermarks.high_water_mark,
                    excluded.high_water_mark
                ),
                updated_at = excluded.updated_at
        """, (_utc_now_iso(),))
        ledger_columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(publication_ledger)")
        }
        if "logical_event_id" not in ledger_columns:
            cursor.execute(
                "ALTER TABLE publication_ledger ADD COLUMN logical_event_id TEXT"
            )
        if "successor_version" not in ledger_columns:
            cursor.execute(
                "ALTER TABLE publication_ledger "
                "ADD COLUMN successor_version INTEGER NOT NULL DEFAULT 0"
            )
        published_rows = cursor.execute(
            """
            SELECT event_id, logical_event_id, successor_version,
                   payload_json, published_at
            FROM publish_outbox
            WHERE status = 'published' AND published_at IS NOT NULL
            """
        ).fetchall()
        for row in published_rows:
            payload_hash = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
            cursor.execute(
                """
                INSERT OR IGNORE INTO publication_ledger (
                    event_id, logical_event_id, successor_version,
                    payload_hash, published_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["event_id"],
                    row["logical_event_id"] or row["event_id"],
                    row["successor_version"],
                    payload_hash,
                    row["published_at"],
                ),
            )
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

    def enqueue_event(self, event: PublishOutboxEvent) -> bool:
        """Append an immutable payload version for a logical remote upsert."""
        now = _utc_now_iso()
        next_retry_at = _iso(event.next_retry_at) if event.next_retry_at else now
        payload_json = json.dumps(event.payload, sort_keys=True, default=_iso)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE")
            logical_event_id = event.event_id
            latest = conn.execute(
                """
                SELECT event_id, payload_json, status, successor_version
                FROM publish_outbox
                WHERE logical_event_id = ? OR event_id = ?
                ORDER BY successor_version DESC
                LIMIT 1
                """,
                (logical_event_id, logical_event_id),
            ).fetchone()
            latest_ledger = conn.execute(
                """
                SELECT event_id, payload_hash, successor_version
                FROM publication_ledger
                WHERE logical_event_id = ? OR event_id = ?
                ORDER BY successor_version DESC
                LIMIT 1
                """,
                (logical_event_id, logical_event_id),
            ).fetchone()
            outbox_successor = (
                int(latest["successor_version"]) if latest is not None else -1
            )
            ledger_successor = (
                int(latest_ledger["successor_version"])
                if latest_ledger is not None
                else -1
            )
            latest_is_outbox = outbox_successor >= ledger_successor
            duplicate_latest = (
                latest_is_outbox
                and latest is not None
                and latest["payload_json"] == payload_json
                and latest["status"] != "dead_letter"
            ) or (
                not latest_is_outbox
                and latest_ledger is not None
                and latest_ledger["payload_hash"] == payload_hash
            )
            if duplicate_latest:
                conn.rollback()
                return False

            latest_successor = max(
                outbox_successor,
                ledger_successor,
            )
            successor_version = latest_successor + 1
            predecessor_event_id = (
                latest["event_id"]
                if latest_is_outbox and latest is not None
                else latest_ledger["event_id"]
                if latest_ledger is not None
                else None
            )
            publication_version = int(conn.execute(
                """
                INSERT INTO publication_version_watermarks (
                    aggregate_type, aggregate_id, destination,
                    high_water_mark, updated_at
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(aggregate_type, aggregate_id, destination) DO UPDATE SET
                    high_water_mark =
                        publication_version_watermarks.high_water_mark + 1,
                    updated_at = excluded.updated_at
                RETURNING high_water_mark
                """,
                (
                    event.aggregate_type,
                    event.aggregate_id,
                    event.destination,
                    now,
                ),
            ).fetchone()[0])
            target_event_id = (
                logical_event_id
                if successor_version == 0
                else f"{logical_event_id}::successor::{successor_version}"
            )
            ledger = conn.execute(
                "SELECT payload_hash FROM publication_ledger WHERE event_id = ?",
                (target_event_id,),
            ).fetchone()
            if ledger is not None and ledger["payload_hash"] == payload_hash:
                conn.rollback()
                return False
            cursor = conn.execute("""
                INSERT INTO publish_outbox (
                    event_id, event_type, aggregate_type, aggregate_id, destination,
                    payload_json, publication_version, logical_event_id, successor_version,
                    predecessor_event_id, status, attempts, next_retry_at,
                    original_event_timestamp, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
            """, (
                target_event_id,
                event.event_type,
                event.aggregate_type,
                event.aggregate_id,
                event.destination,
                payload_json,
                publication_version,
                logical_event_id,
                successor_version,
                predecessor_event_id,
                next_retry_at,
                _iso(event.original_event_timestamp),
                now,
                now,
            ))
            # Older versions that have not started publishing can never be
            # retried after a successor exists. An in-flight predecessor stays
            # immutable and must finish before the successor can be claimed.
            conn.execute(
                """
                UPDATE publish_outbox
                SET status = 'superseded', updated_at = ?
                WHERE logical_event_id = ?
                  AND successor_version < ?
                  AND status IN ('pending', 'failed')
                """,
                (now, logical_event_id, successor_version),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_event_successors(self, event_id: str) -> List[Dict[str, Any]]:
        """Return the immutable dead letter and all versioned successors."""
        rows = self._get_connection().execute(
            """
            SELECT * FROM publish_outbox
            WHERE logical_event_id = ?
            ORDER BY successor_version
            """,
            (event_id,),
        ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            events.append(item)
        return events

    def claim_pending(self, limit: int, claimed_by: str, now: Optional[datetime | str] = None) -> List[Dict[str, Any]]:
        claim_time = _iso(now) if now else _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                SELECT candidate.event_id
                FROM publish_outbox AS candidate
                WHERE candidate.status IN ('pending', 'failed')
                  AND candidate.next_retry_at <= ?
                  AND candidate.successor_version = (
                      SELECT MAX(latest.successor_version)
                      FROM publish_outbox AS latest
                      WHERE latest.logical_event_id = candidate.logical_event_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM publish_outbox AS active
                      WHERE active.logical_event_id = candidate.logical_event_id
                        AND active.status = 'publishing'
                  )
                ORDER BY candidate.created_at, candidate.successor_version
                LIMIT ?
            """, (claim_time, limit))
            event_ids = [row[0] for row in cursor.fetchall()]
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                cursor.execute(f"""
                    UPDATE publish_outbox
                    SET status = 'publishing', claimed_by = ?, claimed_at = ?,
                        claimed_payload_version = payload_version, updated_at = ?
                    WHERE event_id IN ({placeholders}) AND status IN ('pending', 'failed')
                """, [claimed_by, claim_time, claim_time, *event_ids])
            conn.commit()

        return [self.get_event(event_id) for event_id in event_ids if self.get_event(event_id) is not None]

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        if "::successor::" in event_id:
            cursor.execute(
                "SELECT * FROM publish_outbox WHERE event_id = ?",
                (event_id,),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM publish_outbox
                WHERE event_id = ? OR logical_event_id = ?
                ORDER BY successor_version DESC
                LIMIT 1
                """,
                (event_id, event_id),
            )
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def mark_published(
        self,
        event_id: str,
        published_at: Optional[datetime | str] = None,
        expected_payload_version: Optional[int] = None,
    ) -> bool:
        now = _iso(published_at) if published_at else _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                """
                SELECT payload_json FROM publish_outbox
                WHERE event_id = ?
                  AND (? IS NULL OR payload_version = ?)
                """,
                (event_id, expected_payload_version, expected_payload_version),
            ).fetchone()
            if row is None:
                return False
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'published', published_at = ?, claimed_by = NULL,
                    claimed_at = NULL, claimed_payload_version = NULL, updated_at = ?
                WHERE event_id = ?
                  AND (? IS NULL OR (
                      status = 'publishing'
                      AND payload_version = ?
                      AND claimed_payload_version = ?
                  ))
            """, (
                now,
                now,
                event_id,
                expected_payload_version,
                expected_payload_version,
                expected_payload_version,
            ))
            if cursor.rowcount == 0:
                conn.commit()
                return False
            payload_hash = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO publication_ledger (
                    event_id, logical_event_id, successor_version,
                    payload_hash, published_at
                )
                SELECT event_id, logical_event_id, successor_version, ?, ?
                FROM publish_outbox
                WHERE event_id = ?
                ON CONFLICT(event_id) DO UPDATE SET
                    logical_event_id = excluded.logical_event_id,
                    successor_version = excluded.successor_version,
                    payload_hash = excluded.payload_hash,
                    published_at = excluded.published_at
                """,
                (payload_hash, now, event_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def is_published(self, event_id: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        expected_hash = None
        if payload is not None:
            payload_json = json.dumps(payload, sort_keys=True, default=_iso)
            expected_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        row = self._get_connection().execute(
            """
            SELECT payload_hash
            FROM publication_ledger
            WHERE (event_id = ? OR logical_event_id = ?)
              AND (? IS NULL OR payload_hash = ?)
            ORDER BY successor_version DESC
            LIMIT 1
            """,
            (event_id, event_id, expected_hash, expected_hash),
        ).fetchone()
        if row is not None:
            return True
        outbox = self._get_connection().execute(
            """
            SELECT payload_json FROM publish_outbox
            WHERE (event_id = ? OR logical_event_id = ?)
              AND status = 'published'
            ORDER BY successor_version DESC
            LIMIT 1
            """,
            (event_id, event_id),
        ).fetchone()
        if outbox is None:
            return False
        if expected_hash is None:
            return True
        actual_hash = hashlib.sha256(outbox["payload_json"].encode("utf-8")).hexdigest()
        return actual_hash == expected_hash

    def mark_failed(
        self,
        event_id: str,
        error: str,
        next_retry_at: datetime | str,
        expected_payload_version: Optional[int] = None,
    ) -> bool:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM publish_outbox AS newer
                            WHERE newer.logical_event_id = publish_outbox.logical_event_id
                              AND newer.successor_version > publish_outbox.successor_version
                        ) THEN 'superseded'
                        ELSE 'failed'
                    END,
                    attempts = attempts + 1, last_error = ?,
                    next_retry_at = ?, claimed_by = NULL, claimed_at = NULL,
                    claimed_payload_version = NULL, updated_at = ?
                WHERE event_id = ?
                  AND (? IS NULL OR (
                      status = 'publishing'
                      AND payload_version = ?
                      AND claimed_payload_version = ?
                  ))
            """, (
                error,
                _iso(next_retry_at),
                now,
                event_id,
                expected_payload_version,
                expected_payload_version,
                expected_payload_version,
            ))
            conn.commit()
            return cursor.rowcount > 0

    def mark_stale_rejected(
        self,
        event_id: str,
        expected_payload_version: Optional[int] = None,
    ) -> bool:
        """Finish a claimed write that the remote version fence rejected."""
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                UPDATE publish_outbox
                SET status = 'superseded',
                    last_error = 'remote rejected stale publication_version',
                    claimed_by = NULL,
                    claimed_at = NULL,
                    claimed_payload_version = NULL,
                    updated_at = ?
                WHERE event_id = ?
                  AND (? IS NULL OR (
                      status = 'publishing'
                      AND payload_version = ?
                      AND claimed_payload_version = ?
                  ))
                """,
                (
                    now,
                    event_id,
                    expected_payload_version,
                    expected_payload_version,
                    expected_payload_version,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_dead_letter(
        self,
        event_id: str,
        error: str,
        expected_payload_version: Optional[int] = None,
    ) -> bool:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = 'dead_letter', last_error = ?, claimed_by = NULL,
                    claimed_at = NULL, claimed_payload_version = NULL, updated_at = ?
                WHERE event_id = ?
                  AND (? IS NULL OR (
                      status = 'publishing'
                      AND payload_version = ?
                      AND claimed_payload_version = ?
                  ))
            """, (
                error,
                now,
                event_id,
                expected_payload_version,
                expected_payload_version,
                expected_payload_version,
            ))
            conn.commit()
            return cursor.rowcount > 0

    def reset_stale_publishing(self, claimed_before: datetime | str) -> int:
        now = _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                UPDATE publish_outbox
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM publish_outbox AS newer
                            WHERE newer.logical_event_id = publish_outbox.logical_event_id
                              AND newer.successor_version > publish_outbox.successor_version
                        ) THEN 'superseded'
                        ELSE 'pending'
                    END,
                    claimed_by = NULL, claimed_at = NULL,
                    claimed_payload_version = NULL, updated_at = ?
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

    def prune_published_before(self, cutoff_timestamp: datetime | str, batch_size: int = 500) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute("""
                DELETE FROM publish_outbox
                WHERE event_id IN (
                    SELECT event_id FROM publish_outbox
                    WHERE status = 'published' AND published_at < ?
                    ORDER BY published_at
                    LIMIT ?
                )
            """, (_iso(cutoff_timestamp), batch_size))
            conn.commit()
            return cursor.rowcount

    def enqueue_many(self, events: Sequence[PublishOutboxEvent]) -> int:
        return sum(1 for event in events if self.enqueue_event(event))