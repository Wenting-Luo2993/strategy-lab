"""Tests for live dashboard persistence stores."""

from datetime import datetime, timedelta
import sqlite3

import pytest

from vibe.common.models import Trade
from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.storage.dashboard_store import (
    AccountRecord,
    DashboardStore,
    EquitySnapshot,
    OrderEvent,
    PositionSnapshot,
    PriceBar,
    PriceBarStore,
    PublishOutboxEvent,
    PublishOutboxStore,
    StrategyAnnotation,
)
from vibe.trading_bot.storage.trade_store import TradeStore


def test_dashboard_settings_defaults():
    settings = AppSettings()

    assert settings.dashboard.enabled is False
    assert settings.dashboard.local_price_db_path == "./data/market_data.db"
    assert settings.dashboard.local_outbox_db_path == "./data/local/publish_outbox.db"
    assert settings.dashboard.publish_interval_seconds == 30


def test_price_bar_store_upsert_is_idempotent(tmp_path):
    store = PriceBarStore(str(tmp_path / "market_data.db"))
    bar_start = datetime(2026, 7, 20, 13, 30)

    store.upsert_bar(PriceBar(
        symbol="AAPL",
        timeframe="5m",
        bar_start=bar_start,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        provider="interactive_brokers",
        ingestion_time=datetime(2026, 7, 20, 13, 35),
    ))
    store.upsert_bar(PriceBar(
        symbol="AAPL",
        timeframe="5m",
        bar_start=bar_start,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.5,
        volume=1200,
        provider="interactive_brokers",
        ingestion_time=datetime(2026, 7, 20, 13, 36),
    ))

    row = store.get_bar("AAPL", "5m", bar_start)

    assert store.count_bars() == 1
    assert row["high"] == 102.0
    assert row["close"] == 101.5
    assert row["bar_start"] == bar_start.isoformat()
    store.close()


