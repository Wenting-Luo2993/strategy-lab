
# %%
from matplotlib import pyplot as plt
from src.backtester.engine import Backtester
from src.data.base import DataLoaderFactory, DataSource, Timeframe
from src.data.cache import CacheDataLoader
from src.indicators import add_basic_indicators, calculate_orb_levels
from src.strategies.orb import ORBStrategy
from src.visualization.charts import plot_candlestick
import mplfinance as mpf


loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
cachedLoader = CacheDataLoader(loader)  # Wrap with cache
df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start="2025-08-01", end="2025-08-05")

df = add_basic_indicators(df)

df = calculate_orb_levels(df)  # First 5 minutes

# 2. Strategy
strategy = ORBStrategy()
signals = strategy.generate_signals(df)

# 3. Backtest
backtester = Backtester(initial_capital=10000)
result = backtester.run(df, signals)

print(result["ORB_High"].tail())

# 4. Visualize equity curve vs. price
buy_markers = df["low"].where(signals == 1) - 0.2  # place arrow at close price
sell_markers = df["high"].where(signals == -1) + 0.2  # place arrow at close price
apds = [
    mpf.make_addplot(buy_markers, type='scatter', markersize=200, marker='^', color='g'),
    mpf.make_addplot(sell_markers, type='scatter', markersize=200, marker='v', color='r')
]
print("Buy signals:", buy_markers.dropna().head())
print("Sell signals:", sell_markers.dropna().head())

plot_candlestick(result, indicators=["SMA_20", "ATRr_14", "ORB_High", "ORB_Low"], moreplots=apds, title="AAPL ORB Backtest")


print("Main function executed successfully.")

# %%
