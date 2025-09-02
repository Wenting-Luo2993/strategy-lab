from src.data import DataLoaderFactory, DataSource, Timeframe
from src.indicators import add_basic_indicators, calculate_orb_levels

def main():
    # Example: Yahoo loader
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
    df = loader.fetch("AAPL", start="2025-08-01", end="2025-08-05")
    print("Raw data fetched:")
    print(df.head())
    df = add_basic_indicators(df)
    print("Data with technical indicators:")
    print(df.tail())
    df = calculate_orb_levels(df, bars=1)  # First 5 minutes
    print("Data with ORB levels:")
    print(df['ORB_Low'].head())
    print("Main function executed successfully.")

if __name__ == "__main__":
    main()