def test_dashboard_store_upserts_account_equity_position_and_order_event(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    observed_at = datetime(2026, 7, 20, 14, 0)

    store.upsert_account(AccountRecord(
        account_id="DU123",
        broker="interactive_brokers",
        display_name="IB Paper",
    ))
    store.upsert_equity_snapshot(EquitySnapshot(
        snapshot_id="eq-1",
        account_id="DU123",
        timestamp=observed_at,
        net_liquidation=100000.0,
        cash=99000.0,
        buying_power=200000.0,
    ))
    store.upsert_position(PositionSnapshot(
        position_id="DU123:AAPL",
        account_id="DU123",
        symbol="AAPL",
        quantity=10,
        side="long",
        avg_cost=100.0,
        market_price=101.0,
        unrealized_pnl=10.0,
        updated_at=observed_at,
    ))
    store.upsert_order_event(OrderEvent(
        event_id="order-1-filled",
        account_id="DU123",
        broker="interactive_brokers",
        broker_order_id="1001",
        event_type="ORDER_FILLED",
        symbol="AAPL",
        side="buy",
        quantity=10,
        price=101.0,
        expected_price=100.5,
        slippage_bps=49.75,
        latency_ms=120,
        occurred_at=observed_at,
        raw_status="Filled",
    ))
    store.upsert_strategy_annotation(StrategyAnnotation(
        annotation_id="DU123:AAPL:2026-07-20:orb_high",
        account_id="DU123",
        symbol="AAPL",
        strategy="ORB",
        trading_day="2026-07-20",
        annotation_type="level",
        key="orb_high",
        value_json={"price": 101.25, "label": "ORB High"},
    ))

    assert store.count_rows("accounts") == 1
    assert store.count_rows("equity_snapshots") == 1
    assert store.count_rows("positions") == 1
    assert store.count_rows("order_events") == 1
    assert store.count_rows("strategy_annotations") == 1
    assert store.get_row("equity_snapshots", "snapshot_id", "eq-1")["timestamp"] == observed_at.isoformat()
    assert store.get_row("positions", "position_id", "DU123:AAPL")["updated_at"] == observed_at.isoformat()
    assert store.get_row("order_events", "event_id", "order-1-filled")["occurred_at"] == observed_at.isoformat()
    annotation = store.get_row("strategy_annotations", "annotation_id", "DU123:AAPL:2026-07-20:orb_high")
    assert annotation["value_json"]["price"] == 101.25
    store.close()


def test_publish_outbox_enqueue_claim_retry_and_publish(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    event_time = datetime(2026, 7, 20, 13, 30)

    event = PublishOutboxEvent(
        event_id="price:AAPL:5m:2026-07-20T13:30:00",
        event_type="upsert",
        aggregate_type="price_bar",
        aggregate_id="AAPL|5m|2026-07-20T13:30:00",
        destination="supabase",
        payload={"symbol": "AAPL", "bar_start": event_time.isoformat()},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    )
    store.enqueue_event(event)
    store.enqueue_event(event)

    claimed = store.claim_pending(limit=10, claimed_by="test-worker", now=event_time + timedelta(seconds=1))

    assert store.count_by_status("publishing") == 1
    assert len(claimed) == 1
    assert claimed[0]["payload"]["bar_start"] == event_time.isoformat()
    assert claimed[0]["original_event_timestamp"] == event_time.isoformat()

    retry_at = event_time + timedelta(minutes=5)
    assert store.mark_failed(event.event_id, "remote unavailable", retry_at) is True
    assert store.count_by_status("failed") == 1
    assert store.claim_pending(limit=10, claimed_by="test-worker", now=event_time + timedelta(minutes=1)) == []

    claimed_again = store.claim_pending(limit=10, claimed_by="test-worker", now=retry_at)
    assert len(claimed_again) == 1
    assert claimed_again[0]["attempts"] == 1

    assert store.mark_published(event.event_id, retry_at + timedelta(seconds=1)) is True
    assert store.count_by_status("published") == 1
    store.close()


def test_publish_outbox_resets_stale_publishing_rows(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    event_time = datetime(2026, 7, 20, 13, 30)

    store.enqueue_event(PublishOutboxEvent(
        event_id="order:1",
        event_type="upsert",
        aggregate_type="order_event",
        aggregate_id="1",
        destination="supabase",
        payload={"event_id": "order:1"},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    ))
    store.claim_pending(limit=1, claimed_by="test-worker", now=event_time)

    assert store.reset_stale_publishing(event_time + timedelta(seconds=1)) == 1
    assert store.count_by_status("pending") == 1
    store.close()


def test_publish_outbox_refreshes_pending_payload(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    event_time = datetime(2026, 7, 20, 13, 30)

    store.enqueue_event(PublishOutboxEvent(
        event_id="order_event:ORDER_FILLED:1",
        event_type="upsert",
        aggregate_type="order_event",
        aggregate_id="ORDER_FILLED:1",
        destination="supabase",
        payload={"event_id": "ORDER_FILLED:1", "trade_id": None},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    ))
    store.enqueue_event(PublishOutboxEvent(
        event_id="order_event:ORDER_FILLED:1",
        event_type="upsert",
        aggregate_type="order_event",
        aggregate_id="ORDER_FILLED:1",
        destination="supabase",
        payload={"event_id": "ORDER_FILLED:1", "trade_id": "DU123:1"},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    ))

    event = store.get_event("order_event:ORDER_FILLED:1")

    assert store.count_by_status("pending") == 1
    assert event["payload"]["trade_id"] == "DU123:1"
    store.close()


def test_publish_outbox_requeues_update_arriving_during_publish(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    event_time = datetime(2026, 7, 20, 13, 30)
    event_id = "trade:partial"
    store.enqueue_event(PublishOutboxEvent(
        event_id=event_id,
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id="partial",
        destination="supabase",
        payload={"quantity": 6, "closed_quantity": 4},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    ))
    claimed = store.claim_pending(1, "publisher", now=event_time)[0]

    assert store.enqueue_event(PublishOutboxEvent(
        event_id=event_id,
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id="partial",
        destination="supabase",
        payload={"quantity": 0, "closed_quantity": 10},
        original_event_timestamp=event_time,
        next_retry_at=event_time,
    ))
    assert store.mark_published(
        event_id,
        expected_payload_version=claimed["payload_version"],
    )

    current = store.get_event(event_id)
    assert current["status"] == "pending"
    assert current["payload"] == {"quantity": 0, "closed_quantity": 10}
    assert current["successor_version"] == 1
    successors = store.get_event_successors(event_id)
    assert [row["publication_version"] for row in successors] == [1, 2]
    assert successors[0]["status"] == "published"
    assert successors[0]["payload"] == {"quantity": 6, "closed_quantity": 4}
    assert store.claim_pending(1, "successor", now=event_time)[0]["event_id"] == current["event_id"]
    store.close()


def test_outbox_supersedes_older_unpublished_successors(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    now = datetime(2026, 7, 20, 13, 30)

    def enqueue(version):
        return store.enqueue_event(PublishOutboxEvent(
            event_id="trade:ordered",
            event_type="upsert",
            aggregate_type="trade",
            aggregate_id="ordered",
            destination="supabase",
            payload={"version": version},
            original_event_timestamp=now,
            next_retry_at=now,
        ))

    enqueue(1)
    first = store.claim_pending(1, "worker", now=now)[0]
    store.mark_dead_letter(
        first["event_id"],
        "v1 failed",
        expected_payload_version=first["payload_version"],
    )
    enqueue(2)
    second = store.claim_pending(1, "worker", now=now)[0]
    store.mark_failed(
        second["event_id"],
        "v2 failed",
        now,
        expected_payload_version=second["payload_version"],
    )
    enqueue(3)

    rows = store.get_event_successors("trade:ordered")
    assert [row["status"] for row in rows] == [
        "dead_letter",
        "superseded",
        "pending",
    ]
    claimed = store.claim_pending(10, "worker", now=now)
    assert [event["payload"]["version"] for event in claimed] == [3]
    store.close()


def test_dead_letter_can_enqueue_identical_immutable_retry_successor(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "publish_outbox.db"))
    now = datetime(2026, 7, 20, 13, 30)
    event = PublishOutboxEvent(
        event_id="trade:retry",
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id="retry",
        destination="supabase",
        payload={"trade_id": "retry"},
        original_event_timestamp=now,
        next_retry_at=now,
    )
    store.enqueue_event(event)
    claimed = store.claim_pending(1, "worker", now=now)[0]
    store.mark_dead_letter(
        claimed["event_id"],
        "temporary outage",
        expected_payload_version=claimed["payload_version"],
    )

    assert store.enqueue_event(event)
    successors = store.get_event_successors("trade:retry")
    assert [row["status"] for row in successors] == [
        "dead_letter",
        "pending",
    ]
    assert successors[1]["payload"] == successors[0]["payload"]
    store.close()


def test_accounts_currency_rebuild_recovers_interrupted_legacy_table(tmp_path):
    path = tmp_path / "dashboard.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            display_name TEXT NOT NULL,
            currency TEXT,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE accounts_legacy_currency (
            account_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            display_name TEXT NOT NULL,
            currency TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO accounts_legacy_currency VALUES
            ('legacy', 'ib', 'Legacy', 'CAD', 'paper', 't0', 't0');
        INSERT INTO accounts_legacy_currency VALUES
            ('current', 'ib', 'Stale current', 'USD', 'paper', 't0', 't0');
        INSERT INTO accounts VALUES
            ('current', 'ib', 'Current', NULL, 'paper', 't1', 't1');
    """)
    conn.close()

    store = DashboardStore(str(path))
    rows = store._get_connection().execute(
        "SELECT account_id, currency FROM accounts ORDER BY account_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("current", None),
        ("legacy", "CAD"),
    ]
    assert store._get_connection().execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'accounts_legacy_currency'"
    ).fetchone() is None
    store.close()

    rerun = DashboardStore(str(path))
    assert rerun.count_rows("accounts") == 2
    currency_column = next(
        row
        for row in rerun._get_connection().execute("PRAGMA table_info(accounts)")
        if row["name"] == "currency"
    )
    assert currency_column["notnull"] == 0
    rerun.close()


def test_accounts_currency_migration_rolls_back_and_recovers_after_failure(
    tmp_path,
):
    path = tmp_path / "dashboard.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            display_name TEXT NOT NULL,
            currency TEXT,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE accounts_legacy_currency (
            account_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            display_name TEXT NOT NULL,
            currency TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO accounts_legacy_currency VALUES
            ('legacy', 'ib', 'Legacy', 'CAD', 'paper', 't0', 't0');
        CREATE TRIGGER interrupt_accounts_migration
        BEFORE INSERT ON accounts
        WHEN NEW.account_id = 'legacy'
        BEGIN SELECT RAISE(ABORT, 'migration interrupted'); END;
    """)
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="migration interrupted"):
        DashboardStore(str(path))

    interrupted = sqlite3.connect(path)
    assert interrupted.execute(
        "SELECT COUNT(*) FROM accounts_legacy_currency"
    ).fetchone()[0] == 1
    assert interrupted.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    interrupted.execute("DROP TRIGGER interrupt_accounts_migration")
    interrupted.commit()
    interrupted.close()

    recovered = DashboardStore(str(path))
    assert recovered.get_row(
        "accounts", "account_id", "legacy"
    )["currency"] == "CAD"
    recovered.close()

    rerun = DashboardStore(str(path))
    assert rerun.count_rows("accounts") == 1
    rerun.close()


def test_equity_snapshot_provenance_is_backward_compatible(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    historical = EquitySnapshot(
        snapshot_id="historical",
        account_id="A",
        timestamp=datetime(2025, 1, 1),
        realized_pnl=10,
    )
    broker = EquitySnapshot(
        snapshot_id="broker",
        account_id="A",
        timestamp=datetime(2026, 1, 1),
        realized_pnl=20,
        pnl_provenance="broker",
        realized_pnl_provenance="broker",
        pnl_version=2,
    )

    store.upsert_equity_snapshot(historical)
    store.upsert_equity_snapshot(broker)

    assert store.get_row(
        "equity_snapshots", "snapshot_id", "historical"
    )["pnl_provenance"] is None
    current = store.get_row("equity_snapshots", "snapshot_id", "broker")
    assert current["realized_pnl_provenance"] == "broker"
    assert current["pnl_version"] == 2
    store.close()


def test_trade_store_dashboard_columns_and_account_backfill(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade_id = store.insert_trade(Trade(
        trade_id="trade-1",
        symbol="AAPL",
        side="buy",
        quantity=10,
        entry_price=100.0,
        exit_price=101.0,
        exit_reason="TARGET",
    ))

    row = store.get_trade_by_id(trade_id)
    assert row["trade_id"] == "trade-1"
    assert row["exit_reason"] == "TARGET"
    assert row["account_id"] is None

    updated = store.backfill_dashboard_account_id("DU123")
    row = store.get_trade_by_id(trade_id)

    assert updated == 1
    assert row["account_id"] == "DU123"
    store.close()