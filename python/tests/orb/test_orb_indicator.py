import pandas as pd
from datetime import datetime, timedelta

from src.indicators.orb import calculate_orb_levels


def make_sample_df():
    # Create two trading days of 1-minute bars from 09:30 to 09:40
    rows = []
    for day in [datetime(2024, 10, 1), datetime(2024, 10, 2)]:
        start = day.replace(hour=9, minute=30)
        for i in range(11):  # 09:30 through 09:40 inclusive (11 bars)
            ts = start + timedelta(minutes=i)
            # Construct prices so day 1 breaks ORB high after range, day 2 breaks ORB low
            if day.day == 1:
                # Opening range bars first 5 minutes: gradually increasing highs
                base_open = 100 + i * 0.1
                base_close = base_open + 0.05
                high = max(base_open, base_close) + (0.02 if i < 5 else (0.5 if i == 6 else 0.01))
                low = min(base_open, base_close) - 0.02
            else:
                base_open = 200 - i * 0.1
                base_close = base_open - 0.05
                high = max(base_open, base_close) + 0.02
                # Introduce a decisive low breakout at minute 6
                low = min(base_open, base_close) - (0.02 if i < 6 else 0.6)
            rows.append((ts, base_open, high, low, base_close, 1000))
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).set_index("timestamp")
    return df


def test_calculate_orb_levels_basic_properties():
    df = make_sample_df()
    result = calculate_orb_levels(df, start_time="09:30", duration_minutes=5)

    # Columns existence
    for col in ["ORB_High", "ORB_Low", "ORB_Range", "ORB_Breakout"]:
        assert col in result.columns, f"{col} should be present in result"

    # ORB values should be constant within each day
    for day, group in result.groupby(result.index.date):
        assert group["ORB_High"].nunique() == 1
        assert group["ORB_Low"].nunique() == 1
        assert group["ORB_Range"].nunique() == 1

    # Validate day 1 breakout long appears after ORB window closes
    day1 = result[result.index.date == datetime(2024, 10, 1).date()]
    # First 5 bars: inside ORB window -> no breakout
    assert day1.iloc[:5]["ORB_Breakout"].sum() == 0
    # Expect a long breakout flag (1) at bar index 6 (minute 09:36)
    assert 1 in day1.iloc[5:]["ORB_Breakout"].values, "Day 1 should contain a long breakout after ORB window"

    # Validate day 2 breakout short appears
    day2 = result[result.index.date == datetime(2024, 10, 2).date()]
    assert day2.iloc[:5]["ORB_Breakout"].sum() == 0
    assert -1 in day2.iloc[5:]["ORB_Breakout"].values, "Day 2 should contain a short breakout after ORB window"

    # Range calculation
    for day, group in result.groupby(result.index.date):
        expected_range = group["ORB_High"].iloc[0] - group["ORB_Low"].iloc[0]
        assert (group["ORB_Range"].iloc[0] == expected_range)


def test_calculate_orb_levels_no_datetime_index_error():
    # Provide non-datetime index should raise ValueError
    bad_df = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]})
    try:
        calculate_orb_levels(bad_df)
    except ValueError as e:
        assert "DatetimeIndex" in str(e)
    else:
        assert False, "Expected ValueError for non-DatetimeIndex input"
