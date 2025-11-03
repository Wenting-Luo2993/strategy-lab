"""
Dark Trading Orchestrator for Paper Trading Simulation

This module provides a trading orchestrator that simulates minute-by-minute live trading
for quick manual paper tests.
"""

import time
import logging
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

        # Internal state
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.running: bool = False
        self.last_run_time: Optional[datetime] = None
        self.tick_count: int = 0
        self.orders_executed_this_cycle: int = 0
        self.auto_stop_at: Optional[datetime] = None

        # Results tracking
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.results_dir / f"trades_{self.run_id}.csv"
        self.equity_file = self.results_dir / f"equity_{self.run_id}.csv"

        self._init_results_files()
        self._configure_auto_stop()

    def _init_results_files(self) -> None:
        """Initialize result CSV files with headers."""
        # Trades CSV
        with open(self.trades_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'run_id', 'timestamp', 'ticker', 'side', 'qty',
                'price', 'commission', 'pnl', 'order_id'
            ])

        # Equity CSV
        with open(self.equity_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'cash', 'positions_value', 'total_equity'
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
                # For replay mode we rely on custom loader behavior (will be added later)
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
                        logger.debug("data.cache.init", extra={"ticker": ticker, "rows": len(df)})
                    else:
                        # Append new data if not already present
                        last_cached_time = self.data_cache[ticker].index[-1]
                        new_data = df[df.index > last_cached_time]
                        if not new_data.empty:
                            self.data_cache[ticker] = pd.concat([self.data_cache[ticker], new_data])
                            logger.debug("data.cache.extend", extra={"ticker": ticker, "new_rows": len(new_data)})

            except Exception as e:
                logger.error("data.fetch.error", extra={"ticker": ticker, "error": str(e)})

        logger.info(
            "data.fetch.summary",
            extra={
                "tickers": fetched_tickers,
                "count": len(fetched_tickers),
                "duration_ms": int((time.time() - fetch_start) * 1000)
            }
        )
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

    def _process_signals(self, latest_data: Dict[str, pd.DataFrame]) -> None:
        """
        Generate and process trading signals for each ticker.

        Args:
            latest_data: Dictionary of ticker -> DataFrame with latest prices
        """
        signal_cycle_meta = {"evaluated": 0, "with_signal": 0}
        for ticker, df in self.data_cache.items():
            if df.empty:
                continue

            try:
                # Add necessary indicators for the strategy
                if hasattr(IndicatorFactory, 'apply'):
                    # Apply indicators based on strategy configuration
                    if hasattr(self.strategy, 'strategy_config') and self.strategy.strategy_config:
                        indicators = []
                        # Add ATR for risk management
                        indicators.append({'name': 'atr', 'params': {'length': 14}})

                        # Add ORB levels if using ORB strategy
                        if hasattr(self.strategy, 'breakout_window'):
                            # This is likely an ORB strategy
                            start_time = getattr(self.strategy.strategy_config.orb_config, 'start_time', "09:30")
                            duration = getattr(self.strategy.strategy_config.orb_config, 'timeframe', 5)
                            body_pct = getattr(self.strategy.strategy_config.orb_config, 'body_breakout_percentage', 0.5)

                            indicators.append({
                                'name': 'orb_levels',
                                'params': {
                                    'start_time': start_time,
                                    'duration_minutes': int(duration),
                                    'body_pct': float(body_pct)
                                }
                            })

                        df = IndicatorFactory.apply(df, indicators)
                        logger.debug("indicators.applied", extra={"ticker": ticker, "indicators": [i['name'] for i in indicators]})

                # Generate signals using the existing strategy's generate_signals method
                signals = self.strategy.generate_signals(df)
                signal_cycle_meta["evaluated"] += 1

                # Check if we have a signal in the latest bar
                if len(signals) > 0 and signals.iloc[-1] != 0:
                    # We have a signal
                    latest_signal = signals.iloc[-1]
                    latest_bar = df.iloc[-1]
                    signal_cycle_meta["with_signal"] += 1

                    # Create order from signal
                    # Position sizing via TradeManager (create a hypothetical position to get size)
                    signal_val = 1 if latest_signal > 0 else -1
                    # Use trade_manager to compute size & risk vars
                    position_proto = self.trade_manager.create_entry_position(
                        price=float(latest_bar['close']),
                        signal=signal_val,
                        time=latest_bar.name,
                        market_data=df,
                        current_idx=len(df) - 1,
                        initial_stop=self.strategy.initial_stop_value(latest_bar['close'], signal_val == 1, latest_bar),
                        ticker=ticker,
                    )
                    if position_proto is None:
                        logger.info(f"Skipped signal for {ticker}; insufficient funds or sizing error.")
                        continue
                    qty = int(position_proto.get('size', position_proto.get('SIZE', 0)) or position_proto.get('SIZE', 0) or position_proto.get('size', 0))
                    if qty <= 0:
                        logger.info(f"Computed non-positive size for {ticker}, skipping order.")
                        continue
                    order = {
                        "ticker": ticker,
                        "side": "buy" if latest_signal > 0 else "sell",
                        "qty": qty,
                        "order_type": "market",
                        "timestamp": latest_bar.name,
                    }

                    # Get risk management settings (stop loss/take profit)
                    is_long = (latest_signal > 0)
                    stop_loss = position_proto.get('stop_loss')
                    if stop_loss is None and hasattr(self.risk_manager, 'calculate_stop_loss'):
                        stop_loss = self.risk_manager.calculate_stop_loss(
                            entry_price=latest_bar['close'],
                            is_long=is_long,
                            df=df,
                            row=latest_bar,
                        )

                    # Log the signal
                    logger.info(
                        "signal.detected",
                        extra={
                            "ticker": ticker,
                            "signal": int(latest_signal),
                            "price": float(latest_bar['close']),
                            "stop": float(stop_loss) if stop_loss is not None else None,
                            "bar_time": str(latest_bar.name)
                        }
                    )

                    # Execute the order
                    self._execute_signal(order, market_data=df, current_idx=len(df)-1)

            except Exception as e:
                logger.error("signal.processing.error", extra={"ticker": ticker, "error": str(e)})
        # Summary of signal evaluation
        logger.info(
            "signals.summary",
            extra=signal_cycle_meta
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

        logger.info("order.submit", extra={"ticker": order.get("ticker"), "side": order.get("side"), "qty": order.get("qty"), "ts": str(order.get("timestamp"))})

        # Submit order if not in dry run mode
        if not self.cfg.dry_run:
            response = self.exchange.submit_order(order)
            logger.info(
                "order.executed",
                extra={
                    "ticker": order.get("ticker"),
                    "side": order.get("side"),
                    "qty": order.get("qty"),
                    "status": response.get("status"),
                    "fill_price": response.get("avg_fill_price"),
                    "filled_qty": response.get("filled_qty"),
                    "commission": response.get("commission"),
                    "order_id": response.get("order_id")
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
                        # Create a synthetic position record similar to TradeManager.create_entry_position output
                        pos = {
                            TradeColumns.ENTRY_IDX.value: current_idx,
                            TradeColumns.ENTRY_TIME.value: order.get("timestamp"),
                            TradeColumns.ENTRY_PRICE.value: fill_price,
                            TradeColumns.SIZE.value: filled_qty,
                            TradeColumns.DIRECTION.value: direction,
                            TradeColumns.TICKER.value: ticker,
                            TradeColumns.STOP_LOSS.value: None,
                            TradeColumns.TAKE_PROFIT.value: None,
                            TradeColumns.ACCOUNT_BALANCE.value: self.trade_manager.current_balance,
                            TradeColumns.TICKER_REGIME.value: None,
                            TradeColumns.TRAILING_STOP_DATA.value: None,
                        }
                        self.trade_manager.current_positions[ticker] = pos
                        self.trade_manager.current_position = pos
                        logger.debug("position.opened", extra={"ticker": ticker, "size": filled_qty, "entry_price": fill_price})
                    else:
                        # Adjust weighted average entry and size
                        total_size = existing[TradeColumns.SIZE.value] + filled_qty
                        if total_size > 0:
                            existing_price = existing[TradeColumns.ENTRY_PRICE.value]
                            existing[TradeColumns.ENTRY_PRICE.value] = (existing_price * existing[TradeColumns.SIZE.value] + fill_price * filled_qty) / total_size
                            existing[TradeColumns.SIZE.value] = total_size
                            logger.debug("position.adjusted", extra={"ticker": ticker, "new_size": total_size, "avg_entry": existing[TradeColumns.ENTRY_PRICE.value]})
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
                                logger.debug("position.risk.updated", extra={"ticker": ticker, "stop_loss": pos_ref.get(TradeColumns.STOP_LOSS.value), "take_profit": pos_ref.get(TradeColumns.TAKE_PROFIT.value)})
                        except Exception as e:
                            logger.warning(f"Risk reconciliation failed for {ticker}: {e}")
            except Exception as e:
                logger.warning("fill.reconcile.error", extra={"error": str(e)})
            cb = self.callbacks.get("on_trade")
            if cb:
                try:
                    cb(order, response)
                except Exception as e:
                    logger.warning(f"on_trade callback error: {e}")
        else:
            logger.info("order.dry_run", extra={"ticker": order.get("ticker"), "side": order.get("side"), "qty": order.get("qty")})

    def _record_trade(self, order: Dict[str, Any], response: Dict[str, Any]) -> None:
        """
        Record trade details to the trades CSV file.

        Args:
            order: Order dictionary
            response: Exchange response dictionary
        """
        if response["status"] in ["filled", "partial"] and response["filled_qty"] > 0:
            # Calculate rough PnL (for reporting only)
            positions = self.exchange.get_positions()
            pnl = 0.0
            for pos in positions:
                if pos["ticker"] == order["ticker"]:
                    pnl = pos["unrealized_pnl"]
                    break

            # Write to CSV
            with open(self.trades_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    self.run_id,
                    response["timestamp"],
                    order["ticker"],
                    order["side"],
                    response["filled_qty"],
                    response["avg_fill_price"],
                    response["commission"],
                    pnl,
                    response["order_id"]
                ])

    def _record_account_state(self) -> None:
        """Record current account state to the equity CSV file."""
        account = self.exchange.get_account()

        with open(self.equity_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                pd.Timestamp.now(),
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
                logger.debug("position.size.sync", extra={"ticker": ticker, "size": tm_pos[size_col]})
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
                logger.debug("position.adopted", extra={"ticker": ticker})

    def _run_cycle(self) -> Dict[str, Any]:
        """Run a single orchestrator cycle (live or replay)."""
        self.tick_count += 1
        self.orders_executed_this_cycle = 0
        logger.info("cycle.start", extra={"cycle": self.tick_count, "mode": "replay" if self.replay_cfg.enabled else "live"})
        if self.replay_cfg.enabled and isinstance(self.data_fetcher, DataReplayCacheDataLoader):
            self.data_fetcher.advance(n=1)
        if not self.is_market_open():
            logger.info("Market is closed, skipping cycle")
            return self.exchange.get_account()
        latest_data = self._fetch_latest_data()
        if self.replay_cfg.enabled and isinstance(self.data_fetcher, DataReplayCacheDataLoader):
            for t in self.tickers:
                progress = self.data_fetcher.replay_progress(t, self.replay_cfg.timeframe)
                logger.debug("replay.progress", extra={"ticker": t, "progress_pct": round(progress*100,2)})
        if not latest_data:
            logger.warning("cycle.no_data", extra={"cycle": self.tick_count})
            return self.exchange.get_account()
        self._update_exchange_prices(latest_data)
        self._process_signals(latest_data)
        # Position/account sync
        self._sync_positions_with_exchange()
        try:
            acct = self.exchange.get_account()
            logger.info(
                "cycle.summary",
                extra={
                    "cycle": self.tick_count,
                    "orders": self.orders_executed_this_cycle,
                    "cash": round(acct.get('cash', 0), 2),
                    "equity": round(acct.get('equity', 0), 2),
                    "positions": len(self.trade_manager.current_positions)
                },
            )
        except Exception:
            pass
        account = self.exchange.get_account()
        self._record_account_state()
        self.last_run_time = datetime.now()
        return account

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
        logger.info("loop.start", extra={"poll_seconds": self.cfg.polling_seconds, "speedup": self.cfg.speedup, "tickers": self.tickers})

        try:
            while self.running:
                cycle_start = time.time()

                # Check if run duration exceeded
                if run_duration and (time.time() - start_time) > run_duration:
                    logger.info("loop.duration.exceeded", extra={"run_duration": run_duration})
                    self.stop()
                    break

                # Run a single cycle
                self._run_cycle()

                # Calculate time to sleep
                elapsed = time.time() - cycle_start
                # Auto-stop check
                if self.auto_stop_at and datetime.now(pytz.timezone(self.market_hours.timezone)) >= self.auto_stop_at:
                    logger.info("loop.auto_stop", extra={"auto_stop_at": str(self.auto_stop_at)})
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
            logger.info("loop.interrupted", extra={"cycle": self.tick_count})
        finally:
            # Ensure we disconnect from exchange
            self.exchange.disconnect()
            self.running = False

    def stop(self) -> None:
        """Stop the trading loop."""
        if self.running:
            logger.info("loop.stop", extra={"cycle": self.tick_count})
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
        trades_path = folder / f"trades_{self.run_id}.csv"
        with open(trades_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'run_id', 'timestamp', 'ticker', 'side', 'qty',
                'price', 'commission', 'pnl', 'order_id'
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
                    trade_dict["order_id"]
                ])

        # Save equity curve
        account = self.exchange.get_account()
        equity_path = folder / f"equity_{self.run_id}.csv"
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
