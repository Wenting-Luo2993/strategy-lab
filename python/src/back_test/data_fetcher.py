import os
import yaml
import time
from datetime import datetime, timedelta
from src.utils.logger import get_logger
from src.data import DataLoaderFactory, DataSource, Timeframe, CacheDataLoader


def get_last_business_day() -> datetime.date:
    """
    Get the last business day (excluding weekends) from yesterday.

    Returns:
        date: The last business day
    """
    current = datetime.now().date()
    last_day = current - timedelta(days=1)

    # If it's a weekend, move to Friday
    while last_day.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        last_day -= timedelta(days=1)

    return last_day

logger = get_logger("DataLoader")

def batch_list(lst, batch_size):
    for i in range(0, len(lst), batch_size):
        yield lst[i:i+batch_size]


def fetch_ticker_data(tickers, interval, start_date, end_date, batch_size=5, max_retries=3, sleep_seconds=10, enable_incremental_indicators=True):
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
        enable_incremental_indicators (bool): Enable automatic incremental indicator calculation (default: True).
    Returns:
        dict: {ticker: DataFrame}
    """
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    loader = DataLoaderFactory.create(DataSource.YAHOO, interval=interval)
    cached_loader = CacheDataLoader(
        wrapped_loader=loader,
        indicator_mode='incremental' if enable_incremental_indicators else 'skip'
    )
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
                        start=start_date,
                        end=end_date
                    )
                    if df.empty:
                        break
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

def fetch_backtest_data(enable_incremental_indicators=True):
    """
    Fetch backtest data for all configured tickers with automatic indicator calculation.

    Args:
        enable_incremental_indicators (bool): Enable automatic incremental indicator calculation.
                                             When True, calculates and caches CORE_INDICATORS for all tickers.
                                             Default: True

    Returns:
        dict: {ticker: DataFrame with indicators}
    """
    logger.info("Current directory: %s", os.getcwd())
    config_path = os.path.join(os.path.dirname(__file__), "../config/backtest_tickers.yaml")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    tickers = [t for _, tickers in config.items() for t in tickers]

    if enable_incremental_indicators:
        from src.config.indicators import CORE_INDICATORS
        logger.info(f"Fetching data for {len(tickers)} tickers with incremental indicators: {CORE_INDICATORS}")
    else:
        logger.info(f"Fetching data for {len(tickers)} tickers (indicators disabled)")

    end_date = get_last_business_day()
    logger.info(f"Using end date: {end_date} (last business day)")

    data = fetch_ticker_data(
        tickers,
        interval=Timeframe.MIN_5.value,
        start_date=None,
        end_date=end_date,
        batch_size=5,
        enable_incremental_indicators=enable_incremental_indicators
    )
    logger.info("Fetched data for %d tickers.", len(data))

    if enable_incremental_indicators:
        logger.info("All tickers now have cached indicator states (.pkl files) for fast incremental updates")

    return data
