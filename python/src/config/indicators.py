# src/config/indicators.py
"""
Indicator configuration shared across the strategy-lab system.

This module defines the core indicators that are calculated automatically
for cached market data. These indicators are used by:
- CacheDataLoader (src/data/cache.py) for automatic incremental calculation
- apply_indicator_data_cache.py script for batch pre-computation
"""

# Core indicators calculated automatically in incremental mode
# Matches CORE_INDICATORS specification from docs/incremental_indicators.md
CORE_INDICATORS = [
    "EMA_20",
    "EMA_30",
    "EMA_50",
    "EMA_200",
    "RSI_14",
    "ATR_14",
    "orb_levels"
]

# Default ORB (Opening Range Breakout) parameters
# Used by both incremental calculation and batch pre-computation
ORB_DEFAULT_PARAMS = {
    "start_time": "09:30",          # Market open time (HH:MM format)
    "duration_minutes": 5,          # Opening range duration in minutes
    "body_pct": 0.5                 # Body percentage threshold for breakout detection
}
