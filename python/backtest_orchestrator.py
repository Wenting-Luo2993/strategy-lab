import os
import pandas as pd
from pathlib import Path
from src.backtester.engine import BacktestEngine
from src.backtester.metrics import summarize_metrics
from src.backtester.parameters import load_strategy_parameters, StrategyConfig

def main():
    # Paths
    config_path = Path(__file__).parent.parent / 'src' / 'config' / 'backtest_parameters.yaml'
    results_dir = Path(__file__).parent / 'results'
    results_file = results_dir / 'backtest_results.csv'

    # Ensure results directory exists
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load parameter configurations
    configs = load_strategy_parameters(config_path)
    all_results = []

    for idx, config in enumerate(configs):
        print(f"Running backtest {idx+1}/{len(configs)}: {config}")
        # Load data (implement your own data loading logic)
        data = ...  # TODO: Load OHLCV data as pd.DataFrame
        strategy = ...  # TODO: Initialize strategy object
        risk_manager = ...  # TODO: Initialize risk manager object

        # Run backtest
        engine = BacktestEngine(strategy, risk_manager, data, config)
        trades = engine.run()

        # Compute metrics
        metrics = summarize_metrics(trades)

        # Merge config and metrics
        result = {**config, **metrics}
        all_results.append(result)

    # Save all results to CSV
    df_results = pd.DataFrame(all_results)
    df_results.to_csv(results_file, index=False)
    print(f"All backtest results saved to {results_file}")

if __name__ == "__main__":
    main()
