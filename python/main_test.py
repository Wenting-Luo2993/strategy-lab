
# %%
from matplotlib import pyplot as plt
from src.risk_management.fixed_atr_stop import FixedATRStop
from src.backtester.data_fetcher import fetch_backtest_data
from src.backtester.engine import BacktestEngine
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

print("Main function executed successfully.")

# %%
from src.backtester.data_fetcher import fetch_backtest_data
fetch_backtest_data()

# %%
from datetime import datetime, timedelta
from src.data.base import DataLoaderFactory, DataSource, Timeframe
from src.data.cache import CacheDataLoader
loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
cachedLoader = CacheDataLoader(loader)  # Wrap with cache
end_date = datetime(2025, 10, 3).date()
start_date = end_date - timedelta(days=55)
df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start=start_date, end=end_date)

print(df.tail())
print(df.describe())

# %%
