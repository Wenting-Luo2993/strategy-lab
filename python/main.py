from src.data import DataLoaderFactory, DataSource, Timeframe, CacheDataLoader
from src.indicators import add_basic_indicators, calculate_orb_levels
from src.visualization import plot_candlestick
from src.strategies.orb import ORBStrategy
from src.back_test.engine import Backtester

def main():
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    cachedLoader = CacheDataLoader(loader)  # Wrap with cache
    df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start="2025-08-01", end="2025-08-05")

    df = add_basic_indicators(df)

    df = calculate_orb_levels(df)  # First 5 minutes
    print("Data with ORB levels:")
    print(df.tail())

    # 2. Strategy
    strategy = ORBStrategy()
    signals = strategy.generate_signals(df)

    # 3. Backtest
    backtester = Backtester(initial_capital=10000)
    result = backtester.run(df, signals)

    result
    print(result["ORB_High"].tail())

    # 4. Visualize equity curve vs. price
    # plot_candlestick(result, indicators=["SMA_20", "SMA_50",], title="AAPL ORB Backtest")

    print("Main function executed successfully.")

if __name__ == "__main__":
    main()
