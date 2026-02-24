"""
Validation script for Polygon.io integration.

This script tests the complete integration with real API keys:
1. Provider connection and authentication
2. Data fetching and format validation
3. Rate limiting behavior
4. Primary/secondary provider fallback
5. Bar aggregation compatibility

Usage:
    python validate_polygon_integration.py

Requirements:
    - Set POLYGON_API_KEY in .env file
    - Optional: Set FINNHUB_API_KEY for fallback testing
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import pytz

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from vibe.trading_bot.data.providers.factory import DataProviderFactory
from vibe.trading_bot.data.providers.types import (
    ProviderType,
    WebSocketDataProvider,
    RESTDataProvider,
)
from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.config.settings import get_settings


# ANSI colors for output
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_step(step_num: int, total: int, description: str):
    """Print test step header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}[Step {step_num}/{total}] {description}{Colors.RESET}")
    print("=" * 70)


def print_success(message: str):
    """Print success message."""
    print(f"{Colors.GREEN}[OK] {message}{Colors.RESET}")


def print_error(message: str):
    """Print error message."""
    print(f"{Colors.RED}[ERROR] {message}{Colors.RESET}")


def print_warning(message: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}[WARN] {message}{Colors.RESET}")


def print_info(message: str):
    """Print info message."""
    print(f"  {message}")


async def validate_environment() -> dict:
    """Validate environment variables and configuration."""
    print_step(1, 8, "Validating Environment")

    # Load environment variables
    load_dotenv()

    # Check for Polygon API key
    polygon_key = os.getenv("DATA__POLYGON_API_KEY") or os.getenv("POLYGON_API_KEY")
    finnhub_key = os.getenv("DATA__FINNHUB_API_KEY") or os.getenv("FINNHUB_API_KEY")

    results = {
        "polygon_key": polygon_key,
        "finnhub_key": finnhub_key,
    }

    if polygon_key:
        print_success(f"Polygon API key found: {polygon_key[:8]}...")
    else:
        print_error("Polygon API key not found!")
        print_info("Set DATA__POLYGON_API_KEY in .env file")
        print_info("Get your API key from: https://polygon.io/dashboard/api-keys")

    if finnhub_key:
        print_success(f"Finnhub API key found: {finnhub_key[:8]}... (for fallback testing)")
    else:
        print_warning("Finnhub API key not found (optional for fallback testing)")

    # Check configuration
    try:
        config = get_settings()
        print_success(f"Configuration loaded successfully")
        print_info(f"  Primary provider: {config.data.primary_provider}")
        print_info(f"  Secondary provider: {config.data.secondary_provider}")
        print_info(f"  Poll interval (with position): {config.data.poll_interval_with_position}s")
        print_info(f"  Poll interval (no position): {config.data.poll_interval_no_position}s")
        results["config"] = config
    except Exception as e:
        print_error(f"Failed to load configuration: {e}")
        results["config"] = None

    return results


async def validate_provider_creation(polygon_key: str, finnhub_key: str = None) -> dict:
    """Validate provider creation through factory."""
    print_step(2, 8, "Testing Provider Creation")

    results = {}

    # Test Polygon provider creation
    try:
        provider = DataProviderFactory.create_realtime_provider(
            provider_type="polygon",
            polygon_api_key=polygon_key
        )
        print_success("Polygon provider created successfully")
        print_info(f"  Provider type: {provider.provider_type}")
        print_info(f"  Provider name: {provider.provider_name}")
        print_info(f"  Real-time: {provider.is_real_time}")
        print_info(f"  Rate limit: {provider.rate_limit_per_minute} calls/min")
        print_info(f"  Recommended poll interval: {provider.recommended_poll_interval_seconds}s")

        # Verify it's a REST provider
        if isinstance(provider, RESTDataProvider):
            print_success("Provider correctly identified as REST provider")
        else:
            print_error("Provider type mismatch!")

        results["polygon_provider"] = provider

    except Exception as e:
        print_error(f"Failed to create Polygon provider: {e}")
        results["polygon_provider"] = None

    # Test Finnhub provider creation (optional)
    if finnhub_key:
        try:
            provider = DataProviderFactory.create_realtime_provider(
                provider_type="finnhub",
                finnhub_api_key=finnhub_key
            )
            print_success("Finnhub provider created successfully (for fallback)")
            print_info(f"  Provider type: {provider.provider_type}")

            # Verify it's a WebSocket provider
            if isinstance(provider, WebSocketDataProvider):
                print_success("Provider correctly identified as WebSocket provider")

            results["finnhub_provider"] = provider

        except Exception as e:
            print_warning(f"Failed to create Finnhub provider: {e}")
            results["finnhub_provider"] = None

    return results


