"""Quick test to verify timezone fix."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytz

print("=" * 60)
print("TIMEZONE FIX VALIDATION")
print("=" * 60)

# Simulate a Finnhub timestamp (milliseconds since epoch)
# Example: Feb 19, 2026 at 3:50 PM EST
# 3:50 PM EST = 20:50 UTC (EST is UTC-5 during standard time)
# 15:50 EST + 5 hours = 20:50 UTC
finnhub_timestamp_ms = 1771534200000  # 20:50:00 UTC = 15:50 EST (3:50 PM)

print(f"\nFinnhub timestamp (ms): {finnhub_timestamp_ms}")
print(f"Finnhub timestamp (s): {finnhub_timestamp_ms / 1000}")

# OLD WAY (BROKEN)
print("\n--- OLD WAY (Naive datetime) ---")
old_way = datetime.fromtimestamp(finnhub_timestamp_ms / 1000)
print(f"Datetime: {old_way}")
print(f"Timezone: {old_way.tzinfo} (None = naive)")
print(f"ISO format: {old_way.isoformat()}")

# NEW WAY (FIXED)
print("\n--- NEW WAY (UTC-aware datetime) ---")
new_way = datetime.fromtimestamp(finnhub_timestamp_ms / 1000, tz=pytz.UTC)
print(f"Datetime: {new_way}")
print(f"Timezone: {new_way.tzinfo}")
print(f"ISO format: {new_way.isoformat()}")

# Convert to EST
est = pytz.timezone("US/Eastern")
new_way_est = new_way.astimezone(est)
print(f"\nConverted to EST:")
print(f"Datetime: {new_way_est}")
print(f"Timezone: {new_way_est.tzinfo}")
print(f"ISO format: {new_way_est.isoformat()}")
print(f"Display: {new_way_est.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")

# Bar aggregator test
print("\n--- Bar Aggregator Alignment ---")
from vibe.trading_bot.data.aggregator import BarAggregator

aggregator = BarAggregator(bar_interval="5m", timezone="US/Eastern")
bar_start = aggregator._get_bar_start_time(new_way)

print(f"Trade time (EST): {new_way_est.strftime('%H:%M:%S')}")
print(f"Bar start (EST): {bar_start.strftime('%H:%M:%S %Z')}")
print(f"Bar start ISO: {bar_start.isoformat()}")

# Expected: 3:50 PM trade should align to 3:45 PM bar
expected_bar = est.localize(datetime(2026, 2, 19, 15, 45, 0))
print(f"\nExpected bar start: {expected_bar.strftime('%H:%M:%S %Z')}")
print(f"Expected bar ISO: {expected_bar.isoformat()}")

if bar_start == expected_bar:
    print("\nOK Timezone fix working correctly!")
else:
    print(f"\nERROR Timezone mismatch!")
    print(f"  Expected: {expected_bar}")
    print(f"  Got: {bar_start}")

print("\n" + "=" * 60)
