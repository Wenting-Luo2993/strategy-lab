# %%
from typing import List
import sys
from pathlib import Path
import pandas as pd

from src.utils.logger import setup_root_logger, get_logger
from src.utils.performance import track, end_track, PerformanceTracker
from src.backtester.engine import BacktestEngine
from src.backtester.metrics import summarize_metrics
from src.config.parameters import load_strategy_parameters, StrategyConfig
from src.config.columns import TradeColumns
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
    # Initialize and reset performance tracker
    tracker = PerformanceTracker.get_instance()
    tracker.reset()
    
    # Start tracking the entire orchestrator
    main_tracking = track("BackTestOrchestrator")
    
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
    load_params_tracking = track("load_parameters")
    configs: List[StrategyConfig] = load_strategy_parameters()
    load_time = end_track(load_params_tracking)
    log_info(f"Loaded {len(configs)} strategy configurations in {load_time:.2f}s", console=True)

    # Fetch all ticker data
    fetch_data_tracking = track("fetch_data")
    ticker_data = fetch_backtest_data()
    fetch_time = end_track(fetch_data_tracking)
    log_info(f"Fetched data for {len(ticker_data)} tickers in {fetch_time:.2f}s", console=True)

    def clean_data_add_indicators(data):
        # Filter out tickers with empty data or less than 10 rows
        filter_tracking = track("filter_data")
        data = {ticker: data for ticker, data in data.items() 
                    if not data.empty and len(data) >= 10}
        filter_time = end_track(filter_tracking)
        log_info(f"Filtered ticker data: {len(data)} tickers with sufficient data in {filter_time:.2f}s")
        
        # Ensure required indicators exist based on config
        required_indicators = [{
            'name': 'atr',
            'params': {'length': 14, "use_days": True},
            'column': 'ATRr_14'
        }, {
            'name': 'rsi',
            'params': {'length': 14, "use_days": True},
            'column': 'RSI_14'
        }]
        
        # Ensure all required indicators exist for each ticker
        indicators_tracking = track("add_base_indicators")
        for ticker in data:
            data[ticker] = IndicatorFactory.ensure_indicators(data[ticker], required_indicators)
        indicators_time = end_track(indicators_tracking)
        log_info(f"Added base indicators in {indicators_time:.2f}s")
        
        return data

    clean_data_tracking = track("clean_and_add_indicators")
    ticker_data = clean_data_add_indicators(ticker_data)
    _ = end_track(clean_data_tracking)
    
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
    group_tracking = track("group_configs")
    grouped_configs = group_configs_by_orb_params(configs)
    group_time = end_track(group_tracking)
    log_info(f"Grouped configs in {group_time:.2f}s")
    
    # Process each ticker with all configs grouped by ORB parameters
    # Collect per-config results and all trades for metrics at the end
    all_results: List[dict] = []
    all_trades: List[dict] = []
    for ticker, data in ticker_data.items():
        ticker_tracking = track("process_ticker", {"ticker": ticker})
        log_info(f"Processing ticker: {ticker}", console=True)
        
        # Process each group of configs with the same ORB parameters
        for orb_params, config_group in grouped_configs.items():
            timeframe, start_time, body_pct = orb_params
            
            param_group_tracking = track("process_orb_param_group", 
                                        {"timeframe": timeframe, "start_time": start_time, "body_pct": body_pct})
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
            orb_indicator_tracking = track("calculate_orb_indicators", {"ticker": ticker})
            updated_data = IndicatorFactory.ensure_indicators(data, required_indicators)
            orb_time = end_track(orb_indicator_tracking)
            log_info(f"  Calculated ORB indicators in {orb_time:.2f}s")
            
            # Process each config in this group
            for idx, config in config_group:
                config_tracking = track("process_config", {"ticker": ticker, "config_id": idx})
                # Format nested config properties with prefix
                orb_dict = {f"orb_{k}": v for k, v in config.orb_config.__dict__.items()} 
                risk_dict = {f"risk_{k}": v for k, v in config.risk.__dict__.items()}
                
                log_info(f"Running backtest {idx}/{len(configs)} for ticker {ticker} (ORB: {timeframe}m/{start_time}/{body_pct})")
                
                # Initialize strategy and risk manager with config
                strategy_tracking = track("initialize_strategy")
                strategy = ORBStrategy(strategy_config=config)
                risk_manager = FixedATRStop(config.risk)
                init_time = end_track(strategy_tracking)
                log_info(f"  Initialized strategy and risk manager in {init_time:.2f}s")
                
                # Run backtest
                engine = BacktestEngine(strategy, risk_manager, updated_data, config)
                engine_tracking = track("engine_run", {"ticker": ticker, "config_id": idx})
                engine.run()
                engine_time = end_track(engine_tracking)
                log_info(f"  Ran backtest engine in {engine_time:.2f}s")
                
                # Get trades from the engine
                trades = engine.get_trades()

                # Collect run summary for later export
                result = {
                    "ticker": ticker,
                    "configID": idx,
                    "entry_volume_filter": config.entry_volume_filter,
                    "eod_exit": config.eod_exit,
                }
                result.update(orb_dict)
                result.update(risk_dict)
                all_results.append(result)

                # Store trades with minimal metadata for metrics aggregation
                trades_with_context = [{**trade, "ticker": ticker, "configID": idx} for trade in trades]
                all_trades.extend(trades_with_context)

                # Log the number of trades found
                log_info(f"  Found {len(trades)} trades for ticker {ticker}, config {idx}")
                
                # End tracking for this config
                config_time = end_track(config_tracking)
                
            # End tracking for this parameter group
            param_group_time = end_track(param_group_tracking)
            log_info(f"    Completed processing config group {orb_params} for ticker: {ticker} in {param_group_time:.2}s", console=True)
                
        # End tracking for this ticker
        ticker_time = end_track(ticker_tracking)
        log_info(f"Completed processing ticker {ticker} with all configs in {ticker_time:.2f}s")

    # Log overall completion
    log_info("All tickers and configs processed successfully")

    # Convert collected config results to DataFrame for return value
    df_results = pd.DataFrame(all_results)

    # Persist config metadata mapping for later reference
    df_results.to_csv(results_file, index=False)
    log_info(f"Saved configuration map to {results_file}", console=True)

    # Process all trades after all tickers and configs are processed
    if all_trades:
        log_info("Computing metrics across all trades", console=True)
        metrics_tracking = track("compute_all_metrics")

        # Calculate metrics grouped by ticker regime and configuration
        regime_metrics = summarize_metrics(
            all_trades,
            group_by=[TradeColumns.TICKER_REGIME.value, "configID"]
        )

        metrics_time = end_track(metrics_tracking)
        log_info(f"Computed and saved all metrics in {metrics_time:.2f}s", console=True)

        save_tracking = track("save_all_results")
        df_regime_metrics = pd.DataFrame(regime_metrics)
        regime_results_file = results_dir / f'{file_prefix}{strategy_name}_regime_metrics.csv'
        df_regime_metrics.to_csv(regime_results_file, index=False)
        log_info(f"Saved regime metrics to {regime_results_file}", console=True)

        # Persist raw trades for reference
        trades_file = results_dir / f'{file_prefix}{strategy_name}_trades.csv'
        pd.DataFrame(all_trades).to_csv(trades_file, index=False)
        log_info(f"Saved {len(all_trades)} trades to {trades_file}", console=True)
        _ = end_track(save_tracking)

    # End tracking for the main orchestrator function
    total_time = end_track(main_tracking)
    log_info(f"BackTestOrchestrator completed in {total_time:.2f}s")
    
    # Get and save the performance report
    tracker = PerformanceTracker.get_instance()
    
    # Save performance report to file
    performance_file = results_dir / f'{file_prefix}{strategy_name}_performance_metrics.txt'
    with open(performance_file, 'w') as f:
        f.write(tracker.generate_report(include_root=False))
    log_info(f"Performance report saved to {performance_file}")
    
    # Log slow operations
    tracker.log_slow_operations(threshold_seconds=0.5)
    
    return df_results

# Run the orchestrator
results_df = BackTestOrchestrator(dry_run=dry_run_mode)
# %%
