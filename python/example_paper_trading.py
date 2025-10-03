#!/usr/bin/env python
"""
Example usage script for the DarkTradingOrchestrator and MockExchange.

This script demonstrates a simple paper trading setup using the mock exchange
and dark trading orchestrator with real strategy and risk manager implementations.
"""

import time
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

from src.exchange import MockExchange
from src.orchestrator import DarkTradingOrchestrator
from src.data import DataLoaderFactory, DataSource, CacheDataLoader
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.config.parameters import StrategyConfig, RiskConfig, OrbConfig
from src.indicators import IndicatorFactory


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def add_indicators_to_data(df: pd.DataFrame, strategy_config=None) -> pd.DataFrame:
    """
    Add necessary indicators to the dataframe for strategy and risk management.
    
    Args:
        df: DataFrame with OHLCV data
        strategy_config: Strategy configuration for specific indicators
        
    Returns:
        DataFrame with added indicators
    """
    # Basic indicators needed by most strategies
    indicators = [
        {'name': 'atr', 'params': {'length': 14}},
    ]
    
    # Add ORB indicators if we have a strategy config with ORB parameters
    if strategy_config and hasattr(strategy_config, 'orb_config'):
        start_time = strategy_config.orb_config.start_time
        duration = int(strategy_config.orb_config.timeframe)
        body_pct = float(strategy_config.orb_config.body_breakout_percentage)
        
        indicators.append({
            'name': 'orb_levels',
            'params': {
                'start_time': start_time,
                'duration_minutes': duration,
                'body_pct': body_pct
            }
        })
    
    return IndicatorFactory.apply(df, indicators)


def create_data_loader() -> CacheDataLoader:
    """
    Creates and returns a CacheDataLoader instance.
    
    Returns:
        CacheDataLoader: Data loader for fetching market data
    """
    loader = DataLoaderFactory.create(DataSource.YAHOO)
    return CacheDataLoader(loader)
    
def fetch_data(ticker: str, interval: str = "5m", start: Optional[str] = None, 
               end: Optional[str] = None, limit: Optional[int] = None,
               strategy_config=None) -> pd.DataFrame:
    """
    Simple data fetching function using the project's data loader.
    
    Args:
        ticker: Ticker symbol
        interval: Time interval (e.g., "1m")
        start: Start date (optional)
        end: End date (optional)
        limit: Limit number of bars (optional)
        strategy_config: Strategy configuration for specific indicators
        
    Returns:
        pd.DataFrame: DataFrame with OHLCV data
    """
    try:
        # Create data loader
        loader = DataLoaderFactory.create(DataSource.YAHOO, interval=interval)
        cached_loader = CacheDataLoader(loader)
        
        # Fetch data
        df = cached_loader.fetch(
            ticker,
            timeframe=interval,
            start=start,
            end=end
        )
        
        # Add required indicators for strategy and risk management
        df = add_indicators_to_data(df, strategy_config)
        
        # Apply limit if specified
        if limit and limit > 0 and not df.empty:
            return df.iloc[-limit:]
        
        return df
    
    except Exception as e:
        logger.error(f"Error fetching data for {ticker}: {e}")
        return pd.DataFrame()


def main():
    """Main function to run the example."""
    # Set up directories
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # Tickers to trade
    tickers = ["AAPL", "MSFT", "AMD", "TSLA", "NVDA"]
    
    # Initial sample data to bootstrap the exchange
    initial_data = {}
    for ticker in tickers:
        df = fetch_data(ticker, interval="5m", limit=100, start="2025-08-05", end="2025-09-29")
        if not df.empty:
            initial_data[ticker] = df
            logger.info(f"Loaded {len(df)} bars for {ticker}")
    
    # Create MockExchange with initial data
    exchange = MockExchange(
        initial_capital=10000.0,
        slippage_pct=0.001,  # 0.1% slippage
        commission_per_share=0.005,  # $0.005 per share
        price_data=initial_data,
        trade_log_path=str(results_dir / "mock_exchange_trades.csv")
    )
    exchange.connect()
    
    # Create risk configuration
    risk_config = RiskConfig(
        stop_loss_type="atr",
        stop_loss_value=2.0,  # 2x ATR for stop loss
        take_profit_type="atr",
        take_profit_value=4.0,  # 4x ATR for take profit
        risk_per_trade=0.01  # 1% risk per trade
    )
    
    # Create ORB configuration
    orb_config = OrbConfig(
        timeframe="5",        # 5-minute opening range
        start_time="09:30",   # Market open time
        body_breakout_percentage=0.5  # 50% body breakout threshold
    )
    
    # Create complete strategy configuration
    strategy_config = StrategyConfig(
        orb_config=orb_config,
        entry_volume_filter=1.5,  # Volume must be 1.5x average
        risk=risk_config,
        eod_exit=True  # Exit positions at end of day
    )
    
    # Create strategy and risk manager using project's existing implementations
    strategy = ORBStrategy(breakout_window=5, strategy_config=strategy_config)
    risk_manager = FixedATRStop(config=risk_config)
    
    # Create data fetcher
    data_loader = create_data_loader()
    
    # Create orchestrator
    orchestrator = DarkTradingOrchestrator(
        strategy=strategy,
        risk_manager=risk_manager,
        exchange=exchange,
        data_fetcher=data_loader,
        tickers=tickers,
        initial_capital=10000.0,
        polling_interval_secs=1,  # 1 second for fast demo
        speedup=10.0,  # Run 10x faster
        results_dir=str(results_dir),
        dry_run=False  # Actually submit orders
    )
    
    logger.info("Starting orchestrator")
    
    # Option 1: Run a fixed number of iterations
    for i in range(5):
        logger.info(f"Running iteration {i+1}")
        account = orchestrator.run_once_for_backtest_mode()
        logger.info(f"Account state: {account}")
        time.sleep(0.5)  # Small delay between iterations
    
    # Option 2: Start the continuous loop with duration
    # orchestrator.start(run_duration=30)  # Run for 30 seconds
    
    # Save final state
    orchestrator.save_state()
    logger.info("Example completed")


if __name__ == "__main__":
    main()