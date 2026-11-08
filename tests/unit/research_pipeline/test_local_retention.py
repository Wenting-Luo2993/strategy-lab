"""Tests for local row-level retention (P15b).

``RetentionProfile`` promised a local trade ledger (``diagnostic``) and a local
equity curve (``full``) from P0 onward, but schema v8 had nowhere to put either:
``run_evidence.equity_points`` is a count, not a series. The research store
could therefore state that a run produced 214 trades and prove the ledger
digest, while being unable to show a single trade. That is what left the
dashboards unable to answer "how did this run actually behave".

Each test below names the defect it prevents. The two that matter most are the
digest-agreement tests: storing rows that do not reproduce the recorded
checksum would be worse than storing nothing, because the summary row would
still look correct and the disagreement would be invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from vibe.research_pipeline.contracts import RetentionProfile, RunEvidence
from vibe.research_pipeline.evidence import (
    equity_curve_digest,
    trade_ledger_digest,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.store import StoreError
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore


@dataclass
class _Trade:
    """Minimal stand-in carrying exactly the digest fields.

    Deliberately not the real ``Trade`` model: the digest is defined over an
    explicit closed field list, and a test that depended on the full model
    would start failing for unrelated model edits.
    """

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


def _trades(n: int = 3) -> list[_Trade]:
    return [
        _Trade(
            symbol="QQQ",
            side="long" if i % 2 == 0 else "short",
            quantity=10.0 + i,
            entry_price=100.0 + i,
            exit_price=101.0 + i,
            entry_time=datetime(2026, 1, 2 + i, 14, 30, tzinfo=timezone.utc),
            exit_time=datetime(2026, 1, 2 + i, 15, 30, tzinfo=timezone.utc),
            pnl=10.0 * (1 if i % 2 == 0 else -1),
            commission=1.0,
            initial_risk=50.0,
            exit_reason="target" if i % 2 == 0 else "stop",
        )
        for i in range(n)
    ]


def _curve() -> list[tuple[datetime, float]]:
    return [
        (datetime(2026, 1, 2 + i, 16, 0, tzinfo=timezone.utc), 100_000.0 + 100 * i)
        for i in range(5)
    ]


_BASE_FP = dict(
    strategy_id="orb",
    ruleset_content_sha256="a" * 64,
    parameters={"opening_range_minutes": 15},
    universe_hash="b" * 64,
    split_manifest_hash="c" * 64,
    data_snapshot_id="databento-2026-09-01",
    feature_set_version=1,
    execution_model_version=6,
    metric_calculation_version=1,
    code_commit="1234567abcdef",
    code_dirty=False,
)


@pytest.fixture
def store(tmp_path: Path):
    s = SqliteResearchStore(tmp_path / "research.sqlite3")
    yield s
    s.close()


def _prepare(
    store: SqliteResearchStore,
    *,
    profile: RetentionProfile,
    trades: list[_Trade],
    curve: list[tuple[datetime, float]],
    **fp_overrides: Any,
) -> str:
    """Register a run and record evidence whose digests describe the inputs."""
    run_id = store.register_run(
        fingerprint=RunFingerprint(**{**_BASE_FP, **fp_overrides}),
        methodology_version="v1",
    )
    store.record_evidence(
        run_id,
        RunEvidence(
            trade_count=len(trades),
            trade_ledger_sha256=trade_ledger_digest(trades),
            equity_curve_sha256=equity_curve_digest(curve),
            equity_points=len(curve),
            first_session=date(2026, 1, 2),
            last_session=date(2026, 1, 9),
            starting_equity=100_000.0,
            ending_equity=101_000.0,
            gross_pnl=1_100.0,
            total_costs=100.0,
            net_pnl=1_000.0,
            max_observed_leverage=1.2,
            min_cash=50_000.0,
            retention_profile=profile,
        ),
    )
    return run_id


# -- the ledger round-trips faithfully -----------------------------------


def test_diagnostic_run_round_trips_its_ledger(store: SqliteResearchStore):
    """Without this, 'we kept the trades' is an untested claim."""
    trades = _trades()
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=trades, curve=_curve()
    )

    store.record_trades(run_id, trades)
    stored = store.get_trades(run_id)

    assert len(stored) == len(trades)
    assert [row.symbol for row in stored] == [t.symbol for t in trades]
    assert [row.side for row in stored] == [t.side for t in trades]
    assert [row.entry_price for row in stored] == [t.entry_price for t in trades]
    assert [row.pnl for row in stored] == [t.pnl for t in trades]
    assert [row.exit_reason for row in stored] == [t.exit_reason for t in trades]


def test_stored_order_is_execution_order(store: SqliteResearchStore):
    """Both digests are order-sensitive, so storage must not reorder.

    A ledger that round-trips in a different order is a different ledger, and
    reordering usually means a scheduling or merge defect rather than noise.
    """
    trades = _trades(6)
    run_id = _prepare(
        store, profile=RetentionProfile.FULL, trades=trades, curve=_curve()
    )

    store.record_trades(run_id, trades)
    stored = store.get_trades(run_id)

    assert [row.sequence for row in stored] == list(range(len(trades)))
    assert [row.entry_time for row in stored] == [t.entry_time for t in trades]


def test_open_trade_keeps_null_exit_rather_than_zero(store: SqliteResearchStore):
    """Collapsing an absent exit to 0.0 would corrupt every downstream chart."""
    trades = _trades(1)
    trades[0].exit_price = None
    trades[0].exit_time = None
    trades[0].pnl = None
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=trades, curve=_curve()
    )

    store.record_trades(run_id, trades)
    (row,) = store.get_trades(run_id)

    assert row.exit_price is None
    assert row.exit_time is None
    assert row.pnl is None


# -- retention profile is enforced, not advisory -------------------------


def test_summary_run_refuses_to_store_trades(store: SqliteResearchStore):
    """A run that declared 'summary' must not quietly retain a ledger.

    'Summary' is the only profile ever published remotely. If the store let a
    summary run accumulate rows anyway, the retention declaration would stop
    describing what is actually on disk.
    """
    trades = _trades()
    run_id = _prepare(
        store, profile=RetentionProfile.SUMMARY, trades=trades, curve=_curve()
    )

    with pytest.raises(StoreError, match="summary"):
        store.record_trades(run_id, trades)

    assert store.get_trades(run_id) == []


def test_diagnostic_run_refuses_to_store_equity_curve(store: SqliteResearchStore):
    """The curve is a 'full'-only concession; 'diagnostic' adds trades alone."""
    trades = _trades()
    curve = _curve()
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=trades, curve=curve
    )

    with pytest.raises(StoreError, match="full"):
        store.record_equity_curve(run_id, curve)

    assert store.get_equity_curve(run_id) == []


def test_full_run_round_trips_its_equity_curve(store: SqliteResearchStore):
    trades = _trades()
    curve = _curve()
    run_id = _prepare(
        store, profile=RetentionProfile.FULL, trades=trades, curve=curve
    )

    store.record_equity_curve(run_id, curve)
    stored = store.get_equity_curve(run_id)

    assert stored == curve


# -- rows must agree with the checksum that proves reproducibility -------


def test_ledger_disagreeing_with_evidence_is_refused(store: SqliteResearchStore):
    """The decisive test: stored detail must match the recorded digest.

    Evidence is recorded for the real ledger, then a *different* ledger is
    offered. Nothing else in the pipeline would notice: the summary row still
    reports the original digest and trade count, so the dashboards would
    display rows that silently contradict the checksum used to prove the run
    is reproducible.
    """
    real = _trades()
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=real, curve=_curve()
    )

    tampered = _trades()
    tampered[1].exit_price = 999.0

    with pytest.raises(StoreError, match="does not match its recorded evidence"):
        store.record_trades(run_id, tampered)

    assert store.get_trades(run_id) == []


def test_reordered_ledger_is_refused(store: SqliteResearchStore):
    """Reordering changes the digest, so it must be caught like any other edit."""
    real = _trades(4)
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=real, curve=_curve()
    )

    with pytest.raises(StoreError, match="does not match its recorded evidence"):
        store.record_trades(run_id, list(reversed(real)))


def test_curve_disagreeing_with_evidence_is_refused(store: SqliteResearchStore):
    curve = _curve()
    run_id = _prepare(
        store, profile=RetentionProfile.FULL, trades=_trades(), curve=curve
    )

    tampered = list(curve)
    tampered[2] = (tampered[2][0], tampered[2][1] + 1.0)

    with pytest.raises(StoreError, match="does not match its recorded evidence"):
        store.record_equity_curve(run_id, tampered)


def test_detail_before_evidence_is_refused(store: SqliteResearchStore):
    """Detail is verified against evidence, so evidence must exist first.

    Writing trades for a run with no evidence row would create detail that
    nothing can check, which is exactly the state the digests exist to prevent.
    """
    run_id = store.register_run(
        fingerprint=RunFingerprint(**_BASE_FP), methodology_version="v1"
    )

    with pytest.raises(StoreError, match="no evidence row"):
        store.record_trades(run_id, _trades())


# -- local-only by construction ------------------------------------------


def test_retention_tables_are_not_reachable_from_publication(
    store: SqliteResearchStore,
):
    """Row-level detail must never become a publication payload.

    The publication allowlist is the structural guard. This pins that the
    key names our new tables surface are among the aliases it recursively
    refuses, so a future payload builder cannot start forwarding them without
    failing here first.

    ``forbidden_payload_path`` is called directly rather than through
    ``validate_publication_payload`` because the latter also enforces
    top-level key and shape rules; a test that went through it would pass on
    an unrelated rejection and prove nothing about event-level refusal.
    """
    from vibe.research_pipeline.publishing import forbidden_payload_path

    for forbidden in ("trades", "equity_points", "equity_curve", "fills"):
        payload = {"metrics": {"nested": {forbidden: [{"symbol": "QQQ"}]}}}
        assert forbidden_payload_path(payload) is not None, (
            f"{forbidden!r} reached a publication payload unrefused"
        )

    # The guard must still be discriminating: an approved summary payload has
    # to survive, or the assertions above would hold for a function that
    # rejected everything.
    assert forbidden_payload_path({"metrics": {"expectancy_r": 0.17}}) is None


def test_rewriting_detail_replaces_rather_than_duplicates(
    store: SqliteResearchStore,
):
    """Re-recording the same ledger must not double it.

    Re-running a recording step is an ordinary retry; if it appended, trade
    counts would drift away from the evidence row that is supposed to describe
    them.
    """
    trades = _trades()
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=trades, curve=_curve()
    )

    store.record_trades(run_id, trades)
    store.record_trades(run_id, trades)

    assert len(store.get_trades(run_id)) == len(trades)

# -- retrieved rows must be verifiable against the evidence --------------


def test_retrieved_ledger_reproduces_its_digest(store: SqliteResearchStore):
    """A reader must be able to prove retrieved rows are the run's rows.

    This is stronger than checking field equality, and it is the check that
    matters: ``get_trades`` originally returned dicts of ISO strings, every
    field of which compared equal to the original, while the digest computed
    over them did not match the evidence at all. The canonical encoder reads
    fields by attribute and normalizes datetimes to UTC, so the returned shape
    -- not just its contents -- is part of the contract.
    """
    trades = _trades()
    run_id = _prepare(
        store, profile=RetentionProfile.DIAGNOSTIC, trades=trades, curve=_curve()
    )
    store.record_trades(run_id, trades)

    assert trade_ledger_digest(store.get_trades(run_id)) == trade_ledger_digest(trades)


def test_retrieved_curve_reproduces_its_digest(store: SqliteResearchStore):
    """Same contract for the equity curve: retrievable means verifiable."""
    trades = _trades()
    curve = _curve()
    run_id = _prepare(store, profile=RetentionProfile.FULL, trades=trades, curve=curve)
    store.record_equity_curve(run_id, curve)

    assert equity_curve_digest(store.get_equity_curve(run_id)) == equity_curve_digest(
        curve
    )


def test_verify_retained_detail_accepts_untampered_rows(
    store: SqliteResearchStore,
):
    trades = _trades()
    curve = _curve()
    run_id = _prepare(store, profile=RetentionProfile.FULL, trades=trades, curve=curve)
    store.record_trades(run_id, trades)
    store.record_equity_curve(run_id, curve)

    store.verify_retained_detail(run_id)


def test_verify_retained_detail_catches_a_tampered_row(
    store: SqliteResearchStore,
):
    """Drift after the write must be detectable, not just drift during it.

    The writers check on the way in, which only proves the rows were right when
    written. A hand-edited row, a partial delete, or a bad migration would
    otherwise leave the summary totals looking perfectly correct while the
    detail beneath them silently disagreed.
    """
    trades = _trades()
    curve = _curve()
    run_id = _prepare(store, profile=RetentionProfile.FULL, trades=trades, curve=curve)
    store.record_trades(run_id, trades)
    store.record_equity_curve(run_id, curve)

    store._conn.execute(
        "UPDATE run_trades SET pnl = pnl + 1.0 WHERE run_id = ? AND sequence = 0",
        (run_id,),
    )
    store._conn.commit()

    with pytest.raises(StoreError, match="no longer matches"):
        store.verify_retained_detail(run_id)


def test_verify_retained_detail_catches_a_deleted_row(store: SqliteResearchStore):
    """A missing trade must not read as a shorter but still-valid ledger."""
    trades = _trades()
    curve = _curve()
    run_id = _prepare(store, profile=RetentionProfile.FULL, trades=trades, curve=curve)
    store.record_trades(run_id, trades)
    store.record_equity_curve(run_id, curve)

    store._conn.execute(
        "DELETE FROM run_trades WHERE run_id = ? AND sequence = 1", (run_id,)
    )
    store._conn.commit()

    with pytest.raises(StoreError, match="no longer matches"):
        store.verify_retained_detail(run_id)