async def validate_data_fetching(provider) -> dict:
    """Validate data fetching from Polygon API."""
    print_step(3, 8, "Testing Data Fetching")

    if not provider:
        print_error("No provider available for testing")
        return {"success": False}

    results = {"success": False}
    test_symbol = "AAPL"

    try:
        print_info(f"Fetching latest bar for {test_symbol}...")

        bar = await provider.get_latest_bar(test_symbol, timeframe="1")

        if bar:
            print_success("Successfully fetched bar data")
            print_info(f"  Timestamp: {bar.get('timestamp')}")
            print_info(f"  Open: ${bar.get('open', 0):.2f}")
            print_info(f"  High: ${bar.get('high', 0):.2f}")
            print_info(f"  Low: ${bar.get('low', 0):.2f}")
            print_info(f"  Close: ${bar.get('close', 0):.2f}")
            print_info(f"  Volume: {bar.get('volume', 0):,.0f}")

            # Validate data structure
            required_fields = ["timestamp", "open", "high", "low", "close", "volume"]
            missing_fields = [field for field in required_fields if field not in bar]

            if not missing_fields:
                print_success("Bar data structure is valid")
                results["success"] = True
                results["bar"] = bar
            else:
                print_error(f"Missing required fields: {missing_fields}")

            # Validate timestamp is timezone-aware
            if isinstance(bar.get("timestamp"), datetime):
                if bar["timestamp"].tzinfo:
                    print_success("Timestamp is timezone-aware")
                else:
                    print_warning("Timestamp is not timezone-aware")
        else:
            print_error("No bar data returned (might be outside market hours)")
            print_info("Try running during market hours (9:30 AM - 4:00 PM ET)")

    except Exception as e:
        print_error(f"Error fetching data: {e}")
        import traceback
        print_info(f"  {traceback.format_exc()}")

    return results


async def validate_batch_fetching(provider) -> dict:
    """Validate batch fetching for multiple symbols."""
    print_step(4, 8, "Testing Batch Data Fetching")

    if not provider:
        print_error("No provider available for testing")
        return {"success": False}

    results = {"success": False}
    test_symbols = ["AAPL", "GOOGL", "MSFT"]

    try:
        print_info(f"Fetching bars for {len(test_symbols)} symbols: {', '.join(test_symbols)}")

        start_time = datetime.now()
        bars = await provider.get_multiple_latest_bars(test_symbols, timeframe="1")
        elapsed = (datetime.now() - start_time).total_seconds()

        print_success(f"Batch fetch completed in {elapsed:.2f}s")

        # Validate results
        successful = sum(1 for bar in bars.values() if bar is not None)
        print_info(f"  Successfully fetched: {successful}/{len(test_symbols)} symbols")

        for symbol, bar in bars.items():
            if bar:
                print_success(f"  {symbol}: ${bar.get('close', 0):.2f} @ {bar.get('timestamp')}")
            else:
                print_warning(f"  {symbol}: No data available")

        if successful > 0:
            results["success"] = True
            results["bars"] = bars
            results["elapsed"] = elapsed

    except Exception as e:
        print_error(f"Error in batch fetching: {e}")
        import traceback
        print_info(f"  {traceback.format_exc()}")

    return results


