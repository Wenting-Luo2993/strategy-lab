"""Focused tests for the approved local operational-data requirements."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest

from vibe.trading_bot.brokers.base import BrokerAccount, BrokerOrder, BrokerQuote, FillEvent
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine
from vibe.trading_bot.execution.order_manager import OrderManager, OrderRetryPolicy
from vibe.trading_bot.publishing.remote_data_publisher import RemoteDataPublisher
from vibe.trading_bot.storage.dashboard_store import (
    DashboardStore,
    EquitySnapshot,
    OrderEvent,
    PositionSnapshot,
    PublishOutboxEvent,
    PublishOutboxStore,
)
from vibe.trading_bot.storage.metrics_store import MetricType, MetricsStore
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore
from vibe.common.execution.base import OrderResponse
from vibe.common.models import Order, OrderStatus, Trade


class _ErrorEvent:
    def __iadd__(self, handler):
        self.handler = handler
        return self

    def emit(self, *args):
        if hasattr(self, "handler"):
            self.handler(*args)


class _AccountSummaryIB:
    def __init__(self, values, pnl=None):
        self.errorEvent = _ErrorEvent()
        self.values = values
        self.pnl = pnl or SimpleNamespace(
            account="DU123",
            modelCode="",
            realizedPnL=125.25,
            unrealizedPnL=-14.50,
        )
        self.pnl_requests = []
        self.pnl_cancellations = []

    async def accountSummaryAsync(self, account):
        return self.values

    def reqPnL(self, account, model_code):
        self.pnl_requests.append((account, model_code))
        return self.pnl

    def cancelPnL(self, account, model_code):
        self.pnl_cancellations.append((account, model_code))


class _UpdatingPnl:
    account = "DU123"
    modelCode = ""

    def __init__(self):
        self.reads = 0

    @property
    def realizedPnL(self):
        return 0.0

    @property
    def unrealizedPnL(self):
        self.reads += 1
        return 0.0 if self.reads == 1 else 42.5


def _summary(tag: str, value: str, currency: str, account: str = "DU123"):
    return SimpleNamespace(tag=tag, value=value, currency=currency, account=account)


class _MarketDataIB:
    def __init__(self, quote_time):
        self.errorEvent = _ErrorEvent()
        self.quote_time = quote_time
        self.cancelled = []
        self.tick_cancellations = []

    async def qualifyContractsAsync(self, contract):
        return [contract]

    def reqMarketDataType(self, market_data_type):
        self.market_data_type = market_data_type

    def reqMktData(self, contract, *_args):
        return SimpleNamespace(
            bid=99.9,
            ask=100.1,
            last=100.0,
            time=self.quote_time,
            marketPrice=lambda: 100.0,
        )

    def reqTickByTickData(self, contract, tick_type, *_args):
        assert tick_type == "BidAsk"
        ticks = (
            [
                SimpleNamespace(
                    time=self.quote_time,
                    bidPrice=99.9,
                    askPrice=100.1,
                )
            ]
            if self.quote_time is not None
            else []
        )
        return SimpleNamespace(tickByTicks=ticks)

    def cancelMktData(self, contract):
        self.cancelled.append(contract)

    def cancelTickByTickData(self, contract, tick_type):
        self.tick_cancellations.append((contract, tick_type))


def _ib_fill(execution_id: str, shares: float, price: float, commission=None):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol="QQQ", currency="USD"),
        execution=SimpleNamespace(
            execId=execution_id,
            orderId=1001,
            permId=9001,
            acctNumber="DU123",
            side="BOT",
            shares=shares,
            price=price,
            time=datetime.now(timezone.utc),
        ),
        commissionReport=(
            SimpleNamespace(
                execId=execution_id,
                commission=commission,
                currency="USD",
            )
            if commission is not None
            else None
        ),
    )


class _ExecutionIB:
    def __init__(self, reconciled_fills=None):
        self.errorEvent = _ErrorEvent()
        self.execDetailsEvent = _ErrorEvent()
        self.commissionReportEvent = _ErrorEvent()
        self.reconciled_fills = list(reconciled_fills or [])
        self.place_calls = 0

    def isConnected(self):
        return True

    async def reqExecutionsAsync(self):
        return self.reconciled_fills

    async def qualifyContractsAsync(self, contract):
        return [contract]

    def placeOrder(self, contract, order):
        self.place_calls += 1
        return SimpleNamespace(
            contract=contract,
            order=SimpleNamespace(orderId=1001, permId=9001),
            orderStatus=SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0),
            fills=[],
        )


@pytest.mark.asyncio
async def test_restored_open_order_emits_lifecycle_updates_for_later_fills():
    order = Order(
        order_id="restored-1",
        symbol="QQQ",
        side="buy",
        quantity=3,
        price=100.0,
        order_type="market",
        status=OrderStatus.PARTIAL,
        filled_qty=1,
        avg_price=100.0,
    )

    class RestoredExchange:
        cancel_calls = 0

        async def get_order(self, order_id):
            assert order_id == order.order_id
            return order

        async def cancel_order(self, order_id):
            self.cancel_calls += 1
            return OrderResponse(
                order_id=order_id,
                status=order.status,
                filled_qty=order.filled_qty,
                avg_price=order.avg_price,
                remaining_qty=max(order.quantity - order.filled_qty, 0),
            )

    lifecycle_updates = []
    first_update = asyncio.Event()

    async def project_fill(order_id):
        lifecycle_updates.append((order_id, order.filled_qty, order.status))
        first_update.set()

    manager = OrderManager(
        RestoredExchange(),
        retry_policy=OrderRetryPolicy(
            max_retries=3,
            base_delay_seconds=0.1,
            max_delay_seconds=0.1,
            cancel_after_seconds=5,
        ),
        on_order_filled=project_fill,
    )
    assert manager.restore_open_orders([order]) == 1
    assert manager.restore_open_orders([order]) == 0

    await asyncio.sleep(0.12)
    assert lifecycle_updates == []
    assert manager.exchange.cancel_calls == 0

    order.filled_qty = 2
    order.avg_price = 100.1
    order.status = OrderStatus.PARTIAL
    await asyncio.wait_for(first_update.wait(), timeout=1)

    order.filled_qty = 3
    order.avg_price = 100.2
    order.status = OrderStatus.FILLED
    task = manager._monitoring_tasks[order.order_id]
    await asyncio.wait_for(task, timeout=1)

    assert [(quantity, status) for _, quantity, status in lifecycle_updates] == [
        (2, OrderStatus.PARTIAL),
        (3, OrderStatus.FILLED),
    ]


@pytest.mark.asyncio
async def test_ib_account_summary_uses_req_pnl_and_preserves_balance_currencies(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    values = [
        _summary("NetLiquidation", "100000", "BASE"),
        _summary("TotalCashValue", "50000", "CAD"),
        _summary("BuyingPower", "200000", ""),
    ]
    fake_ib = _AccountSummaryIB(values)
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        currency="USD",
        account_base_currency="CAD",
    )

    account = await api.get_account_info()

    assert account.currency == "CAD"
    assert account.net_liquidation_currency == "CAD"
    assert account.cash_currency == "CAD"
    assert account.buying_power_currency is None
    assert account.realized_pnl == 125.25
    assert account.realized_pnl_currency == "CAD"
    assert account.unrealized_pnl == -14.5
    assert account.unrealized_pnl_currency == "CAD"
    assert fake_ib.pnl_requests == [("DU123", "")]
    assert fake_ib.pnl_cancellations == [("DU123", "")]


@pytest.mark.asyncio
async def test_ib_req_pnl_ignores_transient_initial_zero(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _AccountSummaryIB(
        [_summary("NetLiquidation", "100000", "BASE")],
        pnl=_UpdatingPnl(),
    )
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        account_base_currency="CAD",
    )

    account = await api.get_account_info()

    assert account.realized_pnl == 0.0
    assert account.unrealized_pnl == 42.5
    assert fake_ib.pnl_cancellations == [("DU123", "")]


@pytest.mark.asyncio
async def test_ib_req_pnl_returns_legitimate_persistent_zero(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _AccountSummaryIB(
        [_summary("NetLiquidation", "100000", "BASE")],
        pnl=SimpleNamespace(
            account="DU123",
            modelCode="",
            realizedPnL=0.0,
            unrealizedPnL=0.0,
        ),
    )
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        account_base_currency="CAD",
    )

    account = await api.get_account_info()

    assert account.realized_pnl == 0.0
    assert account.unrealized_pnl == 0.0
    assert fake_ib.pnl_cancellations == [("DU123", "")]


@pytest.mark.asyncio
async def test_ib_cad_balance_does_not_imply_unknown_account_base_currency(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    values = [
        _summary("NetLiquidation", "100000", "CAD"),
        _summary("TotalCashValue", "50000", "USD"),
    ]
    fake_ib = _AccountSummaryIB(values)
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        currency="USD",
        account_base_currency=None,
    )

    account = await api.get_account_info()

    assert account.net_liquidation_currency == "CAD"
    assert account.cash_currency == "USD"
    assert account.currency is None
    assert account.realized_pnl == 125.25
    assert account.realized_pnl_currency is None
    assert account.unrealized_pnl_currency is None


@pytest.mark.asyncio
async def test_ib_req_pnl_filters_account_and_model_and_cancels_on_timeout(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    values = [_summary("NetLiquidation", "100000", "BASE")]
    fake_ib = _AccountSummaryIB(
        values,
        pnl=SimpleNamespace(
            account="OTHER",
            modelCode="wrong-model",
            realizedPnL=99.0,
            unrealizedPnL=88.0,
        ),
    )
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        account_base_currency="CAD",
        model_code="growth",
        account_data_timeout_seconds=0.1,
    )

    account = await api.get_account_info()

    assert account.realized_pnl is None
    assert account.unrealized_pnl is None
    assert account.realized_pnl_currency is None
    assert fake_ib.pnl_requests == [("DU123", "growth")]
    assert fake_ib.pnl_cancellations == [("DU123", "growth")]


@pytest.mark.asyncio
async def test_ib_portfolio_pnl_uses_instrument_currency(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    class PortfolioIB(_AccountSummaryIB):
        def portfolio(self):
            return [
                SimpleNamespace(
                    account="DU123",
                    contract=SimpleNamespace(
                        symbol="QQQ",
                        secType="STK",
                        currency="USD",
                    ),
                    position=-6,
                    averageCost=744.45,
                    marketPrice=739.62,
                    unrealizedPNL=28.98,
                )
            ]

    fake_ib = PortfolioIB([])
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        account_base_currency="CAD",
    )

    positions = await api.get_positions()

    assert positions[0].instrument_currency == "USD"
    assert positions[0].unrealized_pnl == 28.98
    assert positions[0].unrealized_pnl_currency == "USD"


@pytest.mark.asyncio
async def test_ib_account_summary_timeout_cancels_owned_request(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    class Client:
        def __init__(self):
            self.requested = []
            self.cancelled = []

        def getReqId(self):
            return 42

        def reqAccountSummary(self, req_id, group, tags):
            self.requested.append((req_id, group, tags))

        def cancelAccountSummary(self, req_id):
            self.cancelled.append(req_id)

    class Wrapper:
        acctSummary = {}

        def startReq(self, req_id):
            self.req_id = req_id
            return asyncio.get_running_loop().create_future()

    fake_ib = SimpleNamespace(
        errorEvent=_ErrorEvent(),
        client=Client(),
        wrapper=Wrapper(),
    )
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        account_id="DU123",
        account_data_timeout_seconds=0.1,
    )

    with pytest.raises(asyncio.TimeoutError):
        await api.get_account_info()

    assert fake_ib.client.requested[0][0:2] == (42, "All")
    assert "NetLiquidation" in fake_ib.client.requested[0][2]
    assert fake_ib.client.cancelled == [42]


@pytest.mark.asyncio
async def test_ib_market_data_preserves_exchange_timestamp_and_cancels(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    exchange_time = datetime(2026, 9, 9, 20, 59, 58, tzinfo=timezone.utc)
    fake_ib = _MarketDataIB(exchange_time)
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    monkeypatch.setattr(
        ib_module,
        "Stock",
        lambda symbol, exchange, currency: SimpleNamespace(
            symbol=symbol,
            exchange=exchange,
            currency=currency,
        ),
    )
    api = InteractiveBrokersAPI()

    quote = await api.get_market_data("QQQ", timeout_seconds=1)

    assert quote.timestamp is exchange_time
    assert len(fake_ib.cancelled) == 1
    assert len(fake_ib.tick_cancellations) == 1


@pytest.mark.asyncio
async def test_ib_market_data_rejects_delayed_feed_without_requests(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _MarketDataIB(datetime.now(timezone.utc))
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(market_data_type=3)

    with pytest.raises(RuntimeError, match="Delayed/frozen"):
        await api.get_market_data("QQQ", timeout_seconds=0.1)

    assert fake_ib.cancelled == []
    assert fake_ib.tick_cancellations == []


def test_ib_unset_pnl_sentinel_is_not_treated_as_money():
    assert InteractiveBrokersAPI._clean_pnl_number(1.7976931348623157e308) is None
    assert InteractiveBrokersAPI._clean_pnl_number(-1.7976931348623157e308) is None
    assert InteractiveBrokersAPI._clean_pnl_number(0.0) == 0.0


def test_ib_account_summary_does_not_guess_ambiguous_or_base_currency(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(ib_module, "IB", lambda: _AccountSummaryIB([]))
    api = InteractiveBrokersAPI(currency="USD")

    assert api._find_account_summary_value([_summary("NetLiquidation", "1", "BASE")], "NetLiquidation", "DU123") == (
        "1",
        None,
    )
    assert api._find_account_summary_value(
        [
            _summary("NetLiquidation", "999", "CAD", account="All"),
            _summary("NetLiquidation", "123", "CAD", account="DU123"),
        ],
        "NetLiquidation",
        "DU123",
    ) == ("123", "CAD")
    assert api._find_account_summary_value(
        [
            _summary("NetLiquidation", "123", "BASE"),
            _summary("NetLiquidation", "123", "CAD"),
        ],
        "NetLiquidation",
        "DU123",
    ) == ("123", None)


@pytest.mark.asyncio
async def test_ib_account_state_propagates_authoritative_pnl_and_currencies():
    class AccountBroker:
        async def get_account_info(self):
            return BrokerAccount(
                account_id="DU123",
                net_liquidation=100000.0,
                cash=50000.0,
                buying_power=200000.0,
                currency="CAD",
                net_liquidation_currency="CAD",
                cash_currency="CAD",
                buying_power_currency="CAD",
                realized_pnl=125.0,
                realized_pnl_currency="CAD",
                unrealized_pnl=-10.0,
                unrealized_pnl_currency="CAD",
            )

    account = await InteractiveBrokersExecutionEngine(AccountBroker()).get_account()

    assert account.account_id == "DU123"
    assert account.base_currency == "CAD"
    assert account.realized_pnl == 125.0
    assert account.realized_pnl_currency == "CAD"
    assert account.unrealized_pnl == -10.0
    assert account.total_pnl == 125.0


def test_ib_commission_report_currency_is_preserved_per_execution(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(ib_module, "IB", lambda: _AccountSummaryIB([]))
    api = InteractiveBrokersAPI(account_id="DU123", currency="USD")
    trade = SimpleNamespace(
        contract=SimpleNamespace(currency="USD"),
        order=SimpleNamespace(permId=9001),
        fills=[
            SimpleNamespace(
                execution=SimpleNamespace(
                    execId="e1",
                    orderId=1001,
                    permId=9001,
                    acctNumber="DU123",
                    shares=1,
                    price=100.0,
                    time=datetime.now(timezone.utc),
                ),
                commissionReport=SimpleNamespace(execId="e1", commission=1.25, currency="USD"),
            ),
            SimpleNamespace(
                execution=SimpleNamespace(
                    execId="e2",
                    orderId=1001,
                    permId=9001,
                    acctNumber="DU123",
                    shares=2,
                    price=100.1,
                    time=datetime.now(timezone.utc),
                ),
                commissionReport=SimpleNamespace(execId="e2", commission=2.50, currency="USD"),
            ),
        ],
    )
    submitted = BrokerOrder("QQQ", "buy", 3, broker_order_id="1001")

    executions = api._resolve_executions(trade, submitted, datetime.now(timezone.utc))

    assert [execution["execution_id"] for execution in executions] == ["e1", "e2"]
    assert [execution["commission"] for execution in executions] == [1.25, 2.5]
    assert all(execution["commission_currency"] == "USD" for execution in executions)


def test_missing_commission_report_remains_unknown(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(ib_module, "IB", lambda: _AccountSummaryIB([]))
    api = InteractiveBrokersAPI()
    trade = SimpleNamespace(
        contract=SimpleNamespace(currency="USD"),
        order=SimpleNamespace(permId=1),
        fills=[
            SimpleNamespace(
                execution=SimpleNamespace(
                    execId="e1", orderId=1, permId=1, acctNumber="DU123",
                    shares=1, price=100.0, time=datetime.now(timezone.utc),
                ),
                commissionReport=None,
            )
        ],
    )
    execution = api._resolve_executions(
        trade,
        BrokerOrder("QQQ", "buy", 1, broker_order_id="1"),
        datetime.now(timezone.utc),
    )[0]
    assert execution["commission"] is None
    assert execution["commission_currency"] is None
    assert api._find_account_summary_value(
        [_summary("Cash", "1", "CAD"), _summary("Cash", "2", "USD")],
        "Cash",
        "DU123",
    ) == (None, None)
    assert api._find_account_summary_value([_summary("Cash", "1", "EUR")], "Cash", "DU123") == (
        "1",
        "EUR",
    )


def test_default_ib_insync_commission_placeholder_is_not_ready(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(ib_module, "IB", lambda: _AccountSummaryIB([]))
    api = InteractiveBrokersAPI()
    fill = _ib_fill("placeholder", 1, 100.0)
    fill.commissionReport = SimpleNamespace(
        execId="",
        commission=0.0,
        currency="",
    )
    trade = SimpleNamespace(fills=[fill])

    assert api._all_commission_reports_received(trade) is False
    payload = api._execution_payload(
        trade,
        fill,
        submitted_order=BrokerOrder("QQQ", "buy", 1, broker_order_id="1001"),
        stored_order=None,
        fallback_filled_at=datetime.now(timezone.utc),
    )
    assert payload["commission"] is None
    assert payload["commission_currency"] is None


@pytest.mark.asyncio
async def test_wait_for_fill_collects_staggered_partial_executions(monkeypatch, tmp_path):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _ExecutionIB()
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(
        execution_db_path=str(tmp_path / "executions.db"),
        commission_report_timeout_seconds=0,
    )
    submitted_at = datetime.now(timezone.utc)
    api._submitted_orders["1001"] = BrokerOrder(
        "QQQ",
        "buy",
        3,
        broker_order_id="1001",
        submitted_at=submitted_at,
        benchmark_type="executable_quote",
        benchmark_price=100.0,
    )
    trade = SimpleNamespace(
        contract=SimpleNamespace(symbol="QQQ", currency="USD"),
        order=SimpleNamespace(orderId=1001, permId=9001),
        orderStatus=SimpleNamespace(status="Submitted", filled=0, avgFillPrice=0),
        fills=[],
    )
    api._trades["1001"] = trade

    async def stagger_fills():
        await asyncio.sleep(0.1)
        trade.fills.append(_ib_fill("part-1", 1, 100.0, 0.5))
        trade.orderStatus.filled = 1
        await asyncio.sleep(0.3)
        trade.fills.append(_ib_fill("part-2", 2, 100.3, 0.75))
        trade.orderStatus.filled = 3
        trade.orderStatus.status = "Filled"

    task = asyncio.create_task(stagger_fills())
    event = await api.wait_for_fill("1001", timeout_seconds=2)
    await task

    assert event.quantity == 3
    assert [item["execution_id"] for item in event.executions] == ["part-1", "part-2"]
    assert event.avg_fill_price == pytest.approx((100.0 + 200.6) / 3)
    assert event.commission == 1.25


def test_delayed_commission_updates_durable_execution_and_listener(monkeypatch, tmp_path):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _ExecutionIB()
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    api = InteractiveBrokersAPI(execution_db_path=str(tmp_path / "executions.db"))
    api.execution_store.upsert_submitted_order({
        "broker_order_id": "1001",
        "symbol": "QQQ",
        "side": "buy",
        "quantity": 1,
        "order_type": "market",
        "benchmark_version": 2,
        "benchmark_price": 100.0,
    })
    updates = []
    api.add_execution_listener(updates.append)
    fill = _ib_fill("late-commission", 1, 100.1)

    api._handle_execution_details(None, fill)
    assert updates[-1]["commission"] is None

    api._handle_commission_report(
        None,
        fill,
        SimpleNamespace(execId="different-execution", commission=9.99, currency="USD"),
    )
    assert api.execution_store.get_execution("late-commission")["commission"] is None
    assert len(updates) == 1

    report = SimpleNamespace(
        execId="late-commission",
        commission=1.25,
        currency="USD",
    )
    api._handle_commission_report(None, fill, report)

    durable = api.execution_store.get_execution("late-commission")
    assert durable["commission"] == 1.25
    assert durable["commission_currency"] == "USD"
    assert len(updates) == 2


@pytest.mark.asyncio
async def test_restart_ingests_commission_from_ib_historical_fill_cache(
    monkeypatch,
    tmp_path,
):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    db_path = str(tmp_path / "executions.db")
    original_fill = _ib_fill("historical-commission", 1, 100.1)
    first_ib = _ExecutionIB([original_fill])
    monkeypatch.setattr(ib_module, "IB", lambda: first_ib)
    first = InteractiveBrokersAPI(execution_db_path=db_path)
    await first.reconcile_executions()
    assert first.execution_store.get_execution("historical-commission")["commission"] is None
    first.execution_store.close()

    corrected_fill = _ib_fill("historical-commission", 1, 100.1, 1.25)

    class HistoricalCommissionIB(_ExecutionIB):
        def fills(self):
            return [corrected_fill]

    restarted_ib = HistoricalCommissionIB()
    monkeypatch.setattr(ib_module, "IB", lambda: restarted_ib)
    restarted = InteractiveBrokersAPI(execution_db_path=db_path)
    updates = []
    restarted.add_execution_listener(updates.append)

    assert await restarted.reconcile_executions() == 1
    durable = restarted.execution_store.get_execution("historical-commission")
    assert durable["commission"] == 1.25
    assert durable["commission_currency"] == "USD"
    assert updates[-1]["execution_id"] == "historical-commission"


@pytest.mark.asyncio
async def test_restart_reconciles_final_fill_without_placing_order(monkeypatch, tmp_path):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    db_path = str(tmp_path / "executions.db")
    first_ib = _ExecutionIB()
    monkeypatch.setattr(ib_module, "IB", lambda: first_ib)
    monkeypatch.setattr(ib_module, "Stock", lambda symbol, exchange, currency: SimpleNamespace(
        symbol=symbol, exchange=exchange, currency=currency
    ))
    monkeypatch.setattr(ib_module, "MarketOrder", lambda *args, **kwargs: SimpleNamespace())
    first = InteractiveBrokersAPI(execution_db_path=db_path)
    order_id = await first.submit_order(BrokerOrder(
        "QQQ",
        "buy",
        3,
        expected_price=100.0,
        decision_at=datetime.now(timezone.utc),
        benchmark_type="executable_quote",
        benchmark_price=100.0,
    ))
    first._handle_execution_details(first._trades[order_id], _ib_fill("restart-1", 1, 100.0))
    first.execution_store.close()

    second_ib = _ExecutionIB([
        _ib_fill("restart-1", 1, 100.0, 0.5),
        _ib_fill("restart-2", 2, 100.2, 0.75),
    ])
    monkeypatch.setattr(ib_module, "IB", lambda: second_ib)
    second = InteractiveBrokersAPI(execution_db_path=db_path)
    engine = InteractiveBrokersExecutionEngine(second)

    await engine.initialize()
    recovered = await engine.get_order("1001")

    assert second_ib.place_calls == 0
    assert recovered.status.name == "FILLED"
    assert recovered.filled_qty == 3
    assert recovered.execution_ids == ["restart-1", "restart-2"]
    assert recovered.commission == 1.25


@pytest.mark.asyncio
async def test_engine_rebuilds_snapshot_from_unchanged_durable_executions():
    now = datetime.now(timezone.utc)
    executions = [
        {
            "execution_id": "durable-1",
            "broker_order_id": "1001",
            "permanent_order_id": "9001",
            "account_id": "DU123",
            "symbol": "QQQ",
            "side": "buy",
            "quantity": 1,
            "price": 100.0,
            "filled_at": now,
            "trade_currency": "USD",
            "commission": 0.5,
            "commission_currency": "USD",
            "order_metadata": {"quantity": 3, "order_type": "market"},
        },
        {
            "execution_id": "durable-2",
            "broker_order_id": "1001",
            "permanent_order_id": "9001",
            "account_id": "DU123",
            "symbol": "QQQ",
            "side": "buy",
            "quantity": 2,
            "price": 100.2,
            "filled_at": now + timedelta(milliseconds=10),
            "trade_currency": "USD",
            "commission": 0.75,
            "commission_currency": "USD",
            "order_metadata": {"quantity": 3, "order_type": "market"},
        },
    ]

    class UnchangedDurableBroker:
        async def connect(self):
            return True

        def list_durable_executions(self):
            return [dict(item) for item in executions]

        def list_open_orders(self):
            return []

    engine = InteractiveBrokersExecutionEngine(UnchangedDurableBroker())
    projected = []
    engine.add_execution_listener(projected.append)

    await engine.initialize()
    order = await engine.get_order("1001")

    assert order.filled_qty == 3
    assert order.status.name == "FILLED"
    assert order.execution_ids == ["durable-1", "durable-2"]
    assert [item["execution_id"] for item in projected] == [
        "durable-1",
        "durable-2",
    ]


@pytest.mark.asyncio
async def test_contract_is_qualified_before_fresh_quote_and_submission():
    events = []
    now = datetime.now(timezone.utc)

    class QualificationBroker(_BenchmarkBroker):
        async def qualify_contract(self, symbol):
            events.append("qualify-start")
            await asyncio.sleep(0.01)
            events.append("qualify-complete")
            return object()

        async def get_market_data(self, symbol, qualified_contract=None):
            assert qualified_contract is not None
            events.append("quote")
            return BrokerQuote(symbol, 99.9, 100.1, 100.0, 100.0, now)

        async def submit_order(self, order, qualified_contract=None):
            assert qualified_contract is not None
            events.append("place")
            return await super().submit_order(order)

    broker = QualificationBroker(
        BrokerQuote("QQQ", 99.9, 100.1, 100.0, 100.0, now)
    )
    engine = InteractiveBrokersExecutionEngine(broker)

    response = await engine.submit_order("QQQ", "buy", 1, "market")
    order = await engine.get_order(response.order_id)

    assert events == ["qualify-start", "qualify-complete", "quote", "place"]
    assert (order.submitted_at - order.decision_at).total_seconds() >= 0.005
    assert order.filled_at >= order.submitted_at


@pytest.mark.asyncio
async def test_quote_that_is_stale_after_delayed_qualification_is_not_submitted():
    class StaleAfterQualificationBroker:
        def __init__(self):
            self.placed = False

        async def qualify_contract(self, symbol):
            await asyncio.sleep(0.01)
            return object()

        async def get_market_data(self, symbol, qualified_contract=None):
            return BrokerQuote(
                symbol,
                99.9,
                100.1,
                100.0,
                100.0,
                datetime.now(timezone.utc) - timedelta(seconds=10),
            )

        async def submit_order(self, order, qualified_contract=None):
            self.placed = True
            return "never"

    broker = StaleAfterQualificationBroker()
    engine = InteractiveBrokersExecutionEngine(broker, quote_max_age_seconds=1)

    with pytest.raises(RuntimeError, match="stale"):
        await engine.submit_order("QQQ", "buy", 1, "market")
    assert broker.placed is False


@pytest.mark.asyncio
async def test_ib_submission_timestamp_is_captured_at_place_order(monkeypatch, tmp_path):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    fake_ib = _ExecutionIB()
    monkeypatch.setattr(ib_module, "IB", lambda: fake_ib)
    monkeypatch.setattr(
        ib_module,
        "Stock",
        lambda symbol, exchange, currency: SimpleNamespace(
            symbol=symbol, exchange=exchange, currency=currency
        ),
    )
    monkeypatch.setattr(
        ib_module,
        "MarketOrder",
        lambda *args, **kwargs: SimpleNamespace(),
    )
    api = InteractiveBrokersAPI(execution_db_path=str(tmp_path / "executions.db"))
    deliberately_wrong = datetime(2000, 1, 1, tzinfo=timezone.utc)
    before = datetime.now(timezone.utc)
    order_id = await api.submit_order(
        BrokerOrder(
            "QQQ",
            "buy",
            1,
            submitted_at=deliberately_wrong,
        )
    )
    after = datetime.now(timezone.utc)

    stored = api.get_submitted_order(order_id)
    assert before <= stored.submitted_at <= after
    assert stored.submitted_at != deliberately_wrong


class _BenchmarkBroker:
    def __init__(self, quote: BrokerQuote):
        self.quote = quote
        self.submitted = None

    async def get_market_data(self, symbol):
        return self.quote

    async def submit_order(self, order):
        self.submitted = order
        return "1001"

    async def wait_for_fill(self, broker_order_id, timeout_seconds=60):
        order = self.submitted
        fill_price = order.benchmark_price + (0.05 if order.side == "buy" else -0.05)
        submitted_at = datetime.now(timezone.utc)
        filled_at = submitted_at + timedelta(milliseconds=100)
        execution = {
            "execution_id": "exec-1",
            "broker_order_id": broker_order_id,
            "permanent_order_id": "9001",
            "account_id": "DU123",
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": fill_price,
            "filled_at": filled_at,
            "trade_currency": "USD",
            "commission": 1.25,
            "commission_currency": "USD",
        }
        return FillEvent(
            broker_order_id=broker_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            avg_fill_price=fill_price,
            expected_price=order.expected_price,
            submitted_at=submitted_at,
            filled_at=filled_at,
            commission=1.25,
            execution_id="exec-1",
            permanent_order_id="9001",
            account_id="DU123",
            instrument_currency="USD",
            commission_currency="USD",
            decision_at=order.decision_at,
            benchmark_type=order.benchmark_type,
            benchmark_price=order.benchmark_price,
            quote_bid=order.quote_bid,
            quote_ask=order.quote_ask,
            quote_midpoint=order.quote_midpoint,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
            executions=(execution,),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side", "order_type", "price", "expected_benchmark", "benchmark_type"),
    [
        ("sell", "market", 99.50, 99.90, "executable_quote"),
        ("buy", "market", 100.50, 100.10, "executable_quote"),
        ("sell", "stop", 99.00, 99.90, "stop_quote"),
        ("sell", "limit", 101.00, 101.00, "limit_price"),
    ],
)
async def test_execution_benchmarks_are_side_and_order_type_aware(
    side, order_type, price, expected_benchmark, benchmark_type
):
    now = datetime.now(timezone.utc)
    broker = _BenchmarkBroker(BrokerQuote("QQQ", 99.90, 100.10, 100.0, 100.0, now))
    engine = InteractiveBrokersExecutionEngine(broker)

    response = await engine.submit_order(
        "QQQ",
        side,
        1,
        order_type,
        price=price,
        limit_price=price if order_type == "limit" else None,
        stop_price=price if order_type == "stop" else None,
    )
    order = await engine.get_order(response.order_id)

    assert order.benchmark_price == expected_benchmark
    assert order.benchmark_type == benchmark_type
    assert order.stop_price == (price if order_type == "stop" else None)
    assert order.limit_price == (price if order_type == "limit" else None)
    assert order.execution_id == "exec-1"
    assert order.permanent_order_id == "9001"
    assert order.commission_currency == "USD"
    assert order.trade_currency == "USD"


@pytest.mark.asyncio
async def test_market_benchmark_rejects_missing_or_stale_executable_quote():
    fresh = datetime.now(timezone.utc)
    fresh_engine = InteractiveBrokersExecutionEngine(
        _BenchmarkBroker(BrokerQuote("QQQ", 99.9, 100.1, 100.0, 100.0, fresh)),
        quote_max_age_seconds=5,
    )
    assert (await fresh_engine.submit_order("QQQ", "sell", 1, "market")).filled_qty == 1

    stale = datetime.now(timezone.utc) - timedelta(seconds=10)
    stale_engine = InteractiveBrokersExecutionEngine(
        _BenchmarkBroker(BrokerQuote("QQQ", 99.9, 100.1, 100.0, 100.0, stale)),
        quote_max_age_seconds=5,
    )
    with pytest.raises(RuntimeError, match="stale"):
        await stale_engine.submit_order("QQQ", "sell", 1, "market")

    undated_engine = InteractiveBrokersExecutionEngine(
        _BenchmarkBroker(BrokerQuote("QQQ", 99.9, 100.1, 100.0, 100.0, None))
    )
    with pytest.raises(RuntimeError, match="missing exchange timestamp"):
        await undated_engine.submit_order("QQQ", "sell", 1, "market")

    missing_engine = InteractiveBrokersExecutionEngine(
        _BenchmarkBroker(BrokerQuote("QQQ", None, 100.1, 100.0, 100.0, fresh))
    )
    with pytest.raises(RuntimeError, match="missing executable"):
        await missing_engine.submit_order("QQQ", "sell", 1, "market")


@pytest.mark.asyncio
async def test_order_manager_emits_distinct_partial_execution_callback():
    class PartialExchange:
        def __init__(self):
            self.order = SimpleNamespace(
                order_id="1",
                symbol="QQQ",
                side="buy",
                quantity=2,
                filled_qty=1,
                order_type="market",
                price=100.0,
                status=SimpleNamespace(name="PARTIAL"),
            )

        async def submit_order(self, **kwargs):
            from vibe.common.execution.base import OrderResponse
            from vibe.common.models import OrderStatus

            return OrderResponse("1", OrderStatus.PARTIAL, 1, 100.0, 1)

        async def get_order(self, order_id):
            return self.order

        async def cancel_order(self, order_id):
            return None

    callbacks = []

    async def on_fill(order_id):
        callbacks.append(order_id)

    manager = OrderManager(PartialExchange(), on_order_filled=on_fill)
    await manager.submit_order("QQQ", "buy", 2, price=100.0)
    await asyncio.sleep(0)
    for task in manager._monitoring_tasks.values():
        task.cancel()
    await asyncio.gather(*manager._monitoring_tasks.values(), return_exceptions=True)
    assert callbacks == ["1"]


@pytest.mark.asyncio
async def test_order_manager_emits_fill_that_arrives_after_zero_fill_response():
    class DelayedPartialExchange:
        def __init__(self):
            self.reads = 0
            self.order = SimpleNamespace(
                order_id="late",
                symbol="QQQ",
                side="buy",
                quantity=2,
                filled_qty=0,
                order_type="market",
                price=100.0,
                status=SimpleNamespace(name="SUBMITTED"),
            )

        async def submit_order(self, **kwargs):
            from vibe.common.execution.base import OrderResponse
            from vibe.common.models import OrderStatus

            return OrderResponse("late", OrderStatus.SUBMITTED, 0, 0.0, 2)

        async def get_order(self, order_id):
            self.reads += 1
            if self.reads >= 2:
                self.order.filled_qty = 1
            return self.order

        async def cancel_order(self, order_id):
            return None

    callbacks = []

    async def on_fill(order_id):
        callbacks.append(order_id)

    manager = OrderManager(
        DelayedPartialExchange(),
        retry_policy=OrderRetryPolicy(max_retries=0, base_delay_seconds=0),
        on_order_filled=on_fill,
    )
    await manager.submit_order("QQQ", "buy", 2, price=100.0)
    await asyncio.gather(*manager._monitoring_tasks.values())

    assert callbacks == ["late"]


@pytest.mark.asyncio
async def test_order_manager_projects_fill_that_wins_timeout_cancellation():
    from vibe.common.execution.base import OrderResponse
    from vibe.common.models import OrderStatus

    class FilledDuringCancelExchange:
        def __init__(self):
            self.order = SimpleNamespace(
                order_id="race",
                symbol="QQQ",
                side="buy",
                quantity=2.0,
                filled_qty=0.0,
                order_type="market",
                price=100.0,
                status=OrderStatus.SUBMITTED,
            )

        async def submit_order(self, **kwargs):
            return OrderResponse(
                "race", OrderStatus.SUBMITTED, 0.0, 0.0, 2.0
            )

        async def get_order(self, order_id):
            return self.order

        async def cancel_order(self, order_id):
            self.order.filled_qty = 2.0
            self.order.status = OrderStatus.FILLED
            return OrderResponse(
                order_id, OrderStatus.CANCELLED, 0.0, 0.0, 2.0
            )

    lifecycle = []

    async def on_fill(order_id):
        lifecycle.append(("fill", order_id))

    async def on_cancel(order_id):
        lifecycle.append(("cancel", order_id))

    manager = OrderManager(
        FilledDuringCancelExchange(),
        retry_policy=OrderRetryPolicy(
            max_retries=0,
            base_delay_seconds=0,
            cancel_after_seconds=0,
        ),
        on_order_filled=on_fill,
        on_order_cancelled=on_cancel,
    )

    await manager.submit_order("QQQ", "buy", 2, price=100.0)
    await asyncio.gather(*manager._monitoring_tasks.values())

    managed = manager.get_order("race")
    assert lifecycle == [("fill", "race")]
    assert managed.terminal_status == OrderStatus.FILLED
    assert managed.filled_qty == 2


@pytest.mark.asyncio
async def test_partial_retry_uses_fill_that_arrives_during_cancellation():
    from vibe.common.execution.base import OrderResponse
    from vibe.common.models import OrderStatus

    class CancellationRaceExchange:
        def __init__(self):
            self.submissions = []
            self.orders = {}

        async def submit_order(self, **kwargs):
            order_id = str(len(self.submissions) + 1)
            quantity = float(kwargs["quantity"])
            self.submissions.append(quantity)
            filled = 4.0 if order_id == "1" else quantity
            status = (
                OrderStatus.PARTIAL
                if order_id == "1"
                else OrderStatus.FILLED
            )
            self.orders[order_id] = SimpleNamespace(
                order_id=order_id,
                symbol=kwargs["symbol"],
                side=kwargs["side"],
                quantity=quantity,
                filled_qty=filled,
                order_type=kwargs["order_type"],
                price=kwargs["price"],
                status=status,
            )
            return OrderResponse(
                order_id,
                status,
                filled,
                100.0,
                quantity - filled,
            )

        async def get_order(self, order_id):
            return self.orders[order_id]

        async def cancel_order(self, order_id):
            # Three more shares execute before the terminal cancel callback.
            self.orders[order_id].filled_qty = 7.0
            self.orders[order_id].status = OrderStatus.CANCELLED
            return OrderResponse(
                order_id,
                OrderStatus.CANCELLED,
                7.0,
                100.0,
                3.0,
            )

    exchange = CancellationRaceExchange()
    callbacks = []

    async def on_fill(order_id):
        callbacks.append(order_id)

    manager = OrderManager(
        exchange,
        retry_policy=OrderRetryPolicy(
            max_retries=1,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
        on_order_filled=on_fill,
    )

    await manager.submit_order("QQQ", "buy", 10, price=100.0)
    await asyncio.gather(*list(manager._monitoring_tasks.values()))

    assert exchange.submissions == [10.0, 3.0]
    assert sum(exchange.submissions[1:]) + 7.0 == 10.0
    assert callbacks.count("1") == 2


@pytest.mark.asyncio
async def test_partial_retry_fails_safe_when_cancel_is_not_confirmed():
    from vibe.common.execution.base import OrderResponse
    from vibe.common.models import OrderStatus

    class UnconfirmedCancellationExchange:
        def __init__(self):
            self.submissions = []
            self.order = None

        async def submit_order(self, **kwargs):
            self.submissions.append(float(kwargs["quantity"]))
            self.order = SimpleNamespace(
                order_id="1",
                symbol=kwargs["symbol"],
                side=kwargs["side"],
                quantity=float(kwargs["quantity"]),
                filled_qty=1.0,
                order_type=kwargs["order_type"],
                price=kwargs["price"],
                status=OrderStatus.PARTIAL,
            )
            return OrderResponse(
                "1", OrderStatus.PARTIAL, 1.0, 100.0, 1.0
            )

        async def get_order(self, order_id):
            return self.order

        async def cancel_order(self, order_id):
            raise TimeoutError("terminal state unavailable")

    exchange = UnconfirmedCancellationExchange()
    manager = OrderManager(
        exchange,
        retry_policy=OrderRetryPolicy(
            max_retries=1,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
    )

    await manager.submit_order("QQQ", "buy", 2, price=100.0)
    await asyncio.gather(*list(manager._monitoring_tasks.values()))

    assert exchange.submissions == [2.0]


def _execution_event(event_id: str, execution_id: str) -> OrderEvent:
    now = datetime.now(timezone.utc)
    return OrderEvent(
        event_id=event_id,
        execution_id=execution_id,
        account_id="DU123",
        broker="interactive_brokers",
        broker_order_id="1001",
        permanent_order_id="9001",
        event_type="ORDER_FILLED",
        symbol="QQQ",
        side="sell",
        quantity=1,
        price=99.9,
        expected_price=100.0,
        benchmark_type="executable_quote",
        benchmark_price=100.0,
        trade_currency="USD",
        commission=1.0,
        commission_currency="USD",
        submitted_at=now,
        filled_at=now,
        slippage_amount=0.1,
        slippage_bps=10.0,
        slippage_version=2,
        slippage_valid=True,
        occurred_at=now,
    )


def test_execution_identity_is_unique_and_restart_idempotent(tmp_path):
    path = str(tmp_path / "dashboard.db")
    first = DashboardStore(path)
    first.upsert_order_event(_execution_event("EXECUTION:abc", "abc"))
    first.upsert_order_event(_execution_event("EXECUTION:abc", "abc"))
    assert first.count_rows("order_events") == 1
    first.close()

    restarted = DashboardStore(path)
    restarted.upsert_order_event(_execution_event("EXECUTION:abc", "abc"))
    assert restarted.count_rows("order_events") == 1
    with pytest.raises(sqlite3.IntegrityError):
        restarted.upsert_order_event(_execution_event("different-event", "abc"))
    restarted.upsert_order_event(_execution_event("EXECUTION:def", "def"))
    assert restarted.count_rows("order_events") == 2


def test_execution_source_reconciles_missing_outbox_after_restart(tmp_path):
    dashboard = DashboardStore(str(tmp_path / "dashboard.db"))
    dashboard.upsert_order_event(_execution_event("EXECUTION:abc", "abc"))
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))

    class NoopDestination:
        async def publish(self, event):
            return None

    publisher = RemoteDataPublisher(outbox, NoopDestination())
    assert publisher.reconcile_sources([dashboard], "2026-01-01") == 1
    assert outbox.get_event("order_event:EXECUTION:abc")["payload"]["execution_id"] == "abc"
    assert publisher.reconcile_sources([dashboard], "2026-01-01") == 0
    assert outbox.count_by_status("pending") == 1


def test_execution_metrics_are_idempotent_across_restarts(tmp_path):
    path = str(tmp_path / "metrics.db")
    first = MetricsStore(path)
    metric_id = first.record_metric(
        MetricType.TRADE.value,
        "slippage_bps",
        10.0,
        {"execution_id": "abc", "slippage_version": "2", "slippage_valid": "true"},
        "2026-01-01T12:00:00+00:00",
        idempotency_key="abc:slippage_bps",
    )
    first.close()
    restarted = MetricsStore(path)
    repeated_id = restarted.record_metric(
        MetricType.TRADE.value,
        "slippage_bps",
        9.5,
        {"execution_id": "abc", "slippage_version": "2", "slippage_valid": "true"},
        "2026-01-01T12:00:00+00:00",
        idempotency_key="abc:slippage_bps",
    )

    assert repeated_id == metric_id
    metrics = restarted.get_metrics(metric_name="slippage_bps")
    assert len(metrics) == 1
    assert metrics[0]["metric_value"] == 9.5


def test_historical_order_events_are_migrated_as_invalid_slippage(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE order_events (
            event_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, broker TEXT NOT NULL,
            broker_order_id TEXT NOT NULL, strategy_order_id TEXT, trade_id TEXT,
            event_type TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
            quantity REAL NOT NULL, price REAL, expected_price REAL, slippage_bps REAL,
            latency_ms REAL, occurred_at TEXT NOT NULL, raw_status TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO order_events VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "TRADE_CLOSED:1",
            "DU123",
            "interactive_brokers",
            "1",
            "TRADE_CLOSED",
            "QQQ",
            "sell",
            1,
            110.0,
            100.0,
            -1000.0,
            5.0,
            "2026-01-01T12:00:00+00:00",
            "Filled",
            "2026-01-01T12:00:00+00:00",
            "2026-01-01T12:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    store = DashboardStore(str(path))
    row = store.get_row("order_events", "event_id", "TRADE_CLOSED:1")
    assert row["slippage_version"] == 1
    assert row["slippage_valid"] == 0


def test_position_change_detection_handles_flat_state_noise_and_thresholds(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    now = datetime.now(timezone.utc)
    flat = PositionSnapshot("DU123:QQQ", "DU123", "QQQ", 0.0, "flat", None, None, None, now, "USD", "USD")
    assert store.upsert_position_if_changed(flat) is True
    assert store.upsert_position_if_changed(flat) is False
    opened = PositionSnapshot("DU123:QQQ", "DU123", "QQQ", 1.0, "long", 100.0, 100.0, 0.0, now, "USD", "USD")
    assert store.upsert_position_if_changed(opened) is True
    noise = PositionSnapshot("DU123:QQQ", "DU123", "QQQ", 1.0, "long", 100.0, 100.004, 0.004, now, "USD", "USD")
    assert store.upsert_position_if_changed(noise, market_price_threshold=0.01, unrealized_pnl_threshold=0.01) is False
    moved = PositionSnapshot("DU123:QQQ", "DU123", "QQQ", 1.0, "long", 100.0, 100.02, 0.02, now, "USD", "USD")
    assert store.upsert_position_if_changed(moved, market_price_threshold=0.01, unrealized_pnl_threshold=0.01) is True
    assert store.upsert_position_if_changed(flat) is True
    assert store.upsert_position_if_changed(flat) is False


def test_local_trade_diagnostics_filter_by_account(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    now = datetime.now(timezone.utc).isoformat()
    for account_id, pnl in (("CAD-ACCOUNT", 10.0), ("USD-ACCOUNT", 20.0)):
        store._get_connection().execute(
            """
            INSERT INTO trades (
                trade_id, account_id, symbol, side, quantity, entry_price,
                entry_time, status, pnl, created_at, updated_at
            ) VALUES (?, ?, 'QQQ', 'buy', 1, 100, ?, 'closed', ?, ?, ?)
            """,
            (f"trade-{account_id}", account_id, now, pnl, now, now),
        )
    store._get_connection().commit()

    cad = store.get_trades(status="closed", account_id="CAD-ACCOUNT")
    usd = store.get_trades(status="closed", account_id="USD-ACCOUNT")

    assert [row["pnl"] for row in cad] == [10.0]
    assert [row["pnl"] for row in usd] == [20.0]

def _outbox_event(event_id: str, when: datetime) -> PublishOutboxEvent:
    return PublishOutboxEvent(
        event_id=event_id,
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id=event_id,
        destination="supabase",
        payload={"trade_id": event_id},
        original_event_timestamp=when,
        next_retry_at=when,
    )


def test_outbox_pruning_is_bounded_repeatable_and_published_only(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "outbox.db"))
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cutoff = old + timedelta(days=8)
    for event_id in ("published-1", "published-2", "boundary", "pending", "claimed", "failed", "dead"):
        store.enqueue_event(_outbox_event(event_id, old))
    store.mark_published("published-1", old)
    store.mark_published("published-2", old)
    store.mark_published("boundary", cutoff)
    store.claim_pending(1, "worker", now=old + timedelta(seconds=1))
    store.mark_failed("failed", "retry", old + timedelta(days=30))
    store.mark_dead_letter("dead", "permanent")

    assert store.prune_published_before(cutoff, batch_size=1) == 1
    assert store.prune_published_before(cutoff, batch_size=1) == 1
    assert store.prune_published_before(cutoff, batch_size=1) == 0
    assert store.get_event("boundary") is not None
    assert store.get_event("pending") is not None
    assert store.get_event("claimed") is not None
    assert store.get_event("failed") is not None
    assert store.get_event("dead") is not None


def test_pruned_execution_is_not_republished_after_restart(tmp_path):
    dashboard = DashboardStore(str(tmp_path / "dashboard.db"))
    dashboard.upsert_order_event(_execution_event("EXECUTION:abc", "abc"))
    outbox_path = str(tmp_path / "outbox.db")
    outbox = PublishOutboxStore(outbox_path)

    class NoopDestination:
        async def publish(self, event):
            return None

    publisher = RemoteDataPublisher(outbox, NoopDestination())
    assert publisher.reconcile_sources([dashboard], "2026-01-01") == 1
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    outbox.mark_published("order_event:EXECUTION:abc", old)
    assert outbox.prune_published_before(old + timedelta(days=1)) == 1
    outbox.close()

    restarted = PublishOutboxStore(outbox_path)
    restarted_publisher = RemoteDataPublisher(restarted, NoopDestination())
    assert restarted_publisher.reconcile_sources([dashboard], "2026-01-01") == 0
    assert restarted.count_by_status("pending") == 0


def test_existing_published_outbox_rows_backfill_publication_ledger(tmp_path):
    path = str(tmp_path / "outbox.db")
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    initial = PublishOutboxStore(path)
    initial.enqueue_event(_outbox_event("historical", old))
    initial.mark_published("historical", old)
    initial._get_connection().execute("DROP TABLE publication_ledger")
    initial._get_connection().commit()
    initial.close()

    migrated = PublishOutboxStore(path)

    assert migrated.is_published("historical", {"trade_id": "historical"})
    assert migrated.prune_published_before(old + timedelta(days=1)) == 1
    assert migrated.is_published("historical", {"trade_id": "historical"})


def test_outbox_claims_are_exclusive_between_publishers(tmp_path):
    path = str(tmp_path / "outbox.db")
    first = PublishOutboxStore(path)
    second = PublishOutboxStore(path)
    now = datetime.now(timezone.utc)
    first.enqueue_event(_outbox_event("execution:1", now))

    assert len(first.claim_pending(1, "worker-1", now=now + timedelta(seconds=1))) == 1
    assert second.claim_pending(1, "worker-2", now=now + timedelta(seconds=1)) == []


def test_published_outbox_payload_correction_is_requeued(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "outbox.db"))
    now = datetime.now(timezone.utc)
    event = _outbox_event("execution:1", now)
    store.enqueue_event(event)
    store.mark_published(event.event_id, now)
    corrected = PublishOutboxEvent(
        **{
            **event.__dict__,
            "payload": {"trade_id": "execution:1", "commission": 1.25, "commission_currency": "USD"},
        }
    )
    store.enqueue_event(corrected)
    row = store.get_event(event.event_id)
    assert row["status"] == "pending"
    assert row["published_at"] is None
    assert row["payload"]["commission_currency"] == "USD"


def test_late_update_after_dead_letter_uses_idempotent_successor(tmp_path):
    store = PublishOutboxStore(str(tmp_path / "outbox.db"))
    now = datetime.now(timezone.utc)
    original = _outbox_event("execution:late-commission", now)
    store.enqueue_event(original)
    store.mark_dead_letter(original.event_id, "schema rejected")
    corrected = PublishOutboxEvent(
        **{
            **original.__dict__,
            "payload": {
                "trade_id": "execution:late-commission",
                "commission": 1.25,
                "commission_currency": "CAD",
            },
        }
    )

    assert store.enqueue_event(corrected) is True
    assert store.enqueue_event(corrected) is False
    chain = store.get_event_successors(original.event_id)

    assert len(chain) == 2
    assert chain[0]["event_id"] == original.event_id
    assert chain[0]["status"] == "dead_letter"
    assert chain[0]["payload"] == original.payload
    assert chain[1]["event_id"] == (
        "execution:late-commission::successor::1"
    )
    assert chain[1]["status"] == "pending"
    assert chain[1]["payload"]["commission_currency"] == "CAD"
    newer = PublishOutboxEvent(
        **{
            **original.__dict__,
            "payload": {
                **corrected.payload,
                "commission": 1.50,
            },
        }
    )
    assert store.enqueue_event(newer) is True
    assert store.enqueue_event(newer) is False
    chain = store.get_event_successors(original.event_id)
    assert len(chain) == 3
    assert chain[2]["event_id"] == (
        "execution:late-commission::successor::2"
    )
    assert chain[1]["payload"]["commission"] == 1.25
    assert chain[2]["payload"]["commission"] == 1.50


def test_partial_close_projection_is_idempotent_after_restart(tmp_path):
    path = str(tmp_path / "trades.db")
    now = datetime.now(timezone.utc)
    first = TradeStore(path)
    row_id = first.insert_trade(
        Trade(
            trade_id="trade-1",
            symbol="QQQ",
            side="buy",
            quantity=10,
            entry_price=100.0,
            entry_time=now,
        )
    )
    first.update_trade(row_id, account_id="DU123", status="open")
    applied = first.apply_exit_projection(
        trade_row_id=row_id,
        trade_id="trade-1",
        order_id="close-1",
        cumulative_quantity=4,
        cumulative_avg_price=105.0,
        remaining_quantity=6,
        exit_time=now,
        exit_reason="target",
    )
    assert applied["closed_quantity"] == 4
    first.close()

    restarted = TradeStore(path)
    # Identical fill rows and late commission callbacks project the same
    # cumulative order state and therefore make no second trade mutation.
    assert restarted.apply_exit_projection(
        trade_row_id=row_id,
        trade_id="trade-1",
        order_id="close-1",
        cumulative_quantity=4,
        cumulative_avg_price=105.0,
        remaining_quantity=6,
        exit_time=now + timedelta(seconds=1),
        exit_reason="target",
    ) is None
    row = restarted.get_trade_by_id(row_id)
    assert row["quantity"] == 6
    assert row["closed_quantity"] == 4
    assert row["pnl"] == 20.0
    assert restarted.get_exit_projection("close-1")["applied_quantity"] == 4


def test_restart_backfills_watermark_for_legacy_persisted_partial_close(tmp_path):
    path = str(tmp_path / "trades.db")
    now = datetime.now(timezone.utc)
    first = TradeStore(path)
    row_id = first.insert_trade(
        Trade(
            trade_id="trade-legacy",
            symbol="QQQ",
            side="buy",
            quantity=6,
            entry_price=100.0,
            entry_time=now,
        )
    )
    first.update_trade(
        row_id,
        account_id="DU123",
        status="open",
        closed_quantity=4,
        exit_price=105.0,
        pnl=20.0,
        broker_order_id="close-legacy",
    )
    first.close()

    restarted = TradeStore(path)
    assert restarted.apply_exit_projection(
        trade_row_id=row_id,
        trade_id="trade-legacy",
        order_id="close-legacy",
        cumulative_quantity=7,
        cumulative_avg_price=105.0,
        remaining_quantity=3,
        exit_time=now,
        exit_reason="target",
    ) is not None
    row = restarted.get_trade_by_id(row_id)
    assert row["quantity"] == 3
    assert row["closed_quantity"] == 7
    assert row["pnl"] == 35
    assert restarted.get_exit_projection("close-legacy")[
        "applied_quantity"
    ] == 7


def test_ib_execution_store_migrates_legacy_tables(tmp_path):
    path = tmp_path / "legacy-ib.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ib_submitted_orders (
            broker_order_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL
        );
        CREATE TABLE ib_executions (
            execution_id TEXT PRIMARY KEY,
            broker_order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            filled_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = IBExecutionStore(str(path))
    store.upsert_submitted_order(
        {
            "broker_order_id": "1001",
            "symbol": "QQQ",
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "submitted_at": datetime.now(timezone.utc),
            "benchmark_price": 100.0,
            "strategy_name": "ORB",
            "strategy_stop_price": 95.0,
            "take_profit": 110.0,
            "exit_reason": "take_profit",
        }
    )
    assert store.upsert_execution(
        {
            "execution_id": "exec-1",
            "broker_order_id": "1001",
            "symbol": "QQQ",
            "side": "buy",
            "quantity": 1,
            "price": 100.1,
            "filled_at": datetime.now(timezone.utc),
            "commission": 1.25,
            "commission_currency": "CAD",
            "order_metadata": {"benchmark_price": 100.0},
        }
    )

    assert store.get_submitted_order("1001")["benchmark_price"] == 100.0
    assert store.get_submitted_order("1001")["strategy_stop_price"] == 95.0
    assert store.get_submitted_order("1001")["exit_reason"] == "take_profit"
    execution = store.get_execution("exec-1")
    assert execution["commission_currency"] == "CAD"
    assert execution["order_metadata"]["benchmark_price"] == 100.0


def _snapshot(account: str, observed: datetime, value: float, event_type: str = "poll") -> EquitySnapshot:
    return EquitySnapshot(
        snapshot_id=f"{account}:{observed.isoformat()}",
        account_id=account,
        timestamp=observed,
        net_liquidation=value,
        cash=value,
        buying_power=value * 2,
        realized_pnl=1.0,
        unrealized_pnl=2.0,
        base_currency="CAD",
        net_liquidation_currency="CAD",
        cash_currency="CAD",
        buying_power_currency="CAD",
        realized_pnl_currency="CAD",
        unrealized_pnl_currency="CAD",
        event_type=event_type,
    )


def test_equity_downsampling_handles_accounts_events_buckets_dst_and_repeats(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    recent_old = now - timedelta(days=20)
    daily_old = now - timedelta(days=100)
    for account in ("A", "B"):
        store.upsert_equity_snapshot(_snapshot(account, recent_old.replace(minute=1), 1.0))
        store.upsert_equity_snapshot(_snapshot(account, recent_old.replace(minute=4), 2.0))
        store.upsert_equity_snapshot(_snapshot(account, daily_old.replace(hour=14), 3.0))
        store.upsert_equity_snapshot(_snapshot(account, daily_old.replace(hour=20), 4.0))
    event_time = now - timedelta(days=120)
    store.upsert_equity_snapshot(_snapshot("A", event_time, 5.0, "order_filled"))
    store.upsert_equity_snapshot(_snapshot("A", datetime(2026, 11, 1, 5, 31, tzinfo=timezone.utc), 6.0))
    store.upsert_equity_snapshot(_snapshot("A", datetime(2026, 11, 1, 6, 31, tzinfo=timezone.utc), 7.0))

    result = store.downsample_equity_snapshots(now=now, market_timezone="America/New_York")
    granularities = store._get_connection().execute(
        "SELECT account_id, granularity, period_start, net_liquidation, event_type FROM equity_snapshots"
    ).fetchall()

    assert result.aggregated == 6
    assert result.removed == 0
    assert result.retained == 1
    assert sum(row["granularity"] == "5m" for row in granularities) == 4
    assert sum(row["granularity"] == "1d" for row in granularities) == 2
    assert any(row["event_type"] == "order_filled" for row in granularities)
    assert len(store.pending_equity_retention_jobs()) == 6
    assert sum(row["granularity"] == "raw" for row in granularities) == 11
    dst_periods = [
        row["period_start"]
        for row in granularities
        if (
            row["account_id"] == "A"
            and row["granularity"] == "5m"
            and row["net_liquidation"] in {6.0, 7.0}
        )
    ]
    assert len(set(dst_periods)) == 2
    assert store.downsample_equity_snapshots(now=now, market_timezone="America/New_York").removed == 0


def test_equity_downsampling_rolls_back_when_delete_fails(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    observed = datetime.now(timezone.utc) - timedelta(days=20)
    store.upsert_equity_snapshot(_snapshot("A", observed, 1.0))
    store._get_connection().execute(
        """
        CREATE TRIGGER fail_equity_retention_job BEFORE INSERT ON equity_retention_jobs
        BEGIN SELECT RAISE(ABORT, 'job blocked'); END
        """
    )
    store._get_connection().commit()

    with pytest.raises(sqlite3.IntegrityError, match="job blocked"):
        store.downsample_equity_snapshots(now=datetime.now(timezone.utc))

    rows = store._get_connection().execute(
        "SELECT snapshot_id, granularity FROM equity_snapshots"
    ).fetchall()
    assert [(row["snapshot_id"], row["granularity"]) for row in rows] == [
        (f"A:{observed.isoformat()}", "raw")
    ]


def test_five_minute_equity_snapshots_promote_to_daily(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    observed = datetime(2026, 1, 1, 15, 4, tzinfo=timezone.utc)
    store.upsert_equity_snapshot(_snapshot("A", observed, 1.0))

    first = store.downsample_equity_snapshots(
        now=observed + timedelta(days=20),
        raw_retention_days=14,
        five_minute_retention_days=90,
    )
    assert first.aggregated == 1
    first_job = store.pending_equity_retention_jobs()[0]
    assert store.complete_equity_retention_job(first_job["job_id"]) == 1
    assert [row["granularity"] for row in store._get_connection().execute(
        "SELECT granularity FROM equity_snapshots"
    ).fetchall()] == ["5m"]

    second = store.downsample_equity_snapshots(
        now=observed + timedelta(days=100),
        raw_retention_days=14,
        five_minute_retention_days=90,
    )
    second_job = store.pending_equity_retention_jobs()[0]
    assert store.complete_equity_retention_job(second_job["job_id"]) == 1
    rows = store._get_connection().execute(
        "SELECT granularity FROM equity_snapshots"
    ).fetchall()
    assert second.removed == 0
    assert [row["granularity"] for row in rows] == ["1d"]


def test_equity_downsampling_only_advances_bucket_close(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    bucket_start = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
    first_observation = bucket_start + timedelta(minutes=4)
    store.upsert_equity_snapshot(_snapshot("A", first_observation, 104.0))
    store.downsample_equity_snapshots(now=first_observation + timedelta(days=20))
    store.complete_equity_retention_job(
        store.pending_equity_retention_jobs()[0]["job_id"]
    )

    late_older = bucket_start + timedelta(minutes=1)
    store.upsert_equity_snapshot(_snapshot("A", late_older, 101.0))
    store.downsample_equity_snapshots(now=first_observation + timedelta(days=20))
    aggregate_id = (
        f"A:5m:{bucket_start.isoformat()}"
    )
    aggregate = store.get_row("equity_snapshots", "snapshot_id", aggregate_id)
    assert aggregate["timestamp"] == first_observation.isoformat()
    assert aggregate["net_liquidation"] == 104.0
    store.complete_equity_retention_job(
        store.pending_equity_retention_jobs()[0]["job_id"]
    )

    late_newer = bucket_start + timedelta(minutes=4, seconds=30)
    store.upsert_equity_snapshot(_snapshot("A", late_newer, 105.0))
    store.downsample_equity_snapshots(now=late_newer + timedelta(days=20))
    aggregate = store.get_row("equity_snapshots", "snapshot_id", aggregate_id)
    assert aggregate["timestamp"] == late_newer.isoformat()
    assert aggregate["net_liquidation"] == 105.0

    store.downsample_equity_snapshots(now=late_newer + timedelta(days=20))
    rerun = store.get_row("equity_snapshots", "snapshot_id", aggregate_id)
    assert rerun["timestamp"] == late_newer.isoformat()
    assert rerun["net_liquidation"] == 105.0


def test_retention_promotes_old_intermediate_after_bucket_config_change(tmp_path):
    store = DashboardStore(str(tmp_path / "dashboard.db"))
    observed = datetime(2026, 1, 1, 15, 4, tzinfo=timezone.utc)
    store.upsert_equity_snapshot(_snapshot("A", observed, 1.0))
    store.downsample_equity_snapshots(
        now=observed + timedelta(days=20),
        five_minute_bucket_minutes=5,
    )
    store.complete_equity_retention_job(
        store.pending_equity_retention_jobs()[0]["job_id"]
    )

    store.downsample_equity_snapshots(
        now=observed + timedelta(days=100),
        five_minute_bucket_minutes=10,
    )
    store.complete_equity_retention_job(
        store.pending_equity_retention_jobs()[0]["job_id"]
    )
    rows = store._get_connection().execute(
        "SELECT granularity, net_liquidation FROM equity_snapshots"
    ).fetchall()
    assert [(row["granularity"], row["net_liquidation"]) for row in rows] == [
        ("1d", 1.0)
    ]


def test_supabase_schema_keeps_legacy_and_durable_metric_keys():
    schema = (
        Path(__file__).parents[3]
        / "docs/trading-bot-mvp/dashboards/supabase-read-model.sql"
    ).read_text(encoding="utf-8")

    assert "operational_metrics_metric_id_key" in schema
    assert "on public.operational_metrics(metric_id)" in schema
    assert "operational_metrics_legacy_name_timestamp_key" in schema
    assert "on public.operational_metrics(metric_name, timestamp)" in schema
    assert "later," in schema and "cleanup migration" in schema
