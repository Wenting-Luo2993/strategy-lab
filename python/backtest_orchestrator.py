# %%
import logging
from typing import List
import pandas as pd
from pathlib import Path

from src.utils.logger import setup_root_logger
from src.backtester.engine import BacktestEngine
from src.backtester.metrics import summarize_metrics
from src.backtester.parameters import load_strategy_parameters, StrategyConfig
from src.backtester.data_fetcher import fetch_backtest_data
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.indicators import IndicatorFactory

strategy_name = "ORB"
setup_root_logger()

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
    logging.info(f"Grouped {len(configs)} configs into {len(grouped_configs)} unique ORB parameter sets")
    for key, configs_list in grouped_configs.items():
        logging.info(f"  ORB params {key}: {len(configs_list)} configs")
        
    return grouped_configs

def BackTestOrchestrator():
    print("Starting BackTest Orchestrator")
    # Paths
    results_dir = Path(__file__).parent / 'results'
    results_file = results_dir / f'{strategy_name}_backtest_results.csv'

    # Ensure results directory exists
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load parameter configurations
    configs: List[StrategyConfig] = load_strategy_parameters()
    print(f"Loaded {len(configs)} strategy configurations")

    # Fetch all ticker data
    ticker_data = fetch_backtest_data()
    print(f"Fetched data for {len(ticker_data)} tickers")

    def clean_data_add_indicators(data):
        # Filter out tickers with empty data or less than 10 rows
        data = {ticker: data for ticker, data in data.items() 
                    if not data.empty and len(data) >= 10}
        logging.info(f"Filtered ticker data: {len(data)} tickers with sufficient data.")
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
    
    # Group configs by unique ORB parameters
    grouped_configs = group_configs_by_orb_params(configs)
    
    # Process each ticker with all configs grouped by ORB parameters
    all_results = []
    for ticker, data in ticker_data.items():
        logging.info(f"Processing ticker: {ticker}")
        print(f"Start processing ticker: {ticker}")
        
        # Process each group of configs with the same ORB parameters
        for orb_params, config_group in grouped_configs.items():
            timeframe, start_time, body_pct = orb_params
            
            print(f"  Processing ORB params: {orb_params} with {len(config_group)} configs")
            logging.info(f"Processing ORB parameter group: {timeframe}m from {start_time} with {body_pct} body percent")
            logging.info(f"This group contains {len(config_group)} different configs")
            
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
                
                logging.info(f"Running backtest {idx}/{len(configs)} for ticker {ticker} (ORB: {timeframe}m/{start_time}/{body_pct})")
                
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
            print(f"    Completed processing config group {orb_params} for ticker: {ticker}")
                
        # Log completion for this parameter group
        logging.info(f"Completed processing ticker {ticker} with all configs")
    
    # Log overall completion    
    logging.info("All tickers and configs processed successfully")

    # Save all results to CSV
    df_results = pd.DataFrame(all_results)
    df_results.to_csv(results_file, index=False)
    print(f"All backtest results saved to {results_file}")


BackTestOrchestrator()
# %%
