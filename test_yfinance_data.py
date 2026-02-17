#!/usr/bin/env python3
"""
Test script to validate yfinance data fetching works correctly.
Tests both period-based and date-range-based fetching.
"""

import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd


def test_period_based_fetch():
    """Test fetching data using period (recommended approach)."""
    print("=" * 60)
    print("TEST 1: Period-Based Fetch (Recommended)")
    print("=" * 60)

    symbols = ["AAPL", "GOOGL", "MSFT"]

    for symbol in symbols:
        print(f"\nFetching {symbol}...")
        try:
            ticker = yf.Ticker(symbol)

            # Fetch last 5 days of 5-minute data
            df = ticker.history(period="5d", interval="5m")

            if df.empty:
                print(f"  [FAIL] No data returned for {symbol}")
            else:
                print(f"  [OK] Success! Got {len(df)} bars")
                print(f"     Date range: {df.index[0]} to {df.index[-1]}")
                print(f"     Columns: {list(df.columns)}")
                print(f"     Sample data:")
                print(df.head(3))
        except Exception as e:
            print(f"  [ERROR] {e}")


def test_date_range_fetch():
    """Test fetching data using specific date range."""
    print("\n" + "=" * 60)
    print("TEST 2: Date-Range-Based Fetch")
    print("=" * 60)

    # Calculate dates: 30 days ago to yesterday (avoid today to prevent future date issues)
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=30)

    print(f"\nDate range: {start_date.date()} to {end_date.date()}")

    symbols = ["AAPL", "GOOGL", "MSFT"]

    for symbol in symbols:
        print(f"\nFetching {symbol}...")
        try:
            ticker = yf.Ticker(symbol)

            df = ticker.history(
                start=start_date.date(),
                end=end_date.date(),
                interval="1d"  # Daily data for 30-day range
            )

            if df.empty:
                print(f"  [FAIL] No data returned for {symbol}")
            else:
                print(f"  [OK] Success! Got {len(df)} bars")
                print(f"     Date range: {df.index[0]} to {df.index[-1]}")
                print(f"     Sample data:")
                print(df[['Open', 'High', 'Low', 'Close', 'Volume']].head(3))
        except Exception as e:
            print(f"  [ERROR] {e}")


def test_safe_date_range():
    """Test fetching with guaranteed safe date range (historical dates)."""
    print("\n" + "=" * 60)
    print("TEST 3: Safe Historical Date Range (Known Good Dates)")
    print("=" * 60)

    # Use known historical dates that definitely have data
    end_date = datetime(2024, 2, 1)  # Feb 1, 2024
    start_date = datetime(2024, 1, 1)  # Jan 1, 2024

    print(f"\nDate range: {start_date.date()} to {end_date.date()}")

    symbol = "AAPL"
    print(f"\nFetching {symbol}...")
    try:
        ticker = yf.Ticker(symbol)

        df = ticker.history(
            start=start_date.date(),
            end=end_date.date(),
            interval="1d"
        )

        if df.empty:
            print(f"  [FAIL] No data returned for {symbol}")
        else:
            print(f"  [OK] Success! Got {len(df)} bars")
            print(f"     Date range: {df.index[0]} to {df.index[-1]}")
            print(f"     Sample data:")
            print(df[['Open', 'High', 'Low', 'Close', 'Volume']].head(5))
    except Exception as e:
        print(f"  [ERROR] {e}")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("YFINANCE DATA FETCHING VALIDATION")
    print("=" * 60)
    print(f"\nCurrent system time: {datetime.now()}")
    print(f"Testing data fetching with yfinance library...\n")

    # Run tests
    test_period_based_fetch()
    test_date_range_fetch()
    test_safe_date_range()

    print("\n" + "=" * 60)
    print("TESTS COMPLETED")
    print("=" * 60)
    print("\nRecommendation:")
    print("  [OK] Use period-based fetching (e.g., period='5d')")
    print("  [OK] This avoids future date issues with system clock")
    print("  [OK] yfinance handles the date logic automatically")
    print()


if __name__ == "__main__":
    main()
