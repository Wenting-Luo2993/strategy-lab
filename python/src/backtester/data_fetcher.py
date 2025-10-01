import os
import yaml
import time
from datetime import datetime, timedelta
from src.utils.logger import get_logger
from src.data import DataLoaderFactory, DataSource, Timeframe, CacheDataLoader

logger = get_logger("DataLoader")

def batch_list(lst, batch_size):
    for i in range(0, len(lst), batch_size):
        yield lst[i:i+batch_size]


def fetch_ticker_data(tickers, interval, start_date, end_date, batch_size=5, max_retries=3, sleep_seconds=60):
    """
    Fetches data for a list of tickers between start_date and end_date.
    Args:
        tickers (list): List of ticker symbols.
        interval (str): Data interval (e.g., '5m').
        start_date (str or date): Start date (YYYY-MM-DD or date object).
        end_date (str or date): End date (YYYY-MM-DD or date object).
        batch_size (int): Number of tickers per batch.
        max_retries (int): Max retry attempts per ticker.
        sleep_seconds (int): Sleep time between retries (seconds).
    Returns:
        dict: {ticker: DataFrame}
    """
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=interval)
    cached_loader = CacheDataLoader(loader)
    results = {}
    for batch in batch_list(tickers, batch_size):
        for ticker in batch:
            attempt = 0
            while attempt < max_retries:
                try:
                    logger.info(f"Fetching {ticker} ({interval}) [{start_date} - {end_date}], attempt {attempt+1}")
                    df = cached_loader.fetch(
                        ticker,
                        timeframe=interval,
                        start=str(start_date),
                        end=str(end_date)
                    )
                    results[ticker] = df
                    break
                except Exception as e:
                    logger.error(f"Error fetching {ticker}: {e}")
                    attempt += 1
                    if attempt < max_retries:
                        logger.info(f"Retrying {ticker} after {sleep_seconds} seconds...")
                        time.sleep(sleep_seconds)
                    else:
                        logger.error(f"Failed to fetch {ticker} after {max_retries} attempts.")
    return results

def fetch_backtest_data():
    logger.info("Current directory: %s", os.getcwd())
    config_path = os.path.join(os.path.dirname(__file__), "../config/backtest_tickers.yaml")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    tickers = [t for _, tickers in config.items() for t in tickers]
    logger.info("Fetching data for tickers: %s", tickers)
    end_date = datetime(2025, 9, 29).date()
    start_date = end_date - timedelta(days=55)
    data = fetch_ticker_data(tickers, interval=Timeframe.MIN_5.value, start_date=start_date, end_date=end_date, batch_size=5)
    logger.info("Fetched data for %d tickers.", len(data))
    return data

