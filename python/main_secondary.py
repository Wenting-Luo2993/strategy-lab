#!/usr/bin/env python
"""
Interactive script for strategy backtesting and verification.

Can be run in multiple ways:
1. Jupyter/VS Code: Run cells individually with # %% markers
2. Terminal (all sections): python main_secondary.py
3. Terminal (interactive): python -i main_secondary.py (then call section functions)
4. IPython: ipython -i main_secondary.py (then call section functions)

When run interactively, you can execute individual sections:
  >>> run_section_1()  # Basic backtest
  >>> run_section_2()  # Cached visualization
  >>> run_section_7()  # Cache verification
  >>> run_section_8(ticker='AAPL', timeframe='5m')  # Inspect indicator state
"""

def run_section_1():
    """Section 1: Basic AAPL ORB Backtest with visualization"""
    # %%
    from matplotlib import pyplot as plt
    from src.risk_management.fixed_atr_stop import FixedATRStop
    from src.back_test.data_fetcher import fetch_backtest_data
    from src.back_test.engine import BacktestEngine
    from src.config.parameters import load_strategy_parameters
    from src.data.base import DataLoaderFactory, DataSource, Timeframe
    from src.data.cache import CacheDataLoader
    from src.indicators import add_basic_indicators, calculate_orb_levels
    from src.strategies.orb import ORBStrategy
    from src.visualization.charts import plot_candlestick
    import mplfinance as mpf

    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    cachedLoader = CacheDataLoader(loader)  # Wrap with cache
    df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start="2025-08-05", end="2025-09-28")

    df = add_basic_indicators(df)

    df = calculate_orb_levels(df)  # First 5 minutes

    # 2. Strategy & risk management
    configs = load_strategy_parameters()
    print(f"Loaded {len(configs)} strategy configurations.")
    strategy = ORBStrategy(strategy_config=configs[0])
    signals = strategy.generate_signals(df)
    risk_manager = FixedATRStop(configs[0].risk)

    # 3. Backtest
    backtester = BacktestEngine(strategy=strategy, risk_manager=risk_manager, initial_capital=10000)
    backtester.run(df, signals)  # Run the backtest

    # Get results using the specific methods
    result = backtester.get_result_dataframe()  # Get result dataframe with equity column
    trades = backtester.get_trades()  # Get list of trades

    print("Equity curve: ", result["equity"].tail())
    print("Number of trades: ", len(trades))

    # 4. Visualize equity curve vs. price
    buy_markers = df["low"].where(signals == 1) - 0.2  # place arrow at close price
    sell_markers = df["high"].where(signals == -1) + 0.2  # place arrow at close price
    apds = [
        mpf.make_addplot(buy_markers, type='scatter', markersize=200, marker='^', color='g'),
        mpf.make_addplot(sell_markers, type='scatter', markersize=200, marker='v', color='r')
    ]
    print("Buy signals:", buy_markers.dropna().head())
    print("Sell signals:", sell_markers.dropna().head())

    plot_candlestick(result, indicators=["SMA_20", "ORB_High", "ORB_Low", "equity"], moreplots=apds, title="AAPL ORB Backtest")


def run_section_2():
    """Section 2: Cached last day visualization"""
    # %% Cached last day visualization
    # Imports specific to this visualization example moved into their own cell
    from datetime import datetime
    from src.visualization.charts import plot_cache_time_range
    ticker = "AMZN"
    try:
        last_day = datetime(2025, 10, 30)
        start_date = datetime(2025, 10, 29)
        plot_cache_time_range(
            ticker,
            timeframe="5m",
            start=start_date,
            end=last_day,
            indicators=["ORB_High", "ORB_Low"],
            title=f"{ticker} Last Day {last_day} (Cached)",
            max_rows=600,
            cache_dir="data_cache",
        )
    except Exception as e:
            print(f"Failed to plot last day range: {e}")

    print("Main function executed successfully.")


def run_section_3():
    """Section 3: Test fetch data for backtester"""
    # %% Test fetch data for backtester
    from src.back_test.data_fetcher import fetch_backtest_data
    fetch_backtest_data()


def run_section_4():
    """Section 4: Fetch and display AAPL data with cache"""
    # %%
    from datetime import datetime, timedelta
    from src.data.base import DataLoaderFactory, DataSource, Timeframe
    from src.data.cache import CacheDataLoader
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    cachedLoader = CacheDataLoader(loader)  # Wrap with cache
    end_date = datetime(2025, 10, 26).date()
    start_date = end_date - timedelta(days=55)
    df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start=start_date, end=end_date)

    print(df.head())
    print(df.tail())
    print(df.describe())
    return df


