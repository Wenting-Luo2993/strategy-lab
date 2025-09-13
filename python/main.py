from src.data import DataLoaderFactory, DataSource, Timeframe, CacheDataLoader
from src.indicators import add_basic_indicators, calculate_orb_levels
from src.visualization import plot_candlestick

def main():
    # Example: Yahoo loader
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    cachedLoader = CacheDataLoader(loader)  # Wrap with cache
    df = cachedLoader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start="2025-08-01", end="2025-08-05")
    
    print("Raw data fetched:")
    print(df.head())

    df = add_basic_indicators(df)
    print("Data with technical indicators:")
    print(df.tail())

    plot_candlestick(df, indicators=["SMA_20", "SMA_50"], title="AAPL with SMA20/50")
    
    df = calculate_orb_levels(df, bars=1)  # First 5 minutes
    print("Data with ORB levels:")
    print(df.tail())
    print("Main function executed successfully.")

if __name__ == "__main__":
    main()
