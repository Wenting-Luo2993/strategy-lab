# %%
import logging
from typing import List
import pandas as pd
from pathlib import Path

from src.utils.logger import setup_root_logger, get_logger
from src.backtester.engine import BacktestEngine
from src.backtester.metrics import summarize_metrics
from src.backtester.parameters import load_strategy_parameters, StrategyConfig
from src.backtester.data_fetcher import fetch_backtest_data
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.indicators import IndicatorFactory

# Set dry_run=True for quick testing or False for full run
dry_run_mode = True
strategy_name = "ORB"

setup_root_logger()
file_only_logger = get_logger("BacktestOrchestrator")
file_console_logger = get_logger("BacktestOrchestrator", log_to_console=True)

def log_info(message: str, console: bool = False):
    if console:
        file_console_logger.info(message)
    else:
        file_only_logger.info(message)

def group_configs_by_orb_params(configs: List[StrategyConfig]):
    """
    Group configs by their unique ORB parameters to minimize indicator recalculations.
    
    Returns a dictionary where:
    - Keys are tuples of (timeframe, start_time, body_breakout_percentage)
    - Values are lists of (index, config) pairs that share those parameters
    """
    grouped_configs = {}
    
    for idx, config in enumerate(configs):
        # Create a key tuple with the ORB parameters
        key = (
            config.orb_config.timeframe,
            config.orb_config.start_time,
            config.orb_config.body_breakout_percentage
        )
        
        # Add this config to the appropriate group
        if key not in grouped_configs:
            grouped_configs[key] = []
        
        grouped_configs[key].append((idx, config))
        
    # Log the grouping results
    log_info(f"Grouped {len(configs)} configs into {len(grouped_configs)} unique ORB parameter sets")
    for key, configs_list in grouped_configs.items():
        log_info(f"  ORB params {key}: {len(configs_list)} configs")

    return grouped_configs

def BackTestOrchestrator(dry_run: bool = False):
    """
    Orchestrate the backtesting process across multiple tickers and configurations.
    
    Args:
        dry_run: If True, only run one config on one ticker as a quick test
    """
    log_info("Starting BackTest Orchestrator", console=True)
    
    # Add dry run mode logging
    if dry_run:
        log_info("RUNNING IN DRY RUN MODE - Testing with one config on one ticker only", console=True)
    
    # Paths
    results_dir = Path(__file__).parent / 'results'
    
    # Add [DryRun] prefix to file name if in dry run mode
    file_prefix = "[DryRun]_" if dry_run else ""
    results_file = results_dir / f'{file_prefix}{strategy_name}_backtest_results.csv'

    # Ensure results directory exists
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load parameter configurations
    configs: List[StrategyConfig] = load_strategy_parameters()
    log_info(f"Loaded {len(configs)} strategy configurations", console=True)

    # Fetch all ticker data
    ticker_data = fetch_backtest_data()
    log_info(f"Fetched data for {len(ticker_data)} tickers", console=True)

    def clean_data_add_indicators(data):
        # Filter out tickers with empty data or less than 10 rows
        data = {ticker: data for ticker, data in data.items() 
                    if not data.empty and len(data) >= 10}
        log_info(f"Filtered ticker data: {len(data)} tickers with sufficient data.")
        # Ensure required indicators exist based on config
        required_indicators = []
        # Check if ATR is needed for stop loss or take profit
        required_indicators.append({
            'name': 'atr',
            'params': {'length': 14},
            'column': 'ATRr_14'
        }) 
        # Ensure all required indicators exist for each ticker
        for ticker in data:
            data[ticker] = IndicatorFactory.ensure_indicators(data[ticker], required_indicators)
        return data

    ticker_data = clean_data_add_indicators(ticker_data)
    
    # If in dry run mode, only use the first ticker and first config
    if dry_run:
        # Take only the first ticker
        first_ticker = next(iter(ticker_data))
        ticker_data = {first_ticker: ticker_data[first_ticker]}
        log_info(f"DRY RUN: Using only ticker {first_ticker}", console=True)
        
        # Take only the first config
        configs = configs[:1]
        log_info("DRY RUN: Using only the first configuration", console=True)
    
    # Group configs by unique ORB parameters
    grouped_configs = group_configs_by_orb_params(configs)
    
    # Process each ticker with all configs grouped by ORB parameters
    all_results = []
    for ticker, data in ticker_data.items():
        log_info(f"Processing ticker: {ticker}", console=True)
        
        # Process each group of configs with the same ORB parameters
        for orb_params, config_group in grouped_configs.items():
            timeframe, start_time, body_pct = orb_params
            
            log_info(f" Processing ORB parameter group: {timeframe}m from {start_time} with {body_pct} body percent", console=True)
            log_info(f" This group contains {len(config_group)} different configs", console=True)
            
            # Calculate ORB levels once for this parameter group
            required_indicators = [{
                'name': 'orb_levels',
                'params': {
                    'start_time': start_time,
                    'duration_minutes': int(timeframe),
                    'body_pct': body_pct
                },
                'column': 'ORB_Breakout'
            }]
            
            # Update data with ORB indicator for this parameter group
            updated_data = IndicatorFactory.ensure_indicators(data, required_indicators)
            
            # Process each config in this group
            for idx, config in config_group:
                # Format nested config properties with prefix
                orb_dict = {f"orb_{k}": v for k, v in config.orb_config.__dict__.items()} 
                risk_dict = {f"risk_{k}": v for k, v in config.risk.__dict__.items()}
                
                log_info(f"Running backtest {idx}/{len(configs)} for ticker {ticker} (ORB: {timeframe}m/{start_time}/{body_pct})")
                
                # Initialize strategy and risk manager with config
                strategy = ORBStrategy(strategy_config=config)
                risk_manager = FixedATRStop(config.risk)

                # Run backtest
                engine = BacktestEngine(strategy, risk_manager, updated_data, config)
                trades = engine.run()

                # Compute metrics
                metrics = summarize_metrics(trades)

                # Create base result with ticker and config ID
                result = {"ticker": ticker, "configID": idx}
                # Add non-nested config properties
                result["entry_volume_filter"] = config.entry_volume_filter
                result["eod_exit"] = config.eod_exit
                
                # Combine all dictionaries
                result.update(orb_dict)
                result.update(risk_dict)
                result.update(metrics)
                
                all_results.append(result)
            log_info(f"    Completed processing config group {orb_params} for ticker: {ticker}", console=True)
                
        # Log completion for this parameter group
        log_info(f"Completed processing ticker {ticker} with all configs", console=True)

    # Log overall completion
    log_info("All tickers and configs processed successfully")

    # Save all results to CSV
    df_results = pd.DataFrame(all_results)
    df_results.to_csv(results_file, index=False)
    log_info(f"All backtest results saved to {results_file}", console=True)

BackTestOrchestrator(dry_run=dry_run_mode)
# %%