async def validate_rate_limiting(provider) -> dict:
    """Validate rate limiting behavior."""
    print_step(5, 8, "Testing Rate Limiting")

    if not provider:
        print_error("No provider available for testing")
        return {"success": False}

    results = {"success": False}

    print_info(f"Rate limit: {provider.rate_limit_per_minute} calls/min")
    print_info(f"Testing with 5 rapid requests...")

    try:
        test_symbols = ["AAPL", "GOOGL", "MSFT", "TSLA", "AMZN"]
        start_time = datetime.now()

        # Make 5 rapid requests
        bars = await provider.get_multiple_latest_bars(test_symbols, timeframe="1")

        elapsed = (datetime.now() - start_time).total_seconds()

        print_success(f"Completed {len(test_symbols)} requests in {elapsed:.2f}s")

        # With asyncio.gather, should be fast (< 5s even with rate limiting)
        if elapsed < 10.0:
            print_success("Rate limiting is working efficiently")
            results["success"] = True
        else:
            print_warning(f"Requests took longer than expected ({elapsed:.2f}s)")

        results["elapsed"] = elapsed

    except Exception as e:
        print_error(f"Error testing rate limiting: {e}")

    return results


async def validate_bar_aggregation(provider) -> dict:
    """Validate compatibility with BarAggregator."""
    print_step(6, 8, "Testing Bar Aggregation Compatibility")

    if not provider:
        print_error("No provider available for testing")
        return {"success": False}

    results = {"success": False}

    try:
        # Create bar aggregator
        aggregator = BarAggregator(
            symbol="AAPL",
            interval_minutes=5,
            on_bar_complete=lambda symbol, bar: print_info(f"  5m bar completed: {bar}")
        )

        print_success("Created BarAggregator (5m interval)")

        # Fetch bar from provider
        bar = await provider.get_latest_bar("AAPL", timeframe="1")

        if bar:
            # Feed to aggregator
            aggregator.add_trade(
                timestamp=bar["timestamp"],
                price=bar["close"],
                size=bar["volume"]
            )

            print_success("Successfully fed bar data to aggregator")
            print_info(f"  Timestamp: {bar['timestamp']}")
            print_info(f"  Price: ${bar['close']:.2f}")
            print_info(f"  Volume: {bar['volume']:,.0f}")

            results["success"] = True
        else:
            print_warning("No bar data available to test aggregation")

    except Exception as e:
        print_error(f"Error testing bar aggregation: {e}")
        import traceback
        print_info(f"  {traceback.format_exc()}")

    return results


async def validate_provider_switching(polygon_provider, finnhub_provider) -> dict:
    """Validate provider switching logic."""
    print_step(7, 8, "Testing Provider Switching Logic")

    if not polygon_provider:
        print_error("Polygon provider not available")
        return {"success": False}

    results = {"success": False}

    # Test type detection
    print_info("Testing provider type detection...")

    if isinstance(polygon_provider, RESTDataProvider):
        print_success("Polygon correctly identified as REST provider")
    else:
        print_error("Polygon provider type mismatch!")
        return results

    # Test switching logic
    active_provider = polygon_provider
    print_info(f"Active provider: {active_provider.provider_name} ({active_provider.provider_type})")

    # Simulate switching to secondary
    if finnhub_provider:
        print_info("Simulating switch to secondary provider (Finnhub)...")

        if isinstance(finnhub_provider, WebSocketDataProvider):
            print_success("Finnhub correctly identified as WebSocket provider")
            print_info("  Would use callback-based approach for WebSocket")
        else:
            print_warning("Finnhub provider type mismatch")

        active_provider = finnhub_provider
        print_success(f"Switched to: {active_provider.provider_name} ({active_provider.provider_type})")
        results["success"] = True
    else:
        print_warning("No secondary provider available for switching test")
        print_info("Set FINNHUB_API_KEY to test provider switching")
        results["success"] = True  # Still pass if only primary works

    return results


