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
from typing import Dict, List, Optional, Any, Union, Callable, Type
from datetime import datetime, time as dt_time
import threading
import pytz

from src.exchange.base import Exchange
from src.exchange.models import Order, Trade, AccountState
from src.strategies.base import StrategyBase
from src.risk_management.base import RiskManagement
from src.data.cache import CacheDataLoader
from src.indicators import IndicatorFactory

# Configure logging
logger = logging.getLogger(__name__)


class DarkTradingOrchestrator:
    """
    Trading orchestrator for paper trading simulation.
    
    This orchestrator manages interaction between strategies, risk managers,
    and exchange to simulate live trading.
    """
    
    def __init__(
        self,
        strategy: StrategyBase,  # Strategy object with generate_signal method
        risk_manager: RiskManagement,  # Risk manager object with apply method
        exchange: Exchange,
        data_fetcher: CacheDataLoader,  # Data fetcher for market data
        tickers: List[str],
        initial_capital: float = 10000.0,
        polling_interval_secs: int = 60,
        speedup: float = 1.0,
        market_hours: Optional[Dict[str, Any]] = None,
        results_dir: Optional[str] = "results",
        run_id: Optional[str] = None,
        dry_run: bool = False
    ):
        """
        Initialize the orchestrator.
        
        Args:
            strategy: Strategy object with generate_signal(df) -> optional signal dict
            risk_manager: Risk manager with apply(signal, df) -> augmented signal
            exchange: Exchange instance
            data_fetcher: Callable that fetches data for tickers
            tickers: List of tickers to trade
            initial_capital: Initial capital for trading
            polling_interval_secs: Seconds between polling cycles
            speedup: Multiplier for time.sleep to run tests faster
            market_hours: Market hours configuration (None for 24/7)
            results_dir: Directory to save results
            run_id: Unique ID for this run
            dry_run: If True, don't actually submit orders
        """
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.exchange = exchange
        self.data_fetcher = data_fetcher
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.polling_interval = polling_interval_secs
        self.speedup = speedup
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.dry_run = dry_run
        
        # Set default market hours (US Eastern Time, 9:30 AM - 4:00 PM)
        self.market_hours = market_hours or {
            "timezone": "America/New_York",
            "open_time": dt_time(9, 30),
            "close_time": dt_time(16, 0),
            "days": [0, 1, 2, 3, 4]  # Monday to Friday
        }
        
        # Internal state
        self.data_cache: Dict[str, pd.DataFrame] = {}  # ticker -> DataFrame
        self.running = False
        self.last_run_time = None
        self.tick_count = 0
        
        # Results tracking
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.results_dir / f"trades_{self.run_id}.csv"
        self.equity_file = self.results_dir / f"equity_{self.run_id}.csv"
        
        # Initialize results files
        self._init_results_files()
    
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
        # If no market hours defined, assume 24/7 market
        if not self.market_hours:
            return True
        
        # Get current time in the configured timezone
        tz = pytz.timezone(self.market_hours["timezone"])
        now = datetime.now(tz)
        
        # Check if today is a trading day (day of week)
        if now.weekday() not in self.market_hours["days"]:
            return False
        
        # Check if current time is within trading hours
        current_time = now.time()
        return (
            self.market_hours["open_time"] <= current_time <= self.market_hours["close_time"]
        )
    
    def _fetch_latest_data(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch the latest data for all tickers.
        
        Returns:
            dict: ticker -> DataFrame with latest minute bar
        """
        latest_data = {}
        
        for ticker in self.tickers:
            try:
                # Fetch latest minute bar using CacheDataLoader
                df = self.data_fetcher.fetch(
                    ticker,
                    timeframe="5m",
                    start="2025-08-05",
                    end="2025-09-29"
                )
                
                if df is not None and not df.empty:
                    latest_data[ticker] = df
                    
                    # Update data cache
                    if ticker not in self.data_cache:
                        self.data_cache[ticker] = df
                    else:
                        # Append new data if not already present
                        last_cached_time = self.data_cache[ticker].index[-1]
                        new_data = df[df.index > last_cached_time]
                        if not new_data.empty:
                            self.data_cache[ticker] = pd.concat([self.data_cache[ticker], new_data])
            
            except Exception as e:
                logger.error(f"Error fetching data for {ticker}: {e}")
        
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
                
                # Generate signals using the existing strategy's generate_signals method
                signals = self.strategy.generate_signals(df)
                
                # Check if we have a signal in the latest bar
                if len(signals) > 0 and signals.iloc[-1] != 0:
                    # We have a signal
                    latest_signal = signals.iloc[-1]
                    latest_bar = df.iloc[-1]
                    
                    # Create order from signal
                    order = {
                        "ticker": ticker,
                        "side": "buy" if latest_signal > 0 else "sell",
                        "qty": 100,  # Default quantity, will be adjusted by position sizing
                        "order_type": "market",
                        "timestamp": latest_bar.name  # Use the timestamp from the bar
                    }
                    
                    # Get risk management settings (stop loss/take profit)
                    is_long = (latest_signal > 0)
                    stop_loss = self.strategy.initial_stop_value(latest_bar['close'], is_long, latest_bar)
                    
                    # If strategy doesn't provide stop, use risk manager to calculate it
                    if stop_loss is None and hasattr(self.risk_manager, 'calculate_stop_loss'):
                        stop_loss = self.risk_manager.calculate_stop_loss(
                            entry_price=latest_bar['close'],
                            is_long=is_long,
                            df=df,
                            row=latest_bar
                        )
                    
                    # Log the signal
                    logger.info(f"Signal for {ticker}: {latest_signal}, Price: {latest_bar['close']}, Stop: {stop_loss}")
                    
                    # Execute the order
                    self._execute_signal(order)
            
            except Exception as e:
                logger.error(f"Error processing signal for {ticker}: {e}")
    
    def _execute_signal(self, order: Dict[str, Any]) -> None:
        """
        Execute a trading signal by submitting an order to the exchange.
        
        Args:
            order: Order dictionary with trade parameters
        """
        # Ensure the order has a timestamp
        if "timestamp" not in order:
            order["timestamp"] = pd.Timestamp.now()
        
        logger.info(f"Executing order: {order}")
        
        # Submit order if not in dry run mode
        if not self.dry_run:
            response = self.exchange.submit_order(order)
            logger.info(f"Order response: {response}")
            
            # Record trade details
            self._record_trade(order, response)
        else:
            logger.info("DRY RUN: Order not submitted")
    
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
    
    def run_once_for_backtest_mode(self) -> Dict[str, Any]:
        """
        Run a single cycle of the trading loop, useful for testing.
        
        Returns:
            dict: Current account state
        """
        self.tick_count += 1
        logger.info(f"Running cycle {self.tick_count}")
        
        # Check if market is open
        if not self.is_market_open():
            logger.info("Market is closed, skipping cycle")
            return self.exchange.get_account()
        
        # Fetch latest data
        latest_data = self._fetch_latest_data()
        
        if not latest_data:
            logger.warning("No data received in this cycle")
            return self.exchange.get_account()
        
        # Update exchange prices
        self._update_exchange_prices(latest_data)
        
        # Process signals
        self._process_signals(latest_data)
        
        # Record account state
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
        
        self.running = True
        start_time = time.time()
        logger.info(f"Starting trading loop with polling interval {self.polling_interval} seconds")
        
        try:
            while self.running:
                cycle_start = time.time()
                
                # Check if run duration exceeded
                if run_duration and (time.time() - start_time) > run_duration:
                    logger.info(f"Run duration of {run_duration} seconds exceeded, stopping")
                    self.stop()
                    break
                
                # Run a single cycle
                self.run_once_for_backtest_mode()
                
                # Calculate time to sleep
                elapsed = time.time() - cycle_start
                sleep_time = max(0, (self.polling_interval - elapsed) / self.speedup)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except KeyboardInterrupt:
            logger.info("Trading loop interrupted by user")
        finally:
            # Ensure we disconnect from exchange
            self.exchange.disconnect()
            self.running = False
    
    def stop(self) -> None:
        """Stop the trading loop."""
        if self.running:
            logger.info("Stopping trading loop")
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