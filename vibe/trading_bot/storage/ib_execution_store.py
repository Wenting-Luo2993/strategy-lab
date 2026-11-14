"""Durable local journal for Interactive Brokers orders and executions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

def permanent_order_identity(account_id: Any, permanent_order_id: Any) -> Optional[str]:
    """IB's permanent ID, unlike its API order number, identifies an order lifetime."""
    value = str(permanent_order_id or "")
    if not value.isdigit() or int(value) <= 0 or not account_id:
        return None
    return f"ib:{account_id}:{int(value)}"


def execution_order_identity(execution: Dict[str, Any]) -> str:
    metadata = execution.get("order_metadata") or {}
    identity = execution.get("order_identity") or metadata.get("order_identity")
    if identity:
        return str(identity)
    permanent = permanent_order_identity(
        execution.get("account_id"), execution.get("permanent_order_id")
    )
    if permanent:
        return permanent
    # Unidentified historical fills must not share a live API routing handle.
    return f"legacy-execution:{execution_revision(execution.get('execution_id'))[0]}"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# IBKR execIds look like ``0000e0d5.6577b2c1.01.01``. A correction re-sends
# the execution with only the final ``.NN`` incremented.
_EXEC_CORRECTION = re.compile(r"^(?P<base>[^.]+\.[^.]+\..+)\.(?P<revision>\d+)$")


def execution_revision(execution_id: Any) -> tuple[str, int]:
    """``(canonical_id, revision)`` of an execution id.

    The canonical id is the id of the first revision (``.01``), so a
    corrected fill keeps the identity it was first projected under. Ids
    without the IBKR shape are their own canonical id at revision 0.
    """
    text = str(execution_id or "")
    match = _EXEC_CORRECTION.match(text)
    if match is None:
        return text, 0
    digits = match["revision"]
    return f"{match['base']}.{'1'.zfill(len(digits))}", int(digits)


def canonical_execution(execution: Dict[str, Any]) -> Dict[str, Any]:
    """The execution under its canonical id, remembering the raw id and revision."""
    raw_id = str(execution.get("execution_id") or "")
    canonical_id, revision = execution_revision(raw_id)
    if canonical_id == raw_id and "execution_revision" in execution:
        return dict(execution)
    return {
        **execution,
        "execution_id": canonical_id,
        "raw_execution_id": execution.get("raw_execution_id") or raw_id,
        "execution_revision": revision,
    }


