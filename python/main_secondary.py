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

# %% Test fetch data for backtester
from src.back_test.data_fetcher import fetch_backtest_data
fetch_backtest_data()

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

# %% Backtest
from src.orchestrator.backtest_orchestrator import BackTestOrchestrator

# Run the orchestrator
results_df = BackTestOrchestrator(dry_run=False, single_config_index=0)

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
