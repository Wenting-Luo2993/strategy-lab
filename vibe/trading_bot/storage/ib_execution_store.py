"""Durable local journal for Interactive Brokers orders and executions."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class IBExecutionStore:
    """Persist IB callback data before exposing it to in-process consumers."""

    def __init__(self, db_path: str = "./data/local/ib_executions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                f"file:{self.db_path}?mode=rwc",
                uri=True,
                timeout=30.0,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA busy_timeout=5000")
        return self._local.connection

    def _init_schema(self) -> None:
        conn = self._connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ib_submitted_orders (
                broker_order_id TEXT PRIMARY KEY,
                permanent_order_id TEXT,
                account_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                order_type TEXT NOT NULL,
                submitted_at TEXT,
                decision_at TEXT,
                benchmark_type TEXT,
                benchmark_price REAL,
                quote_bid REAL,
                quote_ask REAL,
                quote_midpoint REAL,
                stop_price REAL,
                limit_price REAL,
                strategy_name TEXT,
                strategy_stop_price REAL,
                take_profit REAL,
                exit_reason TEXT,
                benchmark_version INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ib_executions (
                execution_id TEXT PRIMARY KEY,
                broker_order_id TEXT NOT NULL,
                permanent_order_id TEXT,
                account_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                filled_at TEXT NOT NULL,
                trade_currency TEXT,
                commission REAL,
                commission_currency TEXT,
                order_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ib_executions_order
                ON ib_executions(broker_order_id, filled_at);
            """
        )
        self._ensure_columns(
            conn,
            "ib_submitted_orders",
            {
                "permanent_order_id": "TEXT",
                "account_id": "TEXT",
                "submitted_at": "TEXT",
                "decision_at": "TEXT",
                "benchmark_type": "TEXT",
                "benchmark_price": "REAL",
                "quote_bid": "REAL",
                "quote_ask": "REAL",
                "quote_midpoint": "REAL",
                "stop_price": "REAL",
                "limit_price": "REAL",
                "strategy_name": "TEXT",
                "strategy_stop_price": "REAL",
                "take_profit": "REAL",
                "exit_reason": "TEXT",
                "benchmark_version": "INTEGER NOT NULL DEFAULT 1",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "ib_executions",
            {
                "permanent_order_id": "TEXT",
                "account_id": "TEXT",
                "trade_currency": "TEXT",
                "commission": "REAL",
                "commission_currency": "TEXT",
                "order_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
        )
        conn.commit()

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection,
        table: str,
        definitions: Dict[str, str],
    ) -> None:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in definitions.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )

    def upsert_submitted_order(self, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection().execute(
                """
                INSERT INTO ib_submitted_orders (
                    broker_order_id, permanent_order_id, account_id, symbol, side,
                    quantity, order_type, submitted_at, decision_at, benchmark_type,
                    benchmark_price, quote_bid, quote_ask, quote_midpoint, stop_price,
                    limit_price, strategy_name, strategy_stop_price, take_profit,
                    exit_reason, benchmark_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(broker_order_id) DO UPDATE SET
                    permanent_order_id = COALESCE(excluded.permanent_order_id, permanent_order_id),
                    account_id = COALESCE(excluded.account_id, account_id),
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    order_type = excluded.order_type,
                    submitted_at = COALESCE(excluded.submitted_at, submitted_at),
                    decision_at = COALESCE(excluded.decision_at, decision_at),
                    benchmark_type = COALESCE(excluded.benchmark_type, benchmark_type),
                    benchmark_price = COALESCE(excluded.benchmark_price, benchmark_price),
                    quote_bid = COALESCE(excluded.quote_bid, quote_bid),
                    quote_ask = COALESCE(excluded.quote_ask, quote_ask),
                    quote_midpoint = COALESCE(excluded.quote_midpoint, quote_midpoint),
                    stop_price = COALESCE(excluded.stop_price, stop_price),
                    limit_price = COALESCE(excluded.limit_price, limit_price),
                    strategy_name = COALESCE(excluded.strategy_name, strategy_name),
                    strategy_stop_price = COALESCE(excluded.strategy_stop_price, strategy_stop_price),
                    take_profit = COALESCE(excluded.take_profit, take_profit),
                    exit_reason = COALESCE(excluded.exit_reason, exit_reason),
                    benchmark_version = excluded.benchmark_version,
                    updated_at = excluded.updated_at
                """,
                (
                    str(payload["broker_order_id"]),
                    payload.get("permanent_order_id"),
                    payload.get("account_id"),
                    payload["symbol"],
                    payload["side"],
                    float(payload["quantity"]),
                    payload.get("order_type") or "market",
                    _iso(payload.get("submitted_at")),
                    _iso(payload.get("decision_at")),
                    payload.get("benchmark_type"),
                    payload.get("benchmark_price"),
                    payload.get("quote_bid"),
                    payload.get("quote_ask"),
                    payload.get("quote_midpoint"),
                    payload.get("stop_price"),
                    payload.get("limit_price"),
                    payload.get("strategy_name"),
                    payload.get("strategy_stop_price"),
                    payload.get("take_profit"),
                    payload.get("exit_reason"),
                    int(payload.get("benchmark_version") or 2),
                    now,
                    now,
                ),
            )
            self._connection().commit()

    def get_submitted_order(self, broker_order_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection().execute(
            "SELECT * FROM ib_submitted_orders WHERE broker_order_id = ?",
            (str(broker_order_id),),
        ).fetchone()
        return dict(row) if row else None

    def upsert_execution(self, payload: Dict[str, Any]) -> bool:
        """Upsert one execId. Return True when stored content changed."""
        execution_id = str(payload["execution_id"])
        existing = self.get_execution(execution_id)
        metadata = payload.get("order_metadata") or {}
        normalized = {
            **payload,
            "execution_id": execution_id,
            "broker_order_id": str(payload["broker_order_id"]),
            "filled_at": _iso(payload["filled_at"]),
            "order_metadata_json": json.dumps(metadata, sort_keys=True, default=_iso),
        }
        if existing is not None:
            for key in (
                "permanent_order_id",
                "account_id",
                "trade_currency",
                "commission",
                "commission_currency",
            ):
                if normalized.get(key) is None:
                    normalized[key] = existing.get(key)
            if normalized["order_metadata_json"] == "{}":
                normalized["order_metadata_json"] = json.dumps(
                    existing.get("order_metadata") or {},
                    sort_keys=True,
                    default=_iso,
                )
        comparable = (
            "broker_order_id",
            "permanent_order_id",
            "account_id",
            "symbol",
            "side",
            "quantity",
            "price",
            "filled_at",
            "trade_currency",
            "commission",
            "commission_currency",
            "order_metadata_json",
        )
        existing_values = dict(existing or {})
        if existing is not None:
            existing_values["order_metadata_json"] = json.dumps(
                existing.get("order_metadata") or {},
                sort_keys=True,
                default=_iso,
            )
        changed = existing is None or any(
            existing_values.get(key) != normalized.get(key) for key in comparable
        )
        if not changed:
            return False

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection().execute(
                """
                INSERT INTO ib_executions (
                    execution_id, broker_order_id, permanent_order_id, account_id,
                    symbol, side, quantity, price, filled_at, trade_currency,
                    commission, commission_currency, order_metadata_json, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    broker_order_id = excluded.broker_order_id,
                    permanent_order_id = COALESCE(excluded.permanent_order_id, permanent_order_id),
                    account_id = COALESCE(excluded.account_id, account_id),
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    filled_at = excluded.filled_at,
                    trade_currency = COALESCE(excluded.trade_currency, trade_currency),
                    commission = COALESCE(excluded.commission, commission),
                    commission_currency = COALESCE(excluded.commission_currency, commission_currency),
                    order_metadata_json = CASE
                        WHEN excluded.order_metadata_json <> '{}' THEN excluded.order_metadata_json
                        ELSE order_metadata_json
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    execution_id,
                    normalized["broker_order_id"],
                    normalized.get("permanent_order_id"),
                    normalized.get("account_id"),
                    normalized["symbol"],
                    normalized["side"],
                    float(normalized["quantity"]),
                    float(normalized["price"]),
                    normalized["filled_at"],
                    normalized.get("trade_currency"),
                    normalized.get("commission"),
                    normalized.get("commission_currency"),
                    normalized["order_metadata_json"],
                    now,
                    now,
                ),
            )
            self._connection().commit()
        return True

    def get_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection().execute(
            "SELECT * FROM ib_executions WHERE execution_id = ?",
            (str(execution_id),),
        ).fetchone()
        return self._decode_execution(row)

    def list_executions(self, broker_order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if broker_order_id is None:
            rows = self._connection().execute(
                "SELECT * FROM ib_executions ORDER BY filled_at, execution_id"
            ).fetchall()
        else:
            rows = self._connection().execute(
                """
                SELECT * FROM ib_executions
                WHERE broker_order_id = ?
                ORDER BY filled_at, execution_id
                """,
                (str(broker_order_id),),
            ).fetchall()
        return [self._decode_execution(row) for row in rows]

    @staticmethod
    def _decode_execution(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        result["order_metadata"] = json.loads(result.pop("order_metadata_json") or "{}")
        return result

    def close(self) -> None:
        if hasattr(self._local, "connection") and self._local.connection is not None:
            self._local.connection.close()
            self._local.connection = None
