"""Main trading orchestrator coordinating all components."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from vibe.trading_bot.config.settings import AppSettings, get_settings
from vibe.trading_bot.core.market_schedulers import create_scheduler, BaseMarketScheduler
from vibe.trading_bot.core.health_monitor import HealthMonitor
from vibe.trading_bot.api.health import start_health_server_task, set_health_state
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider
from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
from vibe.trading_bot.data.providers.factory import DataProviderFactory
from vibe.trading_bot.data.providers.types import RealtimeDataProvider, WebSocketDataProvider, RESTDataProvider
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.trading_bot.storage.metrics_store import MetricType, MetricsStore
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
from vibe.trading_bot.publishing.remote_data_publisher import RemoteDataPublisher, SupabaseRestDestination
import pandas as pd
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.execution.order_manager import OrderManager, OrderRetryPolicy
from vibe.trading_bot.execution.trade_executor import ExecutionResult, TradeExecutor
from vibe.common.models import Trade
from vibe.common.risk import PositionSizer
from vibe.common.strategies import ORBStrategy
from vibe.common.strategies.orb import ORBStrategyConfig
from vibe.common.indicators.engine import IncrementalIndicatorEngine
from vibe.trading_bot.notifications.discord import DiscordNotifier
from vibe.trading_bot.notifications.payloads import (
    OrderNotificationPayload,
    TradeClosedPayload,
    SystemStatusPayload,
)


def _iso_datetime(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def _as_aware_datetime(value: datetime | str | None) -> Optional[datetime]:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _elapsed_ms(start: datetime | str | None, end: datetime | str | None) -> Optional[float]:
    start_dt = _as_aware_datetime(start)
    end_dt = _as_aware_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return max((end_dt - start_dt).total_seconds() * 1000.0, 0.0)
from vibe.trading_bot.notifications.helper import discord_notification_context
from vibe.trading_bot.version import BUILD_VERSION
from vibe.trading_bot.core.phases import WarmupPhaseManager, CooldownPhaseManager
from vibe.common.ruleset import StrategyRuleSet, RuleSetLoader, OrbLevelStopLoss, OrbRangeMultipleTakeProfit


logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main orchestrator coordinating all trading bot components.

    Manages initialization, main trading loop, graceful shutdown, and component
    integration for complete trading system lifecycle.
    """

    def __init__(
        self,
        config: Optional[AppSettings] = None,
        ruleset: Optional[StrategyRuleSet] = None,
        market_scheduler: Optional[BaseMarketScheduler] = None,
        testing_mode: bool = False,
        bar_interval: str = "5m",
    ):
        """Initialize trading orchestrator.

        Args:
            config: Application settings (uses get_settings() if None)
            ruleset: Strategy ruleset (loads from config.active_ruleset if None)
            market_scheduler: Market scheduler (creates default if None)
            testing_mode: If True, use shorter sleep intervals for faster testing
            bar_interval: Bar aggregation interval (e.g., "1m", "5m"). Default "5m" for production.
                         Use "1m" for integration testing to see bars complete faster.
        """
        self.config = config or get_settings()
        self.logger = logging.getLogger(__name__)
        self._testing_mode = testing_mode
        self._bar_interval = bar_interval

        # Load ruleset if not provided
        if ruleset is None:
            try:
                ruleset = RuleSetLoader.from_name(self.config.active_ruleset)
            except Exception as e:
                self.logger.error(f"Failed to load ruleset '{self.config.active_ruleset}': {e}")
                raise
        self.ruleset = ruleset

        # Component initialization order matters
        # Allow dependency injection for testing (defaults to real scheduler for production)
        if market_scheduler is None:
            self.market_scheduler: BaseMarketScheduler = create_scheduler(
                market_type=self.config.trading.market_type,
                exchange=self.config.trading.exchange,
            )
        else:
            self.market_scheduler: BaseMarketScheduler = market_scheduler
        self.health_monitor = HealthMonitor()
        self.trade_store = TradeStore(db_path=self.config.database_path)
        self.operational_metrics_store: Optional[MetricsStore] = None
        if self.config.operational_metrics.enabled:
            self.operational_metrics_store = MetricsStore(db_path=self.config.operational_metrics.local_database_path)
        self.dashboard_price_store: Optional[PriceBarStore] = None
        self.dashboard_store: Optional[DashboardStore] = None
        self.dashboard_outbox_store: Optional[PublishOutboxStore] = None
        self.remote_data_publisher: Optional[RemoteDataPublisher] = None
        self.dashboard_publish_wake_event = asyncio.Event()
        self._resolved_dashboard_account_id: Optional[str] = None
        self._dashboard_order_trade_ids: Dict[str, str] = {}
        self._dashboard_symbol_trade_ids: Dict[str, str] = {}
        self._dashboard_trade_row_ids: Dict[str, int] = {}
        self._dashboard_order_sent_at: Dict[str, datetime] = {}
        self._dashboard_exit_order_progress: Dict[str, tuple[float, float]] = {}
        self._pending_exit_reasons: Dict[str, str] = {}
        self._position_publication_process_id = uuid.uuid4().hex
        self._positions_published_this_process: set[str] = set()
        if self.config.dashboard.enabled:
            self.dashboard_price_store = PriceBarStore(db_path=self.config.dashboard.local_price_db_path)
            self.dashboard_store = DashboardStore(db_path=self.config.dashboard.local_dashboard_db_path)
            self.dashboard_outbox_store = PublishOutboxStore(db_path=self.config.dashboard.local_outbox_db_path)

        # Retry/backoff state
        self._consecutive_failures = 0
        self._max_consecutive_failures = 10

        # Sleep intervals: shorter in testing mode for faster test execution
        if testing_mode:
            self._base_cycle_interval = 1   # 1 second when monitoring positions (testing)
            self._idle_cycle_interval = 2   # 2 seconds when no positions (testing)
            self._max_backoff_seconds = 5   # 5 seconds max backoff (testing)
        else:
            self._base_cycle_interval = 60   # 60 seconds when monitoring positions (production)
            self._idle_cycle_interval = 300  # 5 minutes when no positions (production)
            self._max_backoff_seconds = 900  # 15 minutes max backoff (production)
        self.data_manager: Optional[DataManager] = None
        self.exchange = self._create_execution_engine()
        add_execution_listener = getattr(self.exchange, "add_execution_listener", None)
        if add_execution_listener is not None:
            add_execution_listener(self._ingest_durable_ib_execution)
        self.trade_executor: Optional[TradeExecutor] = None
        self.strategy: Optional[ORBStrategy] = None
        self.indicator_engine: Optional[IncrementalIndicatorEngine] = None

        # Trading loop control
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Main loop task
        self._main_task: Optional[asyncio.Task] = None

        # Health API server task
        self._health_server_task: Optional[asyncio.Task] = None

        # Strategy logging state tracking (to avoid duplicate logs)
        self._orb_logged_today: Dict[str, str] = {}  # symbol -> date_str
        self._last_approach_logged: Dict[str, float] = {}  # symbol -> timestamp

        # Daily statistics tracking for end-of-day summary
        self._daily_stats: Dict[str, Any] = self._initialize_daily_stats()
        self._last_summary_date: Optional[str] = None
        self._orb_notification_sent_date: Optional[str] = None  # Track ORB Discord notification

        # Market closed state tracking (to avoid log spam after cooldown completes)
        # Note: Cooldown manager has its own internal state
        self._market_closed_logged: bool = False

        # Real-time data providers (configurable)
        self.primary_provider: Optional[RealtimeDataProvider] = None
        self.secondary_provider: Optional[RealtimeDataProvider] = None
        self.active_provider: Optional[RealtimeDataProvider] = None

        # Finnhub websocket for real-time intraday data (backward compatibility)
        self.finnhub_ws: Optional[FinnhubWebSocketClient] = None
        self.bar_aggregators: Dict[str, BarAggregator] = {}  # One aggregator per symbol

        # Real-time bars storage (symbol -> DataFrame with today's bars)
        self._realtime_bars: Dict[str, pd.DataFrame] = {}

        # Latest close price per symbol (updated each trading cycle for position monitoring)
        self._latest_bar_prices: Dict[str, float] = {}

        # Polling task for REST providers
        self._polling_task: Optional[asyncio.Task] = None

        # Phase managers (warmup, cooldown)
        self.warmup_manager: Optional[WarmupPhaseManager] = None
        self.cooldown_manager: Optional[CooldownPhaseManager] = None

        # Log active ruleset at initialization
        self.logger.info(
            f"Active ruleset: {self.ruleset.name} (v{self.ruleset.version}) — "
            f"Symbols: {', '.join(self.ruleset.instruments.symbols)} | "
            f"Timeframe: {self.ruleset.instruments.timeframe}"
        )

    def _create_execution_engine(self):
        """Create the configured execution engine."""
        broker_config = getattr(self.config, "broker", None)
        broker_type = getattr(broker_config, "broker_type", "mock") if broker_config else "mock"
        if broker_type == "interactive_brokers":
            return InteractiveBrokersExecutionEngine(
                InteractiveBrokersAPI(
                    host=broker_config.ib_host,
                    port=broker_config.ib_port,
                    client_id=broker_config.ib_client_id,
                    account_id=broker_config.ib_account_id,
                    exchange=broker_config.ib_exchange,
                    currency=broker_config.ib_currency,
                    account_base_currency=broker_config.ib_account_base_currency,
                    model_code=broker_config.ib_model_code,
                    account_data_timeout_seconds=broker_config.ib_account_data_timeout_seconds,
                    market_data_type=broker_config.ib_market_data_type,
                    connect_timeout=broker_config.ib_connect_timeout,
                    connect_max_retries=broker_config.ib_connect_max_retries,
                    connect_retry_delay_seconds=broker_config.ib_connect_retry_delay_seconds,
                    execution_db_path=broker_config.ib_execution_db_path,
                )
            )
        return MockExchange()

    @property
    def active_symbols(self) -> List[str]:
        """Trading symbols driven by ruleset if available, else fall back to config."""
        if self.ruleset and self.ruleset.instruments.symbols:
            return list(self.ruleset.instruments.symbols)
        return list(self.config.trading.symbols)

    def _dashboard_enabled(self) -> bool:
        return bool(
            self.config.dashboard.enabled
            and self.dashboard_store is not None
            and self.dashboard_outbox_store is not None
        )

    def _dashboard_account_id(self) -> str:
        return (
            self.config.dashboard.account_id
            or self.config.broker.ib_account_id
            or self._resolved_dashboard_account_id
            or "default"
        )

    def _dashboard_broker_name(self) -> str:
        broker_type = getattr(self.config.broker, "broker_type", "mock")
        return "interactive_brokers" if broker_type == "interactive_brokers" else broker_type

    async def _start_dashboard_publisher(self) -> None:
        if not self.config.dashboard.enabled or self.dashboard_outbox_store is None:
            return
        if self.config.dashboard.remote_provider != "supabase":
            self.logger.info("Dashboard remote publisher disabled for provider=%s", self.config.dashboard.remote_provider)
            return
        if not self.config.dashboard.supabase_url or not self.config.dashboard.supabase_service_key:
            self.logger.info("Dashboard Supabase publisher not started; URL/service key not configured")
            return
        destination = SupabaseRestDestination(
            url=self.config.dashboard.supabase_url,
            service_key=self.config.dashboard.supabase_service_key,
            request_timeout_seconds=10.0,
        )
        self.remote_data_publisher = RemoteDataPublisher(
            outbox_store=self.dashboard_outbox_store,
            destination=destination,
            wake_event=self.dashboard_publish_wake_event,
            batch_size=25,
            poll_interval_seconds=self.config.dashboard.publish_interval_seconds,
            published_retention_days=self.config.dashboard.local_retention_days,
            prune_batch_size=self.config.dashboard.outbox_prune_batch_size,
        )
        source_stores = [
            store
            for store in (self.dashboard_store, self.dashboard_price_store, self.trade_store)
            if store is not None
        ]
        if source_stores:
            from vibe.trading_bot.utils.datetime_utils import get_market_date

            reconciled = self.remote_data_publisher.reconcile_sources(
                source_stores,
                get_market_date(self.market_scheduler),
            )
            if reconciled:
                self.logger.info("Reconciled %s durable execution publish events", reconciled)
        await self.remote_data_publisher.start()
        self.logger.info("Dashboard RemoteDataPublisher started")

    def _enqueue_dashboard_event(
        self,
        *,
        event_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        original_event_timestamp: datetime | str,
    ) -> bool:
        if self.dashboard_outbox_store is None:
            return False
        try:
            enqueued = self.dashboard_outbox_store.enqueue_event(PublishOutboxEvent(
                event_id=event_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                destination=self.config.dashboard.remote_provider,
                payload=payload,
                original_event_timestamp=original_event_timestamp,
            ))
            self.dashboard_publish_wake_event.set()
            return enqueued or self.dashboard_outbox_store.is_published(event_id)
        except Exception as exc:
            self.logger.warning("Dashboard outbox enqueue failed for %s: %s", event_id, exc)
            return False

    def _trade_payload_from_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "trade_id": row.get("trade_id") or str(row.get("id")),
            "account_id": row.get("account_id"),
            "symbol": row.get("symbol"),
            "side": "long" if row.get("side") == "buy" else "short",
            "quantity": row.get("quantity"),
            "entry_price": row.get("entry_price"),
            "entry_time": row.get("entry_time"),
            "exit_price": row.get("exit_price"),
            "exit_time": row.get("exit_time"),
            "status": row.get("status"),
            "pnl": row.get("pnl"),
            "pnl_pct": row.get("pnl_pct"),
            "pnl_currency": row.get("pnl_currency"),
            "strategy": row.get("strategy"),
            "exit_reason": row.get("exit_reason"),
            "broker_order_id": row.get("broker_order_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _enqueue_trade_row(self, trade_id: str, original_event_timestamp: datetime | str) -> None:
        row_id = self._dashboard_trade_row_ids.get(trade_id)
        if row_id is None:
            return
        row = self.trade_store.get_trade_by_id(row_id)
        if row is None:
            return
        self._enqueue_dashboard_event(
            event_id=f"trade:{trade_id}",
            event_type="upsert",
            aggregate_type="trade",
            aggregate_id=trade_id,
            payload=self._trade_payload_from_row(row),
            original_event_timestamp=original_event_timestamp,
        )

    async def _persist_dashboard_trade_entry(
        self,
        *,
        symbol: str,
        order_id: str,
        signal_value: int,
        quantity: float,
        entry_price: float,
        entry_time: datetime,
    ) -> Optional[str]:
        if not self.config.dashboard.enabled:
            return None
        try:
            account_id = self._dashboard_account_id()
            order = await self.exchange.get_order(order_id)
            executions = list(getattr(order, "executions", []) or [])
            if executions:
                projected_row = None
                for execution in sorted(
                    executions,
                    key=lambda item: (
                        _as_aware_datetime(item.get("filled_at"))
                        or datetime.min.replace(tzinfo=timezone.utc),
                        str(item.get("execution_id") or ""),
                    ),
                ):
                    projected_row, _ = (
                        self.trade_store.apply_entry_execution_projection(
                            execution_id=str(execution["execution_id"]),
                            broker_order_id=str(
                                execution.get("broker_order_id") or order_id
                            ),
                            # Dashboard account scoping is authoritative. Mock
                            # execution IDs use an internal "mock" account tag
                            # that must not create a second logical account.
                            account_id=account_id,
                            symbol=str(execution.get("symbol") or symbol),
                            side=str(execution.get("side") or order.side),
                            quantity=float(execution["quantity"]),
                            price=float(execution["price"]),
                            entry_time=(
                                _as_aware_datetime(execution.get("filled_at"))
                                or entry_time
                            ),
                            pnl_currency=execution.get("trade_currency"),
                            strategy=self.strategy.config.name
                            if self.strategy
                            else "orb",
                        )
                    )
                if projected_row is None:
                    return None
                trade_id = str(projected_row["trade_id"])
                row_id = int(projected_row["id"])
                self._dashboard_order_trade_ids[order_id] = trade_id
                self._dashboard_symbol_trade_ids[symbol] = trade_id
                self._dashboard_trade_row_ids[trade_id] = row_id
                self._enqueue_trade_row(trade_id, entry_time)
                await self._record_dashboard_order_event("ORDER_SENT", order_id)
                await self._record_dashboard_order_event("ORDER_FILLED", order_id)
                return trade_id

            existing = self.trade_store.get_trades(
                symbol=symbol,
                status="open",
                account_id=account_id,
                limit=1,
            )
            if existing:
                row = existing[0]
                row_id = int(row["id"])
                trade_id = row.get("trade_id")
                position = await self.exchange.get_position(symbol)
                cumulative_quantity = (
                    float(position.quantity) if position is not None else float(row["quantity"]) + quantity
                )
                weighted_entry = (
                    float(position.entry_price)
                    if position is not None
                    else (
                        float(row["entry_price"]) * float(row["quantity"])
                        + entry_price * quantity
                    ) / cumulative_quantity
                )
                self.trade_store.update_trade(
                    row_id,
                    quantity=cumulative_quantity,
                    entry_price=weighted_entry,
                    broker_order_id=order_id,
                )
                if trade_id is not None:
                    self._dashboard_order_trade_ids[order_id] = trade_id
                    self._dashboard_symbol_trade_ids[symbol] = trade_id
                    self._dashboard_trade_row_ids[trade_id] = row_id
                    self._enqueue_trade_row(trade_id, entry_time)
                return trade_id
            trade_id = f"{account_id}:{order_id}"
            trade_side = "buy" if signal_value == 1 else "sell"
            trade = Trade(
                trade_id=trade_id,
                symbol=symbol,
                side=trade_side,
                quantity=quantity,
                entry_price=entry_price,
                entry_time=entry_time,
                pnl_currency=getattr(order, "trade_currency", None),
                strategy=self.strategy.config.name if self.strategy else "orb",
            )
            row_id = self.trade_store.insert_trade(trade)
            self.trade_store.update_trade(
                row_id,
                account_id=account_id,
                broker_order_id=order_id,
                status="open",
            )
            self._dashboard_order_trade_ids[order_id] = trade_id
            self._dashboard_symbol_trade_ids[symbol] = trade_id
            self._dashboard_trade_row_ids[trade_id] = row_id
            self._enqueue_trade_row(trade_id, entry_time)
            await self._record_dashboard_order_event("ORDER_SENT", order_id)
            await self._record_dashboard_order_event("ORDER_FILLED", order_id)
            return trade_id
        except Exception as exc:
            self.logger.warning("Dashboard trade entry persistence failed for %s: %s", order_id, exc)
            return None

    async def _persist_dashboard_trade_exit(
        self,
        *,
        symbol: str,
        order_id: str,
        exit_price: float,
        exit_time: datetime,
        exit_reason: str,
        filled_quantity: Optional[float] = None,
        remaining_quantity: float = 0.0,
    ) -> None:
        if not self.config.dashboard.enabled:
            return
        try:
            trade_id = self._dashboard_symbol_trade_ids.get(symbol)
            row_id = self._dashboard_trade_row_ids.get(trade_id) if trade_id is not None else None
            row = self.trade_store.get_trade_by_id(row_id) if row_id is not None else None
            if row is None:
                open_trades = self.trade_store.get_trades(
                    symbol=symbol,
                    status="open",
                    account_id=self._dashboard_account_id(),
                    limit=1,
                )
                if not open_trades:
                    return
                row = open_trades[0]
                row_id = int(row["id"])
                trade_id = row.get("trade_id")
                if trade_id is None:
                    return
                self._dashboard_symbol_trade_ids[symbol] = trade_id
                self._dashboard_trade_row_ids[trade_id] = row_id
            if row is None:
                return
            cumulative_order_quantity = (
                float(filled_quantity)
                if filled_quantity is not None
                else float(row["quantity"])
            )
            updated_row = self.trade_store.apply_exit_projection(
                trade_row_id=row_id,
                trade_id=trade_id,
                order_id=order_id,
                cumulative_quantity=cumulative_order_quantity,
                cumulative_avg_price=exit_price,
                remaining_quantity=remaining_quantity,
                exit_time=exit_time,
                exit_reason=exit_reason,
            )
            if updated_row is None:
                return
            is_flat = remaining_quantity <= 0
            self._dashboard_order_trade_ids[order_id] = trade_id
            self._dashboard_exit_order_progress[order_id] = (
                cumulative_order_quantity,
                exit_price * cumulative_order_quantity,
            )
            self._enqueue_trade_row(trade_id, exit_time)
            await self._record_dashboard_order_event("ORDER_FILLED", order_id)
            if is_flat:
                await self._record_dashboard_order_event("TRADE_CLOSED", order_id)
                self._dashboard_symbol_trade_ids.pop(symbol, None)
        except Exception as exc:
            self.logger.warning("Dashboard trade exit persistence failed for %s: %s", order_id, exc)

    async def _sync_open_trade_after_entry_fill(self, order: Any) -> None:
        """Project later entry/retry fills onto the existing logical trade."""
        position = await self.exchange.get_position(order.symbol)
        if position is None:
            return
        tracked = self.strategy.get_position(order.symbol) if self.strategy is not None else None
        pending_entry = (
            self.trade_executor.get_pending_entry(order.order_id)
            if self.trade_executor is not None
            else None
        )
        open_trades = (
            self.trade_store.get_trades(
                symbol=order.symbol,
                status="open",
                account_id=self._dashboard_account_id(),
                limit=1,
            )
            if self.config.dashboard.enabled
            else []
        )
        row = open_trades[0] if open_trades else None
        projected_side = (
            row["side"]
            if row is not None
            else tracked.get("side")
            if tracked is not None
            else pending_entry.get("side")
            if pending_entry is not None
            else None
        )
        if projected_side != order.side:
            return

        if tracked is not None:
            tracked["quantity"] = float(position.quantity)
            tracked["entry_price"] = float(position.entry_price)
        elif self.strategy is not None and pending_entry is not None:
            entry_time = order.filled_at or datetime.now(timezone.utc)
            trailing_stop = getattr(
                getattr(self.ruleset, "exit", None),
                "trailing_stop",
                None,
            )
            self.strategy.track_position(
                symbol=order.symbol,
                side=order.side,
                entry_price=float(position.entry_price),
                take_profit=pending_entry.get("take_profit"),
                stop_loss=pending_entry.get("stop_price"),
                timestamp=entry_time,
                quantity=float(position.quantity),
                trailing_stop=(
                    trailing_stop.model_dump()
                    if trailing_stop is not None
                    else None
                ),
            )
            if hasattr(self.strategy, "mark_traded_today"):
                self.strategy.mark_traded_today(order.symbol, entry_time.date())

        if not self.config.dashboard.enabled:
            if pending_entry is not None and self.trade_executor is not None:
                self.trade_executor.clear_pending_entry(order.order_id)
            return
        if row is None:
            await self._persist_dashboard_trade_entry(
                symbol=order.symbol,
                order_id=order.order_id,
                signal_value=1 if order.side == "buy" else -1,
                quantity=float(position.quantity),
                entry_price=float(position.entry_price),
                entry_time=order.filled_at or datetime.now(timezone.utc),
            )
            if pending_entry is not None and self.trade_executor is not None:
                self.trade_executor.clear_pending_entry(order.order_id)
            return
        row_id = int(row["id"])
        trade_id = row.get("trade_id")
        self.trade_store.update_trade(
            row_id,
            quantity=float(position.quantity),
            entry_price=float(position.entry_price),
            broker_order_id=order.order_id,
        )
        if trade_id is not None:
            self._dashboard_order_trade_ids[order.order_id] = trade_id
            self._dashboard_symbol_trade_ids[order.symbol] = trade_id
            self._dashboard_trade_row_ids[trade_id] = row_id
            self._enqueue_trade_row(
                trade_id,
                order.filled_at or datetime.now(timezone.utc),
            )
        if pending_entry is not None and self.trade_executor is not None:
            self.trade_executor.clear_pending_entry(order.order_id)

    async def _sync_open_trade_after_exit_fill(self, order: Any) -> None:
        """Apply asynchronous close/retry fills and close state only when flat."""
        if not order.filled_qty:
            return
        exit_reason = self._pending_exit_reasons.get(
            order.symbol,
            "broker_close_fill",
        )
        tracked = self.strategy.get_position(order.symbol) if self.strategy is not None else None
        open_trades = (
            self.trade_store.get_trades(
                symbol=order.symbol,
                status="open",
                account_id=self._dashboard_account_id(),
                limit=1,
            )
            if self.config.dashboard.enabled
            else []
        )
        tracked_side = (
            open_trades[0]["side"]
            if open_trades
            else tracked.get("side")
            if tracked is not None
            else None
        )
        if tracked_side is None or tracked_side == order.side:
            return
        position = await self.exchange.get_position(order.symbol)
        remaining_quantity = float(position.quantity) if position is not None else 0.0
        if remaining_quantity <= 0:
            if self.strategy is not None:
                self.strategy.close_position(order.symbol)
            if self.trade_executor is not None:
                self.trade_executor.clear_pending_close(symbol=order.symbol)
        elif tracked is not None:
            tracked["quantity"] = remaining_quantity
            tracked["entry_price"] = float(position.entry_price)

        if not self.config.dashboard.enabled:
            if remaining_quantity <= 0:
                self._pending_exit_reasons.pop(order.symbol, None)
            return
        exit_time = order.filled_at or datetime.now(timezone.utc)
        await self._persist_dashboard_trade_exit(
            symbol=order.symbol,
            order_id=order.order_id,
            exit_price=float(order.avg_price or order.price),
            exit_time=exit_time,
            exit_reason=exit_reason,
            filled_quantity=float(order.filled_qty),
            remaining_quantity=remaining_quantity,
        )
        if remaining_quantity <= 0:
            self._pending_exit_reasons.pop(order.symbol, None)

    def _record_dashboard_fill_metrics(
        self,
        *,
        event: OrderEvent,
    ) -> None:
        if self.operational_metrics_store is None:
            return
        try:
            if event.execution_id is None:
                return
            dimensions = {
                "account_id": event.account_id,
                "broker": event.broker,
                "symbol": event.symbol,
                "side": event.side,
                "execution_id": event.execution_id,
                "broker_order_id": event.broker_order_id,
                "permanent_order_id": event.permanent_order_id or "",
                "event_type": event.event_type,
                "status": event.raw_status or "",
                "trade_currency": event.trade_currency or "unknown",
                "commission_currency": event.commission_currency or "unknown",
                "slippage_version": str(event.slippage_version),
                "slippage_valid": str(event.slippage_valid).lower(),
            }
            samples = [
                ("actual_fill_price", event.price),
                ("fill_quantity", event.quantity),
                ("commission", event.commission),
            ]
            if event.benchmark_price not in (None, 0):
                samples.append(("expected_fill_price", event.benchmark_price))
            if event.slippage_amount is not None:
                samples.append(("slippage", event.slippage_amount))
            if event.slippage_bps is not None:
                samples.append(("slippage_bps", event.slippage_bps))
            latency_ms = event.submission_to_fill_latency_ms
            if latency_ms is None:
                latency_ms = _elapsed_ms(event.submitted_at, event.filled_at or event.occurred_at)
            if latency_ms is not None:
                samples.append(("latency_ms", latency_ms))

            for metric_name, metric_value in samples:
                if metric_value is None:
                    continue
                aggregate_id = f"{event.execution_id}:{metric_name}"
                self.operational_metrics_store.record_metric(
                    metric_type=MetricType.TRADE.value,
                    metric_name=metric_name,
                    metric_value=float(metric_value),
                    dimensions=dimensions,
                    timestamp=_iso_datetime(event.filled_at or event.occurred_at),
                    idempotency_key=aggregate_id,
                )
                self._enqueue_dashboard_event(
                    event_id=f"metric:{aggregate_id}",
                    event_type="upsert",
                    aggregate_type="metric",
                    aggregate_id=aggregate_id,
                    payload={
                        "metric_id": aggregate_id,
                        "metric_name": metric_name,
                        "metric_value": float(metric_value),
                        "dimensions": dimensions,
                        "timestamp": _iso_datetime(event.filled_at or event.occurred_at),
                    },
                    original_event_timestamp=event.filled_at or event.occurred_at,
                )
        except Exception as exc:
            self.logger.warning("Dashboard fill metric persistence failed for %s: %s", event.execution_id, exc)

    def _ingest_durable_ib_execution(self, execution: Dict[str, Any]) -> None:
        """Project one durable IB execution or commission correction idempotently."""
        if self.dashboard_store is None and self.operational_metrics_store is None:
            return
        try:
            if execution.get("account_id"):
                self._resolved_dashboard_account_id = str(execution["account_id"])
            metadata = execution.get("order_metadata") or {}
            benchmark_price = metadata.get("benchmark_price")
            benchmark_version = int(metadata.get("benchmark_version") or 1)
            benchmark_valid = (
                benchmark_version == 2 and benchmark_price not in (None, 0)
            )
            execution_price = float(execution["price"])
            slippage_amount = None
            slippage_bps = None
            if benchmark_valid:
                benchmark = float(benchmark_price)
                slippage_amount = (
                    execution_price - benchmark
                    if execution["side"] == "buy"
                    else benchmark - execution_price
                )
                slippage_bps = (slippage_amount / benchmark) * 10000.0
            filled_at = execution["filled_at"]
            submitted_at = metadata.get("submitted_at")
            decision_at = metadata.get("decision_at")
            event_id = f"EXECUTION:{execution['execution_id']}"
            existing_event = self.dashboard_store.get_row(
                "order_events", "event_id", event_id
            ) if self.dashboard_store is not None else None
            event = OrderEvent(
                event_id=event_id,
                execution_id=str(execution["execution_id"]),
                account_id=(
                    execution.get("account_id")
                    or getattr(self.config.broker, "ib_account_id", None)
                    or "unknown"
                ),
                broker="interactive_brokers",
                broker_order_id=str(execution["broker_order_id"]),
                permanent_order_id=execution.get("permanent_order_id"),
                event_type="ORDER_FILLED",
                symbol=execution["symbol"],
                side=execution["side"],
                quantity=float(execution["quantity"]),
                trade_id=(
                    self._dashboard_order_trade_ids.get(str(execution["broker_order_id"]))
                    or (existing_event or {}).get("trade_id")
                ),
                price=execution_price,
                expected_price=benchmark_price,
                benchmark_type=metadata.get("benchmark_type"),
                benchmark_price=benchmark_price,
                quote_bid=metadata.get("quote_bid"),
                quote_ask=metadata.get("quote_ask"),
                quote_midpoint=metadata.get("quote_midpoint"),
                stop_price=metadata.get("stop_price"),
                limit_price=metadata.get("limit_price"),
                trade_currency=execution.get("trade_currency"),
                commission=execution.get("commission"),
                commission_currency=execution.get("commission_currency"),
                decision_at=decision_at,
                submitted_at=submitted_at,
                filled_at=filled_at,
                decision_to_submission_latency_ms=_elapsed_ms(decision_at, submitted_at),
                submission_to_fill_latency_ms=_elapsed_ms(submitted_at, filled_at),
                latency_ms=_elapsed_ms(submitted_at, filled_at),
                slippage_amount=slippage_amount,
                slippage_bps=slippage_bps,
                slippage_version=benchmark_version,
                slippage_valid=benchmark_valid,
                occurred_at=filled_at,
                raw_status="Filled",
            )
            if self._dashboard_enabled():
                self._persist_and_publish_order_event(event)
            self._record_dashboard_fill_metrics(event=event)
        except Exception as exc:
            self.logger.exception(
                "Durable IB execution projection failed for %s: %s",
                execution.get("execution_id"),
                exc,
            )

    def _recover_durable_execution_projections(self) -> None:
        """Repair execution projections and metrics across crash boundaries."""
        list_executions = getattr(self.exchange, "list_durable_executions", None)
        if list_executions is not None:
            for execution in list_executions():
                self._ingest_durable_ib_execution(execution)
        if self.dashboard_store is not None:
            for event in self.dashboard_store.iter_execution_order_events():
                self._record_dashboard_fill_metrics(event=event)

    async def _recover_durable_lifecycle_projections(self) -> None:
        """Replay durable executions into trades and live strategy state."""
        list_executions = getattr(self.exchange, "list_durable_executions", None)
        if (
            list_executions is None
            or self.trade_executor is None
            or self.strategy is None
        ):
            return

        executions = sorted(
            list_executions(),
            key=lambda item: (
                (
                    _as_aware_datetime(item.get("filled_at"))
                    or datetime.min.replace(tzinfo=timezone.utc)
                ).astimezone(timezone.utc),
                str(item.get("execution_id") or ""),
            ),
        )
        positions: Dict[tuple[str, str], Dict[str, Any]] = {}
        close_progress: Dict[str, tuple[float, float]] = {}

        for execution in executions:
            execution_id = str(execution["execution_id"])
            order_id = str(execution["broker_order_id"])
            account_id = str(
                execution.get("account_id")
                or getattr(self.config.broker, "ib_account_id", None)
                or self._dashboard_account_id()
            )
            symbol = str(execution["symbol"])
            side = str(execution["side"])
            quantity = abs(float(execution["quantity"]))
            price = float(execution["price"])
            filled_at = _as_aware_datetime(execution.get("filled_at"))
            if filled_at is None:
                raise RuntimeError(
                    f"Durable execution {execution_id} is missing filled_at"
                )
            metadata = execution.get("order_metadata") or {}
            key = (account_id, symbol)
            state = positions.setdefault(
                key,
                {
                    "signed_quantity": 0.0,
                    "avg_price": 0.0,
                    "entry_metadata": {},
                    "last_order_id": order_id,
                    "opened_at": filled_at,
                },
            )
            signed_fill = quantity if side == "buy" else -quantity
            previous_signed = float(state["signed_quantity"])
            is_entry = (
                previous_signed == 0
                or (previous_signed > 0 and signed_fill > 0)
                or (previous_signed < 0 and signed_fill < 0)
            )

            if is_entry:
                row, _ = self.trade_store.apply_entry_execution_projection(
                    execution_id=execution_id,
                    broker_order_id=order_id,
                    account_id=account_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    entry_time=filled_at,
                    pnl_currency=execution.get("trade_currency"),
                    strategy=metadata.get("strategy_name")
                    or self.strategy.config.name,
                )
                previous_quantity = abs(previous_signed)
                total_quantity = previous_quantity + quantity
                state["avg_price"] = (
                    float(state["avg_price"]) * previous_quantity
                    + price * quantity
                ) / total_quantity
                state["entry_metadata"] = {
                    **state["entry_metadata"],
                    **metadata,
                }
                if previous_quantity == 0:
                    state["opened_at"] = filled_at
                trade_id = str(row["trade_id"])
                self._dashboard_symbol_trade_ids[symbol] = trade_id
                self._dashboard_order_trade_ids[order_id] = trade_id
                self._dashboard_trade_row_ids[trade_id] = int(row["id"])
            else:
                open_rows = self.trade_store.get_trades(
                    symbol=symbol,
                    status="open",
                    account_id=account_id,
                    limit=1,
                )
                if open_rows:
                    row = open_rows[0]
                    cumulative_quantity, cumulative_notional = close_progress.get(
                        order_id,
                        (0.0, 0.0),
                    )
                    cumulative_quantity += quantity
                    cumulative_notional += quantity * price
                    close_progress[order_id] = (
                        cumulative_quantity,
                        cumulative_notional,
                    )
                    remaining_quantity = abs(previous_signed + signed_fill)
                    if not self.trade_store.has_execution_lifecycle_projection(
                        execution_id
                    ):
                        self.trade_store.apply_exit_projection(
                            trade_row_id=int(row["id"]),
                            trade_id=str(row["trade_id"]),
                            order_id=order_id,
                            cumulative_quantity=cumulative_quantity,
                            cumulative_avg_price=(
                                cumulative_notional / cumulative_quantity
                            ),
                            remaining_quantity=remaining_quantity,
                            exit_time=filled_at,
                            exit_reason=metadata.get("exit_reason")
                            or "recovered_broker_close",
                        )
                        self.trade_store.mark_execution_lifecycle_projection(
                            execution_id=execution_id,
                            action="exit",
                            trade_id=str(row["trade_id"]),
                            trade_row_id=int(row["id"]),
                        )
                    self._dashboard_order_trade_ids[order_id] = str(
                        row["trade_id"]
                    )

            new_signed = previous_signed + signed_fill
            if previous_signed and new_signed and (
                (previous_signed > 0) != (new_signed > 0)
            ):
                # Strategy orders are not allowed to reverse through zero.
                self.logger.error(
                    "Ignoring unsupported recovered position reversal for %s",
                    symbol,
                )
                new_signed = 0.0
            state["signed_quantity"] = new_signed
            state["last_order_id"] = order_id

        for (_, symbol), state in positions.items():
            signed_quantity = float(state["signed_quantity"])
            if abs(signed_quantity) <= 1e-9:
                continue
            quantity = abs(signed_quantity)
            side = "buy" if signed_quantity > 0 else "sell"
            metadata = state["entry_metadata"]
            stop_price = metadata.get("strategy_stop_price")
            if stop_price is None:
                stop_price = metadata.get("stop_price")
            if stop_price is None:
                # Unknown historical risk levels must not create an immediate
                # price-triggered exit; EOD management remains active.
                stop_price = 0.0 if side == "buy" else float("inf")
            self.strategy.track_position(
                symbol=symbol,
                side=side,
                entry_price=float(state["avg_price"]),
                take_profit=metadata.get("take_profit"),
                stop_loss=float(stop_price),
                timestamp=state["opened_at"],
                quantity=quantity,
            )
            if hasattr(self.strategy, "mark_traded_today"):
                self.strategy.mark_traded_today(
                    symbol,
                    state["opened_at"].date(),
                )
            self.trade_executor._open_trades[symbol] = ExecutionResult(
                success=True,
                order_id=str(state["last_order_id"]),
                reason="Recovered from durable broker executions",
                position_size=quantity,
                avg_price=float(state["avg_price"]),
                remaining_position_size=quantity,
            )
            self.trade_executor.risk_manager.register_position()

    def _persist_dashboard_price_bar(self, symbol: str, bar_dict: dict) -> None:
        if not self.config.dashboard.enabled or self.dashboard_price_store is None:
            return
        try:
            bar_start = bar_dict.get("timestamp")
            if bar_start is None:
                return
            provider = self.active_provider.provider_name if self.active_provider else "unknown"
            ingestion_time = datetime.utcnow()
            price_bar = PriceBar(
                symbol=symbol,
                timeframe=self._bar_interval,
                bar_start=bar_start,
                open=float(bar_dict["open"]),
                high=float(bar_dict["high"]),
                low=float(bar_dict["low"]),
                close=float(bar_dict["close"]),
                volume=float(bar_dict.get("volume", 0.0)),
                provider=provider,
                ingestion_time=ingestion_time,
                is_complete=True,
            )
            self.dashboard_price_store.upsert_bar(price_bar)
            bar_start_text = bar_start.isoformat() if isinstance(bar_start, datetime) else str(bar_start)
            aggregate_id = f"{symbol}|{self._bar_interval}|{bar_start_text}"
            self._enqueue_dashboard_event(
                event_id=f"price_bar:{aggregate_id}",
                event_type="upsert",
                aggregate_type="price_bar",
                aggregate_id=aggregate_id,
                payload={
                    "symbol": symbol,
                    "timeframe": self._bar_interval,
                    "bar_start": bar_start_text,
                    "open": price_bar.open,
                    "high": price_bar.high,
                    "low": price_bar.low,
                    "close": price_bar.close,
                    "volume": price_bar.volume,
                    "provider": provider,
                    "ingestion_time": ingestion_time.isoformat(),
                    "is_complete": True,
                },
                original_event_timestamp=bar_start,
            )
        except Exception as exc:
            self.logger.warning("Dashboard price bar persistence failed for %s: %s", symbol, exc)

    async def _persist_dashboard_account_and_positions(self, reason: str = "poll") -> None:
        if not self._dashboard_enabled():
            return
        account_id = self._dashboard_account_id()
        broker_name = self._dashboard_broker_name()
        try:
            account = await self.exchange.get_account()
            if getattr(account, "account_id", None):
                self._resolved_dashboard_account_id = account.account_id
            account_id = self._dashboard_account_id()
            from vibe.trading_bot.utils.datetime_utils import ensure_timezone_aware

            observed_at = ensure_timezone_aware(account.timestamp, self.market_scheduler.timezone)
            base_currency = account.base_currency
            account_record = AccountRecord(
                account_id=account_id,
                broker=broker_name,
                display_name=account_id,
                currency=base_currency,
                mode=getattr(self.config.broker, "mode", "paper"),
            )
            position_records = []
            for symbol in self.active_symbols:
                position_snapshot_getter = getattr(
                    self.exchange,
                    "get_position_snapshot",
                    None,
                )
                broker_position = (
                    await position_snapshot_getter(symbol)
                    if position_snapshot_getter is not None
                    else None
                )
                position = (
                    None
                    if position_snapshot_getter is not None
                    else await self.exchange.get_position(symbol)
                )
                position_source = broker_position or position
                if position is None:
                    if broker_position is None:
                        quantity = 0.0
                        side = "flat"
                        avg_cost = None
                        market_price = None
                        unrealized_pnl = None
                    else:
                        quantity = abs(float(broker_position.quantity))
                        side = "long" if broker_position.quantity > 0 else "short"
                        avg_cost = broker_position.avg_cost
                        market_price = broker_position.market_price
                        unrealized_pnl = broker_position.unrealized_pnl
                else:
                    quantity = position.quantity
                    side = position.side if position.quantity != 0 else "flat"
                    avg_cost = position.entry_price
                    market_price = position.current_price
                    unrealized_pnl = position.unrealized_pnl
                instrument_currency = (
                    getattr(position_source, "instrument_currency", None)
                    if position_source is not None
                    else None
                )
                unrealized_pnl_currency = (
                    getattr(position_source, "unrealized_pnl_currency", None)
                    if position_source is not None
                    else None
                )
                position_record = PositionSnapshot(
                    position_id=f"{account_id}:{symbol}",
                    account_id=account_id,
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    avg_cost=avg_cost,
                    market_price=market_price,
                    unrealized_pnl=unrealized_pnl,
                    updated_at=observed_at,
                    instrument_currency=instrument_currency,
                    unrealized_pnl_currency=unrealized_pnl_currency,
                )
                position_payload = {
                    "position_id": position_record.position_id,
                    "account_id": account_id,
                    "symbol": symbol,
                    "quantity": quantity,
                    "side": side,
                    "avg_cost": avg_cost,
                    "market_price": market_price,
                    "unrealized_pnl": unrealized_pnl,
                    "instrument_currency": position_record.instrument_currency,
                    "unrealized_pnl_currency": position_record.unrealized_pnl_currency,
                    "updated_at": observed_at.isoformat(),
                    "reason": reason,
                }
                position_records.append((position_record, position_payload))
            closed_trades = self.trade_store.get_trades(
                status="closed",
                account_id=account_id,
                limit=10000,
            )
            partially_closed_trades = [
                trade
                for trade in self.trade_store.get_trades(
                    status="open",
                    account_id=account_id,
                    limit=10000,
                )
                if float(trade.get("closed_quantity") or 0.0) > 0
                and trade.get("pnl") is not None
            ]
            realized_trades = [*closed_trades, *partially_closed_trades]
            pnl_currencies = {
                trade.get("pnl_currency")
                for trade in realized_trades
                if trade.get("pnl") is not None and trade.get("pnl_currency")
            }
            all_realized_currencies_known = all(
                trade.get("pnl_currency")
                for trade in realized_trades
                if trade.get("pnl") is not None
            )
            local_realized_pnl = (
                sum(
                    float(trade["pnl"])
                    for trade in realized_trades
                    if trade.get("pnl") is not None
                )
                if len(pnl_currencies) == 1 and all_realized_currencies_known
                else None
            )
            local_realized_pnl_currency = (
                next(iter(pnl_currencies))
                if len(pnl_currencies) == 1 and all_realized_currencies_known
                else None
            )
            snapshot = EquitySnapshot(
                snapshot_id=f"{account_id}:{observed_at.isoformat()}",
                account_id=account_id,
                timestamp=observed_at,
                net_liquidation=account.broker_equity if account.broker_equity is not None else account.equity,
                cash=account.broker_cash if account.broker_cash is not None else account.cash,
                buying_power=(
                    account.broker_buying_power
                    if account.broker_buying_power is not None
                    else account.buying_power
                ),
                realized_pnl=account.realized_pnl,
                unrealized_pnl=account.unrealized_pnl,
                base_currency=account.base_currency,
                net_liquidation_currency=account.equity_currency,
                cash_currency=account.cash_currency,
                buying_power_currency=account.buying_power_currency,
                realized_pnl_currency=account.realized_pnl_currency,
                unrealized_pnl_currency=account.unrealized_pnl_currency,
                pnl_provenance=(
                    "broker"
                    if account.realized_pnl is not None
                    or account.unrealized_pnl is not None
                    else None
                ),
                realized_pnl_provenance=(
                    "broker" if account.realized_pnl is not None else None
                ),
                unrealized_pnl_provenance=(
                    "broker" if account.unrealized_pnl is not None else None
                ),
                pnl_version=2,
                local_realized_pnl=local_realized_pnl,
                local_realized_pnl_currency=local_realized_pnl_currency,
                event_type=reason,
                source=broker_name,
            )
            self.dashboard_store.upsert_account(account_record)
            self.dashboard_store.upsert_equity_snapshot(snapshot)
            self._enqueue_dashboard_event(
                event_id=f"account:{account_id}",
                event_type="upsert",
                aggregate_type="account",
                aggregate_id=account_id,
                payload=account_record.__dict__,
                original_event_timestamp=observed_at,
            )
            self._enqueue_dashboard_event(
                event_id=f"equity_snapshot:{snapshot.snapshot_id}",
                event_type="upsert",
                aggregate_type="equity_snapshot",
                aggregate_id=snapshot.snapshot_id,
                payload={
                    "snapshot_id": snapshot.snapshot_id,
                    "account_id": account_id,
                    "timestamp": observed_at.isoformat(),
                    "net_liquidation": snapshot.net_liquidation,
                    "cash": snapshot.cash,
                    "buying_power": snapshot.buying_power,
                    "realized_pnl": account.realized_pnl,
                    "unrealized_pnl": account.unrealized_pnl,
                    "base_currency": snapshot.base_currency,
                    "net_liquidation_currency": snapshot.net_liquidation_currency,
                    "cash_currency": snapshot.cash_currency,
                    "buying_power_currency": snapshot.buying_power_currency,
                    "realized_pnl_currency": snapshot.realized_pnl_currency,
                    "unrealized_pnl_currency": snapshot.unrealized_pnl_currency,
                    "pnl_provenance": snapshot.pnl_provenance,
                    "realized_pnl_provenance": snapshot.realized_pnl_provenance,
                    "unrealized_pnl_provenance": snapshot.unrealized_pnl_provenance,
                    "pnl_version": snapshot.pnl_version,
                    "local_realized_pnl": snapshot.local_realized_pnl,
                    "local_realized_pnl_currency": snapshot.local_realized_pnl_currency,
                    "granularity": snapshot.granularity,
                    "period_start": None,
                    "event_type": reason,
                    "source": broker_name,
                    "reason": reason,
                },
                original_event_timestamp=observed_at,
            )

            for position_record, position_payload in position_records:
                first_observation = (
                    position_record.position_id
                    not in self._positions_published_this_process
                )
                changed = self.dashboard_store.upsert_position_if_changed(
                    position_record,
                    market_price_threshold=self.config.dashboard.position_market_price_threshold,
                    unrealized_pnl_threshold=self.config.dashboard.position_unrealized_pnl_threshold,
                )
                needs_publication = self.dashboard_store.position_needs_publication(
                    position_record.position_id
                )
                if not changed and not needs_publication and not first_observation:
                    continue
                enqueued = self._enqueue_dashboard_event(
                    event_id=(
                        f"position-startup:{self._position_publication_process_id}:"
                        f"{position_record.position_id}"
                        if first_observation and not needs_publication
                        else f"position:{position_record.position_id}"
                    ),
                    event_type="upsert",
                    aggregate_type="position",
                    aggregate_id=position_record.position_id,
                    payload=position_payload,
                    original_event_timestamp=observed_at,
                )
                if enqueued:
                    self.dashboard_store.mark_position_enqueued(position_record.position_id)
                    self._positions_published_this_process.add(position_record.position_id)
        except Exception as exc:
            self.logger.warning("Dashboard account/position persistence failed: %s", exc)

    async def _record_dashboard_order_event(self, event_type: str, order_id: str) -> None:
        if not self._dashboard_enabled():
            return
        try:
            order = await self.exchange.get_order(order_id)
            if order is None:
                return
            from vibe.trading_bot.utils.datetime_utils import get_market_now
            occurred_at = get_market_now(self.market_scheduler)
            account_id = self._dashboard_account_id()
            fill_price = order.avg_price if order.avg_price > 0 else None
            if event_type == "ORDER_SENT":
                event_id = f"{event_type}:{order_id}"
                existing_sent_event = self.dashboard_store.get_row("order_events", "event_id", event_id)
                if existing_sent_event is not None:
                    occurred_at = datetime.fromisoformat(existing_sent_event["occurred_at"])
                self._dashboard_order_sent_at[order_id] = occurred_at
            trade_id = self._dashboard_order_trade_ids.get(order_id)
            if event_type == "ORDER_FILLED":
                executions = list(getattr(order, "executions", []) or [])
                if not executions:
                    execution_id = getattr(order, "execution_id", None)
                    if self._dashboard_broker_name() == "interactive_brokers" and not execution_id:
                        raise RuntimeError(f"IB order {order_id} has no execution ID")
                    executions = [{
                        "execution_id": execution_id or f"{self._dashboard_broker_name()}:{order_id}:aggregate",
                        "broker_order_id": order_id,
                        "permanent_order_id": getattr(order, "permanent_order_id", None),
                        "account_id": getattr(order, "account_id", None),
                        "symbol": order.symbol,
                        "side": order.side,
                        "quantity": order.filled_qty,
                        "price": fill_price,
                        "filled_at": getattr(order, "filled_at", None) or occurred_at,
                        "trade_currency": getattr(order, "trade_currency", None),
                        "commission": order.commission,
                        "commission_currency": getattr(order, "commission_currency", None),
                    }]
                for execution in executions:
                    execution_id = str(execution["execution_id"])
                    execution_price = float(execution["price"])
                    benchmark_price = getattr(order, "benchmark_price", None)
                    slippage_amount = None
                    slippage_bps = None
                    benchmark_valid = bool(
                        getattr(order, "benchmark_valid", False)
                        and benchmark_price not in (None, 0)
                    )
                    if benchmark_valid:
                        if order.side == "buy":
                            slippage_amount = execution_price - benchmark_price
                        else:
                            slippage_amount = benchmark_price - execution_price
                        slippage_bps = (slippage_amount / benchmark_price) * 10000.0
                    fill_at = execution.get("filled_at") or getattr(order, "filled_at", None) or occurred_at
                    submitted_at = getattr(order, "submitted_at", None)
                    decision_at = getattr(order, "decision_at", None)
                    order_event = OrderEvent(
                        event_id=f"EXECUTION:{execution_id}",
                        execution_id=execution_id,
                        account_id=execution.get("account_id") or account_id,
                        broker=self._dashboard_broker_name(),
                        broker_order_id=str(execution.get("broker_order_id") or order_id),
                        permanent_order_id=execution.get("permanent_order_id"),
                        event_type=event_type,
                        symbol=execution.get("symbol") or order.symbol,
                        side=execution.get("side") or order.side,
                        quantity=float(execution.get("quantity") or 0.0),
                        trade_id=trade_id,
                        price=execution_price,
                        expected_price=benchmark_price,
                        benchmark_type=getattr(order, "benchmark_type", None),
                        benchmark_price=benchmark_price,
                        quote_bid=getattr(order, "quote_bid", None),
                        quote_ask=getattr(order, "quote_ask", None),
                        quote_midpoint=getattr(order, "quote_midpoint", None),
                        stop_price=getattr(order, "stop_price", None),
                        limit_price=getattr(order, "limit_price", None),
                        trade_currency=execution.get("trade_currency") or getattr(order, "trade_currency", None),
                        commission=(
                            float(execution["commission"])
                            if execution.get("commission") is not None
                            else None
                        ),
                        commission_currency=execution.get("commission_currency"),
                        decision_at=decision_at,
                        submitted_at=submitted_at,
                        filled_at=fill_at,
                        decision_to_submission_latency_ms=_elapsed_ms(decision_at, submitted_at),
                        submission_to_fill_latency_ms=_elapsed_ms(submitted_at, fill_at),
                        latency_ms=_elapsed_ms(submitted_at, fill_at),
                        slippage_amount=slippage_amount,
                        slippage_bps=slippage_bps,
                        slippage_version=int(getattr(order, "benchmark_version", 2)),
                        slippage_valid=benchmark_valid,
                        occurred_at=fill_at,
                        raw_status=getattr(order.status, "name", str(order.status)),
                    )
                    self._persist_and_publish_order_event(order_event)
                    self._record_dashboard_fill_metrics(event=order_event)
                return

            order_event = OrderEvent(
                event_id=f"{event_type}:{order_id}",
                account_id=account_id,
                broker=self._dashboard_broker_name(),
                broker_order_id=order_id,
                event_type=event_type,
                symbol=order.symbol,
                side=order.side,
                quantity=order.filled_qty if event_type == "TRADE_CLOSED" and order.filled_qty > 0 else order.quantity,
                trade_id=trade_id,
                price=fill_price if event_type == "TRADE_CLOSED" else order.price,
                trade_currency=getattr(order, "trade_currency", None),
                expected_price=None,
                slippage_version=2,
                slippage_valid=False,
                occurred_at=occurred_at,
                raw_status=getattr(order.status, "name", str(order.status)),
            )
            self._persist_and_publish_order_event(order_event)
        except Exception as exc:
            self.logger.warning("Dashboard order event persistence failed for %s: %s", order_id, exc)

    def run_dashboard_retention_maintenance(self) -> None:
        """Stage and advance crash-safe local/remote dashboard retention."""
        if self.dashboard_store is not None:
            from vibe.trading_bot.utils.datetime_utils import get_market_now

            result = self.dashboard_store.downsample_equity_snapshots(
                now=get_market_now(self.market_scheduler),
                raw_retention_days=self.config.dashboard.equity_raw_retention_days,
                five_minute_retention_days=self.config.dashboard.equity_five_minute_retention_days,
                five_minute_bucket_minutes=self.config.dashboard.equity_bucket_minutes,
                market_timezone=self.config.dashboard.equity_market_timezone,
            )
            self.logger.info(
                "Equity retention staged: aggregated=%s retained=%s removed=%s",
                result.aggregated,
                result.retained,
                result.removed,
            )
            removed = self._advance_equity_retention_jobs()
            if removed:
                self.logger.info(
                    "Equity retention finalized after remote confirmation: removed=%s",
                    removed,
                )
        if self.remote_data_publisher is not None:
            self.remote_data_publisher.run_retention_maintenance(force=True)

    def _advance_equity_retention_jobs(self) -> int:
        if self.dashboard_store is None or self.dashboard_outbox_store is None:
            return 0
        removed = 0
        for job in self.dashboard_store.pending_equity_retention_jobs():
            aggregate_id = job["aggregate_snapshot_id"]
            aggregate_event_id = f"equity_snapshot:{aggregate_id}"
            payload = self.dashboard_store.equity_snapshot_payload(aggregate_id)
            if payload is None:
                continue
            if not self.dashboard_outbox_store.is_published(aggregate_event_id, payload):
                self._enqueue_dashboard_event(
                    event_id=aggregate_event_id,
                    event_type="upsert",
                    aggregate_type="equity_snapshot",
                    aggregate_id=aggregate_id,
                    payload=payload,
                    original_event_timestamp=payload["timestamp"],
                )
                continue

            delete_event_id = f"equity_retention_delete:{job['job_id']}"
            delete_payload = {"snapshot_ids": job["source_snapshot_ids"]}
            if not self.dashboard_outbox_store.is_published(delete_event_id, delete_payload):
                self._enqueue_dashboard_event(
                    event_id=delete_event_id,
                    event_type="delete",
                    aggregate_type="equity_snapshot_delete",
                    aggregate_id=job["job_id"],
                    payload=delete_payload,
                    original_event_timestamp=datetime.now(timezone.utc),
                )
                continue

            removed += self.dashboard_store.complete_equity_retention_job(job["job_id"])
        return removed

    def _persist_and_publish_order_event(self, event: OrderEvent) -> None:
        self.dashboard_store.upsert_order_event(event)
        payload = {
            key: _iso_datetime(value) if isinstance(value, datetime) else value
            for key, value in event.__dict__.items()
        }
        self._enqueue_dashboard_event(
            event_id=f"order_event:{event.event_id}",
            event_type="upsert",
            aggregate_type="order_event",
            aggregate_id=event.event_id,
            payload=payload,
            original_event_timestamp=event.occurred_at,
        )

    async def initialize(self) -> bool:
        """Initialize all components in correct order.

        Returns:
            True if all components initialized successfully

        Raises:
            Exception if initialization fails critically
        """
        try:
            self.logger.info("Starting component initialization...")

            # 1. Initialize data manager
            try:
                # Create data provider
                data_provider = YahooDataProvider()

                # Create cache directory
                cache_dir = Path(self.config.data.cache_dir) if hasattr(self.config.data, 'cache_dir') else Path("./data/cache")
                cache_dir.mkdir(parents=True, exist_ok=True)

                # Create aggregator for real-time data
                # Use configured interval (default 5m, but 1m for integration testing)
                aggregator = BarAggregator(bar_interval=self._bar_interval)

                # Initialize data manager
                self.data_manager = DataManager(
                    provider=data_provider,
                    cache_dir=cache_dir,
                    aggregator=aggregator,
                    cache_ttl_seconds=self.config.data.data_cache_ttl_seconds,
                )
                self.logger.info("Data manager initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize data manager: {e}")
                raise

            # 2. Initialize exchange
            try:
                await self.exchange.initialize()
                self._recover_durable_execution_projections()
                self.logger.info("Exchange initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize exchange: {e}")
                raise

            # 3. Initialize trading components
            try:
                # Create position sizer with percentage-based risk
                # Risk amount is calculated dynamically based on current account value
                # Risk pct driven by ruleset if available, else default 1%
                risk_pct = 0.01
                max_shares = None
                max_position_pct = None
                if self.ruleset and self.ruleset.position_size.method == "max_loss_pct":
                    risk_pct = self.ruleset.position_size.value
                    max_shares = self.ruleset.position_size.max_shares
                    max_position_pct = self.ruleset.position_size.max_position_pct
                position_sizer = PositionSizer(
                    risk_pct=risk_pct,
                    max_position_size=max_shares,
                    max_position_pct=max_position_pct,
                )

                # Create order manager with retry policy
                retry_policy = OrderRetryPolicy(
                    max_retries=3,
                    base_delay_seconds=1.0,
                    cancel_after_seconds=60,
                )
                order_manager = OrderManager(
                    exchange=self.exchange,
                    retry_policy=retry_policy,
                    on_order_created=self._on_order_created,
                    on_order_filled=self._on_order_filled,
                    on_order_cancelled=self._on_order_cancelled,
                )

                # Create trade executor
                self.trade_executor = TradeExecutor(
                    exchange=self.exchange,
                    order_manager=order_manager,
                    position_sizer=position_sizer,
                )
                self.logger.info("Trade executor initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize trade executor: {e}")
                raise

            # 4. Initialize indicator engine
            try:
                indicator_state_dir = Path(self.config.database_path).parent / "indicator_state"
                self.indicator_engine = IncrementalIndicatorEngine(state_dir=indicator_state_dir)
                self.logger.info("Indicator engine initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize indicator engine: {e}")
                raise

            # 5. Initialize strategy — driven by ruleset if available, else fall back to .env
            try:
                if self.ruleset:
                    orb_params = self.ruleset.strategy
                    # Take profit multiplier: 0.0 means disabled (no TP target)
                    tp_multiplier = 0.0
                    if self.ruleset.exit.take_profit is not None:
                        tp_multiplier = getattr(self.ruleset.exit.take_profit, "multiplier", 0.0)
                    # Stop loss: True if ORB level stop, False if ATR-based
                    stop_at_level = isinstance(self.ruleset.exit.stop_loss, OrbLevelStopLoss)
                    strategy_config = ORBStrategyConfig(
                        name="ORB",
                        orb_start_time=orb_params.orb_start_time,
                        orb_duration_minutes=orb_params.orb_duration_minutes,
                        orb_body_pct_filter=orb_params.orb_body_pct_filter,
                        breakout_evaluation=orb_params.breakout_evaluation,
                        entry_cutoff_time=orb_params.entry_cutoff_time,
                        take_profit_multiplier=tp_multiplier,
                        stop_loss_at_level=stop_at_level,
                        use_volume_filter=self.ruleset.trade_filter.volume_confirmation,
                        volume_threshold=self.ruleset.trade_filter.volume_threshold,
                        market_close_time=self.config.strategy.market_close_time,
                    )
                    self.logger.info(
                        f"Strategy config from ruleset '{self.ruleset.name}': "
                        f"ORB {orb_params.orb_start_time}+{orb_params.orb_duration_minutes}m, "
                        f"body_filter={orb_params.orb_body_pct_filter:.0%}, "
                        f"breakout={orb_params.breakout_evaluation}, "
                        f"tp={'disabled' if tp_multiplier == 0 else f'{tp_multiplier}x'}, "
                        f"sl={'orb_level' if stop_at_level else 'atr'}"
                    )
                else:
                    strategy_config = ORBStrategyConfig(
                        name="ORB",
                        orb_start_time=self.config.strategy.orb_start_time,
                        orb_duration_minutes=self.config.strategy.orb_duration_minutes,
                        orb_body_pct_filter=self.config.strategy.orb_body_pct_filter,
                        entry_cutoff_time=self.config.strategy.entry_cutoff_time,
                        take_profit_multiplier=self.config.strategy.take_profit_multiplier,
                        stop_loss_at_level=self.config.strategy.stop_loss_at_level,
                        use_volume_filter=self.config.strategy.use_volume_filter,
                        volume_threshold=self.config.strategy.volume_threshold,
                        market_close_time=self.config.strategy.market_close_time,
                    )
                    self.logger.info(
                        f"Strategy config from .env: ORB window={self.config.strategy.orb_start_time} "
                        f"duration={self.config.strategy.orb_duration_minutes}m"
                    )
                self.strategy = ORBStrategy(config=strategy_config)
                await self._recover_durable_lifecycle_projections()
                list_open_orders = getattr(self.exchange, "list_open_orders", None)
                if list_open_orders is not None and self.trade_executor is not None:
                    restored_count = self.trade_executor.order_manager.restore_open_orders(
                        list_open_orders()
                    )
                    if restored_count:
                        self.logger.info(
                            "Restored %s open broker orders into lifecycle monitoring",
                            restored_count,
                        )
            except Exception as e:
                self.logger.error(f"Failed to initialize strategy: {e}")
                raise

            # 6. Initialize real-time data providers (primary + secondary)
            try:
                # Get API keys from config
                finnhub_key = getattr(self.config.data, 'finnhub_api_key', None)
                polygon_key = getattr(self.config.data, 'polygon_api_key', None)

                # Create primary provider (MANDATORY)
                primary_type = getattr(self.config.data, 'primary_provider', 'polygon')
                self.logger.info(f"Initializing primary data provider: {primary_type}")

                self.primary_provider = DataProviderFactory.create_realtime_provider(
                    provider_type=primary_type,
                    finnhub_api_key=finnhub_key,
                    polygon_api_key=polygon_key,
                    ib_host=self.config.broker.ib_host,
                    ib_port=self.config.broker.ib_port,
                    ib_client_id=self.config.broker.ib_client_id + 1,
                    ib_account_id=self.config.broker.ib_account_id,
                    ib_exchange=self.config.broker.ib_exchange,
                    ib_currency=self.config.broker.ib_currency,
                    ib_market_data_type=self.config.broker.ib_market_data_type,
                    ib_connect_timeout=self.config.broker.ib_connect_timeout,
                    ib_connect_max_retries=self.config.broker.ib_connect_max_retries,
                    ib_connect_retry_delay_seconds=self.config.broker.ib_connect_retry_delay_seconds,
                )

                if not self.primary_provider:
                    raise ValueError(f"Failed to create primary provider: {primary_type}")

                self.active_provider = self.primary_provider
                self.logger.info(
                    f"[OK] Primary provider: {self.primary_provider.provider_name} "
                    f"(type={self.primary_provider.provider_type.value}, "
                    f"real_time={self.primary_provider.is_real_time})"
                )

                # Create secondary provider (OPTIONAL fallback)
                secondary_type = getattr(self.config.data, 'secondary_provider', None)
                if secondary_type:
                    self.logger.info(f"Initializing secondary data provider: {secondary_type}")
                    try:
                        self.secondary_provider = DataProviderFactory.create_realtime_provider(
                            provider_type=secondary_type,
                            finnhub_api_key=finnhub_key,
                            polygon_api_key=polygon_key,
                            ib_host=self.config.broker.ib_host,
                            ib_port=self.config.broker.ib_port,
                            ib_client_id=self.config.broker.ib_client_id + 2,
                            ib_account_id=self.config.broker.ib_account_id,
                            ib_exchange=self.config.broker.ib_exchange,
                            ib_currency=self.config.broker.ib_currency,
                            ib_market_data_type=self.config.broker.ib_market_data_type,
                            ib_connect_timeout=self.config.broker.ib_connect_timeout,
                            ib_connect_max_retries=self.config.broker.ib_connect_max_retries,
                            ib_connect_retry_delay_seconds=self.config.broker.ib_connect_retry_delay_seconds,
                        )
                        if self.secondary_provider:
                            self.logger.info(
                                f"[OK] Secondary provider: {self.secondary_provider.provider_name} (fallback)"
                            )
                    except Exception as e:
                        self.logger.warning(f"Failed to create secondary provider: {e}")
                        self.secondary_provider = None

                # Create bar aggregators for all active symbols (driven by ruleset)
                for symbol in self.active_symbols:
                    aggregator = BarAggregator(
                        bar_interval=self._bar_interval,
                        timezone=str(self.market_scheduler.timezone)
                    )
                    # Set up bar completion callback with symbol binding
                    aggregator.on_bar_complete(
                        lambda bar_dict, sym=symbol: self._handle_completed_bar(sym, bar_dict)
                    )
                    self.bar_aggregators[symbol] = aggregator

                # Handle WebSocket provider (callback-based)
                if isinstance(self.primary_provider, WebSocketDataProvider):
                    self.logger.info("Primary provider is WebSocket - setting up callbacks")
                    self.finnhub_ws = self.primary_provider  # For backward compatibility

                    # Set up trade callback to feed aggregators
                    self.primary_provider.on_trade(self._handle_realtime_trade)
                    self.primary_provider.on_error(self._handle_provider_error)

                    self.logger.info("WebSocket callbacks configured (will connect at market open)")

                # Handle REST provider (polling-based)
                elif isinstance(self.primary_provider, RESTDataProvider):
                    self.logger.info("Primary provider is REST - will poll at intervals")
                    poll_with = getattr(self.config.data, 'poll_interval_with_position', 60)
                    poll_without = getattr(self.config.data, 'poll_interval_no_position', 300)
                    self.logger.info(
                        f"Poll interval: {poll_with}s with positions, {poll_without}s without"
                    )

            except Exception as e:
                self.logger.error(f"Failed to initialize data providers: {e}")
                self.logger.warning("Falling back to Yahoo Finance only (15-min delay)")
                self.primary_provider = None
                self.secondary_provider = None
                self.active_provider = None
                self.finnhub_ws = None
                # NOTE: Do NOT clear bar_aggregators here!
                # Bar aggregators have callbacks registered - clearing them destroys callbacks
                # and prevents real-time bars even if provider reconnects later.
                # Keep aggregators alive - they'll be reset in warmup phase.

            # 7. Register health checks
            self._register_health_checks()

            # 8. Initialize phase managers
            self.warmup_manager = WarmupPhaseManager(self)
            self.cooldown_manager = CooldownPhaseManager(self)

            # 8.5. Start dashboard remote publisher if configured.
            await self._start_dashboard_publisher()
            # Publish the first broker-observed account/position state once per
            # process even when the market is closed and no trading cycle runs.
            await self._persist_dashboard_account_and_positions(reason="startup")

            # 9. Provider connection now handled in warm-up phase (Step 2)
            # Removed old duplicate connection code that was causing rate limiting

            # Log data source configuration
            market_is_open = self.market_scheduler.is_market_open()
            self.logger.info("=" * 60)
            self.logger.info("DATA SOURCE CONFIGURATION")
            self.logger.info("=" * 60)
            if self.active_provider:
                if market_is_open:
                    self.logger.info("Market Status: OPEN")
                    self.logger.info(
                        "Primary Source: %s (%s)",
                        self.active_provider.provider_name,
                        "real-time" if self.active_provider.is_real_time else "delayed",
                    )
                    self.logger.info("Fallback Source: Yahoo Finance (15-min delayed)")
                    self.logger.info("Expected Gap: ~15 minutes between yfinance and realtime provider on restart")
                else:
                    self.logger.info("Market Status: CLOSED")
                    self.logger.info(
                        "Data Source: Yahoo Finance historical plus %s at market open",
                        self.active_provider.provider_name,
                    )
            else:
                self.logger.info("Realtime provider: Not configured")
                self.logger.info("Data Source: Yahoo Finance only (15-min delayed)")
            self.logger.info("=" * 60)

            self.logger.info("All components initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Component initialization failed: {e}", exc_info=True)
            raise

    def _register_health_checks(self) -> None:
        """Register health check callbacks for all components."""
        def check_data():
            return {"status": "healthy" if self.data_manager else "unhealthy"}

        def check_exchange():
            return {"status": "healthy" if self.exchange else "unhealthy"}

        def check_strategy():
            return {"status": "healthy" if self.strategy else "unhealthy"}

        self.health_monitor.register_component("data", check_data)
        self.health_monitor.register_component("exchange", check_exchange)
        self.health_monitor.register_component("strategy", check_strategy)

    def _initialize_daily_stats(self) -> Dict[str, Any]:
        """Initialize daily statistics dictionary."""
        from datetime import datetime
        return {
            "date": datetime.now().date().isoformat(),
            "orb_levels": {},  # symbol -> {high, low, range}
            "breakouts_detected": 0,
            "breakouts_rejected": {},  # reason -> count
            "signals_generated": 0,
            "trades_executed": 0,
            "signals_by_symbol": {},  # symbol -> count
        }

    def _update_daily_stats(self, symbol: str, signal_value: int, metadata: Dict[str, Any]) -> None:
        """Update daily statistics based on strategy evaluation."""
        from datetime import datetime

        # Safety check: Reset stats if new day (warmup phase should handle this proactively)
        current_date = datetime.now(self.market_scheduler.timezone).date().isoformat()
        if self._daily_stats["date"] != current_date:
            self.logger.debug(f"Late daily stats reset during trading (expected in warmup)")
            self._daily_stats = self._initialize_daily_stats()

        # Record ORB levels (include body_pct from current bar)
        if "orb_high" in metadata and symbol not in self._daily_stats["orb_levels"]:
            # Guard: only store ORB levels for today's trading date.
            # At startup, replayed yfinance bars carry yesterday's date — reject those
            # so they cannot prematurely trigger the ORB Discord notification.
            orb_trading_date = metadata.get("orb_trading_date")
            if orb_trading_date is not None and orb_trading_date.isoformat() != current_date:
                self.logger.debug(
                    f"[ORB SKIP] {symbol}: ORB levels from {orb_trading_date} "
                    f"(stale historical data, today={current_date}) — skipping"
                )
            else:
                # Calculate body percentage of current bar if available
                body_pct = 0.0
                if "current_bar" in metadata:
                    bar = metadata["current_bar"]
                    if "open" in bar and "close" in bar and "high" in bar and "low" in bar:
                        total_range = bar["high"] - bar["low"]
                        if total_range > 0:
                            body_pct = abs(bar["close"] - bar["open"]) / total_range * 100

                self._daily_stats["orb_levels"][symbol] = {
                    "high": metadata["orb_high"],
                    "low": metadata["orb_low"],
                    "range": metadata["orb_range"],
                    "body_pct": body_pct,
                }
                self._persist_dashboard_orb_annotations(
                    symbol=symbol,
                    trading_day=current_date,
                    levels=self._daily_stats["orb_levels"][symbol],
                )
                self.logger.info(
                    f"[ORB STORED] {symbol}: High=${metadata['orb_high']:.2f}, "
                    f"Low=${metadata['orb_low']:.2f}, Range=${metadata['orb_range']:.2f}"
                )

        # Count breakouts detected
        price_position = metadata.get("price_position", "")
        if price_position in ["above_high", "below_low"] and signal_value == 0:
            self._daily_stats["breakouts_detected"] += 1

            # Count rejection reasons
            reason = metadata.get("reason", "unknown")
            if reason in self._daily_stats["breakouts_rejected"]:
                self._daily_stats["breakouts_rejected"][reason] += 1
            else:
                self._daily_stats["breakouts_rejected"][reason] = 1

        # Count signals generated
        if signal_value != 0:
            self._daily_stats["signals_generated"] += 1

            # Count by symbol
            if symbol in self._daily_stats["signals_by_symbol"]:
                self._daily_stats["signals_by_symbol"][symbol] += 1
            else:
                self._daily_stats["signals_by_symbol"][symbol] = 1

    def _persist_dashboard_orb_annotations(self, *, symbol: str, trading_day: str, levels: Dict[str, Any]) -> None:
        if not self._dashboard_enabled() or self.dashboard_store is None:
            return
        try:
            account_id = self._dashboard_account_id()
            strategy_name = self.strategy.config.name if self.strategy else "ORB"
            created_at = datetime.now(self.market_scheduler.timezone)
            annotations = {
                "orb_high": {"price": float(levels["high"]), "label": "ORB High"},
                "orb_low": {"price": float(levels["low"]), "label": "ORB Low"},
                "orb_range": {"range": float(levels["range"]), "body_pct": float(levels.get("body_pct", 0.0))},
            }
            for key, value_json in annotations.items():
                annotation_id = f"{account_id}:{symbol}:{trading_day}:{key}"
                annotation = StrategyAnnotation(
                    annotation_id=annotation_id,
                    account_id=account_id,
                    symbol=symbol,
                    strategy=strategy_name,
                    trading_day=trading_day,
                    annotation_type="level" if key in {"orb_high", "orb_low"} else "label",
                    key=key,
                    value_json=value_json,
                    enabled=True,
                )
                self.dashboard_store.upsert_strategy_annotation(annotation)
                self._enqueue_dashboard_event(
                    event_id=f"strategy_annotation:{annotation_id}",
                    event_type="upsert",
                    aggregate_type="strategy_annotation",
                    aggregate_id=annotation_id,
                    payload={
                        "annotation_id": annotation.annotation_id,
                        "account_id": annotation.account_id,
                        "symbol": annotation.symbol,
                        "strategy": annotation.strategy,
                        "trading_day": annotation.trading_day,
                        "annotation_type": annotation.annotation_type,
                        "key": annotation.key,
                        "value_json": annotation.value_json,
                        "enabled": annotation.enabled,
                    },
                    original_event_timestamp=created_at,
                )
        except Exception as exc:
            self.logger.warning("Dashboard ORB annotation persistence failed for %s: %s", symbol, exc)

    async def _check_and_send_orb_notification(self) -> None:
        """Check if ORB levels are ready and send Discord notification once per day.

        Sends notification when:
        1. ORB levels collected for all tracked symbols
        2. Notification not sent yet today
        3. Discord notifications enabled
        """
        from datetime import datetime

        # Check if routine notifications enabled
        if (
            not self.config.notifications.discord_webhook_url
            or not getattr(self.config.notifications, "notify_routine", True)
        ):
            return

        # Use market timezone for date comparison
        now = datetime.now(self.market_scheduler.timezone)
        current_date = now.date().isoformat()

        # Only send once per day
        if self._orb_notification_sent_date == current_date:
            return

        # Check if we have ORB levels for all symbols
        orb_levels = self._daily_stats.get("orb_levels", {})
        expected_symbols = set(self.active_symbols)
        collected_symbols = set(orb_levels.keys())

        if not collected_symbols or not collected_symbols.issuperset(expected_symbols):
            # Not all symbols have ORB levels yet
            return

        # All ORB levels collected - send notification
        try:
            from vibe.trading_bot.notifications.payloads import ORBLevelsPayload
            from vibe.trading_bot.notifications.discord import DiscordNotifier
            from vibe.trading_bot.version import BUILD_VERSION

            self.logger.info(
                f"[ORB NOTIFICATION] Sending Discord notification for {len(orb_levels)} symbols..."
            )

            payload = ORBLevelsPayload(
                event_type="ORB_ESTABLISHED",
                timestamp=now,
                symbols=orb_levels,
                version=BUILD_VERSION,
            )

            # Create notifier temporarily (like _send_daily_summary does)
            notifier = DiscordNotifier(webhook_url=self.config.notifications.discord_webhook_url)
            await notifier.start()

            success = await notifier.send_orb_notification(payload)

            await notifier.stop()

            if success:
                self._orb_notification_sent_date = current_date
                self.logger.info("[ORB NOTIFICATION] Discord notification sent successfully")
            else:
                self.logger.warning(
                    "[ORB NOTIFICATION] Discord notification failed — will retry next cycle"
                )

        except Exception as e:
            self.logger.error(f"Failed to send ORB Discord notification: {e}", exc_info=True)
            # Don't set the flag so we can retry

    async def _check_and_send_daily_summary(self) -> None:
        """Check if it's time to send daily summary and send if needed."""
        from vibe.trading_bot.utils.datetime_utils import get_market_now

        # Use market scheduler's time (supports both real and mock schedulers)
        now = get_market_now(self.market_scheduler)
        current_date = now.date().isoformat()

        # Only send once per day, after session end
        if self._last_summary_date == current_date:
            return

        # Get session end time for today
        session_end = self.market_scheduler.get_session_end_time()
        if not session_end:
            return

        # Check if we're past session end (already have 'now' in market timezone)
        if now >= session_end:
            await self._send_daily_summary()
            self._last_summary_date = current_date

    async def _send_daily_summary(self) -> None:
        """Generate and send end-of-day summary to Discord."""
        if (
            not self.config.notifications.discord_webhook_url
            or not getattr(self.config.notifications, "notify_routine", True)
        ):
            self.logger.debug("Routine Discord notifications disabled or webhook not configured, skipping daily summary")
            return

        try:
            from vibe.trading_bot.utils.datetime_utils import get_market_now
            from vibe.trading_bot.notifications.payloads import DailySummaryPayload
            from vibe.trading_bot.notifications.helper import discord_notification_context
            from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter
            from vibe.trading_bot.version import BUILD_VERSION
            import aiohttp

            # Get current time in market timezone
            now = get_market_now(self.market_scheduler)

            # Get account equity (guard against corrupted state e.g. negative cash)
            initial_capital = self.config.trading.initial_capital
            try:
                account = await self.exchange.get_account()
                account_value = account.equity
            except Exception as acct_err:
                self.logger.warning(
                    f"Could not read account state for daily summary "
                    f"(using initial capital as fallback): {acct_err}"
                )
                account_value = initial_capital
            pnl_pct = ((account_value - initial_capital) / initial_capital) * 100

            # Build ORB levels dict
            orb_levels = {}
            for symbol, levels in self._daily_stats["orb_levels"].items():
                orb_levels[symbol] = {
                    "high": levels["high"],
                    "low": levels["low"],
                    "range": levels["range"]
                }

            # Create payload
            payload = DailySummaryPayload(
                event_type="DAILY_SUMMARY",
                timestamp=now,
                date=self._daily_stats["date"],
                account_equity=account_value,
                initial_capital=initial_capital,
                pnl_pct=pnl_pct,
                orb_levels=orb_levels,
                breakouts_detected=self._daily_stats["breakouts_detected"],
                signals_generated=self._daily_stats["signals_generated"],
                trades_executed=self._daily_stats["trades_executed"],
                signals_by_symbol=self._daily_stats["signals_by_symbol"].copy(),
                breakouts_rejected=self._daily_stats["breakouts_rejected"].copy(),
                version=BUILD_VERSION
            )

            # Use formatter to convert payload to webhook format
            formatter = DiscordNotificationFormatter()
            webhook_payload = formatter.format_daily_summary(payload)

            # Send directly using aiohttp (notifier doesn't have send_daily_summary method yet)
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self.config.notifications.discord_webhook_url,
                    json=webhook_payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                )

            self.logger.info(f"Daily summary sent to Discord for {self._daily_stats['date']}")

        except Exception as e:
            self.logger.error(f"Error sending daily summary: {e}", exc_info=True)

    async def _handle_realtime_trade(self, trade: dict) -> None:
        """
        Handle real-time trade from Finnhub websocket.

        Feeds trades to appropriate BarAggregator which builds 5m bars.

        Args:
            trade: Trade dict with {symbol, price, size, timestamp}
        """
        try:
            symbol = trade.get("symbol")
            price = trade.get("price")
            size = trade.get("size", 0)
            timestamp = trade.get("timestamp")

            if not all([symbol, price, timestamp]):
                return

            # Get aggregator for this symbol
            aggregator = self.bar_aggregators.get(symbol)
            if not aggregator:
                return

            # Add trade to aggregator (will trigger _handle_completed_bar when bar completes)
            aggregator.add_trade(
                timestamp=timestamp,
                price=price,
                size=size
            )

        except Exception as e:
            self.logger.error(f"Error handling real-time trade: {e}", exc_info=True)

    def _handle_completed_bar(self, symbol: str, bar_dict: dict) -> None:
        """
        Handle completed 5m bar from aggregator.

        Stores completed bar for use in strategy evaluation.

        Args:
            symbol: Trading symbol
            bar_dict: Completed bar dict with {timestamp, open, high, low, close, volume}
        """
        try:
            self.logger.info(
                f"[REALTIME BAR] {symbol}: "
                f"timestamp={bar_dict.get('timestamp')}, "
                f"O={bar_dict.get('open'):.2f}, "
                f"H={bar_dict.get('high'):.2f}, "
                f"L={bar_dict.get('low'):.2f}, "
                f"C={bar_dict.get('close'):.2f}, "
                f"V={bar_dict.get('volume'):.0f}"
            )

            # Convert to DataFrame row
            bar_row = pd.DataFrame([bar_dict])

            # Append to real-time bars for this symbol
            if symbol in self._realtime_bars:
                self._realtime_bars[symbol] = pd.concat(
                    [self._realtime_bars[symbol], bar_row],
                    ignore_index=True
                )
            else:
                self._realtime_bars[symbol] = bar_row

            self._persist_dashboard_price_bar(symbol, bar_dict)

        except Exception as e:
            self.logger.error(f"Error handling completed bar for {symbol}: {e}", exc_info=True)

    async def _flush_elapsed_bars(self) -> None:
        """
        Flush bars that have crossed time boundaries (quiet market handling).

        This is the TIME-TRIGGERED completion path that complements the existing
        TRADE-TRIGGERED completion. Called periodically from trading loop.

        Why needed:
        - Trade-triggered: Bar completes when first trade of NEXT minute arrives (fast)
        - Time-triggered: Bar completes after time boundary even if no trades (safety net)

        Example scenario:
        - 9:32:00 bar is building with trades at 9:32:05, 9:32:15, 9:32:30
        - Market goes quiet - NO trades arrive at 9:33:00+
        - Without this method: 9:32:00 bar never completes!
        - With this method: 9:32:00 bar completes within 1-60 seconds (guaranteed)

        Called every iteration of trading loop (1-60 seconds depending on mode).
        """
        from vibe.trading_bot.utils.datetime_utils import get_market_now

        try:
            current_time = get_market_now(self.market_scheduler)

            for symbol, aggregator in self.bar_aggregators.items():
                # Check if this aggregator has a bar that crossed time boundary
                # NOTE: flush_if_elapsed() already calls the callback if bar completes,
                # so we don't need to call _handle_completed_bar() here
                aggregator.flush_if_elapsed(current_time)

        except Exception as e:
            self.logger.error(f"Error flushing elapsed bars: {e}", exc_info=True)

    # Old Finnhub connection methods removed - now handled by provider system in warm-up phase

    async def _start_rest_polling(self):
        """
        Start polling loop for REST API providers (Polygon-style).

        Polls at different intervals based on whether we have open positions:
        - With positions: poll every 60 seconds (monitor closely)
        - No positions: poll every 300 seconds (reduce API calls)
        """
        if not isinstance(self.active_provider, RESTDataProvider):
            return

        self.logger.info("Starting REST API polling loop")

        try:
            while self._running and self.market_scheduler.is_market_open():
                try:
                    # Determine poll interval based on positions
                    positions = {}
                    if hasattr(self.exchange, "get_all_positions"):
                        positions = self.exchange.get_all_positions()
                    elif hasattr(self.exchange, "get_positions"):
                        maybe_positions = self.exchange.get_positions()
                        positions = await maybe_positions if hasattr(maybe_positions, "__await__") else maybe_positions
                    has_positions = len(positions) > 0
                    poll_interval = (
                        self.config.data.poll_interval_with_position if has_positions
                        else self.config.data.poll_interval_no_position
                    )

                    self.logger.debug(
                        f"Polling {self.active_provider.provider_name} "
                        f"(positions={has_positions}, interval={poll_interval}s)"
                    )

                    # Fetch latest bars for all symbols
                    bars = await self.active_provider.get_multiple_latest_bars(
                        symbols=self.active_symbols,
                        timeframe="5"  # 5-minute bars (Massive free tier supports 5min, not 1min)
                    )

                    # Process each bar
                    for symbol, bar in bars.items():
                        if bar:
                            # Feed to bar aggregator (same as WebSocket flow)
                            aggregator = self.bar_aggregators.get(symbol)
                            if aggregator:
                                # Convert bar to trade format for aggregator
                                aggregator.add_trade(
                                    timestamp=bar['timestamp'],
                                    price=bar['close'],
                                    size=bar['volume']
                                )
                        else:
                            self.logger.warning(f"No bar data received for {symbol}")

                    # Wait before next poll
                    await asyncio.sleep(poll_interval)

                except Exception as e:
                    self.logger.error(f"Error during REST polling: {e}", exc_info=True)

                    # Try fallback to secondary provider
                    if self.secondary_provider and self.active_provider != self.secondary_provider:
                        await self._switch_to_secondary_provider()

                    # Wait before retry
                    await asyncio.sleep(30)

        except asyncio.CancelledError:
            self.logger.info("REST polling task cancelled")
        except Exception as e:
            self.logger.error(f"Fatal error in REST polling: {e}", exc_info=True)

    async def _switch_to_secondary_provider(self):
        """Switch from primary to secondary provider on failure."""
        if not self.secondary_provider:
            self.logger.error("No secondary provider available for fallback")
            return

        self.logger.warning(
            f"Switching from {self.active_provider.provider_name} "
            f"to {self.secondary_provider.provider_name}"
        )

        # Disconnect primary
        try:
            await self.active_provider.disconnect()
        except Exception as e:
            self.logger.error(f"Error disconnecting primary provider: {e}")

        # Switch to secondary
        self.active_provider = self.secondary_provider

        # Connect secondary
        try:
            await self.active_provider.connect()

            if self.active_provider.connected:
                self.logger.info(f"[OK] Successfully switched to {self.active_provider.provider_name}")

                # If WebSocket, subscribe to symbols
                if isinstance(self.active_provider, WebSocketDataProvider):
                    for symbol in self.active_symbols:
                        await self.active_provider.subscribe(symbol)
                    self.active_provider.on_trade(self._handle_realtime_trade)
                    self.active_provider.on_error(self._handle_provider_error)

                # If REST, polling loop will handle it automatically
                elif isinstance(self.active_provider, RESTDataProvider):
                    self.logger.info("REST provider - will continue polling")
            else:
                self.logger.error("Secondary provider connected but status not set")

        except Exception as e:
            self.logger.error(f"Failed to connect to secondary provider: {e}", exc_info=True)

    async def _handle_provider_error(self, error_data: dict):
        """Handle errors from real-time data provider."""
        error_type = error_data.get("type", "unknown")
        message = error_data.get("message", "Unknown error")

        self.logger.error(f"Provider error ({error_type}): {message}")

        # If critical error, try secondary provider
        critical_errors = ["connection_error", "auth_error", "rate_limit", "gap_detected"]
        if error_type in critical_errors and self.secondary_provider:
            await self._switch_to_secondary_provider()

    async def run(self) -> None:
        """Run main trading loop.

        Continuously checks market hours, fetches data, generates signals,
        and executes trades until shutdown is triggered.
        """
        await self.initialize()

        # Start health API server for Docker healthcheck
        self._health_server_task = await start_health_server_task(
            host="0.0.0.0",
            port=self.config.health_check_port
        )
        self.logger.info(f"Health API server started on port {self.config.health_check_port}")

        # Mark bot as alive for healthcheck
        set_health_state(is_alive=True)

        self._running = True
        self.logger.info("Trading loop started")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Check if we should send end-of-day summary
                    await self._check_and_send_daily_summary()

                    # Check if bot should be active (warm-up OR market open)
                    from vibe.trading_bot.utils.datetime_utils import get_market_now

                    if not self.market_scheduler.should_bot_be_active():
                        now = get_market_now(self.market_scheduler)

                        # If provider already disconnected, we've completed cooldown - just sleep until morning
                        if self.active_provider and not self.active_provider.connected:
                            # Calculate sleep time until next warm-up
                            # Use next_market_open() and subtract 5 min to ensure FUTURE time
                            next_open = self.market_scheduler.next_market_open()
                            target_time = next_open - timedelta(minutes=5)  # Warmup is 5 min before open

                            # Log once
                            if not self._market_closed_logged:
                                current_time = get_market_now(self.market_scheduler)
                                hours_until_warmup = (target_time - current_time).total_seconds() / 3600
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                                    f"({hours_until_warmup:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )
                                self._market_closed_logged = True

                            try:
                                sleep_seconds = (target_time - get_market_now(
                                    self.market_scheduler
                                )).total_seconds()
                                # Use shorter sleep in testing mode
                                max_sleep = 1 if self._testing_mode else 300
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, max_sleep)
                                )
                            except asyncio.TimeoutError:
                                pass
                            continue

                        # Run cooldown phase (process final data, disconnect provider)
                        await self.cooldown_manager.execute()

                        # If cooldown complete, sleep until next warmup
                        if self.cooldown_manager.is_cooldown_complete():
                            sleep_seconds = self.cooldown_manager.calculate_sleep_until_warmup()
                            # Use next_market_open() and subtract 5 min to ensure FUTURE time
                            next_open = self.market_scheduler.next_market_open()
                            target_time = next_open - timedelta(minutes=5)  # Warmup is 5 min before open

                            # Log sleep message once (avoid spam)
                            if sleep_seconds > 0 and self.cooldown_manager.should_log_sleep_message():
                                self.logger.info(
                                    f"Market closed, sleeping until warm-up at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                                    f"({sleep_seconds/3600:.1f} hours). "
                                    f"Checking for shutdown every 5 minutes."
                                )

                            try:
                                # Check for shutdown every 5 minutes (or 1s in testing mode)
                                max_sleep = 1 if self._testing_mode else 300
                                await asyncio.wait_for(
                                    self._shutdown_event.wait(),
                                    timeout=min(sleep_seconds, max_sleep)
                                )
                            except asyncio.TimeoutError:
                                pass

                        continue

                    # Bot is active - check if warm-up phase or trading
                    if self.market_scheduler.is_warmup_phase():
                        # Pre-market warm-up phase (9:25-9:30 AM)
                        self.logger.info("Entering pre-market warm-up phase...")

                        # Reset cooldown from previous day
                        self.cooldown_manager.reset()

                        await self.warmup_manager.execute()

                        # Sleep until market actually opens
                        market_open = self.market_scheduler.get_open_time()
                        if market_open:
                            now = get_market_now(self.market_scheduler)
                            sleep_until_open = (market_open - now).total_seconds()

                            if sleep_until_open > 0:
                                self.logger.info(
                                    f"Warm-up complete. Waiting {sleep_until_open:.0f}s "
                                    f"until market open at {market_open.strftime('%H:%M:%S')}..."
                                )
                                # Use shorter sleep in testing mode
                                max_sleep = 1 if self._testing_mode else sleep_until_open
                                await asyncio.sleep(max_sleep)

                        continue

                    elif self.market_scheduler.is_market_open():
                        # If bot started during market hours, run warmup (without Discord notification)
                        # Note: Warmup phase handles all state reset (bars, flags, stats, etc.)
                        if self.primary_provider and not self.primary_provider.connected:
                            self.logger.info("Bot started during market hours - running warmup phase...")
                            await self.warmup_manager.execute(send_notification=False)

                        # Run trading cycle
                        success = await self._trading_cycle()

                    # Update failure counter
                    if success:
                        self._consecutive_failures = 0
                    else:
                        self._consecutive_failures += 1

                    # Heartbeat
                    self.health_monitor.check_heartbeat()

                    # Calculate sleep interval with exponential backoff
                    sleep_interval = self._calculate_sleep_interval()

                    if self._consecutive_failures > 0:
                        self.logger.warning(
                            f"Consecutive failures: {self._consecutive_failures}, "
                            f"sleeping for {sleep_interval}s before retry"
                        )

                    await asyncio.sleep(sleep_interval)

                except asyncio.CancelledError:
                    self.logger.info("Trading loop cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in trading cycle: {e}", exc_info=True)
                    self.health_monitor.record_error("trading_cycle")
                    self._consecutive_failures += 1
                    # Use exponential backoff on errors too
                    backoff = min(5 * (2 ** min(self._consecutive_failures, 5)), 300)
                    self.logger.info(f"Backing off for {backoff}s after error")
                    await asyncio.sleep(backoff)

        finally:
            await self.shutdown()

    def _has_active_positions(self) -> bool:
        """Check if we have any active positions.

        Returns:
            True if any positions are open, False otherwise
        """
        try:
            positions = self.exchange.get_positions()
            return len(positions) > 0
        except Exception as e:
            self.logger.debug(f"Error checking positions: {e}")
            # On error, assume we have positions (conservative approach)
            return True

    def _log_strategy_events(
        self,
        symbol: str,
        signal_value: int,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Smart event-based logging for strategy evaluation.

        Only logs interesting events to avoid spam:
        - ORB establishment (once per day per symbol)
        - Price approaching breakout levels (within 0.5%)
        - Breakout detected but rejected by filters
        - Signal generated (handled by caller)

        Args:
            symbol: Trading symbol
            signal_value: Signal value (1=long, -1=short, 0=no signal)
            metadata: Strategy metadata dict
        """
        # Check if we have ORB data in metadata
        if "orb_high" not in metadata or "orb_low" not in metadata:
            return

        orb_high = metadata.get("orb_high", 0)
        orb_low = metadata.get("orb_low", 0)
        orb_range = metadata.get("orb_range", 0)
        current_price = metadata.get("current_price", 0)

        # Get current date for tracking
        from datetime import datetime
        current_date = datetime.now().date().isoformat()

        # Event 1: Log ORB establishment once per day per symbol
        if symbol not in self._orb_logged_today or self._orb_logged_today[symbol] != current_date:
            self.logger.info(
                f"[ORB] {symbol}: Opening range established "
                f"${orb_low:.2f}-${orb_high:.2f} (range: ${orb_range:.2f})"
            )
            self._orb_logged_today[symbol] = current_date

        # Event 2: Price approaching breakout (within 0.5%, but don't spam)
        price_position = metadata.get("price_position", "")
        distance_to_high = metadata.get("distance_to_high_pct", 100)
        distance_to_low = metadata.get("distance_to_low_pct", 100)

        # Approaching high breakout
        if price_position == "within_range" and 0 < distance_to_high < 0.5:
            # Only log if we haven't logged in the last 5 minutes (300 seconds)
            last_approach = self._last_approach_logged.get(f"{symbol}_high", 0)
            if datetime.now().timestamp() - last_approach > 300:
                self.logger.info(
                    f"[ORB] {symbol}: Price approaching HIGH breakout - "
                    f"Current: ${current_price:.2f}, Breakout: ${orb_high:.2f} "
                    f"({distance_to_high:.2f}% away)"
                )
                self._last_approach_logged[f"{symbol}_high"] = datetime.now().timestamp()

        # Approaching low breakout
        elif price_position == "within_range" and 0 < distance_to_low < 0.5:
            last_approach = self._last_approach_logged.get(f"{symbol}_low", 0)
            if datetime.now().timestamp() - last_approach > 300:
                self.logger.info(
                    f"[ORB] {symbol}: Price approaching LOW breakout - "
                    f"Current: ${current_price:.2f}, Breakout: ${orb_low:.2f} "
                    f"({distance_to_low:.2f}% away)"
                )
                self._last_approach_logged[f"{symbol}_low"] = datetime.now().timestamp()

        # Event 3: Breakout detected but rejected by filters
        if signal_value == 0 and price_position in ["above_high", "below_low"]:
            reason = metadata.get("reason", "unknown")
            reason_detail = metadata.get("reason_detail", "")

            # Only log interesting rejection reasons (not repetitive ones)
            interesting_reasons = {
                "after_entry_cutoff_time",
                "insufficient_volume",
                "position_already_open",
                "weak_breakout_candle",
            }

            if reason in interesting_reasons:
                # Avoid logging the same rejection multiple times
                last_rejection = self._last_approach_logged.get(f"{symbol}_rejection_{reason}", 0)
                if datetime.now().timestamp() - last_rejection > 600:  # 10 minutes
                    breakout_type = "HIGH" if price_position == "above_high" else "LOW"
                    detail_str = f" ({reason_detail})" if reason_detail else ""

                    self.logger.info(
                        f"[ORB] {symbol}: {breakout_type} breakout detected at ${current_price:.2f} "
                        f"but REJECTED - Reason: {reason}{detail_str}"
                    )
                    self._last_approach_logged[f"{symbol}_rejection_{reason}"] = datetime.now().timestamp()

    def _calculate_sleep_interval(self) -> int:
        """Calculate sleep interval with exponential backoff on failures.

        Optimizes interval based on position status:
        - With active positions: 60s (active monitoring)
        - No positions: 300s (idle, less frequent checks)

        Returns:
            Sleep interval in seconds
        """
        # Check if we're in failure/backoff mode
        if self._consecutive_failures > 0:
            # Check if we've exceeded max failures
            if self._consecutive_failures >= self._max_consecutive_failures:
                self.logger.error(
                    f"Exceeded max consecutive failures ({self._max_consecutive_failures}). "
                    f"Pausing for {self._max_backoff_seconds}s. "
                    "This may indicate a persistent issue (e.g., no market data available)."
                )
                return self._max_backoff_seconds

            # Exponential backoff: base * 2^failures, capped at max
            backoff = min(
                self._base_cycle_interval * (2 ** self._consecutive_failures),
                self._max_backoff_seconds
            )
            return int(backoff)

        # No failures - choose interval based on position status
        has_positions = self._has_active_positions()

        if has_positions:
            self.logger.debug(f"Active positions detected, using {self._base_cycle_interval}s interval")
            return self._base_cycle_interval
        else:
            self.logger.info(
                f"No active positions, using idle interval: {self._idle_cycle_interval}s "
                f"(checking for entry signals)"
            )
            return self._idle_cycle_interval

    async def _trading_cycle(self) -> bool:
        """Execute one trading cycle: fetch data, generate signals, execute trades.

        Returns:
            True if cycle completed successfully, False if data fetch failed
        """
        successful_fetches = 0
        try:
            # Start REST polling if needed and market is open
            if isinstance(self.active_provider, RESTDataProvider):
                market_open = self.market_scheduler.is_market_open()
                if market_open and (not self._polling_task or self._polling_task.done()):
                    self._polling_task = asyncio.create_task(self._start_rest_polling())
                    self.logger.info("Started REST API polling task")

            # 1. Fetch fresh data for all symbols
            # During market hours with a live provider active, don't use Yahoo Finance fallback
            # (Yahoo is 15-min delayed and would interfere with real-time data)
            market_open = self.market_scheduler.is_market_open()
            realtime_provider_active = bool(
                self.active_provider
                and self.active_provider.connected
                and self.active_provider.is_real_time
            )

            allow_yfinance = not (market_open and realtime_provider_active)

            if market_open and not allow_yfinance:
                self.logger.debug(
                    f"Market is open and {self.active_provider.provider_name} is active - relying on real-time data"
                )

            for symbol in self.active_symbols:
                try:
                    # Fetch historical data from Yahoo (includes yesterday + today with staleness check)
                    # During market hours with a live provider, disable yfinance fallback
                    bars = await self.data_manager.get_data(
                        symbol=symbol,
                        timeframe="5m",
                        days=1,
                        allow_yfinance_fallback=allow_yfinance,
                    )

                    if bars is None or bars.empty:
                        # Only log on first few failures, then reduce verbosity
                        if self._consecutive_failures < 3:
                            self.logger.warning(f"No bars fetched for {symbol}")
                        continue

                    # If we have real-time bars from a live provider, append them
                    if symbol in self._realtime_bars and not self._realtime_bars[symbol].empty:
                        realtime_bars = self._realtime_bars[symbol]

                        # Detect data gap between yfinance (delayed) and real-time provider data
                        if "timestamp" in bars.columns and not bars.empty:
                            import pytz
                            last_yf_bar = pd.to_datetime(bars.iloc[-1]["timestamp"])
                            first_rt_bar = pd.to_datetime(realtime_bars.iloc[0]["timestamp"])

                            # Ensure timezone awareness
                            if last_yf_bar.tzinfo is None:
                                last_yf_bar = pytz.utc.localize(last_yf_bar)
                            if first_rt_bar.tzinfo is None:
                                first_rt_bar = pytz.utc.localize(first_rt_bar)

                            gap_minutes = (first_rt_bar - last_yf_bar).total_seconds() / 60

                            # Expected gap is 5 minutes (one bar interval)
                            # If gap > 10 minutes, we're missing data
                            # Note: This happens when bot restarts during market hours due to yfinance 15-min delay.
                            # Consider keeping bot running or using Finnhub REST API for backfill.
                            if gap_minutes > 10:
                                self.logger.warning(
                                    f"[DATA GAP] {symbol}: {gap_minutes:.1f} minute gap between "
                                    f"yfinance (last: {last_yf_bar.strftime('%H:%M:%S')}) and "
                                    f"realtime (first: {first_rt_bar.strftime('%H:%M:%S')})"
                                )

                        # Combine historical (Yahoo) + real-time provider bars
                        bars = pd.concat([bars, realtime_bars], ignore_index=True)

                        # Remove duplicates based on timestamp (prefer real-time data)
                        if "timestamp" in bars.columns:
                            bars = bars.drop_duplicates(subset=["timestamp"], keep="last")
                            bars = bars.sort_values("timestamp").reset_index(drop=True)

                            # CRITICAL: Ensure timestamp is datetime (fixes dtype issues from stale cache)
                            if not pd.api.types.is_datetime64_any_dtype(bars["timestamp"]):
                                self.logger.warning(
                                    f"[DTYPE FIX] {symbol}: timestamp column is {bars['timestamp'].dtype}, converting to datetime"
                                )
                                bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)

                        self.logger.info(
                            f"[HYBRID DATA] {symbol}: Combined {len(bars) - len(realtime_bars)} "
                            f"yfinance bars + {len(realtime_bars)} real-time provider bars"
                        )

                    # Successfully fetched data
                    successful_fetches += 1

                    # 2. Calculate technical indicators (ATR required for strategy)
                    try:
                        # Calculate ATR_14 using the incremental indicator engine
                        self.logger.info(f"Calculating indicators for {symbol} ({len(bars)} bars)...")

                        bars = self.indicator_engine.update(
                            df=bars,
                            start_idx=0,
                            indicators=[{"name": "atr", "params": {"length": 14}}],
                            symbol=symbol,
                            timeframe="5m",
                        )

                        if "ATR_14" in bars.columns:
                            # Count how many bars have valid ATR (non-null)
                            valid_atr_count = bars["ATR_14"].notna().sum()
                            self.logger.info(
                                f"ATR_14 calculated for {symbol}: {valid_atr_count}/{len(bars)} bars have valid values"
                            )
                        else:
                            self.logger.error(
                                f"CRITICAL: ATR_14 column not added to DataFrame for {symbol}! "
                                f"Strategy evaluation will fail."
                            )
                            self.health_monitor.record_error(f"indicators_{symbol}")

                    except Exception as e:
                        # DO NOT CATCH SILENTLY - Log as ERROR with traceback
                        self.logger.error(
                            f"CRITICAL: Failed to calculate indicators for {symbol}: {e}",
                            exc_info=True
                        )
                        self.health_monitor.record_error(f"indicators_{symbol}")
                        # Continue without indicators - strategy will return early with error

                    # 3. Generate signals using incremental method (for real-time trading)
                    if bars.empty or len(bars) == 0:
                        self.logger.debug(f"Empty bars for {symbol}, skipping signal generation")
                        continue

                    # Get the latest bar for incremental signal generation
                    current_bar = bars.iloc[-1].to_dict()
                    self._latest_bar_prices[symbol] = float(current_bar.get("close", 0.0))

                    # Generate signal for current bar with historical context
                    signal_value, signal_metadata = self.strategy.generate_signal_incremental(
                        symbol=symbol,
                        current_bar=current_bar,
                        df_context=bars,
                    )

                    # Log strategy evaluation issues at INFO level for visibility
                    reason = signal_metadata.get('reason', None)
                    if reason == 'insufficient_data':
                        self.logger.warning(
                            f"[STRATEGY] {symbol}: Insufficient data - "
                            f"missing ATR_14 or empty context. Strategy cannot evaluate."
                        )
                    elif reason and signal_value == 0:
                        # Log no-signal reasons at info level for visibility
                        self.logger.info(
                            f"[STRATEGY] {symbol}: No signal - {reason}"
                        )

                    # Smart logging based on metadata
                    self._log_strategy_events(symbol, signal_value, signal_metadata)

                    # Update daily statistics
                    self._update_daily_stats(symbol, signal_value, signal_metadata)

                    # 4. Execute trade if we have a signal
                    if signal_value != 0:  # 1=long, -1=short, 0=no signal
                        broker_position = await self.exchange.get_position(symbol)
                        if broker_position is not None and broker_position.quantity != 0:
                            self.logger.warning(
                                f"[STRATEGY] {symbol}: No signal - carryover_position_active "
                                f"({broker_position.side} {broker_position.quantity}). "
                                "New entries are blocked until the position is flattened."
                            )
                            continue

                        tp = signal_metadata.get('take_profit')
                        rr = signal_metadata.get('risk_reward')
                        tp_str = f"${tp:.2f}" if tp is not None else "none"
                        rr_str = f"{rr:.1f}" if rr is not None else "n/a"
                        self.logger.info(
                            f"[SIGNAL] {symbol}: {signal_metadata.get('signal', 'unknown').upper()} at "
                            f"${signal_metadata.get('current_price', 0):.2f} "
                            f"(ORB: ${signal_metadata.get('orb_low', 0):.2f}-${signal_metadata.get('orb_high', 0):.2f}, "
                            f"TP: {tp_str}, "
                            f"SL: ${signal_metadata.get('stop_loss', 0):.2f}, "
                            f"R/R: {rr_str})"
                        )
                        # Execute trade via TradeExecutor
                        try:
                            entry_price = signal_metadata.get('current_price', 0.0)
                            stop_price = signal_metadata.get('stop_loss', 0.0)
                            stop_distance = abs(entry_price - stop_price) if stop_price else 0
                            risk_pct = 0.01
                            max_shares = None
                            max_position_pct = None
                            if self.ruleset and self.ruleset.position_size:
                                risk_pct = self.ruleset.position_size.value
                                max_shares = self.ruleset.position_size.max_shares
                                max_position_pct = self.ruleset.position_size.max_position_pct
                            risk_amount = self.config.trading.initial_capital * risk_pct
                            risk_shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
                            est_shares = risk_shares
                            cap_details = []
                            if max_shares is not None and est_shares > max_shares:
                                est_shares = max_shares
                                cap_details.append(f"max_shares={max_shares}")
                            if max_position_pct is not None and entry_price > 0:
                                max_notional_shares = int(
                                    (self.config.trading.initial_capital * max_position_pct) / entry_price
                                )
                                if est_shares > max_notional_shares:
                                    est_shares = max_notional_shares
                                    cap_details.append(f"max_position={max_position_pct * 100:.0f}%")
                            cap_msg = f", caps={','.join(cap_details)}" if cap_details else ""
                            self.logger.info(
                                f"[SIZING] {symbol}: risk={risk_pct*100:.1f}% (${risk_amount:.0f}), "
                                f"stop_distance=${stop_distance:.2f}, risk_shares={risk_shares}, "
                                f"est_shares={est_shares}{cap_msg}, "
                                f"est_cost=${est_shares * entry_price:.0f}"
                            )
                            result = await self.trade_executor.execute_signal(
                                symbol=symbol,
                                signal=signal_value,
                                entry_price=entry_price,
                                stop_price=stop_price,
                                take_profit=signal_metadata.get('take_profit'),
                                strategy_name=self.strategy.config.name,
                            )

                            if result.success:
                                # Use actual fill price from exchange (includes slippage).
                                # Falls back to bar close price if avg_price unavailable.
                                actual_fill_price = result.avg_price if result.avg_price > 0 else entry_price
                                slippage_dollars = (actual_fill_price - entry_price) * int(result.position_size)
                                commission_est = actual_fill_price * int(result.position_size) * 0.001
                                position_cost = actual_fill_price * int(result.position_size)
                                self.logger.info(
                                    f"[TRADE ENTRY] {symbol}: {int(result.position_size)} shares | "
                                    f"Bar: ${entry_price:.2f} | Fill: ${actual_fill_price:.2f} | "
                                    f"Slippage: ${slippage_dollars:+.2f} | Commission: ~${commission_est:.2f} | "
                                    f"Position cost: ${position_cost:.2f}"
                                )
                                self._daily_stats["trades_executed"] += 1

                                from vibe.trading_bot.utils.datetime_utils import get_market_now
                                entry_time = get_market_now(self.market_scheduler)
                                await self._persist_dashboard_trade_entry(
                                    symbol=symbol,
                                    order_id=result.order_id,
                                    signal_value=signal_value,
                                    quantity=result.position_size,
                                    entry_price=actual_fill_price,
                                    entry_time=entry_time,
                                )

                                # Log account state after fill to track cash/equity impact
                                try:
                                    _acct = await self.exchange.get_account()
                                    self.logger.info(
                                        f"[ACCOUNT] After entry: Cash=${_acct.cash:.2f} | "
                                        f"Equity=${_acct.equity:.2f} | "
                                        f"P&L vs start: ${_acct.equity - self.config.trading.initial_capital:+.2f}"
                                    )
                                except Exception as _acct_err:
                                    self.logger.warning(f"Could not read account state after entry: {_acct_err}")

                                # Wire position into strategy tracking using actual fill price
                                trade_side = "buy" if signal_value == 1 else "sell"
                                self.strategy.track_position(
                                    symbol=symbol,
                                    side=trade_side,
                                    entry_price=actual_fill_price,
                                    take_profit=signal_metadata.get("take_profit"),
                                    stop_loss=stop_price,
                                    timestamp=entry_time,
                                    quantity=result.position_size,
                                    trailing_stop=(
                                        self.ruleset.exit.trailing_stop.model_dump()
                                        if self.ruleset and self.ruleset.exit.trailing_stop
                                        else None
                                    ),
                                )
                                if hasattr(self.strategy, "mark_traded_today"):
                                    trading_date = signal_metadata.get("orb_trading_date") or entry_time.date()
                                    self.strategy.mark_traded_today(symbol, trading_date)

                            else:
                                self.logger.warning(
                                    f"[TRADE] {symbol}: Execution failed — {result.reason}"
                                )

                        except Exception as e:
                            self.logger.error(
                                f"Failed to execute signal for {symbol}: {e}",
                                exc_info=True
                            )
                            self.health_monitor.record_error("execution")

                except Exception as e:
                    # Only log full traceback on first few failures
                    if self._consecutive_failures < 3:
                        self.logger.error(f"Error processing {symbol}: {e}", exc_info=True)
                    else:
                        self.logger.debug(f"Error processing {symbol}: {e}")
                    self.health_monitor.record_error(f"data_{symbol}")

            # Monitor open positions for stop-loss, take-profit, and EOD exits
            await self._monitor_open_positions()

            # Check if ORB notification should be sent (after all symbols evaluated)
            await self._check_and_send_orb_notification()

            # Flush any bars that crossed time boundaries (quiet market handling)
            # This is the TIME-TRIGGERED completion path (complements trade-triggered)
            await self._flush_elapsed_bars()

            # Persist account/position snapshot at the trading-cycle polling cadence.
            await self._persist_dashboard_account_and_positions(reason="poll")

            # Return success if we got data for at least one symbol
            return successful_fetches > 0

        except Exception as e:
            self.logger.error(f"Trading cycle error: {e}", exc_info=True)
            return False

    async def _monitor_open_positions(self) -> None:
        """Check all open positions for stop-loss, take-profit, and EOD exit triggers.

        Called every trading cycle. Uses current bar prices captured during the cycle.
        """
        if not self.strategy or not self.strategy.positions:
            return

        from vibe.trading_bot.utils.datetime_utils import get_market_now

        now = get_market_now(self.market_scheduler)
        bar_time_str = now.strftime("%H:%M")

        # Get EOD exit time from ruleset (default 15:55)
        eod_time_str = "15:55"
        if self.ruleset and self.ruleset.exit.eod_time:
            eod_time_str = self.ruleset.exit.eod_time

        for symbol in list(self.strategy.positions.keys()):
            current_price = self._latest_bar_prices.get(symbol)
            if not current_price:
                continue

            pos = self.strategy.get_position(symbol)
            if pos is None:
                continue

            exit_signal = self.strategy.check_exit_conditions(
                symbol=symbol,
                current_price=current_price,
                current_time=bar_time_str,
                market_close=eod_time_str,
            )

            if exit_signal is None:
                # Log position status
                entry = pos["entry_price"]
                pnl_pct = ((current_price - entry) / entry * 100) if pos["side"] == "buy" \
                    else ((entry - current_price) / entry * 100)
                self.logger.info(
                    f"[POSITION] {symbol} {pos['side'].upper()} @ ${entry:.2f} | "
                    f"Current: ${current_price:.2f} | P&L: {pnl_pct:+.2f}% | "
                    f"SL: ${pos['stop_loss']:.2f}"
                    + (f" | TP: ${pos['take_profit']:.2f}" if pos.get("take_profit") else " | TP: EOD")
                )
                continue

            self.logger.info(
                f"[EXIT TRIGGERED] {symbol}: {exit_signal.exit_type.upper()} — {exit_signal.reason}"
            )
            await self._close_position_with_notification(symbol, pos, current_price, exit_signal.exit_type)

    async def _on_order_created_notification(self, order_id: str) -> None:
        """Send ORDER_SENT Discord notification when OrderManager submits an order."""
        if (
            not self.config.notifications.discord_webhook_url
            or not getattr(self.config.notifications, "notify_routine", True)
            or not self.config.notifications.notify_on_trade
        ):
            return
        try:
            from vibe.trading_bot.utils.datetime_utils import get_market_now
            order = await self.exchange.get_order(order_id)
            if order is None:
                return
            payload = OrderNotificationPayload(
                event_type="ORDER_SENT",
                timestamp=get_market_now(self.market_scheduler),
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                strategy_name=self.strategy.config.name if self.strategy else "unknown",
                order_price=order.price,
                exchange="PAPER",
            )
            async with discord_notification_context(
                self.config.notifications.discord_webhook_url
            ) as notifier:
                await notifier.send_order_event(payload)
        except Exception as e:
            self.logger.error(f"Failed to send ORDER_SENT notification: {e}", exc_info=True)

    async def _on_order_created(self, order_id: str) -> None:
        """Record and notify ORDER_SENT events."""
        await self._record_dashboard_order_event("ORDER_SENT", order_id)
        await self._on_order_created_notification(order_id)

    async def _on_order_filled_notification(self, order_id: str) -> None:
        """Send ORDER_FILLED Discord notification when OrderManager detects a fill."""
        if (
            not self.config.notifications.discord_webhook_url
            or not getattr(self.config.notifications, "notify_routine", True)
            or not self.config.notifications.notify_on_trade
        ):
            return
        try:
            from vibe.trading_bot.utils.datetime_utils import get_market_now
            order = await self.exchange.get_order(order_id)
            if order is None:
                return
            payload = OrderNotificationPayload(
                event_type="ORDER_FILLED",
                timestamp=get_market_now(self.market_scheduler),
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                strategy_name=self.strategy.config.name if self.strategy else "unknown",
                fill_price=order.avg_price if order.avg_price > 0 else order.price,
                filled_quantity=order.filled_qty if order.filled_qty > 0 else order.quantity,
                order_price=order.price,
                exchange="PAPER",
            )
            async with discord_notification_context(
                self.config.notifications.discord_webhook_url
            ) as notifier:
                await notifier.send_order_event(payload)
        except Exception as e:
            self.logger.error(f"Failed to send ORDER_FILLED notification: {e}", exc_info=True)

    async def _on_order_filled(self, order_id: str) -> None:
        """Record and notify ORDER_FILLED events."""
        await self._record_dashboard_order_event("ORDER_FILLED", order_id)
        order = await self.exchange.get_order(order_id)
        if order is not None and self.trade_executor is not None:
            await self.trade_executor.refresh_open_trade(order.symbol, order_id)
        if order is not None:
            await self._sync_open_trade_after_entry_fill(order)
            await self._sync_open_trade_after_exit_fill(order)
        await self._persist_dashboard_account_and_positions(reason="order_filled")
        await self._on_order_filled_notification(order_id)

    async def _on_order_cancelled(self, order_id: str) -> None:
        """Record ORDER_CANCELLED events."""
        if self.trade_executor is not None:
            self.trade_executor.clear_pending_entry(order_id)
            order = await self.exchange.get_order(order_id)
            if order is not None:
                self.trade_executor.clear_pending_close(symbol=order.symbol)
        await self._record_dashboard_order_event("ORDER_CANCELLED", order_id)

    async def _close_position_with_notification(
        self,
        symbol: str,
        pos: dict,
        current_price: float,
        exit_reason: str,
    ) -> None:
        """Close a position and send TRADE_CLOSED Discord notification."""
        from vibe.trading_bot.utils.datetime_utils import get_market_now

        self._pending_exit_reasons[symbol] = exit_reason
        close_result = await self.trade_executor._close_position(
            symbol,
            exit_reason=exit_reason,
        )

        if not close_result.success:
            # An accepted zero-fill order remains asynchronous and still owns
            # this reason. Only a pre-submission failure has no order ID.
            if not close_result.order_id:
                self._pending_exit_reasons.pop(symbol, None)
            self.logger.warning(f"[EXIT FAILED] {symbol}: {close_result.reason}")
            return

        entry_price = pos["entry_price"]  # actual fill price (set by track_position since v1.4.5)
        quantity = abs(int(close_result.position_size)) if close_result.position_size else 0

        # Use actual exit fill price from exchange (includes slippage).
        # Falls back to bar close price if unavailable.
        actual_exit_price = close_result.avg_price if close_result.avg_price > 0 else current_price
        exit_order = await self.exchange.get_order(close_result.order_id)
        benchmark_price = (
            getattr(exit_order, "benchmark_price", None)
            if exit_order is not None
            else None
        )
        close_side = "sell" if pos["side"] == "buy" else "buy"
        if benchmark_price is None:
            slippage_dollars_exit = None
        else:
            per_share_slippage = (
                actual_exit_price - benchmark_price
                if close_side == "buy"
                else benchmark_price - actual_exit_price
            )
            slippage_dollars_exit = per_share_slippage * quantity
        commission_est_exit = getattr(exit_order, "commission", 0.0) if exit_order is not None else 0.0

        pnl_per_share = (actual_exit_price - entry_price) if pos["side"] == "buy" \
            else (entry_price - actual_exit_price)
        pnl_total = pnl_per_share * quantity
        pnl_pct = (pnl_per_share / entry_price) * 100 if entry_price > 0 else 0.0

        lifecycle_label = "TRADE CLOSED" if close_result.fully_closed else "TRADE PARTIALLY CLOSED"
        self.logger.info(
            f"[{lifecycle_label}] {symbol}: {exit_reason} | "
            f"Bar: ${current_price:.2f} | Fill: ${actual_exit_price:.2f} | "
            f"Entry (fill): ${entry_price:.2f} | {quantity} shares | "
            f"Slippage: {slippage_dollars_exit if slippage_dollars_exit is not None else 'unavailable'} | "
            f"Commission: {commission_est_exit:.2f} | "
            f"P&L (fills): ${pnl_total:+.2f} ({pnl_pct:+.2f}%)"
        )

        # Log account state immediately after exit for full audit trail
        try:
            _acct = await self.exchange.get_account()
            self.logger.info(
                f"[ACCOUNT] After exit: Cash=${_acct.cash:.2f} | "
                f"Equity=${_acct.equity:.2f} | "
                f"P&L vs start: ${_acct.equity - self.config.trading.initial_capital:+.2f}"
            )
        except Exception as _acct_err:
            self.logger.warning(f"Could not read account state after exit: {_acct_err}")

        await self._persist_dashboard_trade_exit(
            symbol=symbol,
            order_id=close_result.order_id,
            exit_price=actual_exit_price,
            exit_time=get_market_now(self.market_scheduler),
            exit_reason=exit_reason,
            filled_quantity=quantity,
            remaining_quantity=close_result.remaining_position_size,
        )
        await self._persist_dashboard_account_and_positions(
            reason="trade_closed" if close_result.fully_closed else "partial_close"
        )

        if not close_result.fully_closed:
            pos["quantity"] = close_result.remaining_position_size
            self.logger.info(
                "[POSITION RETAINED] %s: %s shares remain open",
                symbol,
                close_result.remaining_position_size,
            )
            return

        # Remove strategy state only after the broker confirms the position is flat.
        self.strategy.close_position(symbol)
        self._pending_exit_reasons.pop(symbol, None)

        # Send TRADE_CLOSED notification with actual fill-based P&L
        if (
            self.config.notifications.discord_webhook_url
            and getattr(self.config.notifications, "notify_routine", True)
            and self.config.notifications.notify_on_trade
        ):
            now = get_market_now(self.market_scheduler)
            try:
                async with discord_notification_context(
                    self.config.notifications.discord_webhook_url
                ) as notifier:
                    await notifier.send_trade_closed(
                        TradeClosedPayload(
                            event_type="TRADE_CLOSED",
                            timestamp=now,
                            symbol=symbol,
                            strategy_name=self.strategy.config.name,
                            side=pos["side"],
                            entry_price=entry_price,
                            exit_price=actual_exit_price,
                            quantity=quantity,
                            pnl_total=pnl_total,
                            pnl_pct=pnl_pct,
                            exit_reason=exit_reason,
                            version=BUILD_VERSION,
                        )
                    )
            except Exception as e:
                self.logger.error(f"Failed to send TRADE_CLOSED notification: {e}", exc_info=True)

    async def _execute_signal(self, signal: Any) -> None:
        """Execute a single trade signal.

        Args:
            signal: Trade signal from strategy
        """
        try:
            # Execute the trade
            result = await self.trade_executor.execute(
                signal=signal,
                account=None,  # Use default
            )

            if result.success:
                self.logger.info(f"Trade executed: {signal.symbol} {result.reason}")
            else:
                self.logger.warning(f"Trade execution failed: {result.reason}")

        except Exception as e:
            self.logger.error(f"Signal execution error: {e}", exc_info=True)

    async def shutdown(self) -> None:
        """Gracefully shutdown all components.

        Closes positions, syncs data, and cleans up resources.
        """
        if not self._running:
            return

        self.logger.info("Initiating graceful shutdown...")
        self._running = False
        self._shutdown_event.set()

        try:
            # Cancel main task if still running
            if self._main_task and not self._main_task.done():
                self._main_task.cancel()
                try:
                    await asyncio.wait_for(
                        self._main_task,
                        timeout=self.config.shutdown_timeout_seconds
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    self.logger.warning("Main task did not complete gracefully")

            # Cancel polling task if running
            if self._polling_task and not self._polling_task.done():
                self._polling_task.cancel()
                try:
                    await self._polling_task
                except asyncio.CancelledError:
                    self.logger.debug("Polling task cancelled")
                except Exception as e:
                    self.logger.error(f"Error cancelling polling task: {e}")

            # Disconnect from data providers
            try:
                if self.remote_data_publisher is not None:
                    await self.remote_data_publisher.stop()
                    self.logger.info("Dashboard RemoteDataPublisher stopped")
            except Exception as e:
                self.logger.error(f"Error stopping dashboard publisher: {e}")

            # Disconnect from data providers
            try:
                if self.active_provider:
                    await self.active_provider.disconnect()
                    self.logger.info(f"Disconnected from {self.active_provider.provider_name}")
            except Exception as e:
                self.logger.error(f"Error disconnecting active provider: {e}")

            try:
                if self.secondary_provider and self.secondary_provider != self.active_provider:
                    await self.secondary_provider.disconnect()
                    self.logger.info(f"Disconnected from {self.secondary_provider.provider_name}")
            except Exception as e:
                self.logger.error(f"Error disconnecting secondary provider: {e}")

            # Update health state before shutdown
            set_health_state(is_alive=False, websocket_connected=False)

            # Stop health server
            if self._health_server_task and not self._health_server_task.done():
                self._health_server_task.cancel()
                try:
                    await self._health_server_task
                except asyncio.CancelledError:
                    self.logger.debug("Health server task cancelled")
                except Exception as e:
                    self.logger.error(f"Error stopping health server: {e}")

            # Close components
            try:
                await self.exchange.close()
            except Exception as e:
                self.logger.error(f"Error closing exchange: {e}")

            if self.trade_store:
                self.trade_store.close()

            self.logger.info("Graceful shutdown complete")

        except Exception as e:
            self.logger.error(f"Shutdown error: {e}", exc_info=True)

    def get_health(self) -> Dict[str, Any]:
        """Get current system health status."""
        return self.health_monitor.get_health()

    def get_status(self) -> Dict[str, Any]:
        """Get trading bot status."""
        return {
            "running": self._running,
            "market_open": self.market_scheduler.is_market_open(),
            "health": self.health_monitor.get_status_summary(),
        }

    async def run_backtest(
        self,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """Run backtest for a date range (placeholder for e2e testing).

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            Backtest results
        """
        await self.initialize()

        self.logger.info(f"Running backtest from {start_date} to {end_date}")

        # This is a placeholder - full backtest would be more complex
        # For now, just run a few iterations of the trading cycle
        try:
            for _ in range(5):
                if self._shutdown_event.is_set():
                    break
                await self._trading_cycle()

            return {
                "start_date": start_date,
                "end_date": end_date,
                "status": "completed",
                "trades": self.trade_store.get_trades(limit=100),
            }

        except Exception as e:
            self.logger.error(f"Backtest error: {e}", exc_info=True)
            return {
                "start_date": start_date,
                "end_date": end_date,
                "status": "failed",
                "error": str(e),
            }
        finally:
            await self.shutdown()
