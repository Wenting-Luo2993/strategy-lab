"""SQLite-based metrics storage."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MetricType(str, Enum):
    """Types of metrics."""

    TRADE = "trade"
    PERFORMANCE = "performance"
    HEALTH = "health"


class MetricsStore:
    """Thread-safe SQLite store for metrics."""

    def __init__(self, db_path: str = "./data/metrics.db"):
        """Initialize metrics store.

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
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_type TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                dimensions TEXT,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_metric_type ON metrics(metric_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_metric_name ON metrics(metric_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)
        """)

        conn.commit()

    def record_metric(
        self,
        metric_type: str,
        metric_name: str,
        metric_value: float,
        dimensions: Optional[Dict[str, str]] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Record a metric.

        Args:
            metric_type: Type of metric (trade, performance, health)
            metric_name: Name of the metric (e.g., 'pnl', 'win_rate')
            metric_value: Numeric value of the metric
            dimensions: Optional dictionary of dimensions (e.g., {'symbol': 'AAPL'})
            timestamp: Optional timestamp (ISO format), defaults to now

        Returns:
            Metric ID

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

            dimensions_json = json.dumps(dimensions) if dimensions else None

            cursor.execute("""
                INSERT INTO metrics (
                    metric_type, metric_name, metric_value, dimensions, timestamp, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                metric_type,
                metric_name,
                metric_value,
                dimensions_json,
                timestamp,
                now,
            ))

            conn.commit()
            return cursor.lastrowid

    def get_metrics(
        self,
        metric_type: Optional[str] = None,
        metric_name: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get metrics with optional filtering.

        Args:
            metric_type: Filter by metric type
            metric_name: Filter by metric name
            start_time: Filter by start time (ISO format)
            end_time: Filter by end time (ISO format)
            limit: Maximum number of metrics to return

        Returns:
            List of metric dictionaries
        """
        import json

        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM metrics WHERE 1=1"
        params = []

        if metric_type:
            query += " AND metric_type = ?"
            params.append(metric_type)

        if metric_name:
            query += " AND metric_name = ?"
            params.append(metric_name)

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
            if result['dimensions']:
                result['dimensions'] = json.loads(result['dimensions'])
            else:
                result['dimensions'] = {}
            results.append(result)

        return results

    def aggregate_metrics(
        self,
        metric_type: str,
        metric_name: str,
        aggregation: str = "sum",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Optional[float]:
        """Aggregate metrics over a time range.

        Args:
            metric_type: Type of metric to aggregate
            metric_name: Name of metric to aggregate
            aggregation: Aggregation function (sum, avg, count, min, max)
            start_time: Start time (ISO format)
            end_time: End time (ISO format)

        Returns:
            Aggregated value or None if no matching metrics
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Map aggregation names to SQL functions
        agg_map = {
            "sum": "SUM",
            "avg": "AVG",
            "count": "COUNT",
            "min": "MIN",
            "max": "MAX",
        }

        agg_func = agg_map.get(aggregation.lower(), "SUM")

        query = f"""
            SELECT {agg_func}(metric_value) FROM metrics
            WHERE metric_type = ? AND metric_name = ?
        """
        params = [metric_type, metric_name]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        cursor.execute(query, params)
        result = cursor.fetchone()

        return result[0] if result and result[0] is not None else None

    def get_metric_stats(
        self,
        metric_type: str,
        metric_name: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get statistics for a metric.

        Args:
            metric_type: Type of metric
            metric_name: Name of metric
            start_time: Start time (ISO format)
            end_time: End time (ISO format)

        Returns:
            Dictionary with count, sum, avg, min, max
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT
                COUNT(*) as count,
                SUM(metric_value) as sum,
                AVG(metric_value) as avg,
                MIN(metric_value) as min,
                MAX(metric_value) as max
            FROM metrics
            WHERE metric_type = ? AND metric_name = ?
        """
        params = [metric_type, metric_name]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row or row[0] == 0:
            return {
                'count': 0,
                'sum': 0.0,
                'avg': 0.0,
                'min': 0.0,
                'max': 0.0,
            }

        count, total, avg, min_val, max_val = row
        return {
            'count': count,
            'sum': total or 0.0,
            'avg': avg or 0.0,
            'min': min_val or 0.0,
            'max': max_val or 0.0,
        }

    def delete_metrics(
        self,
        metric_type: Optional[str] = None,
        before_time: Optional[str] = None,
    ) -> int:
        """Delete metrics matching criteria.

        Args:
            metric_type: Delete only this metric type (optional)
            before_time: Delete metrics before this time (optional)

        Returns:
            Number of deleted metrics
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            query = "DELETE FROM metrics WHERE 1=1"
            params = []

            if metric_type:
                query += " AND metric_type = ?"
                params.append(metric_type)

            if before_time:
                query += " AND timestamp < ?"
                params.append(before_time)

            cursor.execute(query, params)
            conn.commit()

            return cursor.rowcount

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
