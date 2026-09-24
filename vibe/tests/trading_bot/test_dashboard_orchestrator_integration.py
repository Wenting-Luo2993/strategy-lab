"""Tests for live dashboard persistence integration in the orchestrator."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.publishing.remote_data_publisher import RemoteDataPublisher
from vibe.trading_bot.storage.dashboard_store import EquitySnapshot
from vibe.common.models import AccountState, Position, Trade
from vibe.trading_bot.brokers.base import BrokerPosition
from vibe.common.risk import PositionSizer
from vibe.common.strategies import ORBStrategy
from vibe.common.strategies.orb import ORBStrategyConfig
from vibe.trading_bot.execution.trade_executor import TradeExecutor


def _dashboard_config(tmp_path):
    return AppSettings(
        environment="test",
        database_path=str(tmp_path / "trades.db"),
        broker={"broker_type": "mock", "mode": "paper"},
        trading={"symbols": ["AAPL"]},
        dashboard={
            "enabled": True,
            "account_id": "DU123",
            "symbols": ["AAPL"],
            "local_price_db_path": str(tmp_path / "market_data.db"),
            "local_dashboard_db_path": str(tmp_path / "dashboard.db"),
            "local_outbox_db_path": str(tmp_path / "publish_outbox.db"),
        },
        operational_metrics={
            "enabled": True,
            "local_database_path": str(tmp_path / "operational_metrics.db"),
        },
        notifications={"discord_webhook_url": None},
    )


def _ruleset():
    return SimpleNamespace(
        name="test_ruleset",
        version="1.0",
        instruments=SimpleNamespace(symbols=["AAPL"], timeframe="5m"),
    )


def _scheduler():
    return MockMarketScheduler(
        initial_date=datetime(2026, 7, 20, 9, 35),
        timezone="America/New_York",
    )


def test_completed_bar_persists_price_bar_and_outbox_event(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
        bar_interval="5m",
    )
    orchestrator.active_provider = SimpleNamespace(provider_name="interactive_brokers")
    bar_start = datetime(2026, 7, 20, 9, 30)

    orchestrator._handle_completed_bar("AAPL", {
        "timestamp": bar_start,
        "open": 100.0,
        "high": 101.0,
        "low": 99.5,
        "close": 100.5,
        "volume": 1200,
    })

    stored_bar = orchestrator.dashboard_price_store.get_bar("AAPL", "5m", bar_start)

    assert stored_bar["close"] == 100.5
    assert stored_bar["provider"] == "interactive_brokers"
    assert orchestrator.dashboard_outbox_store.count_by_status("pending") == 1
    assert orchestrator.dashboard_publish_wake_event.is_set()
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()



@pytest.mark.asyncio
async def test_order_fill_persists_order_account_position_and_outbox_events(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)

    response = await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="buy",
        quantity=10,
        order_type="market",
        price=100.0,
    )

    await orchestrator._on_order_created(response.order_id)
    initial_sent_event = orchestrator.dashboard_store.get_row(
        "order_events",
        "event_id",
        f"ORDER_SENT:{response.order_id}",
    )
    orchestrator.market_scheduler.advance_time(seconds=2)
    await orchestrator._persist_dashboard_trade_entry(
        symbol="AAPL",
        order_id=response.order_id,
        signal_value=1,
        quantity=response.filled_qty,
        entry_price=response.avg_price,
        entry_time=datetime(2026, 7, 20, 9, 35),
    )
    await orchestrator._on_order_filled(response.order_id)

    sent_event = orchestrator.dashboard_store.get_row(
        "order_events",
        "event_id",
        f"ORDER_SENT:{response.order_id}",
    )
    filled_event = orchestrator.dashboard_store.get_row(
        "order_events",
        "event_id",
        f"EXECUTION:{(await orchestrator.exchange.get_order(response.order_id)).execution_id}",
    )
    position = orchestrator.dashboard_store.get_row("positions", "position_id", "DU123:AAPL")
    trade = orchestrator.trade_store.get_trades(symbol="AAPL", status="open")[0]
    metrics = orchestrator.operational_metrics_store.get_metrics(metric_type="trade")
    trade_id = f"DU123:{response.order_id}"

    assert sent_event["symbol"] == "AAPL"
    assert sent_event["trade_id"] == trade_id
    assert sent_event["price"] == 100.0
    assert sent_event["slippage_bps"] is None
    assert sent_event["occurred_at"] == initial_sent_event["occurred_at"]
    assert filled_event["price"] == response.avg_price
    assert filled_event["slippage_bps"] is not None
    assert filled_event["latency_ms"] >= 0.0
    assert filled_event["slippage_version"] == 2
    assert filled_event["slippage_valid"] == 1
    assert filled_event["trade_id"] == trade_id
    assert filled_event["commission_currency"] == "USD"
    assert filled_event["trade_currency"] == "USD"
    assert orchestrator.dashboard_store._get_connection().execute(
        "SELECT COUNT(*) FROM order_events WHERE execution_id = ?",
        (filled_event["execution_id"],),
    ).fetchone()[0] == 1
    assert trade["trade_id"] == trade_id
    assert trade["broker_order_id"] == response.order_id
    assert orchestrator.trade_store.has_execution_lifecycle_projection(
        filled_event["execution_id"]
    )
    assert orchestrator.dashboard_store.count_rows("accounts") == 1
    assert orchestrator.dashboard_store.count_rows("equity_snapshots") == 1
    assert position["quantity"] == 10
    assert {metric["metric_name"] for metric in metrics} >= {
        "actual_fill_price",
        "fill_quantity",
        "commission",
        "expected_fill_price",
        "latency_ms",
        "slippage_bps",
    }
    assert orchestrator.dashboard_outbox_store.count_by_status("pending") >= 12
    assert orchestrator.dashboard_publish_wake_event.is_set()
    orchestrator.operational_metrics_store.close()
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()


@pytest.mark.asyncio
async def test_trade_close_updates_trade_and_links_trade_closed_event(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)

    entry_response = await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="buy",
        quantity=10,
        order_type="market",
        price=100.0,
    )
    await orchestrator._persist_dashboard_trade_entry(
        symbol="AAPL",
        order_id=entry_response.order_id,
        signal_value=1,
        quantity=entry_response.filled_qty,
        entry_price=entry_response.avg_price,
        entry_time=datetime(2026, 7, 20, 9, 35),
    )

    await orchestrator.exchange.set_price("AAPL", 102.0)
    exit_response = await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="sell",
        quantity=10,
        order_type="market",
        price=102.0,
    )
    await orchestrator._persist_dashboard_trade_exit(
        symbol="AAPL",
        order_id=exit_response.order_id,
        exit_price=exit_response.avg_price,
        exit_time=datetime(2026, 7, 20, 10, 0),
        exit_reason="take_profit",
    )

    trade_id = f"DU123:{entry_response.order_id}"
    trade = orchestrator.trade_store.get_trades(symbol="AAPL", status="closed")[0]
    closed_event = orchestrator.dashboard_store.get_row(
        "order_events",
        "event_id",
        f"TRADE_CLOSED:{exit_response.order_id}",
    )

    assert trade["trade_id"] == trade_id
    assert trade["exit_reason"] == "take_profit"
    assert trade["pnl"] > 0
    assert closed_event["trade_id"] == trade_id
    assert closed_event["price"] == exit_response.avg_price
    assert len(orchestrator.operational_metrics_store.get_metrics(metric_name="slippage_bps")) == 2
    assert closed_event["execution_id"] is None
    assert closed_event["slippage_valid"] == 0
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()
    orchestrator.operational_metrics_store.close()


@pytest.mark.asyncio
async def test_trade_close_recovers_open_trade_after_restart(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)

    entry_response = await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="buy",
        quantity=10,
        order_type="market",
        price=100.0,
    )
    await orchestrator._persist_dashboard_trade_entry(
        symbol="AAPL",
        order_id=entry_response.order_id,
        signal_value=1,
        quantity=entry_response.filled_qty,
        entry_price=entry_response.avg_price,
        entry_time=datetime(2026, 7, 20, 9, 35),
    )
    orchestrator._dashboard_symbol_trade_ids.clear()
    orchestrator._dashboard_trade_row_ids.clear()

    await orchestrator.exchange.set_price("AAPL", 102.0)
    exit_response = await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="sell",
        quantity=10,
        order_type="market",
        price=102.0,
    )
    await orchestrator._persist_dashboard_trade_exit(
        symbol="AAPL",
        order_id=exit_response.order_id,
        exit_price=exit_response.avg_price,
        exit_time=datetime(2026, 7, 20, 10, 0),
        exit_reason="take_profit",
    )

    trade = orchestrator.trade_store.get_trades(symbol="AAPL", status="closed")[0]
    closed_event = orchestrator.dashboard_store.get_row(
        "order_events",
        "event_id",
        f"TRADE_CLOSED:{exit_response.order_id}",
    )

    assert trade["trade_id"] == f"DU123:{entry_response.order_id}"
    assert trade["exit_reason"] == "take_profit"
    assert closed_event["trade_id"] == trade["trade_id"]
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()
    orchestrator.operational_metrics_store.close()


def test_orb_levels_persist_strategy_annotations_and_outbox_events(tmp_path):
    scheduler = _scheduler()
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=scheduler,
        testing_mode=True,
    )
    trading_day = datetime.now(scheduler.timezone).date()
    trading_day_text = trading_day.isoformat()

    orchestrator._update_daily_stats("AAPL", 0, {
        "orb_high": 101.25,
        "orb_low": 99.75,
        "orb_range": 1.5,
        "orb_trading_date": trading_day,
        "current_bar": {"open": 100.0, "close": 101.0, "high": 101.25, "low": 99.75},
    })

    annotation = orchestrator.dashboard_store.get_row(
        "strategy_annotations",
        "annotation_id",
        f"DU123:AAPL:{trading_day_text}:orb_high",
    )

    assert annotation["key"] == "orb_high"
    assert annotation["value_json"]["price"] == 101.25
    assert orchestrator.dashboard_store.count_rows("strategy_annotations") == 3
    assert orchestrator.dashboard_outbox_store.count_by_status("pending") == 3
    assert orchestrator.dashboard_publish_wake_event.is_set()
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()
    orchestrator.operational_metrics_store.close()


@pytest.mark.asyncio
async def test_account_position_poll_publishes_flat_snapshot_when_no_position(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()

    await orchestrator._persist_dashboard_account_and_positions(reason="poll")

    position = orchestrator.dashboard_store.get_row("positions", "position_id", "DU123:AAPL")
    outbox_events = orchestrator.dashboard_outbox_store.claim_pending(limit=10, claimed_by="test")
    outbox_event = next(event for event in outbox_events if event["aggregate_type"] == "position")

    assert position["quantity"] == 0
    assert position["side"] == "flat"
    assert outbox_event["payload"]["quantity"] == 0.0
    assert outbox_event["payload"]["side"] == "flat"
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()


@pytest.mark.asyncio
async def test_repeated_flat_poll_does_not_publish_duplicate_position(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()

    await orchestrator._persist_dashboard_account_and_positions(reason="poll")
    orchestrator.market_scheduler.advance_time(minutes=1)
    await orchestrator._persist_dashboard_account_and_positions(reason="poll")

    events = orchestrator.dashboard_outbox_store.claim_pending(limit=20, claimed_by="test")
    position_events = [event for event in events if event["aggregate_type"] == "position"]
    assert len(position_events) == 1
    assert orchestrator.dashboard_store.count_rows("positions") == 1
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()


@pytest.mark.asyncio
async def test_restart_always_publishes_first_position_observation_once(tmp_path):
    config = _dashboard_config(tmp_path)
    first = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await first.exchange.initialize()
    await first._persist_dashboard_account_and_positions(reason="poll")

    class Destination:
        async def publish(self, event):
            return None

    publisher = RemoteDataPublisher(first.dashboard_outbox_store, Destination())
    await publisher.flush_pending(timeout_seconds=5, max_batches=20)
    publisher.reconcile_sources(
        [first.dashboard_store],
        first.market_scheduler.now().date().isoformat(),
    )
    assert first.dashboard_outbox_store._get_connection().execute(
        """
        SELECT COUNT(*) FROM publish_outbox
        WHERE aggregate_type = 'equity_snapshot' AND status = 'pending'
        """
    ).fetchone()[0] == 0
    first.dashboard_price_store.close()
    first.dashboard_store.close()
    first.dashboard_outbox_store.close()
    first.trade_store.close()
    first.operational_metrics_store.close()

    restarted = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await restarted.exchange.initialize()
    await restarted._persist_dashboard_account_and_positions(reason="poll")
    startup_event_id = (
        f"position-startup:{restarted._position_publication_process_id}:DU123:AAPL"
    )
    assert restarted.dashboard_outbox_store.get_event(startup_event_id)["status"] == "pending"

    await restarted._persist_dashboard_account_and_positions(reason="poll")
    assert restarted.dashboard_outbox_store._get_connection().execute(
        "SELECT COUNT(*) FROM publish_outbox WHERE event_id = ?",
        (startup_event_id,),
    ).fetchone()[0] == 1
    reconciled_position = next(
        event
        for event in restarted.dashboard_store.iter_publish_events()
        if event.aggregate_type == "position"
    )
    assert reconciled_position.payload["updated_at"]


@pytest.mark.asyncio
async def test_initialize_publishes_first_position_observation(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=None,
        market_scheduler=_scheduler(),
        testing_mode=True,
    )

    assert await orchestrator.initialize()

    events = orchestrator.dashboard_outbox_store.claim_pending(
        limit=20,
        claimed_by="test",
    )
    position_events = [
        event for event in events if event["aggregate_type"] == "position"
    ]
    assert position_events
    assert len({event["aggregate_id"] for event in position_events}) == len(
        position_events
    )
    assert all(event["payload"]["quantity"] == 0.0 for event in position_events)


@pytest.mark.asyncio
async def test_restart_recovers_dirty_position_without_duplicate_startup_event(tmp_path, monkeypatch):
    config = _dashboard_config(tmp_path)
    first = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await first.exchange.initialize()
    first.exchange.partial_fill_probability = 0.0
    await first._persist_dashboard_account_and_positions(reason="poll")
    await first.exchange.set_price("AAPL", 100.0)
    await first.exchange.submit_order("AAPL", "buy", 1, "market", price=100.0)

    original_enqueue = first.dashboard_outbox_store.enqueue_event

    def fail_position(event):
        if event.aggregate_type == "position":
            raise RuntimeError("remote queue unavailable")
        return original_enqueue(event)

    monkeypatch.setattr(first.dashboard_outbox_store, "enqueue_event", fail_position)
    await first._persist_dashboard_account_and_positions(reason="order_filled")
    assert first.dashboard_store.position_needs_publication("DU123:AAPL")
    first.dashboard_price_store.close()
    first.dashboard_store.close()
    first.dashboard_outbox_store.close()
    first.trade_store.close()
    first.operational_metrics_store.close()

    restarted = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await restarted.exchange.initialize()
    restarted.exchange.partial_fill_probability = 0.0
    await restarted.exchange.set_price("AAPL", 100.0)
    await restarted.exchange.submit_order("AAPL", "buy", 1, "market", price=100.0)
    await restarted._persist_dashboard_account_and_positions(reason="poll")

    recovered = restarted.dashboard_outbox_store.get_event("position:DU123:AAPL")
    assert recovered["payload"]["quantity"] == 1
    assert not restarted.dashboard_store.position_needs_publication("DU123:AAPL")
    startup_prefix = (
        f"position-startup:{restarted._position_publication_process_id}:"
    )
    assert restarted.dashboard_outbox_store._get_connection().execute(
        "SELECT COUNT(*) FROM publish_outbox WHERE event_id LIKE ?",
        (f"{startup_prefix}%",),
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_mixed_currency_positions_preserve_broker_instrument_currency(tmp_path):
    config = _dashboard_config(tmp_path)
    config.trading.symbols = ["AAPL", "SHOP"]
    config.dashboard.symbols = ["AAPL", "SHOP"]
    config.broker.ib_currency = "USD"
    ruleset = _ruleset()
    ruleset.instruments.symbols = ["AAPL", "SHOP"]
    orchestrator = TradingOrchestrator(
        config=config,
        ruleset=ruleset,
        market_scheduler=_scheduler(),
        testing_mode=True,
    )

    class MixedCurrencyExchange:
        async def get_account(self):
            return AccountState(
                cash=1000,
                equity=2000,
                buying_power=3000,
                portfolio_value=2000,
                base_currency="USD",
                timestamp=datetime.now(timezone.utc),
            )

        async def get_position(self, symbol):
            currency = {"AAPL": "EUR", "SHOP": "CAD"}[symbol]
            return Position(
                symbol=symbol,
                side="long",
                quantity=2,
                entry_price=100,
                current_price=101,
                instrument_currency=currency,
                unrealized_pnl_currency=currency,
            )

    orchestrator.exchange = MixedCurrencyExchange()
    await orchestrator._persist_dashboard_account_and_positions(reason="poll")

    aapl = orchestrator.dashboard_store.get_row("positions", "position_id", "DU123:AAPL")
    shop = orchestrator.dashboard_store.get_row("positions", "position_id", "DU123:SHOP")
    assert (aapl["instrument_currency"], aapl["unrealized_pnl_currency"]) == ("EUR", "EUR")
    assert (shop["instrument_currency"], shop["unrealized_pnl_currency"]) == ("CAD", "CAD")


@pytest.mark.asyncio
async def test_entry_retry_fill_updates_same_trade_weighted_quantity_and_price(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)
    first = await orchestrator.exchange.submit_order("AAPL", "buy", 4, "market", price=100.0)
    await orchestrator._persist_dashboard_trade_entry(
        symbol="AAPL",
        order_id=first.order_id,
        signal_value=1,
        quantity=first.filled_qty,
        entry_price=first.avg_price,
        entry_time=datetime.now(timezone.utc),
    )

    await orchestrator.exchange.set_price("AAPL", 110.0)
    retry = await orchestrator.exchange.submit_order("AAPL", "buy", 6, "market", price=110.0)
    await orchestrator._on_order_filled(retry.order_id)

    trade = orchestrator.trade_store.get_trades(symbol="AAPL", status="open")
    position = await orchestrator.exchange.get_position("AAPL")
    assert len(trade) == 1
    assert trade[0]["quantity"] == 10
    assert trade[0]["entry_price"] == pytest.approx(position.entry_price)
    assert orchestrator._dashboard_order_trade_ids[retry.order_id] == trade[0]["trade_id"]


@pytest.mark.asyncio
async def test_partial_close_pipeline_retains_state_and_dashboard_pnl_until_flat(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)
    entry = await orchestrator.exchange.submit_order("AAPL", "buy", 10, "market", price=100.0)
    entry_time = datetime.now(timezone.utc)
    await orchestrator._persist_dashboard_trade_entry(
        symbol="AAPL",
        order_id=entry.order_id,
        signal_value=1,
        quantity=entry.filled_qty,
        entry_price=entry.avg_price,
        entry_time=entry_time,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.strategy.track_position(
        "AAPL",
        "buy",
        entry.avg_price,
        120.0,
        95.0,
        entry_time,
        quantity=10,
    )

    class SequencedCloseManager:
        def __init__(self, exchange):
            self.exchange = exchange
            self.fill_quantities = [4, 6]

        async def submit_order(self, **kwargs):
            fill_quantity = self.fill_quantities.pop(0)
            return await self.exchange.submit_order(
                symbol=kwargs["symbol"],
                side=kwargs["side"],
                quantity=fill_quantity,
                order_type=kwargs["order_type"],
                price=kwargs["price"],
            )

    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SequencedCloseManager(orchestrator.exchange),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator.exchange.set_price("AAPL", 102.0)
    tracked = orchestrator.strategy.get_position("AAPL")
    await orchestrator._close_position_with_notification("AAPL", tracked, 102.0, "target")

    partial = orchestrator.trade_store.get_trades(symbol="AAPL", status="open")[0]
    assert partial["quantity"] == 6
    assert partial["closed_quantity"] == 4
    assert partial["pnl"] == pytest.approx(
        (partial["exit_price"] - partial["entry_price"]) * 4
    )
    latest_equity = orchestrator.dashboard_store._get_connection().execute(
        "SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    assert latest_equity["local_realized_pnl"] == pytest.approx(partial["pnl"])
    assert orchestrator.strategy.get_position("AAPL")["quantity"] == 6
    assert orchestrator.trade_executor.get_open_trades()["AAPL"].position_size == 6

    await orchestrator.exchange.set_price("AAPL", 103.0)
    retry_close = await orchestrator.exchange.submit_order(
        "AAPL",
        "sell",
        6,
        "market",
        price=103.0,
    )
    await orchestrator._on_order_filled(retry_close.order_id)

    closed = orchestrator.trade_store.get_trades(symbol="AAPL", status="closed")[0]
    assert closed["quantity"] == 10
    assert closed["closed_quantity"] == 10
    assert closed["pnl"] == pytest.approx(
        (closed["exit_price"] - closed["entry_price"]) * 10
    )
    assert orchestrator.strategy.get_position("AAPL") is None
    assert "AAPL" not in orchestrator.trade_executor.get_open_trades()


def test_supabase_metric_migration_keeps_legacy_and_durable_unique_keys():
    migration = (
        Path(__file__).parents[3]
        / "docs"
        / "trading-bot-mvp"
        / "dashboards"
        / "supabase-read-model.sql"
    ).read_text(encoding="utf-8")
    backfill = migration.index("add column if not exists legacy_row_id")
    compatibility_default = migration.index(
        "operational_metrics_legacy_metric_id_seq"
    )
    validation = migration.index("Duplicate operational metric IDs remain")
    durable_key = migration.index(
        "operational_metrics_metric_id_key"
    )
    legacy_key = migration.index(
        "operational_metrics_legacy_name_timestamp_key"
    )
    assert "'legacy:id:' || id::text || ':row:' || legacy_row_id::text" in migration
    assert "row_number() over" in migration
    assert "drop constraint" not in migration
    assert backfill < compatibility_default < validation < durable_key < legacy_key


@pytest.mark.asyncio
async def test_delayed_entry_fill_creates_strategy_and_dashboard_projection(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    order = SimpleNamespace(
        order_id="late-entry",
        symbol="AAPL",
        side="buy",
        filled_qty=3,
        avg_price=101.0,
        price=101.0,
        filled_at=datetime.now(timezone.utc),
        trade_currency="CAD",
    )

    class LateFillExchange:
        async def get_position(self, symbol):
            assert symbol == "AAPL"
            return Position(
                symbol="AAPL",
                side="long",
                quantity=3,
                entry_price=101.0,
                current_price=102.0,
                instrument_currency="CAD",
            )

        async def get_order(self, order_id):
            assert order_id == "late-entry"
            return order

    orchestrator.exchange = LateFillExchange()
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )
    orchestrator.trade_executor._pending_entries[order.order_id] = {
        "symbol": "AAPL",
        "side": "buy",
        "stop_price": 95.0,
        "take_profit": 110.0,
        "strategy_name": "test",
    }

    await orchestrator._sync_open_trade_after_entry_fill(order)

    trade = orchestrator.trade_store.get_trades(symbol="AAPL", status="open")
    assert len(trade) == 1
    assert trade[0]["quantity"] == 3
    assert trade[0]["entry_price"] == 101.0
    assert trade[0]["pnl_currency"] == "CAD"
    assert orchestrator.strategy.get_position("AAPL")["quantity"] == 3
    assert orchestrator.trade_executor.get_pending_entry(order.order_id) is None


@pytest.mark.asyncio
async def test_async_exit_fill_clears_strategy_when_dashboard_disabled(tmp_path):
    config = _dashboard_config(tmp_path)
    config.dashboard.enabled = False
    orchestrator = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.strategy.track_position(
        "AAPL",
        "buy",
        100.0,
        110.0,
        95.0,
        datetime.now(timezone.utc),
        quantity=5,
    )
    orchestrator._pending_exit_reasons["AAPL"] = "target"

    class FlatExchange:
        async def get_position(self, symbol):
            return None

    orchestrator.exchange = FlatExchange()
    await orchestrator._sync_open_trade_after_exit_fill(
        SimpleNamespace(
            symbol="AAPL",
            side="sell",
            filled_qty=5,
            avg_price=105.0,
            price=105.0,
            filled_at=datetime.now(timezone.utc),
        )
    )

    assert orchestrator.strategy.get_position("AAPL") is None
    assert "AAPL" not in orchestrator._pending_exit_reasons


@pytest.mark.parametrize("exit_reason", ["stop_loss", "take_profit", "eod"])
@pytest.mark.asyncio
async def test_delayed_exit_fill_preserves_reason_until_projection(
    tmp_path,
    exit_reason,
):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.strategy.track_position(
        "AAPL",
        "buy",
        100.0,
        110.0,
        95.0,
        datetime.now(timezone.utc),
        quantity=5,
    )
    trade_id = "DU123:entry"
    row_id = orchestrator.trade_store.insert_trade(Trade(
        trade_id=trade_id,
        symbol="AAPL",
        side="buy",
        quantity=5,
        entry_price=100,
        entry_time=datetime.now(timezone.utc),
        pnl_currency="USD",
        strategy="test",
    ))
    orchestrator.trade_store.update_trade(
        row_id,
        account_id="DU123",
        status="open",
    )
    orchestrator._dashboard_symbol_trade_ids["AAPL"] = trade_id
    orchestrator._dashboard_trade_row_ids[trade_id] = row_id
    orchestrator._pending_exit_reasons["AAPL"] = exit_reason
    order = SimpleNamespace(
        order_id="late-close",
        symbol="AAPL",
        side="sell",
        quantity=5,
        filled_qty=5,
        avg_price=105.0,
        price=105.0,
        filled_at=datetime.now(timezone.utc),
        executions=[],
        execution_id=None,
        permanent_order_id=None,
        account_id="DU123",
        trade_currency="USD",
        commission=0.0,
        commission_currency=None,
        submitted_at=None,
        decision_at=None,
        benchmark_price=None,
        benchmark_valid=False,
    )

    class FlatExchange:
        async def get_position(self, symbol):
            return None

        async def get_order(self, order_id):
            return order

    orchestrator.exchange = FlatExchange()
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator._sync_open_trade_after_exit_fill(order)

    projected = orchestrator.trade_store.get_trade_by_id(row_id)
    assert projected["status"] == "closed"
    assert projected["exit_reason"] == exit_reason
    assert "AAPL" not in orchestrator._pending_exit_reasons


@pytest.mark.asyncio
async def test_delayed_exit_fill_closes_trade_when_broker_position_snapshot_is_stale(
    tmp_path,
):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.strategy.track_position(
        "AAPL",
        "buy",
        100.0,
        110.0,
        95.0,
        datetime.now(timezone.utc),
        quantity=6,
    )
    row_id = orchestrator.trade_store.insert_trade(Trade(
        trade_id="DU123:entry",
        symbol="AAPL",
        side="buy",
        quantity=6,
        entry_price=100,
        entry_time=datetime.now(timezone.utc),
        pnl_currency="USD",
        strategy="test",
    ))
    orchestrator.trade_store.update_trade(row_id, account_id="DU123", status="open")
    orchestrator._pending_exit_reasons["AAPL"] = "stop_loss"
    order = SimpleNamespace(
        order_id="close",
        symbol="AAPL",
        side="sell",
        quantity=6,
        filled_qty=6,
        avg_price=99.0,
        price=99.0,
        filled_at=datetime.now(timezone.utc),
        executions=[],
        execution_id=None,
        permanent_order_id=None,
        account_id="DU123",
        trade_currency="USD",
        commission=0.0,
        commission_currency=None,
        submitted_at=None,
        decision_at=None,
        benchmark_price=None,
        benchmark_valid=False,
    )

    class StalePositionExchange:
        async def get_position(self, symbol):
            return SimpleNamespace(quantity=6, entry_price=100.0)

        async def get_order(self, order_id):
            return order

    orchestrator.exchange = StalePositionExchange()
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator._sync_open_trade_after_exit_fill(order)

    projected = orchestrator.trade_store.get_trade_by_id(row_id)
    assert projected["status"] == "closed"
    assert projected["closed_quantity"] == 6
    assert orchestrator.strategy.get_position("AAPL") is None
    assert "AAPL" not in orchestrator._pending_exit_reasons


@pytest.mark.asyncio
async def test_partial_exit_fill_stays_open_when_broker_snapshot_is_temporarily_flat(
    tmp_path,
):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.strategy.track_position(
        "AAPL",
        "buy",
        100.0,
        110.0,
        95.0,
        datetime.now(timezone.utc),
        quantity=6,
    )
    row_id = orchestrator.trade_store.insert_trade(Trade(
        trade_id="DU123:entry",
        symbol="AAPL",
        side="buy",
        quantity=6,
        entry_price=100,
        entry_time=datetime.now(timezone.utc),
        pnl_currency="USD",
        strategy="test",
    ))
    orchestrator.trade_store.update_trade(row_id, account_id="DU123", status="open")
    orchestrator._pending_exit_reasons["AAPL"] = "stop_loss"
    order = SimpleNamespace(
        order_id="partial-close",
        symbol="AAPL",
        side="sell",
        quantity=6,
        filled_qty=2,
        avg_price=99.0,
        price=99.0,
        filled_at=datetime.now(timezone.utc),
        executions=[],
        execution_id=None,
        permanent_order_id=None,
        account_id="DU123",
        trade_currency="USD",
        commission=0.0,
        commission_currency=None,
        submitted_at=None,
        decision_at=None,
        benchmark_price=None,
        benchmark_valid=False,
    )

    class TemporarilyFlatExchange:
        async def get_position(self, symbol):
            return None

        async def get_order(self, order_id):
            return order

    orchestrator.exchange = TemporarilyFlatExchange()
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator._sync_open_trade_after_exit_fill(order)

    projected = orchestrator.trade_store.get_trade_by_id(row_id)
    assert projected["status"] == "open"
    assert projected["quantity"] == 4
    assert projected["closed_quantity"] == 2
    assert orchestrator.strategy.get_position("AAPL")["quantity"] == 4
    assert orchestrator._pending_exit_reasons["AAPL"] == "stop_loss"

    order.filled_qty = 4
    order.avg_price = 98.5
    await orchestrator._sync_open_trade_after_exit_fill(order)

    projected = orchestrator.trade_store.get_trade_by_id(row_id)
    assert projected["status"] == "open"
    assert projected["quantity"] == 2
    assert projected["closed_quantity"] == 4
    assert orchestrator.strategy.get_position("AAPL")["quantity"] == 2


@pytest.mark.asyncio
async def test_accepted_zero_fill_close_retains_pending_exit_reason(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    position = {
        "entry_price": 100.0,
        "side": "buy",
        "quantity": 2,
    }

    class AcceptedClose:
        async def _close_position(self, symbol, exit_reason=None):
            assert exit_reason == "stop_loss"
            return SimpleNamespace(
                success=False,
                order_id="accepted-close",
                reason="Close order not filled (status=SUBMITTED)",
            )

    orchestrator.trade_executor = AcceptedClose()
    await orchestrator._close_position_with_notification(
        "AAPL",
        position,
        95.0,
        "stop_loss",
    )

    assert orchestrator._pending_exit_reasons["AAPL"] == "stop_loss"


@pytest.mark.asyncio
async def test_restart_replays_entry_and_partial_then_full_close(tmp_path):
    now = datetime.now(timezone.utc)
    entry = {
        "execution_id": "entry-1",
        "broker_order_id": "entry-order",
        "account_id": "DU123",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100,
        "filled_at": now.isoformat(),
        "trade_currency": "USD",
        "order_metadata": {
            "strategy_name": "test",
            "stop_price": 95,
            "take_profit": 110,
        },
    }
    partial = {
        "execution_id": "close-1",
        "broker_order_id": "close-order",
        "account_id": "DU123",
        "symbol": "AAPL",
        "side": "sell",
        "quantity": 4,
        "price": 108,
        "filled_at": (now + timedelta(seconds=1)).isoformat(),
        "trade_currency": "USD",
        "order_metadata": {"exit_reason": "take_profit"},
    }
    final = {
        "execution_id": "close-2",
        "broker_order_id": "close-order",
        "account_id": "DU123",
        "symbol": "AAPL",
        "side": "sell",
        "quantity": 6,
        "price": 109,
        "filled_at": (now + timedelta(seconds=2)).isoformat(),
        "trade_currency": "USD",
        "order_metadata": {"exit_reason": "take_profit"},
    }

    async def recover(executions):
        instance = TradingOrchestrator(
            config=_dashboard_config(tmp_path),
            ruleset=_ruleset(),
            market_scheduler=_scheduler(),
            testing_mode=True,
        )
        instance.exchange = SimpleNamespace(
            list_durable_executions=lambda: executions
        )
        instance.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
        instance.trade_executor = TradeExecutor(
            exchange=instance.exchange,
            order_manager=SimpleNamespace(),
            position_sizer=PositionSizer(risk_per_trade=100),
        )
        await instance._recover_durable_lifecycle_projections()
        return instance

    after_entry = await recover([entry])
    entry_trade = after_entry.trade_store.get_trades(symbol="AAPL")[0]
    assert entry_trade["quantity"] == 10
    assert after_entry.strategy.get_position("AAPL")["quantity"] == 10
    assert after_entry.trade_executor.get_open_trades()["AAPL"].position_size == 10
    after_entry.trade_store.close()
    after_entry.dashboard_store.close()
    after_entry.dashboard_outbox_store.close()
    after_entry.dashboard_price_store.close()

    after_partial = await recover([entry, partial])
    partial_trade = after_partial.trade_store.get_trades(symbol="AAPL")[0]
    assert partial_trade["status"] == "open"
    assert partial_trade["quantity"] == 6
    assert partial_trade["closed_quantity"] == 4
    assert after_partial.strategy.get_position("AAPL")["quantity"] == 6
    after_partial.trade_store.close()
    after_partial.dashboard_store.close()
    after_partial.dashboard_outbox_store.close()
    after_partial.dashboard_price_store.close()

    after_full = await recover([entry, partial, final])
    closed_trade = after_full.trade_store.get_trades(symbol="AAPL")[0]
    assert closed_trade["status"] == "closed"
    assert closed_trade["quantity"] == 10
    assert closed_trade["closed_quantity"] == 10
    assert closed_trade["exit_reason"] == "take_profit"
    assert after_full.strategy.get_position("AAPL") is None
    assert after_full.trade_executor.get_open_trades() == {}

    await after_full._recover_durable_lifecycle_projections()
    rerun_trade = after_full.trade_store.get_trades(symbol="AAPL")[0]
    assert rerun_trade["closed_quantity"] == 10


@pytest.mark.asyncio
async def test_restart_orders_mixed_offset_execution_timestamps_by_instant(
    tmp_path,
):
    executions = [
        {
            "execution_id": "entry-offset",
            "broker_order_id": "entry-offset-order",
            "account_id": "DU123",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "price": 100,
            # 2026-01-01 22:30 UTC
            "filled_at": "2026-01-02T00:30:00+02:00",
            "trade_currency": "USD",
            "order_metadata": {"strategy_stop_price": 95},
        },
        {
            "execution_id": "close-offset",
            "broker_order_id": "close-offset-order",
            "account_id": "DU123",
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 1,
            "price": 101,
            # Lexically earlier date, but later instant.
            "filled_at": "2026-01-01T23:00:00+00:00",
            "trade_currency": "USD",
            "order_metadata": {"exit_reason": "eod"},
        },
    ]
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.exchange = SimpleNamespace(
        list_durable_executions=lambda: executions
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator._recover_durable_lifecycle_projections()

    trade = orchestrator.trade_store.get_trades(symbol="AAPL")[0]
    assert trade["status"] == "closed"
    assert trade["exit_reason"] == "eod"


@pytest.mark.asyncio
async def test_restart_ignores_durable_executions_from_other_accounts(tmp_path):
    execution = {
        "execution_id": "foreign-entry",
        "broker_order_id": "foreign-order",
        "account_id": "OTHER",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 100,
        "filled_at": datetime.now(timezone.utc).isoformat(),
        "trade_currency": "USD",
        "order_metadata": {"strategy_stop_price": 95},
    }
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    orchestrator.exchange = SimpleNamespace(
        list_durable_executions=lambda: [execution]
    )
    orchestrator.strategy = ORBStrategy(ORBStrategyConfig(name="test"))
    orchestrator.trade_executor = TradeExecutor(
        exchange=orchestrator.exchange,
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )

    await orchestrator._recover_durable_lifecycle_projections()

    assert orchestrator.trade_store.get_trades() == []
    assert orchestrator.strategy.get_position("AAPL") is None
    assert orchestrator.trade_executor.get_open_trades() == {}


@pytest.mark.asyncio
async def test_trade_executor_blocks_duplicate_pending_entry_and_close():
    executor = TradeExecutor(
        exchange=SimpleNamespace(),
        order_manager=SimpleNamespace(),
        position_sizer=PositionSizer(risk_per_trade=100),
    )
    executor._pending_entries["entry-1"] = {
        "symbol": "AAPL",
        "side": "buy",
        "stop_price": 95.0,
        "take_profit": 110.0,
        "strategy_name": "test",
    }

    duplicate_entry = await executor.execute_signal(
        "AAPL",
        1,
        entry_price=100.0,
        stop_price=95.0,
    )
    executor._pending_closes["AAPL"] = "close-1"
    duplicate_close = await executor.execute_signal(
        "AAPL",
        0,
        entry_price=100.0,
        stop_price=95.0,
    )

    assert not duplicate_entry.success
    assert duplicate_entry.order_id == "entry-1"
    assert "already pending" in duplicate_entry.reason
    assert not duplicate_close.success
    assert duplicate_close.order_id == "close-1"


@pytest.mark.asyncio
async def test_position_open_to_flat_retries_after_enqueue_failure(tmp_path, monkeypatch):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)
    await orchestrator.exchange.submit_order("AAPL", "buy", 1, "market", price=100.0)
    await orchestrator._persist_dashboard_account_and_positions(reason="poll")

    await orchestrator.exchange.submit_order("AAPL", "sell", 1, "market", price=100.0)
    original_enqueue = orchestrator.dashboard_outbox_store.enqueue_event
    failed = False

    def fail_flat_once(event):
        nonlocal failed
        if event.aggregate_type == "position" and event.payload["quantity"] == 0 and not failed:
            failed = True
            raise sqlite3.OperationalError("simulated enqueue failure")
        return original_enqueue(event)

    import sqlite3
    monkeypatch.setattr(orchestrator.dashboard_outbox_store, "enqueue_event", fail_flat_once)
    await orchestrator._persist_dashboard_account_and_positions(reason="poll")
    assert orchestrator.dashboard_store.position_needs_publication("DU123:AAPL")
    assert orchestrator.dashboard_outbox_store.get_event("position:DU123:AAPL")["payload"]["quantity"] == 1

    await orchestrator._persist_dashboard_account_and_positions(reason="poll")
    position_event = orchestrator.dashboard_outbox_store.get_event("position:DU123:AAPL")
    assert position_event["payload"]["quantity"] == 0
    assert not orchestrator.dashboard_store.position_needs_publication("DU123:AAPL")


def test_execution_metric_recovery_repairs_crash_boundary(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    from vibe.trading_bot.storage.dashboard_store import OrderEvent

    now = datetime.now(timezone.utc)
    orchestrator.dashboard_store.upsert_order_event(OrderEvent(
        event_id="EXECUTION:crash",
        execution_id="crash",
        account_id="DU123",
        broker="interactive_brokers",
        broker_order_id="1001",
        event_type="ORDER_FILLED",
        symbol="AAPL",
        side="buy",
        quantity=2,
        price=100.1,
        benchmark_type="executable_quote",
        benchmark_price=100.0,
        submitted_at=now - timedelta(milliseconds=50),
        filled_at=now,
        slippage_amount=0.1,
        slippage_bps=10.0,
        slippage_version=2,
        slippage_valid=True,
        occurred_at=now,
    ))

    orchestrator._recover_durable_execution_projections()
    orchestrator._recover_durable_execution_projections()

    metrics = orchestrator.operational_metrics_store.get_metrics(metric_type="trade")
    assert {item["metric_name"] for item in metrics} == {
        "actual_fill_price",
        "fill_quantity",
        "expected_fill_price",
        "slippage",
        "slippage_bps",
        "latency_ms",
    }
    assert len(metrics) == 6


def test_durable_ib_metrics_record_when_dashboard_is_disabled(tmp_path):
    config = _dashboard_config(tmp_path)
    config.dashboard.enabled = False
    orchestrator = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    filled_at = datetime.now(timezone.utc)
    orchestrator._ingest_durable_ib_execution({
        "execution_id": "metrics-only",
        "broker_order_id": "1001",
        "account_id": "DU123",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "price": 100.1,
        "filled_at": filled_at.isoformat(),
        "trade_currency": "USD",
        "commission": None,
        "commission_currency": None,
        "order_metadata": {
            "submitted_at": (filled_at - timedelta(milliseconds=10)).isoformat(),
            "benchmark_version": 2,
            "benchmark_price": 100.0,
            "benchmark_type": "executable_quote",
        },
    })

    metrics = orchestrator.operational_metrics_store.get_metrics(metric_type="trade")
    assert {item["metric_name"] for item in metrics} >= {
        "actual_fill_price",
        "fill_quantity",
        "slippage_bps",
    }


def test_restart_execution_recovery_preserves_existing_trade_link(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    now = datetime.now(timezone.utc)
    from vibe.trading_bot.storage.dashboard_store import OrderEvent
    orchestrator.dashboard_store.upsert_order_event(OrderEvent(
        event_id="EXECUTION:linked",
        execution_id="linked",
        account_id="DU123",
        broker="interactive_brokers",
        broker_order_id="1001",
        trade_id="trade-existing",
        event_type="ORDER_FILLED",
        symbol="AAPL",
        side="buy",
        quantity=1,
        price=100.0,
        occurred_at=now,
    ))

    orchestrator._ingest_durable_ib_execution({
        "execution_id": "linked",
        "broker_order_id": "1001",
        "account_id": "DU123",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "price": 100.0,
        "filled_at": now.isoformat(),
        "trade_currency": "USD",
        "commission": 1.0,
        "commission_currency": "USD",
        "order_metadata": {"benchmark_version": 1},
    })

    recovered = orchestrator.dashboard_store.get_row(
        "order_events", "event_id", "EXECUTION:linked"
    )
    assert recovered["trade_id"] == "trade-existing"


@pytest.mark.asyncio
async def test_equity_retention_waits_for_remote_upsert_and_delete(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw_id = f"DU123:{observed.isoformat()}"
    orchestrator.dashboard_store.upsert_equity_snapshot(EquitySnapshot(
        snapshot_id=raw_id,
        account_id="DU123",
        timestamp=observed,
        net_liquidation=100.0,
        granularity="raw",
        event_type="poll",
        source="ib",
    ))
    orchestrator.dashboard_store.downsample_equity_snapshots(
        now=observed + timedelta(days=20)
    )

    class RecordingDestination:
        def __init__(self):
            self.events = []

        async def publish(self, event):
            self.events.append(event)

    destination = RecordingDestination()
    publisher = RemoteDataPublisher(orchestrator.dashboard_outbox_store, destination)
    orchestrator.remote_data_publisher = publisher

    assert orchestrator._advance_equity_retention_jobs() == 0
    assert orchestrator.dashboard_store.get_row("equity_snapshots", "snapshot_id", raw_id)
    await publisher.flush_pending(timeout_seconds=5, max_batches=5)

    assert orchestrator._advance_equity_retention_jobs() == 0
    await publisher.flush_pending(timeout_seconds=5, max_batches=5)
    assert destination.events[-1]["aggregate_type"] == "equity_snapshot_delete"
    assert orchestrator.dashboard_store.get_row("equity_snapshots", "snapshot_id", raw_id)

    assert orchestrator._advance_equity_retention_jobs() == 1
    assert orchestrator.dashboard_store.get_row("equity_snapshots", "snapshot_id", raw_id) is None
    jobs = orchestrator.dashboard_store.pending_equity_retention_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_account_position_poll_publishes_equity_unrealized_pnl(tmp_path):
    orchestrator = TradingOrchestrator(
        config=_dashboard_config(tmp_path),
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )
    await orchestrator.exchange.initialize()
    orchestrator.exchange.partial_fill_probability = 0.0
    await orchestrator.exchange.set_price("AAPL", 100.0)
    await orchestrator.exchange.submit_order(
        symbol="AAPL",
        side="buy",
        quantity=10,
        order_type="market",
        price=100.0,
    )
    await orchestrator.exchange.set_price("AAPL", 102.0)

    await orchestrator._persist_dashboard_account_and_positions(reason="poll")

    equity_row = orchestrator.dashboard_store._get_connection().execute(
        """
        SELECT unrealized_pnl, unrealized_pnl_provenance, pnl_version
        FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1
        """
    ).fetchone()
    outbox_events = orchestrator.dashboard_outbox_store.claim_pending(limit=10, claimed_by="test")
    equity_event = next(event for event in outbox_events if event["aggregate_type"] == "equity_snapshot")

    assert equity_row["unrealized_pnl"] > 0
    assert equity_row["unrealized_pnl_provenance"] == "broker"
    assert equity_row["pnl_version"] == 2
    assert equity_event["payload"]["unrealized_pnl"] == pytest.approx(equity_row["unrealized_pnl"])
    assert equity_event["payload"]["unrealized_pnl_provenance"] == "broker"
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()


@pytest.mark.asyncio
async def test_position_poll_does_not_infer_missing_instrument_currency(
    tmp_path,
):
    config = _dashboard_config(tmp_path)
    config.broker.ib_currency = "USD"
    orchestrator = TradingOrchestrator(
        config=config,
        ruleset=_ruleset(),
        market_scheduler=_scheduler(),
        testing_mode=True,
    )

    class MissingCurrencyExchange:
        async def get_account(self):
            return AccountState(
                account_id="DU123",
                cash=1000,
                equity=1000,
                buying_power=1000,
                portfolio_value=1000,
                base_currency="CAD",
            )

        async def get_position_snapshot(self, symbol):
            return BrokerPosition(
                symbol=symbol,
                quantity=1,
                avg_cost=100,
                market_price=101,
                unrealized_pnl=1,
                instrument_currency=None,
                unrealized_pnl_currency=None,
            )

    orchestrator.exchange = MissingCurrencyExchange()
    await orchestrator._persist_dashboard_account_and_positions()

    position = orchestrator.dashboard_store.get_row(
        "positions", "position_id", "DU123:AAPL"
    )
    assert position["instrument_currency"] is None
    assert position["unrealized_pnl_currency"] is None