def run_section_5():
    """Section 5: Run backtest orchestrator"""
    # %% Backtest
    from src.orchestrator.backtest_orchestrator import BackTestOrchestrator

    # Run the orchestrator
    results_df = BackTestOrchestrator(dry_run=False, single_config_index=0)
    return results_df


def run_section_6():
    """Section 6: Upload existing results to Google Drive (skip files with '[DryRun]' in name)"""
    # %% Upload existing results to Google Drive (skip files with '[DryRun]' in name)
    import os
    from pathlib import Path
    from src.utils.google_drive_sync import DriveSync

    results_root_candidates = [Path("python") / "results", Path("results")]  # support both locations
    existing_dirs = [p for p in results_root_candidates if p.exists()]
    if not existing_dirs:
        print("No results directory found; skipping upload.")
    else:
        credentials_path = str(Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")).absolute())
        root_folder_id = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID")
        sync = DriveSync(
            enable=True,
            root_folder_id=root_folder_id,
            use_service_account=False,
            oauth_client_secret_path=Path(
                os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
            ).absolute(),
            oauth_token_path=Path(os.getenv("GOOGLE_OAUTH_CLIENT_TOKEN")).absolute()
        )
        uploaded = 0
        skipped = 0
        for base in existing_dirs:
            for file in base.rglob("*"):
                if file.is_dir():
                    continue
                name = file.name
                if "[DryRun]" in name:
                    skipped += 1
                    continue
                # Relative path under Drive 'results/' preserving subfolders after base
                rel_sub = file.relative_to(base)
                remote_rel = f"results/{rel_sub.as_posix()}"
                try:
                    sync.sync_up(file, remote_rel)
                    uploaded += 1
                except Exception as e:
                    print(f"Failed to upload {file}: {e}")
        print(f"Drive upload complete. Uploaded={uploaded}, Skipped DryRun={skipped}")


def run_section_7():
    """Section 7: Manual functional verification of CacheDataLoader with incremental indicators"""
    # %% Manual functional verification of CacheDataLoader with incremental indicators
    from src.data.base import DataLoaderFactory, DataSource, Timeframe
    from src.data.cache import CacheDataLoader

    print("\n=== CacheDataLoader Functional Verification ===")
    print("Testing automatic incremental indicator calculation with latest data fetch\n")

    # Create Yahoo data loader and wrap with cache
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    cached_loader = CacheDataLoader(
        wrapped_loader=loader,
        cache_dir="data_cache",
        indicator_mode="incremental"  # Enable incremental indicators (default)
    )

    # Fetch AAPL without start/end dates - will fetch latest missing segment
    print("Fetching AAPL 5m data (no start/end specified - fetching latest)...")
    df = cached_loader.fetch("AAPL", timeframe=Timeframe.MIN_5.value)

    print(f"\nDataFrame shape: {df.shape}")
    print(f"Date range: {df.index.min()} to {df.index.max()}")
    print(f"Columns: {list(df.columns)}")

    # Verify CORE_INDICATORS are present
    from src.config.indicators import CORE_INDICATORS
    print(f"\nVerifying CORE_INDICATORS: {CORE_INDICATORS}")
    for indicator in CORE_INDICATORS:
        if indicator == "orb_levels":
            # Check ORB columns
            orb_cols = ["ORB_High", "ORB_Low", "ORB_Range", "ORB_Breakout"]
            present = [col for col in orb_cols if col in df.columns]
            print(f"  ORB columns present: {present}")
        else:
            # Check standard indicator columns
            col_name = f"ATRr_{indicator.split('_')[1]}" if indicator.startswith("ATR") else indicator
            if col_name in df.columns:
                non_null = df[col_name].notna().sum()
                print(f"  ✓ {col_name}: {non_null}/{len(df)} non-null values")
            else:
                print(f"  ✗ {col_name}: NOT FOUND")

    print("\nLast 5 rows with indicators:")
    display_cols = ["open", "high", "low", "close", "volume"]
    indicator_cols = [col for col in df.columns if col not in ["open", "high", "low", "close", "volume"]]
    print(df[display_cols + indicator_cols[:5]].tail())

    print("\n=== Verification Complete ===\n")
    return df


def run_section_8(ticker="AAPL", timeframe="5m"):
    """Section 8: Inspect indicator state .pkl file for a given ticker and timeframe

    Args:
        ticker: Stock symbol (default: AAPL)
        timeframe: Timeframe string (default: 5m)
    """
    # %% Inspect indicator state .pkl file
    import pickle
    from pathlib import Path
    from src.utils.workspace import resolve_workspace_path
    from src.config.indicators import CORE_INDICATORS

    print(f"\n=== Indicator State Inspection for {ticker} ({timeframe}) ===")

    # Construct state file path using resolve_workspace_path
    state_filename = f"{ticker}_{timeframe}_indicators.pkl"
    state_path = resolve_workspace_path(f"data_cache/{state_filename}")

    if not state_path.exists():
        print(f"\n✗ State file not found: {state_path}")
        print(f"\nTry fetching data first with run_section_4() or run_section_7()")
        return None

    print(f"\n✓ State file found: {state_path}")
    print(f"  File size: {state_path.stat().st_size:,} bytes")
    print(f"  Last modified: {state_path.stat().st_mtime}")

    # Load the pickle file
    try:
        with open(state_path, "rb") as f:
            state_data = pickle.load(f)

        print(f"\nState file loaded successfully!")
        print(f"Type: {type(state_data)}")

        if isinstance(state_data, dict):
            print(f"\nOuter dictionary keys: {list(state_data.keys())}")

            # The state file has structure: {(ticker, timeframe): {indicator_name: indicator_obj}}
            # Find the matching key
            matching_key = None
            for key in state_data.keys():
                if isinstance(key, tuple) and len(key) == 2:
                    if key[0] == ticker and key[1] == timeframe:
                        matching_key = key
                        break

            if matching_key is None:
                print(f"\n⚠ No matching key found for ({ticker}, {timeframe})")
                print(f"   Available keys: {list(state_data.keys())}")
                return state_data

            state_metadata = state_data[matching_key]
            print(f"\nFound state data for key: {matching_key}")
            print(f"\nMetadata structure:")
            for key, val in state_metadata.items():
                val_type = type(val).__name__
                if hasattr(val, '__len__') and not isinstance(val, str):
                    print(f"  - {key}: {val_type} (length: {len(val)})")
                else:
                    print(f"  - {key}: {val_type} = {val}")

            # The actual indicators are nested inside the 'indicators' key
            if 'indicators' not in state_metadata:
                print(f"\n⚠ No 'indicators' key found in state metadata")
                return state_data

            indicators_dict = state_metadata['indicators']
            print(f"\nNumber of indicators in state: {len(indicators_dict)}")
            print(f"\nIndicators stored:")

            total_datapoints = 0
            optimal_datapoints = 0
            for ind_name, indicator in indicators_dict.items():
                indicator_type = type(indicator).__name__

                # Try to get indicator value/length info
                try:
                    if hasattr(indicator, '__len__'):
                        length = len(indicator)
                        print(f"  - {ind_name}: {indicator_type} (length: {length})")
                    else:
                        print(f"  - {ind_name}: {indicator_type}")

                    # Try to inspect the wrapper contents
                    if isinstance(indicator, dict) and 'wrapper' in indicator:
                        wrapper = indicator['wrapper']
                        wrapper_type = type(wrapper).__name__
                        if hasattr(wrapper, '__len__'):
                            wrapper_len = len(wrapper)
                            total_datapoints += wrapper_len

                            # Calculate optimal size based on indicator type
                            ind_type = ind_name.split('_')[0]
                            if 'length=' in ind_name:
                                param_val = int(ind_name.split('length=')[1].split('_')[0])
                                if ind_type == 'ema':
                                    optimal = param_val * 3
                                elif ind_type == 'rsi':
                                    optimal = param_val * 2 + 10
                                elif ind_type == 'atr':
                                    optimal = param_val + 10
                                else:
                                    optimal = 300
                                optimal_datapoints += optimal
                                efficiency = (optimal / wrapper_len * 100) if wrapper_len > 0 else 100
                                print(f"      Wrapper: {wrapper_type} with {wrapper_len:,} datapoints (optimal: ~{optimal}, efficiency: {efficiency:.1f}%)")
                            else:
                                print(f"      Wrapper: {wrapper_type} with {wrapper_len:,} datapoints")

                        # Try to show last value
                        if hasattr(wrapper, 'value'):
                            last_val = wrapper.value
                            if last_val is not None:
                                print(f"      Last value: {last_val:.4f}" if isinstance(last_val, float) else f"      Last value: {last_val}")
                except Exception as e:
                    print(f"  - {ind_name}: {indicator_type} (inspection failed: {e})")

            print(f"\n📊 Storage Analysis:")
            print(f"  Current datapoints: {total_datapoints:,}")
            if optimal_datapoints > 0:
                print(f"  Optimal datapoints: {optimal_datapoints:,}")
                savings = (1 - optimal_datapoints / total_datapoints) * 100 if total_datapoints > 0 else 0
                print(f"  Potential space savings: {savings:.1f}%")

            # Compare with CORE_INDICATORS
            # Note: State file uses keys like "ema_length=20", while config uses "EMA_20"
            print(f"\nCore indicators defined in config: {CORE_INDICATORS}")

            # Create mapping from config format to state file format
            config_to_state_mapping = {
                'EMA_20': 'ema_length=20',
                'EMA_30': 'ema_length=30',
                'EMA_50': 'ema_length=50',
                'EMA_200': 'ema_length=200',
                'RSI_14': 'rsi_length=14',
                'ATR_14': 'atr_length=14',
                'orb_levels': lambda keys: any('orb_levels' in k for k in keys)
            }

            missing = []
            found = []
            for config_name in CORE_INDICATORS:
                if config_name == 'orb_levels':
                    if any('orb_levels' in k for k in indicators_dict.keys()):
                        found.append(config_name)
                    else:
                        missing.append(config_name)
                else:
                    state_key = config_to_state_mapping.get(config_name, config_name)
                    if state_key in indicators_dict:
                        found.append(f"{config_name} → {state_key}")
                    else:
                        missing.append(config_name)

            if found:
                print(f"\n✓ Found in state file:")
                for item in found:
                    print(f"  - {item}")

            if missing:
                print(f"\n⚠ Missing from state file: {missing}")
            else:
                print(f"\n✓ All core indicators present in state file!")
        else:
            print(f"\nWarning: State data is not a dictionary. Structure:")
            print(state_data)

        print("\n=== Inspection Complete ===\n")
        return state_data

    except Exception as e:
        print(f"\n✗ Error loading state file: {e}")
        import traceback
        traceback.print_exc()
        return None


# Main execution block for running all sections sequentially
if __name__ == "__main__":
    import sys

    print("=" * 80)
    print("Interactive Strategy Lab Script")
    print("=" * 80)
    print("\nAvailable sections:")
    print("  run_section_1() - Basic AAPL ORB Backtest with visualization")
    print("  run_section_2() - Cached last day visualization")
    print("  run_section_3() - Test fetch data for backtester")
    print("  run_section_4() - Fetch and display AAPL data with cache")
    print("  run_section_5() - Run backtest orchestrator")
    print("  run_section_6() - Upload results to Google Drive")
    print("  run_section_7() - Cache verification with incremental indicators")
    print("  run_section_8(ticker='AAPL', timeframe='5m') - Inspect indicator state .pkl file")
    print("\nTo run a specific section interactively:")
    print("  python -i main_secondary.py")
    print("  >>> run_section_7()  # Run cache verification")
    print("  >>> run_section_8(ticker='NVDA')  # Inspect NVDA state")
    print("\nTo run in IPython:")
    print("  ipython -i main_secondary.py")
    print("  >>> run_section_7()  # Run cache verification")
    print("  >>> run_section_8()  # Inspect indicator state")
    print("\n" + "=" * 80)

    # If running with arguments, execute specific sections
    if len(sys.argv) > 1:
        section = sys.argv[1]
        if section == "all":
            print("\nRunning all sections...")
            for i in range(1, 9):
                func_name = f"run_section_{i}"
                print(f"\n{'=' * 60}")
                print(f"Executing {func_name}...")
                print('=' * 60)
                try:
                    globals()[func_name]()
                except Exception as e:
                    print(f"Error in {func_name}: {e}")
        elif section.isdigit():
            section_num = int(section)
            func_name = f"run_section_{section_num}"
            if func_name in globals():
                print(f"\nRunning {func_name}...")
                globals()[func_name]()
            else:
                print(f"Section {section_num} not found. Valid sections: 1-8")
        else:
            print(f"Invalid argument: {section}")
            print("Usage: python main_secondary.py [all|1|2|3|4|5|6|7|8]")
    else:
        print("\nNo section specified. Enter interactive mode with: python -i main_secondary.py")
