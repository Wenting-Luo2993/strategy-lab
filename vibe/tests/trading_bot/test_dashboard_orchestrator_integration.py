"""Tests for live dashboard persistence integration in the orchestrator."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator


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
        f"ORDER_FILLED:{response.order_id}",
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
    assert filled_event["latency_ms"] == 2000.0
    assert filled_event["trade_id"] == trade_id
    assert trade["trade_id"] == trade_id
    assert trade["broker_order_id"] == response.order_id
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
    assert orchestrator.dashboard_outbox_store.count_by_status("pending") == 12
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
    assert orchestrator.operational_metrics_store.get_metrics(metric_name="slippage_bps")
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
        "SELECT unrealized_pnl FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    outbox_events = orchestrator.dashboard_outbox_store.claim_pending(limit=10, claimed_by="test")
    equity_event = next(event for event in outbox_events if event["aggregate_type"] == "equity_snapshot")

    assert equity_row["unrealized_pnl"] > 0
    assert equity_event["payload"]["unrealized_pnl"] == pytest.approx(equity_row["unrealized_pnl"])
    orchestrator.dashboard_price_store.close()
    orchestrator.dashboard_store.close()
    orchestrator.dashboard_outbox_store.close()
    orchestrator.trade_store.close()