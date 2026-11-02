"""Schema v17: MAE/MFE columns on ``run_trades`` (research plan S2)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from vibe.research_pipeline.contracts import RetentionProfile, RunEvidence
from vibe.research_pipeline.evidence import equity_curve_digest, trade_ledger_digest
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore


@dataclass
class _Trade:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: Optional[float]
    entry_time: datetime
    exit_time: Optional[datetime]
    pnl: Optional[float]
    commission: Optional[float]
    initial_risk: Optional[float]
    exit_reason: Optional[str]
    mae_r: Optional[float] = None
    mfe_r: Optional[float] = None


def _trades() -> list[_Trade]:
    return [
        _Trade(
            "QQQ", "buy", 10.0 + i, 100.0, 101.0,
            datetime(2026, 1, 2 + i, 14, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 2 + i, 15, 30, tzinfo=timezone.utc),
            10.0, 1.0, 50.0, "EOD", mae_r=-0.25 * i, mfe_r=1.5 + i,
        )
        for i in range(3)
    ]


_FP = dict(
    strategy_id="orb",
    ruleset_content_sha256="a" * 64,
    parameters={"opening_range_minutes": 15},
    universe_hash="b" * 64,
    split_manifest_hash="c" * 64,
    data_snapshot_id="snap",
    feature_set_version=1,
    execution_model_version=6,
    metric_calculation_version=1,
    code_commit="1234567abcdef",
    code_dirty=False,
)


def _register(store: SqliteResearchStore, trades: list[_Trade]) -> str:
    run_id = store.register_run(
        fingerprint=RunFingerprint(**_FP), methodology_version="v1"
    )
    curve = [(datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc), 100_000.0)]
    store.record_evidence(
        run_id,
        RunEvidence(
            trade_count=len(trades),
            trade_ledger_sha256=trade_ledger_digest(trades),
            equity_curve_sha256=equity_curve_digest(curve),
            equity_points=1,
            first_session=date(2026, 1, 2),
            last_session=date(2026, 1, 9),
            starting_equity=100_000.0,
            ending_equity=100_000.0,
            gross_pnl=0.0,
            total_costs=0.0,
            net_pnl=0.0,
            max_observed_leverage=1.0,
            min_cash=50_000.0,
            retention_profile=RetentionProfile.DIAGNOSTIC,
        ),
    )
    return run_id


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(run_trades)")}


def test_fresh_database_reaches_v17_with_excursion_columns(tmp_path: Path) -> None:
    assert schema.SCHEMA_VERSION >= 17
    with sqlite3.connect(tmp_path / "fresh.sqlite3") as conn:
        assert schema.migrate(conn) == schema.SCHEMA_VERSION
        assert {"mae_r", "mfe_r"} <= _columns(conn)


def test_v16_database_migrates_and_old_rows_have_null_excursions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "v16.sqlite3"
    trades = _trades()
    with monkeypatch.context() as patch:
        patch.setattr(schema, "_MIGRATIONS", schema._MIGRATIONS[:16])
        patch.setattr(schema, "SCHEMA_VERSION", 16)
        store = SqliteResearchStore(path)
        run_id = _register(store, trades)
        store._conn.execute(
            """
            INSERT INTO run_trades (run_id, sequence, symbol, side, quantity,
                entry_price, exit_price, entry_time, exit_time, pnl,
                commission, initial_risk, exit_reason, recorded_at)
            VALUES (?, 0, 'QQQ', 'buy', 10, 100, 101, '2026-01-02T14:30:00+00:00',
                '2026-01-02T15:30:00+00:00', 10, 1, 50, 'EOD', '2026-01-02T00:00:00Z')
            """,
            (run_id,),
        )
        store._conn.commit()
        assert "mae_r" not in _columns(store._conn)
        store.close()

    store = SqliteResearchStore(path)
    try:
        assert {"mae_r", "mfe_r"} <= _columns(store._conn)
        row = store._conn.execute(
            "SELECT mae_r, mfe_r FROM run_trades WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert tuple(row) == (None, None)
        triggers = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {"protect_frozen_run_trades", "protect_frozen_run_equity"} <= triggers
    finally:
        store.close()


def test_record_trades_round_trips_excursions(tmp_path: Path) -> None:
    store = SqliteResearchStore(tmp_path / "rt.sqlite3")
    try:
        trades = _trades()
        run_id = _register(store, trades)
        store.record_trades(run_id, trades)
        stored = store.get_trades(run_id)
        assert [t.mae_r for t in stored] == [t.mae_r for t in trades]
        assert [t.mfe_r for t in stored] == [t.mfe_r for t in trades]
        store.verify_retained_detail(run_id)
    finally:
        store.close()
