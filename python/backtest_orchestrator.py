# %%
from typing import List
import pandas as pd
from pathlib import Path

from src.backtester.engine import BacktestEngine
from src.backtester.metrics import summarize_metrics
from src.backtester.parameters import load_strategy_parameters, StrategyConfig
from src.backtester.data_fetcher import fetch_backtest_data
from src.strategies.orb import ORBStrategy
from src.risk_management.fixed_atr_stop import FixedATRStop

strategy_name = "ORB"

def BackTestOrchestrator():
    # Paths
    results_dir = Path(__file__).parent / 'results'
    results_file = results_dir / f'{strategy_name}_backtest_results.csv'

    # Ensure results directory exists
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load parameter configurations
    configs: List[StrategyConfig] = load_strategy_parameters()
    all_results = []

    # Fetch all ticker data
    ticker_data = fetch_backtest_data()

    # for idx, config in enumerate(configs):
    config = configs[0]  # For testing, only run the first config
    print(f"Running backtest 1/{len(configs)}: {config}")
    for ticker, data in ticker_data.items():
        print(f"Ticker: {ticker}")
        # Initialize strategy and risk manager with config
        strategy = ORBStrategy(strategy_config=config)
        risk_manager = FixedATRStop(config.risk)

        # Run backtest
        engine = BacktestEngine(strategy, risk_manager, data, config)
        trades = engine.run()

        # Compute metrics
        metrics = summarize_metrics(trades)

        # Merge config, ticker, and metrics
        result = {"ticker": ticker, **config.__dict__, **metrics}
        all_results.append(result)

    # Save all results to CSV
    df_results = pd.DataFrame(all_results)
    df_results.to_csv(results_file, index=False)
    print(f"All backtest results saved to {results_file}")


BackTestOrchestrator()
# %%
