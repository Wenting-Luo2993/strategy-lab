"""SQLite-based log storage with retention."""

import sqlite3
import threading
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta


class LogStore:
    """Thread-safe SQLite store for service logs."""

    def __init__(self, db_path: str = "./data/logs.db", retention_days: int = 3):
        """Initialize log store.

        Args:
            db_path: Path to SQLite database file
            retention_days: Number of days to retain logs
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days

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
            CREATE TABLE IF NOT EXISTS service_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                logger TEXT NOT NULL,
                message TEXT NOT NULL,
                extra_data TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON service_logs(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_level ON service_logs(level)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logger ON service_logs(logger)
        """)

        conn.commit()

    def insert_log(
        self,
        timestamp: Optional[str] = None,
        level: str = "INFO",
        logger: str = "app",
        message: str = "",
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert a log entry.

        Args:
            timestamp: Log timestamp (ISO format), defaults to now
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            logger: Logger name
            message: Log message
            extra_data: Optional extra data (JSON serializable)

        Returns:
            Log ID

        Raises:
            sqlite3.Error: If database operation fails
        """
        import json

        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.utcnow().isoformat()
            if timestamp is None:
                timestamp = now

            extra_json = json.dumps(extra_data) if extra_data else None

            cursor.execute("""
                INSERT INTO service_logs (
                    timestamp, level, logger, message, extra_data, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                level,
                logger,
                message,
                extra_json,
                now,
            ))

            conn.commit()
            return cursor.lastrowid

    def get_logs(
        self,
        level: Optional[str] = None,
        logger: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get logs with optional filtering.

        Args:
            level: Filter by log level
            logger: Filter by logger name
            start_time: Filter by start time (ISO format)
            end_time: Filter by end time (ISO format)
            limit: Maximum number of logs to return

        Returns:
            List of log dictionaries
        """
        import json

        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM service_logs WHERE 1=1"
        params = []

        if level:
            query += " AND level = ?"
            params.append(level)

        if logger:
            query += " AND logger = ?"
            params.append(logger)

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            result = dict(row)
            if result['extra_data']:
                try:
                    result['extra_data'] = json.loads(result['extra_data'])
                except (json.JSONDecodeError, TypeError):
                    result['extra_data'] = {}
            else:
                result['extra_data'] = {}
            results.append(result)

        return results

    def count_logs(
        self,
        level: Optional[str] = None,
        logger: Optional[str] = None,
    ) -> int:
        """Count logs matching criteria.

        Args:
            level: Filter by log level
            logger: Filter by logger name

        Returns:
            Number of matching logs
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT COUNT(*) FROM service_logs WHERE 1=1"
        params = []

        if level:
            query += " AND level = ?"
            params.append(level)

        if logger:
            query += " AND logger = ?"
            params.append(logger)

        cursor.execute(query, params)
        result = cursor.fetchone()

        return result[0] if result else 0

    def cleanup_old_logs(self, retention_days: Optional[int] = None) -> int:
        """Delete logs older than retention period.

        Args:
            retention_days: Days to retain (uses instance default if not specified)

        Returns:
            Number of deleted logs
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            days = retention_days or self.retention_days
            cutoff_time = (datetime.utcnow() - timedelta(days=days)).isoformat()

            cursor.execute(
                "DELETE FROM service_logs WHERE timestamp < ?",
                (cutoff_time,),
            )
            conn.commit()

            return cursor.rowcount

    def get_log_stats(self) -> Dict[str, Any]:
        """Get statistics about logs.

        Returns:
            Dictionary with log counts by level
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT level, COUNT(*) as count
            FROM service_logs
            GROUP BY level
        """)
        rows = cursor.fetchall()

        stats = {
            'total': self.count_logs(),
            'by_level': {},
        }

        for row in rows:
            stats['by_level'][row[0]] = row[1]

        return stats

    def delete_logs(self, before_time: str) -> int:
        """Delete logs before a specific time.

        Args:
            before_time: Delete logs before this time (ISO format)

        Returns:
            Number of deleted logs
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "DELETE FROM service_logs WHERE timestamp < ?",
                (before_time,),
            )
            conn.commit()

            return cursor.rowcount

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


class DatabaseLogHandler(logging.Handler):
    """Logging handler that writes logs to database."""

    def __init__(self, log_store: LogStore):
        """Initialize database log handler.

        Args:
            log_store: LogStore instance to write to
        """
        super().__init__()
        self.log_store = log_store

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to database.

        Args:
            record: LogRecord to emit
        """
        try:
            self.log_store.insert_log(
                timestamp=datetime.fromtimestamp(record.created).isoformat(),
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
                extra_data=record.__dict__.get('meta'),
            )
        except Exception:
            self.handleError(record)
