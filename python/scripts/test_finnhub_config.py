"""
Test Finnhub Configuration Loading

This script validates that the Finnhub configuration file is set up correctly.
Run this before implementing the WebSocket client to ensure credentials are valid.

Usage:
    python scripts/test_finnhub_config.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.finnhub_config_loader import load_finnhub_config


def main():
    """Test configuration loading with detailed validation."""
    print("=" * 70)
    print("Finnhub Configuration Test")
    print("=" * 70)
    print()

    # Test 1: Load configuration
    print("Test 1: Loading configuration file...")
    try:
        config = load_finnhub_config()
        print("✅ Configuration loaded successfully")
    except FileNotFoundError as e:
        print(f"❌ FAILED: Config file not found")
        print(f"   {e}")
        print()
        print("To fix this:")
        print("1. Copy src/config/finnhub_config.example.json to src/config/finnhub_config.json")
        print("2. Replace 'REPLACE_WITH_YOUR_FINNHUB_API_KEY' with your actual API key")
        print("3. Get a free API key at: https://finnhub.io/register")
        return False
    except ValueError as e:
        print(f"❌ FAILED: Invalid configuration")
        print(f"   {e}")
        return False
    except Exception as e:
        print(f"❌ FAILED: Unexpected error")
        print(f"   {e}")
        return False

    print()

    # Test 2: Validate API key format
    print("Test 2: Validating API key...")
    if not config.api_key or len(config.api_key) < 10:
        print("❌ FAILED: API key appears invalid (too short)")
        return False
    print(f"✅ API key present: {'*' * len(config.api_key)}")
    print()

    # Test 3: Validate WebSocket URL
    print("Test 3: Validating WebSocket URL...")
    if not config.websocket_url.startswith("wss://"):
        print(f"⚠️  WARNING: WebSocket URL should use secure wss:// protocol")
        print(f"   Current: {config.websocket_url}")
    else:
        print(f"✅ WebSocket URL: {config.websocket_url}")
    print()

    # Test 4: Validate bar interval
    print("Test 4: Validating bar interval...")
    valid_intervals = ["1m", "5m", "15m", "30m", "1h"]
    if config.bar_interval not in valid_intervals:
        print(f"⚠️  WARNING: Unusual bar interval: {config.bar_interval}")
        print(f"   Common intervals: {', '.join(valid_intervals)}")
    else:
        print(f"✅ Bar interval: {config.bar_interval}")
    print()

    # Test 5: Validate symbols
    print("Test 5: Validating symbols...")
    if not config.symbols:
        print("⚠️  WARNING: No symbols configured")
        print("   Add symbols to the 'symbols' array in config file")
    else:
        print(f"✅ Symbols configured: {', '.join(config.symbols)}")
    print()

    # Test 6: Validate market hours
    print("Test 6: Validating market hours...")
    print(f"   Timezone: {config.market_hours.timezone}")
    print(f"   Regular hours: {config.market_hours.regular_start} - {config.market_hours.regular_end}")
    print(f"   Pre-market: {config.market_hours.pre_market_start}")
    print(f"   After-hours: {config.market_hours.after_hours_end}")
    print(f"   Filter after-hours: {config.filter_after_hours}")
    print("✅ Market hours configured")
    print()

    # Test 7: Validate reconnection settings
    print("Test 7: Validating reconnection settings...")
    print(f"   Enabled: {config.reconnect.enabled}")
    print(f"   Max attempts: {config.reconnect.max_attempts}")
    print(f"   Backoff range: {config.reconnect.initial_backoff_seconds}s - {config.reconnect.max_backoff_seconds}s")
    print("✅ Reconnection settings configured")
    print()

    # Test 8: Validate REST API settings
    print("Test 8: Validating REST API settings...")
    print(f"   Enabled: {config.rest_api.enabled}")
    print(f"   Cache TTL: {config.rest_api.cache_ttl_seconds}s")
    print("✅ REST API settings configured")
    print()

    # Summary
    print("=" * 70)
    print("Configuration Summary")
    print("=" * 70)
    print(f"API Key:          {'*' * min(20, len(config.api_key))}")
    print(f"WebSocket:        {config.websocket_url}")
    print(f"Bar Interval:     {config.bar_interval}")
    print(f"Symbols:          {len(config.symbols)} configured")
    print(f"Market Timezone:  {config.market_hours.timezone}")
    print(f"Reconnect:        {'Enabled' if config.reconnect.enabled else 'Disabled'}")
    print(f"REST API:         {'Enabled' if config.rest_api.enabled else 'Disabled'}")
    print()
    print("✅ All configuration tests passed!")
    print()
    print("Next steps:")
    print("1. ✓ Configuration is valid")
    print("2. → Proceed to Phase 2: Implement WebSocket client")
    print("3. → Test connection with scripts/test_finnhub_connection.py (during market hours)")
    print()

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
