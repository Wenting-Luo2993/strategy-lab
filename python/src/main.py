from src.data import DataLoaderFactory, DataSource, Timeframe

def main():
    # Example: Yahoo loader
    loader = DataLoaderFactory.create(DataSource.YAHOO)
    df = loader.fetch("AAPL", start="2024-01-01", end="2024-03-01")
    print(df.head())
    print("Main function executed successfully.")

if __name__ == "__main__":
    main()
