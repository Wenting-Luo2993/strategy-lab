"""Tests for live dashboard persistence stores."""

from datetime import datetime, timedelta

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

    assert store.count_rows("accounts") == 1
    assert store.count_rows("equity_snapshots") == 1
    assert store.count_rows("positions") == 1
    assert store.count_rows("order_events") == 1
    assert store.get_row("equity_snapshots", "snapshot_id", "eq-1")["timestamp"] == observed_at.isoformat()
    assert store.get_row("positions", "position_id", "DU123:AAPL")["updated_at"] == observed_at.isoformat()
    assert store.get_row("order_events", "event_id", "order-1-filled")["occurred_at"] == observed_at.isoformat()
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