"""
Dark Trading Orchestrator for Paper Trading Simulation

This module provides a trading orchestrator that simulates minute-by-minute live trading
for quick manual paper tests.
"""

import time
import uuid
import pandas as pd
import csv
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, time as dt_time, timedelta
import pytz

from src.exchange.base import Exchange
from src.strategies.base import StrategyBase
from src.risk_management.base import RiskManagement
from src.data.cache import CacheDataLoader
from src.data.replay_cache import DataReplayCacheDataLoader
from src.indicators import IndicatorFactory
from src.core.trade_manager import TradeManager
from src.config.orchestrator_config import MarketHoursConfig, OrchestratorConfig, DataReplayConfig
from src.utils.logger import get_logger
from src.config.columns import TradeColumns

# Configure module-specific logger using project pattern
logger = get_logger("DarkTradingOrchestrator")


class DarkTradingOrchestrator:
    """Dark trading orchestrator / data replay controller.

    Responsibilities:
      * Fetch latest incremental data (live or replay).
      * Apply indicators and generate strategy signals.
      * Convert signals into orders using TradeManager sizing & risk.
      * Submit orders to exchange (or simulate if dry_run).
      * Track positions & account equity, writing periodic logs.
      * Manage lifecycle: start() loop, manual stop(), optional auto-stop after close.
    """

    def __init__(
        self,
        strategy: StrategyBase,
        risk_manager: RiskManagement,
        trade_manager: TradeManager,
        exchange: Exchange,
        data_fetcher: CacheDataLoader,
        tickers: List[str],
        market_hours: Optional[MarketHoursConfig] = None,
        orchestrator_cfg: Optional[OrchestratorConfig] = None,
        replay_cfg: Optional[DataReplayConfig] = None,
        results_dir: str = "results",
        run_id: Optional[str] = None,
        callbacks: Optional[Dict[str, Callable]] = None,
    ) -> None:
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.trade_manager = trade_manager
        self.exchange = exchange
        # In replay mode we expect caller to supply a DataReplayCacheDataLoader
        if replay_cfg and replay_cfg.enabled:
            if not isinstance(data_fetcher, DataReplayCacheDataLoader):
                raise TypeError("Replay mode enabled but data_fetcher is not a DataReplayCacheDataLoader.")
        self.data_fetcher = data_fetcher
        self.tickers = tickers
        self.market_hours = market_hours or MarketHoursConfig()
        self.cfg = orchestrator_cfg or OrchestratorConfig(initial_capital=trade_manager.initial_capital)
        self.replay_cfg = replay_cfg or DataReplayConfig(enabled=False)
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.callbacks = callbacks or {}
        # Inject market hours into strategy for EOD-aware exit logic (Fix A)
        try:
            if hasattr(self.strategy, 'market_hours'):
                self.strategy.market_hours = self.market_hours
        except Exception:
            pass

        # Internal state
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.running: bool = False
        self.last_run_time: Optional[datetime] = None
        self.tick_count: int = 0
        self.orders_executed_this_cycle: int = 0
        # Track most recent data timestamp (for replay to record simulated time instead of wall-clock)
        self.last_data_timestamp: Optional[pd.Timestamp] = None
        self.auto_stop_at: Optional[datetime] = None

        # Results tracking
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.results_dir / f"{self.run_id}_trades.csv"
        self.equity_file = self.results_dir / f"{self.run_id}_equity.csv"

        self._init_results_files()
        self._configure_auto_stop()
        # Persistent per-ticker stateless strategy contexts (tracks per-day gating)
        self._strategy_ctx_map: Dict[str, Dict[str, Any]] = {}

    def _init_results_files(self) -> None:
        """Initialize result CSV files with headers."""
        # Trades CSV
        with open(self.trades_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'run_id', 'timestamp', 'ticker', 'side', 'qty',
                'price', 'commission', 'pnl', 'order_id', 'order_status'
            ])

        # Equity CSV
        with open(self.equity_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'cash', 'positions_value', 'total_equity'
            ])

        # Signal diagnostics CSV (for troubleshooting sizing / skipped signals)
        self.signal_diag_file = self.results_dir / f"{self.run_id}_signal_diagnostics.csv"
        with open(self.signal_diag_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'run_id', 'bar_time', 'ticker', 'open', 'high', 'low', 'close', 'volume',
                'signal', 'direction', 'size_float', 'size_int', 'skip_reason', 'stop_loss', 'exit_flag',
                'available_funds', 'account_cash', 'account_equity'
            ])

    def is_market_open(self) -> bool:
        """
        Check if the market is currently open based on market_hours config.

        Returns:
            bool: True if market is open, False otherwise
        """
        # Replay mode may choose to ignore market hours entirely for fast simulation
        if self.replay_cfg.enabled and getattr(self.replay_cfg, 'ignore_market_hours', False):
            return True
        # If no market hours defined, assume 24/7 market
        if not self.market_hours:
            return True
        tz = pytz.timezone(self.market_hours.timezone)
        now = datetime.now(tz)
        if not self.market_hours.should_trade_today(now.weekday()):
            return False
        current_time = now.time()
        return self.market_hours.open_time <= current_time <= self.market_hours.close_time

    def _configure_auto_stop(self) -> None:
        """Compute auto-stop timestamp for today if configured."""
        if not self.market_hours.auto_stop_grace:
            return
        tz = pytz.timezone(self.market_hours.timezone)
        now = datetime.now(tz)
        close_dt = datetime.combine(now.date(), self.market_hours.close_time)
        close_dt = tz.localize(close_dt)
        self.auto_stop_at = close_dt + self.market_hours.auto_stop_grace

    def _fetch_latest_data(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch the latest data for all tickers.

        Returns:
            dict: ticker -> DataFrame with latest minute bar
        """
        latest_data = {}
        fetch_start = time.time()
        fetched_tickers = []

        for ticker in self.tickers:
            try:
                timeframe = self.replay_cfg.timeframe if self.replay_cfg.enabled else "5m"
                # For replay mode we rely on custom loader behavior
                # TODO: probably need to specify start and end during paper trading
                df = self.data_fetcher.fetch(
                    ticker,
                    timeframe=timeframe,
                    start=None,
                    end=None,
                )

                if df is not None and not df.empty:
                    latest_data[ticker] = df
                    fetched_tickers.append(ticker)

                    # Update data cache
                    if ticker not in self.data_cache:
                        self.data_cache[ticker] = df
                        logger.debug("data.cache.init", extra={"meta": {"ticker": ticker, "rows": len(df), "lastTimestamp": str(df.index[-1])}})
                    else:
                        # Append new data if not already present
                        last_cached_time = self.data_cache[ticker].index[-1]
                        new_data = df[df.index > last_cached_time]
                        if not new_data.empty:
                            self.data_cache[ticker] = pd.concat([self.data_cache[ticker], new_data])
                            logger.debug("data.cache.extend", extra={"meta": {"ticker": ticker, "new_rows": len(new_data), "lastTimestamp": str(new_data.index[-1])}})

            except Exception as e:
                logger.error("data.fetch.error", extra={"meta": {"ticker": ticker, "error": str(e)}})
                raise e

        logger.info(
            "data.fetch.summary",
            extra={
                "meta": {
                    "tickers": fetched_tickers,
                    "count": len(fetched_tickers),
                    "duration_ms": int((time.time() - fetch_start) * 1000)
                }
            }
        )
        # Update last_data_timestamp for replay / simulated timekeeping
        if latest_data:
            try:
                self.last_data_timestamp = max(df.index.max() for df in latest_data.values() if not df.empty)
            except Exception:
                pass
        return latest_data

    def _update_exchange_prices(self, latest_data: Dict[str, pd.DataFrame]) -> None:
        """
        Update the exchange with latest price data.

        Args:
            latest_data: Dictionary of ticker -> DataFrame with latest prices
        """
        # Check if the exchange implementation has update_market_data method
        if hasattr(self.exchange, "update_market_data"):
            # This is specific to MockExchange implementation
            self.exchange.update_market_data(latest_data)
        else:
            # Log that the exchange doesn't support updating market data
            logger.warning("Exchange implementation does not support updating market data")

    def _process_signals(self) -> None:
        """
        Generate and process trading signals for each ticker.
        """
        signal_cycle_meta = {"evaluated": 0, "with_signal": 0}
        for ticker, df in self.data_cache.items():
            if df.empty:
                continue

            try:
                # Add necessary indicators for the strategy
                # TODO: we need a per tick calculation of indicators
                # if hasattr(IndicatorFactory, 'apply'):
                #     # Apply indicators based on strategy configuration
                #     if hasattr(self.strategy, 'strategy_config') and self.strategy.strategy_config:
                        # indicators = []
                        # # Add ATR for risk management
                        # indicators.append({'name': 'atr', 'params': {'length': 14}})

                        # # Add ORB levels if using ORB strategy
                        # if hasattr(self.strategy, 'breakout_window'):
                        #     # This is likely an ORB strategy
                        #     start_time = getattr(self.strategy.strategy_config.orb_config, 'start_time', "09:30")
                        #     duration = getattr(self.strategy.strategy_config.orb_config, 'timeframe', 5)
                        #     body_pct = getattr(self.strategy.strategy_config.orb_config, 'body_breakout_percentage', 0.5)

                        #     indicators.append({
                        #         'name': 'orb_levels',
                        #         'params': {
                        #             'start_time': start_time,
                        #             'duration_minutes': int(duration),
                        #             'body_pct': float(body_pct)
                        #         }
                        #     })

                        # df = IndicatorFactory.apply(df, indicators)
                        # logger.debug("indicators.applied", extra={"meta": {"ticker": ticker, "indicators": [i['name'] for i in indicators]}})

                # Incremental signal generation (stateless context path preferred)
                latest_bar = df.iloc[-1]
                from src.config.columns import TradeColumns
                tm_pos = self.trade_manager.current_positions.get(ticker)
                existing_ctx = self._strategy_ctx_map.get(ticker)
                position_ctx = None
                if tm_pos:
                    direction = tm_pos.get(TradeColumns.DIRECTION.value, 0)
                    entry_price = tm_pos.get(TradeColumns.ENTRY_PRICE.value)
                    take_profit = tm_pos.get(TradeColumns.TAKE_PROFIT.value)
                    initial_stop = tm_pos.get(TradeColumns.STOP_LOSS.value)
                    entry_time = tm_pos.get(TradeColumns.ENTRY_TIME.value)
                    entry_day = None
                    try:
                        if entry_time is not None and hasattr(entry_time, 'date'):
                            entry_day = entry_time.date()
                    except Exception:
                        entry_day = None
                    position_ctx = {
                        'in_position': direction,
                        'entry_price': entry_price,
                        'take_profit': take_profit,
                        'initial_stop': initial_stop,
                        'entry_day': entry_day
                    }
                elif existing_ctx:
                    # Preserve flat-day context (last entry_day) even when no open position
                    position_ctx = existing_ctx
                entry_signal, exit_flag, new_ctx = self.strategy.generate_signal_incremental_ctx(df, position_ctx)
                # Persist returned context regardless of signal (may be None)
                self._strategy_ctx_map[ticker] = new_ctx if new_ctx is not None else existing_ctx
                # Record diagnostics even when no entry (signal may be 0)
                if entry_signal != 0:
                        signal_cycle_meta["with_signal"] += 1
                        logger.info(
                            "signal.received",
                            extra={
                                "meta": {
                                    "ticker": ticker,
                                    "signal": int(entry_signal),
                                    "direction": "long" if entry_signal > 0 else "short",
                                    "price": float(latest_bar['close']),
                                    "bar_time": str(latest_bar.name)
                                }
                            }
                        )
                        # Position sizing for entry signal
                        position_proto = self.trade_manager.create_entry_position(
                            price=float(latest_bar['close']),
                            signal=int(entry_signal),
                            time=latest_bar.name,
                            market_data=df,
                            current_idx=len(df) - 1,
                            initial_stop=self.strategy.initial_stop_value(latest_bar['close'], entry_signal == 1, latest_bar),
                            ticker=ticker,
                        )
                        if position_proto is None:
                            self._log_and_record_signal(
                                latest_bar=latest_bar,
                                ticker=ticker,
                                signal=int(entry_signal),
                                size_float=None,
                                size_int=None,
                                skip_reason="sizing_failed",
                                stop_loss=None,
                                exit_flag=exit_flag,
                                event='signal.skipped'
                            )
                        else:
                            qty = int(position_proto.get('size', position_proto.get('SIZE', 0)) or position_proto.get('SIZE', 0) or position_proto.get('size', 0))
                            if qty <= 0:
                                self._log_and_record_signal(
                                    latest_bar=latest_bar,
                                    ticker=ticker,
                                    signal=int(entry_signal),
                                    size_float=position_proto.get('size') or position_proto.get('SIZE'),
                                    size_int=int(qty),
                                    skip_reason="non_positive_size",
                                    stop_loss=position_proto.get('stop_loss'),
                                    exit_flag=exit_flag,
                                    event='signal.skipped',
                                    extra_meta={'computed_qty': int(qty)}
                                )
                            else:
                                self._log_and_record_signal(
                                    latest_bar=latest_bar,
                                    ticker=ticker,
                                    signal=int(entry_signal),
                                    size_float=position_proto.get('size') or position_proto.get('SIZE'),
                                    size_int=qty,
                                    skip_reason=None,
                                    stop_loss=position_proto.get('stop_loss'),
                                    exit_flag=exit_flag,
                                    event='signal.detected'
                                )
                                order = {
                                    "ticker": ticker,
                                    "side": "buy" if entry_signal > 0 else "sell",
                                    "qty": qty,
                                    "order_type": "market",
                                    "timestamp": latest_bar.name,
                                }
                                self._execute_signal(order, market_data=df, current_idx=len(df)-1)
                                # Persist ORB-specific context values onto position after initial fill creation
                                if new_ctx:
                                    try:
                                        pos_ref = self.trade_manager.current_positions.get(ticker)
                                        if pos_ref:
                                            if new_ctx.get('take_profit') is not None:
                                                pos_ref[TradeColumns.TAKE_PROFIT.value] = new_ctx.get('take_profit')
                                            if new_ctx.get('initial_stop') is not None:
                                                pos_ref[TradeColumns.STOP_LOSS.value] = new_ctx.get('initial_stop')
                                                pos_ref[TradeColumns.INITIAL_STOP.value] = new_ctx.get('initial_stop')
                                        # Update persistent context after successful entry
                                        self._strategy_ctx_map[ticker] = new_ctx
                                    except Exception as e:
                                        logger.debug('position.ctx.persist.error', extra={'meta': {'ticker': ticker, 'error': str(e)}})
                else:
                    # Record a holding/no-signal bar for visibility
                    self._record_signal_diagnostic(
                        latest_bar=latest_bar,
                        ticker=ticker,
                        signal=0,
                        direction=None,
                        size_float=None,
                        size_int=None,
                        skip_reason="no_signal",
                        stop_loss=None,
                        exit_flag=exit_flag
                    )
                # Respect exit_flag: log and execute exit immediately for existing position
                if exit_flag:
                    # Retrieve per-ticker exit reason if strategy supports it (multi-ticker safe)
                    exit_reason = getattr(self.strategy, 'get_last_exit_reason', lambda t: getattr(self.strategy, '_last_exit_reason', 'strategy_exit_flag'))(ticker) or 'strategy_exit_flag'
                    logger.info(
                        "signal.exit",
                        extra={"meta": {"ticker": ticker, "bar_time": str(latest_bar.name), "price": float(latest_bar['close']), "reason": exit_reason}}
                    )
                    # Check for open position to close
                    pos = self.trade_manager.current_positions.get(ticker)
                    if pos:
                        direction = pos.get(TradeColumns.DIRECTION.value)
                        size = int(pos.get(TradeColumns.FILLED_QTY.value, pos.get(TradeColumns.SIZE.value, 0)))
                        if size > 0 and direction in (1, -1):
                            side = 'sell' if direction == 1 else 'buy'
                            exit_order = {
                                'ticker': ticker,
                                'side': side,
                                'qty': size,
                                'order_type': 'market',
                                'timestamp': latest_bar.name,
                            }
                            logger.info('exit.flag.execute', extra={'meta': {'ticker': ticker, 'side': side, 'qty': size, 'reason': exit_reason, 'bar_time': str(latest_bar.name)}})
                            if not self.cfg.dry_run:
                                try:
                                    response = self.exchange.submit_order(exit_order)
                                    logger.info('exit.flag.executed', extra={'meta': {
                                        'ticker': ticker,
                                        'status': response.get('status'),
                                        'fill_price': response.get('avg_fill_price'),
                                        'filled_qty': response.get('filled_qty'),
                                        'order_id': response.get('order_id'),
                                        'reason': exit_reason
                                    }})
                                    self._record_trade(exit_order, response)
                                    self.orders_executed_this_cycle += 1
                                    realized_price = response.get('avg_fill_price') or float(latest_bar['close'])
                                    closed = self.trade_manager.close_position(
                                        exit_price=realized_price,
                                        time=latest_bar.name,
                                        current_idx=len(df) - 1,
                                        exit_reason=exit_reason,
                                        ticker=ticker
                                    )
                                    if closed:
                                        logger.info('position.closed', extra={'meta': {'ticker': ticker, 'exit_price': realized_price, 'pnl': closed.get(TradeColumns.PNL.value), 'reason': exit_reason}})
                                except Exception as e:
                                    logger.warning('exit.flag.error', extra={'meta': {'ticker': ticker, 'error': str(e), 'reason': exit_reason}})
                            else:
                                # Dry run mode: simulate close
                                logger.info('exit.flag.dry_run', extra={'meta': {'ticker': ticker, 'side': side, 'qty': size, 'reason': exit_reason}})
                                closed = self.trade_manager.close_position(
                                    exit_price=float(latest_bar['close']),
                                    time=latest_bar.name,
                                    current_idx=len(df) - 1,
                                    exit_reason=exit_reason,
                                    ticker=ticker
                                )
                                if closed:
                                    logger.info('position.closed', extra={'meta': {'ticker': ticker, 'exit_price': float(latest_bar['close']), 'pnl': closed.get(TradeColumns.PNL.value), 'reason': exit_reason, 'dry_run': True}})

            except Exception as e:
                logger.error("signal.processing.error", extra={"meta": {"ticker": ticker, "error": str(e)}})
        # Summary of signal evaluation (structured via meta dict so formatter can include raw dict)
        logger.info("signals.summary", extra={"meta": signal_cycle_meta})

    def _process_exits(self) -> None:
        """Evaluate exit conditions for all open positions and execute exits.

        Uses TradeManager.check_exit_conditions to determine if a stop loss or take profit
        should trigger an exit. Submits a market order to flatten the position and then
        closes it in the TradeManager, logging lifecycle events.
        """
        positions_snapshot = list(self.trade_manager.current_positions.items())
        if not positions_snapshot:
            return
        for ticker, pos in positions_snapshot:
            try:
                df = self.data_cache.get(ticker)
                if df is None or df.empty:
                    continue
                latest_bar = df.iloc[-1]
                current_idx = len(df) - 1
                current_price = float(latest_bar.get('close'))
                high = float(latest_bar.get('high', current_price))
                low = float(latest_bar.get('low', current_price))
                # Evaluate exit conditions
                should_exit, exit_data = self.trade_manager.check_exit_conditions(
                    current_price=current_price,
                    high=high,
                    low=low,
                    time=latest_bar.name,
                    current_idx=current_idx,
                    ticker=ticker
                )
                if not should_exit or not exit_data:
                    continue
                exit_price = exit_data['exit_price']
                exit_reason = exit_data['exit_reason']
                direction = pos.get(TradeColumns.DIRECTION.value)
                size = pos.get(TradeColumns.FILLED_QTY.value, pos.get(TradeColumns.SIZE.value))
                side = 'sell' if direction == 1 else 'buy'  # flatten position
                logger.info(
                    'exit.signal',
                    extra={'meta': {
                        'ticker': ticker,
                        'reason': exit_reason,
                        'exit_price': exit_price,
                        'size': size,
                        'bar_time': str(latest_bar.name)
                    }}
                )
                # Construct and execute exit order
                exit_order = {
                    'ticker': ticker,
                    'side': side,
                    'qty': int(size),
                    'order_type': 'market',
                    'timestamp': latest_bar.name,
                    'exit_reason': exit_reason
                }
                # Reuse execution flow; mark as exit via meta logs
                logger.info('exit.order.submit', extra={'meta': {'ticker': ticker, 'side': side, 'qty': int(size), 'reason': exit_reason}})
                if not self.cfg.dry_run:
                    response = self.exchange.submit_order(exit_order)
                    logger.info('exit.order.executed', extra={'meta': {
                        'ticker': ticker,
                        'side': side,
                        'qty': int(size),
                        'status': response.get('status'),
                        'fill_price': response.get('avg_fill_price'),
                        'filled_qty': response.get('filled_qty'),
                        'order_id': response.get('order_id'),
                        'reason': exit_reason
                    }})
                    # Record trade (closing leg)
                    self._record_trade(exit_order, response)
                    self.orders_executed_this_cycle += 1
                    # Close position in TradeManager with executed fill price (fallback to planned exit_price)
                    realized_price = response.get('avg_fill_price') or exit_price
                    closed = self.trade_manager.close_position(
                        exit_price=realized_price,
                        time=latest_bar.name,
                        current_idx=current_idx,
                        exit_reason=exit_reason,
                        ticker=ticker
                    )
                    if closed:
                        logger.info('position.closed', extra={'meta': {
                            'ticker': ticker,
                            'exit_price': realized_price,
                            'pnl': closed.get(TradeColumns.PNL.value),
                            'reason': exit_reason
                        }})
                else:
                    logger.info('exit.order.dry_run', extra={'meta': {'ticker': ticker, 'side': side, 'qty': int(size), 'reason': exit_reason}})
                    # Simulate close in dry run mode
                    closed = self.trade_manager.close_position(
                        exit_price=exit_price,
                        time=latest_bar.name,
                        current_idx=current_idx,
                        exit_reason=exit_reason,
                        ticker=ticker
                    )
                    if closed:
                        logger.info('position.closed', extra={'meta': {
                            'ticker': ticker,
                            'exit_price': exit_price,
                            'pnl': closed.get(TradeColumns.PNL.value),
                            'reason': exit_reason,
                            'dry_run': True
                        }})
            except Exception as e:
                logger.warning('exit.processing.error', extra={'meta': {'ticker': ticker, 'error': str(e)}})

    def _record_signal_diagnostic(
        self,
        latest_bar: pd.Series,
        ticker: str,
        signal: int,
        direction: Optional[str],
        size_float: Optional[float],
        size_int: Optional[int],
        skip_reason: Optional[str],
        stop_loss: Optional[float],
        exit_flag: bool = False
    ) -> None:
        """Append a single diagnostic row for the latest bar to the diagnostics CSV."""
        try:
            account = self.exchange.get_account()
            available_funds = getattr(self.trade_manager, 'get_available_funds', lambda: None)()
            row = [
                self.run_id,
                str(latest_bar.name),
                ticker,
                float(latest_bar.get('open', float('nan'))),
                float(latest_bar.get('high', float('nan'))),
                float(latest_bar.get('low', float('nan'))),
                float(latest_bar.get('close', float('nan'))),
                float(latest_bar.get('volume', float('nan'))),
                signal,
                direction,
                size_float if size_float is not None else '',
                size_int if size_int is not None else '',
                skip_reason or '',
                stop_loss if stop_loss is not None else '',
                int(exit_flag),
                available_funds if available_funds is not None else '',
                account.get('cash'),
                account.get('equity')
            ]
            with open(self.signal_diag_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            logger.warning("signal.diagnostic.write.error", extra={"meta": {"error": str(e)}})

    def _log_and_record_signal(
        self,
        *,
        latest_bar: pd.Series,
        ticker: str,
        signal: int,
        size_float: Optional[float],
        size_int: Optional[int],
        skip_reason: Optional[str],
        stop_loss: Optional[float],
        exit_flag: bool,
        event: str,
        extra_meta: Optional[dict] = None,
    ) -> None:
        """Unified helper to log a signal-related event and record diagnostics.

        Minimizes duplicated boilerplate for skipped / detected signals.

        Args:
            latest_bar: The most recent price bar (Series).
            ticker: Symbol string.
            signal: Entry signal integer (1, -1, 0).
            size_float: Float size before int casting (may be None).
            size_int: Final integer size (may be None).
            skip_reason: Reason for skipping (None if not skipped).
            stop_loss: Associated stop loss value if available.
            exit_flag: Whether strategy requested an exit this bar.
            event: Log event name (e.g. 'signal.skipped', 'signal.detected').
            extra_meta: Additional metadata to merge into log meta.
        """
        try:
            direction = None
            if signal != 0:
                direction = 'long' if signal > 0 else 'short'
            meta = {
                'ticker': ticker,
                'signal': int(signal),
                'direction': direction,
                'price': float(latest_bar.get('close')),
                'bar_time': str(latest_bar.name),
            }
            if stop_loss is not None:
                meta['stop'] = float(stop_loss)
            if skip_reason:
                meta['reason'] = skip_reason
            if extra_meta:
                meta.update(extra_meta)
            logger.info(event, extra={'meta': meta})
        except Exception as e:
            logger.warning('signal.log.error', extra={'meta': {'ticker': ticker, 'error': str(e), 'event': event}})
        # Always record diagnostics row
        self._record_signal_diagnostic(
            latest_bar=latest_bar,
            ticker=ticker,
            signal=int(signal),
            direction=direction,
            size_float=size_float,
            size_int=size_int,
            skip_reason=skip_reason,
            stop_loss=stop_loss,
            exit_flag=exit_flag,
        )

    def _execute_signal(self, order: Dict[str, Any], market_data: Optional[pd.DataFrame] = None, current_idx: Optional[int] = None) -> None:
        """
        Execute a trading signal by submitting an order to the exchange.

        Args:
            order: Order dictionary with trade parameters
        """
        # Ensure the order has a timestamp
        if "timestamp" not in order:
            order["timestamp"] = pd.Timestamp.now()

        logger.info("order.submit", extra={"meta": {"ticker": order.get("ticker"), "side": order.get("side"), "qty": order.get("qty"), "ts": str(order.get("timestamp"))}})

        # Submit order if not in dry run mode
        if not self.cfg.dry_run:
            response = self.exchange.submit_order(order)
            logger.info(
                "order.executed",
                extra={
                    "meta": {
                        "ticker": order.get("ticker"),
                        "side": order.get("side"),
                        "qty": order.get("qty"),
                        "status": response.get("status"),
                        "fill_price": response.get("avg_fill_price"),
                        "filled_qty": response.get("filled_qty"),
                        "commission": response.get("commission"),
                        "order_id": response.get("order_id")
                    }
                }
            )
            # Record trade details (exchange trade log)
            self._record_trade(order, response)
            self.orders_executed_this_cycle += 1

            # Reconcile fills back into TradeManager state if filled
            try:
                if response.get("status") in ("filled", "partial") and response.get("filled_qty", 0) > 0 and market_data is not None:
                    from src.config.columns import TradeColumns
                    ticker = order.get("ticker")
                    # If new position (direction by side) -> create position entry if not already
                    side = order.get("side")
                    direction = 1 if side == "buy" else -1
                    filled_qty = response.get("filled_qty")
                    fill_price = response.get("avg_fill_price")
                    # If we already have a position for ticker, accumulate (simple aggregation)
                    existing = self.trade_manager.current_positions.get(ticker)
                    if not existing:
                        # Create a base position record (target size = order qty); fills start at 0 then ledger updated
                        pos = {
                            TradeColumns.ENTRY_IDX.value: current_idx,
                            TradeColumns.ENTRY_TIME.value: order.get("timestamp"),
                            TradeColumns.ENTRY_PRICE.value: fill_price,  # initial price; will become VWAP
                            TradeColumns.SIZE.value: order.get("qty"),
                            TradeColumns.FILLED_QTY.value: 0,
                            TradeColumns.DIRECTION.value: direction,
                            TradeColumns.TICKER.value: ticker,
                            TradeColumns.STOP_LOSS.value: None,
                            TradeColumns.TAKE_PROFIT.value: None,
                            TradeColumns.ACCOUNT_BALANCE.value: self.trade_manager.current_balance,
                            TradeColumns.TICKER_REGIME.value: None,
                            TradeColumns.TRAILING_STOP_DATA.value: None,
                            TradeColumns.FILLS.value: []
                        }
                        self.trade_manager.current_positions[ticker] = pos
                        self.trade_manager.current_position = pos
                        logger.debug("position.opened", extra={"meta": {"ticker": ticker, "target_size": order.get('qty'), "initial_fill": filled_qty, "entry_price": fill_price}})
                    # Record the new fill (updates VWAP + filled quantity)
                    ts_fill = response.get("timestamp") or order.get("timestamp")
                    self.trade_manager.record_fill(ticker, int(filled_qty), float(fill_price), ts_fill)
                    ledger_pos = self.trade_manager.current_positions.get(ticker)
                    if ledger_pos:
                        logger.debug("position.fill.ledger", extra={"meta": {"ticker": ticker, "filled_qty": ledger_pos.get(TradeColumns.FILLED_QTY.value), "target_size": ledger_pos.get(TradeColumns.SIZE.value), "vwap": ledger_pos.get(TradeColumns.ENTRY_PRICE.value)}})
                    # Apply/update risk parameters if available
                    if hasattr(self.risk_manager, 'apply') and current_idx is not None:
                        signal_series = pd.Series({
                            'entry_price': fill_price,
                            'signal': direction,
                            'index': current_idx,
                            'initial_stop': fill_price  # placeholder; underlying risk manager adjusts
                        })
                        try:
                            risk_result = self.risk_manager.apply(signal_series, market_data)
                            pos_ref = self.trade_manager.current_positions.get(ticker)
                            if pos_ref and risk_result:
                                if TradeColumns.TRAILING_STOP_DATA.value in risk_result:
                                    pos_ref[TradeColumns.TRAILING_STOP_DATA.value] = risk_result[TradeColumns.TRAILING_STOP_DATA.value]
                                if 'stop_loss' in risk_result:
                                    pos_ref[TradeColumns.STOP_LOSS.value] = risk_result['stop_loss']
                                if 'take_profit' in risk_result:
                                    pos_ref[TradeColumns.TAKE_PROFIT.value] = risk_result['take_profit']
                                logger.debug("position.risk.updated", extra={"meta": {"ticker": ticker, "stop_loss": pos_ref.get(TradeColumns.STOP_LOSS.value), "take_profit": pos_ref.get(TradeColumns.TAKE_PROFIT.value)}})
                        except Exception as e:
                            logger.warning(f"Risk reconciliation failed for {ticker}: {e}")
            except Exception as e:
                logger.warning("fill.reconcile.error", extra={"meta": {"error": str(e)}})
            cb = self.callbacks.get("on_trade")
            if cb:
                try:
                    cb(order, response)
                except Exception as e:
                    logger.warning(f"on_trade callback error: {e}")
        else:
            logger.info("order.dry_run", extra={"meta": {"ticker": order.get("ticker"), "side": order.get("side"), "qty": order.get("qty")}})

    def _record_trade(self, order: Dict[str, Any], response: Dict[str, Any]) -> None:
        """
        Record trade details to the trades CSV file.

        Args:
            order: Order dictionary
            response: Exchange response dictionary
        """
        # Always record, including open orders (filled_qty may be 0)
        if response.get("status"):
            # Calculate rough PnL (for reporting only)
            positions = self.exchange.get_positions()
            pnl = 0.0
            for pos in positions:
                if pos["ticker"] == order["ticker"]:
                    pnl = pos["unrealized_pnl"]
                    break

            # Choose timestamp: in replay prefer original order (bar) timestamp
            if self.replay_cfg.enabled:
                ts = order.get("timestamp") or response.get("timestamp")
            else:
                ts = response.get("timestamp") or order.get("timestamp")
            with open(self.trades_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    self.run_id,
                    str(ts),
                    order["ticker"],
                    order["side"],
                    response.get("filled_qty", 0),
                    response.get("avg_fill_price"),
                    response.get("commission", 0.0),
                    pnl,
                    response.get("order_id"),
                    response.get("status")
                ])

    def _record_account_state(self) -> None:
        """Record current account state to the equity CSV file."""
        account = self.exchange.get_account()
        # Use simulated data timestamp in replay mode if available; else use market timezone now
        if self.replay_cfg.enabled and self.last_data_timestamp is not None:
            ts = self.last_data_timestamp
        else:
            tz = pytz.timezone(self.market_hours.timezone) if self.market_hours else pytz.UTC
            ts = pd.Timestamp.now(tz=tz)
        with open(self.equity_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                ts.isoformat(),
                account["cash"],
                account["positions_value"],
                account["equity"]
            ])

    def _sync_positions_with_exchange(self) -> None:
        """Synchronize TradeManager positions with exchange (basic implementation).

        Ensures unrealized PnL and position sizes reflect exchange state. Placeholder for
        deeper reconciliation (e.g., partial fills, multi-leg)."""
        try:
            exch_positions = {p.ticker: p for p in self.exchange.get_positions()}
        except Exception:
            return
        from src.config.columns import TradeColumns
        for ticker, tm_pos in list(self.trade_manager.current_positions.items()):
            exch_pos = exch_positions.get(ticker)
            if not exch_pos:
                # Position disappeared on exchange; close locally at last known price if possible
                continue
            # Update size if drifted
            size_col = TradeColumns.SIZE.value
            if hasattr(exch_pos, 'qty') and tm_pos.get(size_col) != abs(exch_pos.qty):
                tm_pos[size_col] = abs(exch_pos.qty)
                logger.debug("position.size.sync", extra={"meta": {"ticker": ticker, "size": tm_pos[size_col]}})
            # Could compute unrealized pnl if exchange exposes it; skipped for brevity
        # Add any new exchange-only positions (unlikely in replay, but defensive)
        for ticker, exch_pos in exch_positions.items():
            if ticker not in self.trade_manager.current_positions:
                self.trade_manager.current_positions[ticker] = {
                    TradeColumns.TICKER.value: ticker,
                    TradeColumns.SIZE.value: abs(getattr(exch_pos, 'qty', 0)),
                    TradeColumns.ENTRY_PRICE.value: getattr(exch_pos, 'avg_price', None),
                    TradeColumns.DIRECTION.value: 1 if getattr(exch_pos, 'qty', 0) >= 0 else -1,
                    TradeColumns.ENTRY_TIME.value: getattr(exch_pos, 'entry_time', None),
                }
                logger.debug("position.adopted", extra={"meta": {"ticker": ticker}})

    def _finalize_cycle(self) -> Dict[str, Any]:
        """Finalize a cycle: log summary, record equity/account state, update timestamps.

        Returns:
            Latest account dict from exchange.
        """
        try:
            acct = self.exchange.get_account()
            logger.info(
                "cycle.summary",
                extra={
                    "meta": {
                        "cycle": self.tick_count,
                        "orders": self.orders_executed_this_cycle,
                        "cash": round(acct.get('cash', 0), 2),
                        "equity": round(acct.get('equity', 0), 2),
                        "positions": self.trade_manager.current_positions
                    }
                },
            )
        except Exception:
            pass
        account = self.exchange.get_account()
        self._record_account_state()
        self.last_run_time = datetime.now()
        return account

    def _handle_replay_progress(self, latest_data: Dict[str, pd.DataFrame]) -> Optional[Dict[str, Any]]:
        """Handle replay progress tracking and completion logic.

        Logs per-ticker progress, determines if replay is complete, records final equity, and halts loop.
        Also updates exchange prices for replay cycles that are still in progress.

        Args:
            latest_data: Most recently fetched market data map.

        Returns:
            Account dict if replay completed this cycle; otherwise None.
        """
        if self.replay_cfg.enabled and isinstance(self.data_fetcher, DataReplayCacheDataLoader):
            all_complete = True
            progress_map = {}
            for t in self.tickers:
                progress = self.data_fetcher.replay_progress(t, self.replay_cfg.timeframe)
                pct = round(progress * 100, 2)
                progress_map[t] = pct
                logger.debug("replay.progress", extra={"meta": {"ticker": t, "progress_pct": pct}})
                if progress < 1.0:
                    all_complete = False
            if all_complete:
                # Final summary before stopping; record equity and halt loop
                logger.info("replay.completed", extra={"meta": {"cycle": self.tick_count, "progress_pct_map": progress_map}})
                account = self.exchange.get_account()
                self._record_account_state()
                self.last_run_time = datetime.now()
                self.running = False
                return account
            # Replay still in progress: update exchange prices using latest data snapshot
            self._update_exchange_prices(latest_data)
        return None

    def _run_cycle(self) -> Dict[str, Any]:
        """Run a single orchestrator cycle (live or replay)."""
        self.tick_count += 1
        self.orders_executed_this_cycle = 0
        logger.info("cycle.start", extra={"meta": {"cycle": self.tick_count, "mode": "replay" if self.replay_cfg.enabled else "live"}})

        if self.replay_cfg.enabled and isinstance(self.data_fetcher, DataReplayCacheDataLoader):
            self.data_fetcher.advance(n=1)

        if not self.is_market_open():
            logger.info("Market is closed, skipping cycle")
            return self.exchange.get_account()

        latest_data = self._fetch_latest_data()

        # Check for no data scenario
        if not latest_data:
            logger.warning("cycle.no_data", extra={"meta": {"cycle": self.tick_count}})
            return self.exchange.get_account()

        # Handle replay progress & potential completion (includes exchange price updates for replay)
        replay_completion = self._handle_replay_progress(latest_data)
        if replay_completion is not None:
            return replay_completion

        # Process new signals
        self._process_signals()
        # Evaluate exits for existing positions after processing new signals
        self._process_exits()
        # Position/account sync
        self._sync_positions_with_exchange()
        return self._finalize_cycle()

    def start(self, run_duration: Optional[int] = None) -> None:
        """
        Start the trading loop.

        Args:
            run_duration: Optional duration in seconds to run the loop
        """
        if self.running:
            logger.warning("Trading loop already running")
            return

        # Connect to exchange
        if not self.exchange.connect():
            logger.error("Failed to connect to exchange")
            return
        if self.replay_cfg.enabled and hasattr(self.exchange, 'enable_force_fill'):
            try:
                self.exchange.enable_force_fill(True)
                logger.info("Force-fill enabled (replay mode)")
            except Exception as e:
                logger.warning(f"Unable to enable force-fill: {e}")

        self.running = True
        start_time = time.time()
        logger.info("loop.start", extra={"meta": {"poll_seconds": self.cfg.polling_seconds, "speedup": self.cfg.speedup, "tickers": self.tickers}})

        try:
            while self.running:
                cycle_start = time.time()

                # Check if run duration exceeded
                if run_duration and (time.time() - start_time) > run_duration:
                    logger.info("loop.duration.exceeded", extra={"meta": {"run_duration": run_duration}})
                    self.stop()
                    break

                # Run a single cycle
                self._run_cycle()

                # Calculate time to sleep
                elapsed = time.time() - cycle_start
                # Auto-stop check (use simulated replay time if in replay mode)
                market_tz = pytz.timezone(self.market_hours.timezone)
                if self.replay_cfg.enabled and self.last_data_timestamp is not None:
                    current_market_time = self.last_data_timestamp
                    if isinstance(current_market_time, pd.Timestamp):
                        if current_market_time.tzinfo is None:
                            current_market_time = current_market_time.tz_localize(market_tz)
                        else:
                            current_market_time = current_market_time.tz_convert(market_tz)
                else:
                    current_market_time = datetime.now(market_tz)
                if self.auto_stop_at and current_market_time >= self.auto_stop_at:
                    logger.info("loop.auto_stop", extra={"meta": {"auto_stop_at": str(self.auto_stop_at), "current_market_time": str(current_market_time)}})
                    self.stop()
                    break

                # Use specialized replay sleep timing if configured
                if self.replay_cfg.enabled and hasattr(self.replay_cfg, 'replay_sleep_seconds') and self.replay_cfg.replay_sleep_seconds is not None:
                    sleep_time = max(0, self.replay_cfg.replay_sleep_seconds)
                else:
                    sleep_time = max(0, (self.cfg.polling_seconds - elapsed) / self.cfg.speedup)

                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            logger.info("loop.interrupted", extra={"meta": {"cycle": self.tick_count}})
        finally:
            # Ensure we disconnect from exchange
            self.exchange.disconnect()
            self.running = False

    def stop(self) -> None:
        """Stop the trading loop."""
        if self.running:
            logger.info("loop.stop", extra={"meta": {"cycle": self.tick_count}})
            self.running = False

    def save_state(self, csv_folder: Optional[str] = None) -> None:
        """
        Save current state to CSV files.

        Args:
            csv_folder: Folder to save CSV files (defaults to self.results_dir)
        """
        folder = Path(csv_folder) if csv_folder else self.results_dir
        folder.mkdir(parents=True, exist_ok=True)

        # Save trades
        trades_path = folder / f"{self.run_id}_trades.csv"
        with open(trades_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'run_id', 'timestamp', 'ticker', 'side', 'qty',
                'price', 'commission', 'pnl', 'order_id', 'order_status'
            ])
            for trade in self.exchange.trade_log:
                trade_dict = trade.to_dict()
                writer.writerow([
                    self.run_id,
                    trade_dict["timestamp"],
                    trade_dict["ticker"],
                    trade_dict["side"],
                    trade_dict["qty"],
                    trade_dict["price"],
                    trade_dict["commission"],
                    0,  # PnL not tracked in trade object
                    trade_dict["order_id"],
                    trade_dict.get("order_status")
                ])

        # Save equity curve
        account = self.exchange.get_account()
        equity_path = folder / f"{self.run_id}_equity.csv"
        with open(equity_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'cash', 'positions_value', 'total_equity'
            ])
            writer.writerow([
                pd.Timestamp.now(),
                account["cash"],
                account["positions_value"],
                account["equity"]
            ])