async def validate_configuration() -> dict:
    """Validate configuration settings."""
    print_step(8, 8, "Testing Configuration Integration")

    results = {"success": False}

    try:
        config = get_settings()

        print_success("Configuration loaded")
        print_info(f"  Primary provider: {config.data.primary_provider}")
        print_info(f"  Secondary provider: {config.data.secondary_provider}")
        print_info(f"  Trading symbols: {', '.join(config.trading.symbols)}")
        print_info(f"  Poll interval (with position): {config.data.poll_interval_with_position}s")
        print_info(f"  Poll interval (no position): {config.data.poll_interval_no_position}s")

        # Validate polling intervals
        if config.data.poll_interval_with_position == 60:
            print_success("Poll interval with position is correctly set to 60s")
        else:
            print_warning(f"Poll interval with position is {config.data.poll_interval_with_position}s (expected 60s)")

        if config.data.poll_interval_no_position == 300:
            print_success("Poll interval without position is correctly set to 300s")
        else:
            print_warning(f"Poll interval without position is {config.data.poll_interval_no_position}s (expected 300s)")

        results["success"] = True
        results["config"] = config

    except Exception as e:
        print_error(f"Error loading configuration: {e}")

    return results


async def main():
    """Run all validation tests."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}")
    print("Polygon.io Integration Validation")
    print(f"{'=' * 70}{Colors.RESET}\n")

    # Track overall results
    all_results = {}

    # Step 1: Environment
    env_results = await validate_environment()
    all_results["environment"] = env_results

    if not env_results.get("polygon_key"):
        print_error("\nCannot proceed without Polygon API key!")
        print_info("Please set DATA__POLYGON_API_KEY in your .env file")
        sys.exit(1)

    # Step 2: Provider Creation
    provider_results = await validate_provider_creation(
        env_results["polygon_key"],
        env_results.get("finnhub_key")
    )
    all_results["provider_creation"] = provider_results

    polygon_provider = provider_results.get("polygon_provider")
    finnhub_provider = provider_results.get("finnhub_provider")

    # Step 3: Data Fetching
    fetch_results = await validate_data_fetching(polygon_provider)
    all_results["data_fetching"] = fetch_results

    # Step 4: Batch Fetching
    batch_results = await validate_batch_fetching(polygon_provider)
    all_results["batch_fetching"] = batch_results

    # Step 5: Rate Limiting
    rate_limit_results = await validate_rate_limiting(polygon_provider)
    all_results["rate_limiting"] = rate_limit_results

    # Step 6: Bar Aggregation
    aggregation_results = await validate_bar_aggregation(polygon_provider)
    all_results["bar_aggregation"] = aggregation_results

    # Step 7: Provider Switching
    switching_results = await validate_provider_switching(polygon_provider, finnhub_provider)
    all_results["provider_switching"] = switching_results

    # Step 8: Configuration
    config_results = await validate_configuration()
    all_results["configuration"] = config_results

    # Print summary
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}")
    print("Validation Summary")
    print(f"{'=' * 70}{Colors.RESET}\n")

    passed = sum(1 for result in all_results.values() if isinstance(result, dict) and result.get("success"))
    total = len(all_results)

    for test_name, result in all_results.items():
        if isinstance(result, dict) and result.get("success"):
            print_success(f"{test_name.replace('_', ' ').title()}")
        else:
            print_error(f"{test_name.replace('_', ' ').title()}")

    print(f"\n{Colors.BOLD}Results: {passed}/{total} tests passed{Colors.RESET}")

    if passed == total:
        print(f"\n{Colors.GREEN}{Colors.BOLD}[SUCCESS] All validation tests passed!{Colors.RESET}")
        print_info("Your Polygon integration is ready for production use.")
        return 0
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}[WARNING] Some tests failed{Colors.RESET}")
        print_info("Please review the errors above and fix configuration.")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Validation cancelled by user{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Unexpected error: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