def dedupe_corrections(executions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse IBKR execution corrections onto their latest revision.

    Each fill appears once, under its canonical id, carrying the data of its
    highest revision. Order of first appearance is kept.
    """
    kept: Dict[str, Dict[str, Any]] = {}
    for execution in executions:
        item = canonical_execution(execution)
        key = item["execution_id"] or f"__anonymous_{len(kept)}"
        current = kept.get(key)
        if current is None or item["execution_revision"] >= current["execution_revision"]:
            kept[key] = item
    return list(kept.values())


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
                entry_cancel_after_seconds REAL,
                entry_expiry_bars INTEGER,
                entry_signal_bar_timestamp TEXT,
                entry_bar_interval_seconds REAL,
                order_role TEXT,
                oca_group TEXT,
                parent_order_id TEXT,
                gap_fill INTEGER,
                placement_latency_s REAL,
                capped_by TEXT,
                or_source TEXT,
                or_high REAL,
                or_low REAL,
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

            CREATE TABLE IF NOT EXISTS ib_order_lifetimes (
                order_identity TEXT PRIMARY KEY,
                broker_order_id TEXT NOT NULL,
                account_id TEXT,
                permanent_order_id TEXT,
                client_id INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ib_lifetimes_api
                ON ib_order_lifetimes(broker_order_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ib_lifetimes_permanent
                ON ib_order_lifetimes(account_id, permanent_order_id)
                WHERE account_id IS NOT NULL AND permanent_order_id IS NOT NULL;
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
                "entry_cancel_after_seconds": "REAL",
                "entry_expiry_bars": "INTEGER",
                "entry_signal_bar_timestamp": "TEXT",
                "entry_bar_interval_seconds": "REAL",
                "order_role": "TEXT",
                "oca_group": "TEXT",
                "parent_order_id": "TEXT",
                "gap_fill": "INTEGER",
                "placement_latency_s": "REAL",
                "capped_by": "TEXT",
                "or_source": "TEXT",
                "or_high": "REAL",
                "or_low": "REAL",
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
        # Keep the original table intact for evidence/rollback. Legacy rows are
        # archived, not assumed to belong to whichever order now uses that ID.
        for row in conn.execute("SELECT * FROM ib_submitted_orders").fetchall():
            payload = dict(row)
            seed = json.dumps(
                [payload["broker_order_id"], payload.get("submitted_at"), payload.get("created_at")],
                separators=(",", ":"),
            )
            identity = "legacy-submission:" + hashlib.sha256(seed.encode()).hexdigest()
            payload["order_identity"] = identity
            conn.execute(
                """INSERT OR IGNORE INTO ib_order_lifetimes
                   (order_identity, broker_order_id, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (identity, str(payload["broker_order_id"]), json.dumps(payload),
                 payload.get("created_at") or "", payload.get("updated_at") or ""),
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
        payload = json.loads(json.dumps(payload, default=_iso))
        identity = payload.get("order_identity")
        if not identity:
            # Import compatibility for legacy callers; production submissions
            # supply a UUID before placeOrder, never an API-number-only key.
            seed = json.dumps([
                payload.get("account_id"), payload.get("client_id"),
                payload["broker_order_id"], payload.get("submitted_at"),
            ])
            identity = "legacy-submission:" + hashlib.sha256(seed.encode()).hexdigest()
        permanent = permanent_order_identity(
            payload.get("account_id"), payload.get("permanent_order_id")
        )
        payload["order_identity"] = identity
        payload["permanent_order_id"] = str(int(payload["permanent_order_id"])) if permanent else None
        with self._lock:
            existing = self.get_submitted_order(order_identity=identity)
            if existing:
                for field in ("account_id", "permanent_order_id", "symbol", "side"):
                    if existing.get(field) and payload.get(field) and existing[field] != payload[field]:
                        raise ValueError(f"Order lifetime identity conflict: {field}")
                payload = {**existing, **{k: v for k, v in payload.items() if v is not None}}
            payload["created_at"] = (existing or {}).get("created_at") or now
            payload["updated_at"] = now
            self._connection().execute(
                """INSERT INTO ib_order_lifetimes
                   (order_identity, broker_order_id, account_id, permanent_order_id,
                    client_id, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(order_identity) DO UPDATE SET
                     broker_order_id=excluded.broker_order_id,
                     account_id=excluded.account_id,
                     permanent_order_id=excluded.permanent_order_id,
                     client_id=excluded.client_id, payload_json=excluded.payload_json,
                     updated_at=excluded.updated_at""",
                (identity, str(payload["broker_order_id"]), payload.get("account_id"),
                 payload.get("permanent_order_id"), payload.get("client_id"),
                 json.dumps(payload, sort_keys=True), payload["created_at"], now),
            )
            self._connection().commit()

    def get_submitted_order(
        self, broker_order_id: Optional[str] = None, *,
        order_identity: Optional[str] = None,
        account_id: Optional[str] = None, permanent_order_id: Any = None,
    ) -> Optional[Dict[str, Any]]:
        if order_identity:
            clause, params = "order_identity = ?", (order_identity,)
        elif permanent_order_identity(account_id, permanent_order_id):
            clause, params = "account_id = ? AND permanent_order_id = ?", (
                account_id, str(int(permanent_order_id)),
            )
        elif broker_order_id is not None:
            clause, params = "broker_order_id = ?", (str(broker_order_id),)
        else:
            return None
        rows = self._connection().execute(
            f"SELECT payload_json FROM ib_order_lifetimes WHERE {clause}", params,
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("Ambiguous API order ID; an order lifetime identity is required")
        return json.loads(rows[0]["payload_json"]) if rows else None

    def list_submitted_orders(
        self,
        *,
        symbol: Optional[str] = None,
        order_role: Optional[str] = None,
        submitted_after: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Submitted orders, optionally filtered (``submitted_after`` is inclusive)."""
        clauses, params = [], []
        if symbol is not None:
            clauses.append("json_extract(payload_json, '$.symbol') = ?")
            params.append(symbol)
        if order_role is not None:
            clauses.append("json_extract(payload_json, '$.order_role') = ?")
            params.append(order_role)
        rows = self._connection().execute(
            "SELECT payload_json FROM ib_order_lifetimes"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY json_extract(payload_json, '$.submitted_at')",
            params,
        ).fetchall()
        result = [json.loads(row["payload_json"]) for row in rows]
        if submitted_after is not None:
            cutoff = submitted_after.astimezone(timezone.utc)
            result = [
                row
                for row in result
                if row.get("submitted_at")
                and datetime.fromisoformat(row["submitted_at"]).astimezone(timezone.utc) >= cutoff
            ]
        return result

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
