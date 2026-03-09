"""Test ORB calculator fix for multi-day DataFrames."""
import pandas as pd
from datetime import datetime, timedelta
import pytz
from vibe.common.indicators.orb_levels import ORBCalculator

# Create test data with multiple days
est = pytz.timezone('America/New_York')

# Historical data from 3 days ago (like Yahoo Finance)
historical_date = datetime.now(est) - timedelta(days=3)
historical_date = historical_date.replace(hour=9, minute=30, second=0, microsecond=0)

historical_bars = []
for i in range(10):  # 10 bars of historical data
    bar_time = historical_date + timedelta(minutes=i*5)
    historical_bars.append({
        "timestamp": bar_time,
        "open": 100.0 + i,
        "high": 101.0 + i,
        "low": 99.0 + i,
        "close": 100.5 + i,
        "volume": 1000
    })

# Today's data (like Finnhub real-time)
today = datetime.now(est).replace(hour=9, minute=30, second=0, microsecond=0)

todays_bars = [
    {
        "timestamp": today,
        "open": 200.0,
        "high": 202.0,
        "low": 199.0,
        "close": 201.5,  # Strong bullish body (>50%)
        "volume": 5000
    },
    {
        "timestamp": today + timedelta(minutes=5),
        "open": 201.0,
        "high": 203.0,
        "low": 200.0,
        "close": 202.5,  # Strong bullish body
        "volume": 4000
    }
]

# Combine like orchestrator does
all_bars = historical_bars + todays_bars
df = pd.DataFrame(all_bars)

print("=" * 60)
print("ORB Calculator Fix Validation")
print("=" * 60)
print(f"Historical bars: {len(historical_bars)} (from {historical_date.date()})")
print(f"Today's bars: {len(todays_bars)} (from {today.date()})")
print(f"Total DataFrame size: {len(df)}")
print()

# Test ORB calculator
calculator = ORBCalculator(start_time="09:30", duration_minutes=10)

print("Testing ORB calculation...")
levels = calculator.calculate(df)

print()
print(f"Valid: {levels.valid}")
print(f"ORB High: ${levels.high:.2f}")
print(f"ORB Low: ${levels.low:.2f}")
print(f"ORB Range: ${levels.range:.2f}")
print(f"Reason: {levels.reason}")
print()

# Validation
if levels.valid:
    # Check if levels are from today's data (200 range) not historical (100 range)
    if 199 <= levels.low <= 201 and 202 <= levels.high <= 204:
        print("[OK] ORB levels calculated from TODAY's bars (not historical)!")
        print("[OK] Fix is working correctly!")
    else:
        print("[ERROR] ORB levels calculated from wrong day!")
        print(f"[ERROR] Expected levels in range 199-204, got {levels.low:.2f}-{levels.high:.2f}")
else:
    print(f"[ERROR] ORB levels invalid: {levels.reason}")

print("=" * 60)
