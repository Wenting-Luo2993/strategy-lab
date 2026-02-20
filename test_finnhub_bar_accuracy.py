"""Finnhub WebSocket Bar Accuracy Test

This script:
1. Connects to Finnhub websocket
2. Collects trades for 15-20 minutes
3. Aggregates trades into 5-minute bars
4. Compares first completed bar with Yahoo Finance data
5. Validates timezone handling and OHLCV accuracy

Run during market hours (9:30 AM - 4:00 PM EST) for meaningful results.
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

import pytz
import pandas as pd
from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider


class BarAccuracyTester:
    """Tests Finnhub websocket bar accuracy against Yahoo Finance."""

    def __init__(self, api_key: str, symbols: List[str], verbose: bool = True):
        """Initialize tester.

        Args:
            api_key: Finnhub API key
            symbols: List of symbols to test (e.g., ['AAPL'])
            verbose: Enable detailed logging
        """
        self.api_key = api_key
        self.symbols = symbols
        self.verbose = verbose

        # Websocket and aggregators
        self.ws_client = None
        self.aggregators: Dict[str, BarAggregator] = {}

        # Data collection
        self.trades_received = 0
        self.completed_bars: Dict[str, List[dict]] = {sym: [] for sym in symbols}
        self.start_time = None

        # Enhanced diagnostics
        self.trade_timestamps = []  # Track all trade timestamps for gap detection
        self.last_trade_time: Dict[str, datetime] = {}  # Track last trade per symbol
        self.trades_per_symbol: Dict[str, int] = {sym: 0 for sym in symbols}
        self.price_changes: Dict[str, List[float]] = {sym: [] for sym in symbols}
        self.websocket_events = []  # Track WS connection events
        self.bar_latencies = []  # Track time from bar end to bar complete callback

        # Yahoo Finance provider for comparison
        self.yahoo = YahooDataProvider()

    async def run_test(self, duration_minutes: int = 15):
        """Run the bar accuracy test.

        Args:
            duration_minutes: How long to collect data (default: 15 minutes)
        """
        print("=" * 70)
        print("FINNHUB WEBSOCKET BAR ACCURACY TEST")
        print("=" * 70)
        print()
        print(f"Symbols: {', '.join(self.symbols)}")
        print(f"Duration: {duration_minutes} minutes")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print()
        print("This test will:")
        print("  1. Connect to Finnhub websocket")
        print("  2. Collect trades and aggregate into 5-minute bars")
        print("  3. After collection, compare first bar with Yahoo Finance")
        print("  4. Validate OHLCV accuracy and timezone handling")
        print()
        print("-" * 70)
        print()

        self.start_time = datetime.now()

        # Step 1: Initialize websocket
        print("Step 1: Connecting to Finnhub websocket...")
        self.ws_client = FinnhubWebSocketClient(api_key=self.api_key)
        self.ws_client.on_trade(self._on_trade)

        try:
            await self.ws_client.connect()
            print(f"[OK] Connected to Finnhub websocket")
        except Exception as e:
            print(f"[FAIL] Failed to connect: {e}")
            return

        print()

        # Step 2: Create bar aggregators
        print("Step 2: Creating bar aggregators...")
        for symbol in self.symbols:
            aggregator = BarAggregator(
                bar_interval="5m",
                timezone="US/Eastern"
            )
            aggregator.on_bar_complete(
                lambda bar_dict, sym=symbol: self._on_bar_complete(sym, bar_dict)
            )
            self.aggregators[symbol] = aggregator
            print(f"[OK] Created aggregator for {symbol}")

        print()

        # Step 3: Subscribe to symbols
        print("Step 3: Subscribing to symbols...")
        for symbol in self.symbols:
            await self.ws_client.subscribe(symbol)
            print(f"[OK] Subscribed to {symbol}")

        print()
        print("-" * 70)
        print()
        print(f"Collecting data for {duration_minutes} minutes...")
        print("(You'll see live trade updates below)")
        print()

        # Step 4: Collect trades
        await self._collect_trades(duration_minutes)

        # Step 5: Disconnect
        print()
        print("Stopping data collection...")
        await self.ws_client.disconnect()

        # Step 6: Analyze results
        print()
        print("=" * 70)
        await self._analyze_results()

    async def _collect_trades(self, duration_minutes: int):
        """Collect trades for specified duration."""
        end_time = datetime.now() + timedelta(minutes=duration_minutes)
        last_status_time = datetime.now()
        last_trade_count = 0

        while datetime.now() < end_time:
            # Print status every 60 seconds
            if (datetime.now() - last_status_time).total_seconds() >= 60:
                elapsed = (datetime.now() - self.start_time).total_seconds() / 60
                remaining = (end_time - datetime.now()).total_seconds() / 60
                total_bars = sum(len(bars) for bars in self.completed_bars.values())

                # Calculate trade rate since last status
                trades_delta = self.trades_received - last_trade_count
                time_delta = (datetime.now() - last_status_time).total_seconds()
                current_rate = trades_delta / time_delta if time_delta > 0 else 0

                print()
                print(f"[STATUS @ {elapsed:.1f}m] Remaining: {remaining:.1f}m")
                print(f"  Trades: {self.trades_received} total ({current_rate:.1f}/sec current rate)")
                print(f"  Bars completed: {total_bars}")

                # Check websocket connection
                if self.ws_client:
                    ws_status = "connected" if self.ws_client.connected else "disconnected"
                    print(f"  WebSocket: {ws_status}")

                    if not self.ws_client.connected:
                        print(f"  [WARN] WebSocket disconnected! Attempting reconnect...")
                        self.websocket_events.append({
                            "time": datetime.now(),
                            "event": "disconnected"
                        })

                # Warn if no trades in last minute
                if trades_delta == 0:
                    print(f"  [WARN] No trades received in last {time_delta:.0f}s")
                    print(f"         Possible causes: market closed, low volume, or connection issue")

                print()

                last_status_time = datetime.now()
                last_trade_count = self.trades_received

            await asyncio.sleep(1)

    async def _on_trade(self, trade: dict):
        """Handle incoming trade from Finnhub."""
        self.trades_received += 1
        receive_time = datetime.now(pytz.UTC)

        symbol = trade.get("symbol")
        price = trade.get("price")
        size = trade.get("size")
        timestamp = trade.get("timestamp")

        # Track per-symbol stats
        self.trades_per_symbol[symbol] = self.trades_per_symbol.get(symbol, 0) + 1

        # Detect gaps in trade stream
        if symbol in self.last_trade_time:
            gap = (timestamp - self.last_trade_time[symbol]).total_seconds()
            if gap > 10:  # More than 10 seconds between trades
                if self.verbose:
                    print(f"  [GAP] {symbol}: {gap:.1f}s gap since last trade")

        self.last_trade_time[symbol] = timestamp
        self.trade_timestamps.append(timestamp)

        # Track price changes
        if self.trades_per_symbol[symbol] > 1:
            # Calculate price change from previous trade
            prev_price = self.price_changes[symbol][-1] if self.price_changes[symbol] else price
            price_change = price - prev_price
            self.price_changes[symbol].append(price)

        # Timezone diagnostics
        est = pytz.timezone("US/Eastern")
        utc = pytz.UTC

        # Show original timezone info
        ts_tz_info = "TZ-aware" if timestamp.tzinfo else "naive"

        # Convert to EST for display
        if timestamp.tzinfo:
            ts_est = timestamp.astimezone(est)
            ts_utc = timestamp.astimezone(utc)
        else:
            ts_est = est.localize(timestamp)
            ts_utc = utc.localize(timestamp)

        # Calculate latency (time from trade to received)
        latency_ms = (receive_time - ts_utc).total_seconds() * 1000

        # Print detailed info for first 20 trades
        if self.trades_received <= 20:
            print(f"  Trade #{self.trades_received}: {symbol}")
            print(f"    Price: ${price:.2f} x {size} shares")
            print(f"    Timestamp ({ts_tz_info}): {timestamp}")
            print(f"    -> EST: {ts_est.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"    -> UTC: {ts_utc.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"    Latency: {latency_ms:.0f}ms")

            if self.trades_per_symbol[symbol] > 1 and self.price_changes[symbol]:
                print(f"    Price change: ${price_change:+.2f}")
            print()

        # Add to aggregator
        aggregator = self.aggregators.get(symbol)
        if aggregator:
            aggregator.add_trade(timestamp=timestamp, price=price, size=size)

            # Log aggregator state occasionally
            if self.verbose and self.trades_received % 50 == 0:
                print(f"  [AGGREGATOR STATUS] {symbol}: {self.trades_per_symbol[symbol]} trades processed")

    def _on_bar_complete(self, symbol: str, bar_dict: dict):
        """Handle completed bar."""
        completion_time = datetime.now(pytz.UTC)
        self.completed_bars[symbol].append(bar_dict)

        timestamp = bar_dict["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        # Convert to EST for display
        est = pytz.timezone("US/Eastern")
        if timestamp.tzinfo:
            timestamp_est = timestamp.astimezone(est)
            timestamp_utc = timestamp.astimezone(pytz.UTC)
        else:
            timestamp_est = est.localize(timestamp)
            timestamp_utc = pytz.UTC.localize(timestamp)

        # Calculate bar latency (time from bar end to callback)
        bar_end_time = timestamp_utc + timedelta(minutes=5)
        latency = (completion_time - bar_end_time).total_seconds()
        self.bar_latencies.append(latency)

        print()
        print("=" * 70)
        print(f"[BAR COMPLETE] {symbol} @ {timestamp_est.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("=" * 70)

        # OHLCV with additional metrics
        print(f"  OHLCV:")
        print(f"    Open:   ${bar_dict['open']:.2f}")
        print(f"    High:   ${bar_dict['high']:.2f}")
        print(f"    Low:    ${bar_dict['low']:.2f}")
        print(f"    Close:  ${bar_dict['close']:.2f}")
        print(f"    Volume: {bar_dict['volume']:.0f} shares")

        # Price movement
        price_range = bar_dict['high'] - bar_dict['low']
        price_change = bar_dict['close'] - bar_dict['open']
        price_change_pct = (price_change / bar_dict['open'] * 100) if bar_dict['open'] > 0 else 0

        print(f"  Metrics:")
        print(f"    Range: ${price_range:.2f} ({price_range/bar_dict['open']*100:.2f}%)")
        print(f"    Change: ${price_change:+.2f} ({price_change_pct:+.2f}%)")
        print(f"    Trades: {bar_dict.get('trade_count', 0)}")
        print(f"    Avg trade size: {bar_dict['volume'] / max(bar_dict.get('trade_count', 1), 1):.0f} shares")

        # Timing diagnostics
        print(f"  Timing:")
        print(f"    Bar period: {timestamp_est.strftime('%H:%M:%S')} - {(timestamp_est + timedelta(minutes=5)).strftime('%H:%M:%S')} EST")
        print(f"    Completion latency: {latency:.1f}s after bar end")

        if self.verbose:
            print(f"    Bar end (UTC): {bar_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    Callback (UTC): {completion_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Check for bar alignment (should be on 5-minute boundaries)
        minute = timestamp_est.minute
        if minute % 5 != 0:
            print(f"  [WARN] Bar not aligned to 5-minute boundary! (minute={minute})")

        print()

    async def _analyze_results(self):
        """Analyze collected data and compare with Yahoo Finance."""
        print("DATA COLLECTION SUMMARY")
        print("=" * 70)
        print()

        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"Collection duration: {elapsed/60:.1f} minutes ({elapsed:.0f}s)")
        print(f"Total trades received: {self.trades_received}")
        print(f"Trade rate: {self.trades_received / elapsed:.1f} trades/second")
        print()

        # Per-symbol stats
        print("Per-symbol trade distribution:")
        for symbol, count in self.trades_per_symbol.items():
            pct = count / self.trades_received * 100 if self.trades_received > 0 else 0
            print(f"  {symbol}: {count} trades ({pct:.1f}%)")
        print()

        # Trade timing analysis
        if len(self.trade_timestamps) > 1:
            print("Trade timing analysis:")
            gaps = []
            for i in range(1, len(self.trade_timestamps)):
                gap = (self.trade_timestamps[i] - self.trade_timestamps[i-1]).total_seconds()
                gaps.append(gap)

            if gaps:
                avg_gap = sum(gaps) / len(gaps)
                max_gap = max(gaps)
                print(f"  Average gap between trades: {avg_gap:.2f}s")
                print(f"  Maximum gap: {max_gap:.1f}s")

                # Detect long gaps
                long_gaps = [g for g in gaps if g > 5]
                if long_gaps:
                    print(f"  Gaps >5s: {len(long_gaps)} occurrences")
            print()

        # Bar completion latency
        if self.bar_latencies:
            avg_latency = sum(self.bar_latencies) / len(self.bar_latencies)
            max_latency = max(self.bar_latencies)
            min_latency = min(self.bar_latencies)

            print("Bar completion latencies:")
            print(f"  Average: {avg_latency:.1f}s after bar end")
            print(f"  Min: {min_latency:.1f}s")
            print(f"  Max: {max_latency:.1f}s")

            if avg_latency > 10:
                print(f"  [WARN] High latency! Bars completing {avg_latency:.0f}s after period ends")
            print()

        # Price volatility
        print("Price volatility (per symbol):")
        for symbol, prices in self.price_changes.items():
            if len(prices) > 1:
                price_diffs = [prices[i] - prices[i-1] for i in range(1, len(prices))]
                avg_change = sum(abs(d) for d in price_diffs) / len(price_diffs)
                max_change = max(abs(d) for d in price_diffs)
                print(f"  {symbol}:")
                print(f"    Avg price change: ${avg_change:.3f}")
                print(f"    Max price change: ${max_change:.2f}")
        print()

        # Analyze each symbol
        for symbol in self.symbols:
            bars = self.completed_bars[symbol]

            print("-" * 70)
            print(f"SYMBOL: {symbol}")
            print("-" * 70)
            print()

            if not bars:
                print(f"[WARN] No completed bars for {symbol}")
                print("  Possible reasons:")
                print("  - Market is closed")
                print("  - Not enough trades to complete a 5-minute bar")
                print("  - Low trading volume")
                print()
                continue

            print(f"Completed bars: {len(bars)}")
            print()

            # Show all bars
            print("All completed bars:")
            for i, bar in enumerate(bars, 1):
                ts = bar["timestamp"]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                est = pytz.timezone("US/Eastern")
                ts_est = ts.astimezone(est) if ts.tzinfo else est.localize(ts)

                print(f"  Bar {i}: {ts_est.strftime('%H:%M:%S %Z')}")
                print(f"    OHLCV: O={bar['open']:.2f} H={bar['high']:.2f} "
                      f"L={bar['low']:.2f} C={bar['close']:.2f} V={bar['volume']:.0f}")
                print(f"    Trades: {bar.get('trade_count', 0)}")

            print()

            # Compare first bar with Yahoo Finance
            print("Comparing first bar with Yahoo Finance...")
            await self._compare_with_yahoo(symbol, bars[0])
            print()

    async def _compare_with_yahoo(self, symbol: str, finnhub_bar: dict):
        """Compare Finnhub bar with Yahoo Finance data.

        Args:
            symbol: Stock symbol
            finnhub_bar: Bar dict from Finnhub aggregator
        """
        # Get timestamp
        ts = finnhub_bar["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        est = pytz.timezone("US/Eastern")
        ts_est = ts.astimezone(est) if ts.tzinfo else est.localize(ts)

        print(f"  Finnhub bar: {ts_est.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"    O={finnhub_bar['open']:.2f} H={finnhub_bar['high']:.2f} "
              f"L={finnhub_bar['low']:.2f} C={finnhub_bar['close']:.2f} V={finnhub_bar['volume']:.0f}")
        print()

        # Fetch Yahoo Finance data for comparison
        print("  Fetching Yahoo Finance data...")
        try:
            # Get data for today
            yf_bars = await self.yahoo.get_data(
                symbol=symbol,
                interval="5m",
                days=1
            )

            if yf_bars.empty:
                print("  [WARN] No Yahoo Finance data available")
                return

            # Find matching bar by timestamp
            # Yahoo Finance might be delayed, so look for closest timestamp
            yf_bars['timestamp'] = pd.to_datetime(yf_bars['timestamp'])

            # Convert Finnhub timestamp to match Yahoo format
            finnhub_ts = pd.Timestamp(ts_est)

            # Find closest Yahoo bar (within 10 minutes)
            time_diffs = abs(yf_bars['timestamp'] - finnhub_ts)
            closest_idx = time_diffs.idxmin()
            min_diff = time_diffs.min()

            if min_diff > pd.Timedelta(minutes=10):
                print(f"  [WARN] No matching Yahoo Finance bar found")
                print(f"         Closest bar is {min_diff.total_seconds()/60:.1f} minutes away")
                return

            yf_bar = yf_bars.iloc[closest_idx]

            yf_ts = yf_bar['timestamp']
            if yf_ts.tzinfo is None:
                yf_ts = est.localize(yf_ts)

            print(f"  Yahoo Finance bar: {yf_ts.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"    O={yf_bar['open']:.2f} H={yf_bar['high']:.2f} "
                  f"L={yf_bar['low']:.2f} C={yf_bar['close']:.2f} V={yf_bar['volume']:.0f}")
            print()

            # Compare values
            print("  Comparison:")

            # OHLC comparison
            open_diff = abs(finnhub_bar['open'] - yf_bar['open'])
            high_diff = abs(finnhub_bar['high'] - yf_bar['high'])
            low_diff = abs(finnhub_bar['low'] - yf_bar['low'])
            close_diff = abs(finnhub_bar['close'] - yf_bar['close'])

            tolerance = 0.10  # $0.10 tolerance for OHLC

            print(f"    Open:  ${open_diff:.2f} diff {'[OK]' if open_diff <= tolerance else '[WARN]'}")
            print(f"    High:  ${high_diff:.2f} diff {'[OK]' if high_diff <= tolerance else '[WARN]'}")
            print(f"    Low:   ${low_diff:.2f} diff {'[OK]' if low_diff <= tolerance else '[WARN]'}")
            print(f"    Close: ${close_diff:.2f} diff {'[OK]' if close_diff <= tolerance else '[WARN]'}")

            # Volume comparison
            vol_ratio = finnhub_bar['volume'] / yf_bar['volume'] if yf_bar['volume'] > 0 else 0
            print(f"    Volume: {vol_ratio:.1%} of Yahoo Finance")

            if vol_ratio < 0.5:
                print("      [WARN] Finnhub free tier provides sampled trades (expected)")

            # Timestamp comparison
            ts_diff = abs((finnhub_ts - yf_ts).total_seconds())
            print(f"    Timestamp: {ts_diff:.0f}s diff {'[OK]' if ts_diff <= 300 else '[WARN]'}")

            # Overall assessment
            print()
            ohlc_ok = all(d <= tolerance for d in [open_diff, high_diff, low_diff, close_diff])
            ts_ok = ts_diff <= 300

            if ohlc_ok and ts_ok:
                print("  [OK] Bar data matches Yahoo Finance within tolerance!")
            else:
                print("  [WARN] Bar data has discrepancies (see details above)")

        except Exception as e:
            print(f"  [FAIL] Error fetching Yahoo Finance data: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """Run the bar accuracy test."""
    # Get API key from .env
    env_path = Path(__file__).parent / "vibe" / "trading_bot" / ".env"
    api_key = None

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("FINNHUB_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break

    if not api_key or api_key == "your_finnhub_api_key_here":
        print("ERROR: FINNHUB_API_KEY not found in .env file")
        print(f"Please set your API key in: {env_path}")
        return 1

    # Check if market is open
    print("Checking market status...")
    from vibe.trading_bot.core.market_schedulers import create_scheduler
    scheduler = create_scheduler(market_type="stocks", exchange="NYSE")

    if not scheduler.is_market_open():
        print()
        print("=" * 70)
        print("WARNING: Market is currently closed")
        print("=" * 70)
        next_open = scheduler.next_market_open()
        print(f"Next market open: {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print()
        print("This test should be run during market hours (9:30 AM - 4:00 PM EST)")
        print("for meaningful results.")
        print()
        print("Continuing anyway (may have limited/no data)...")
        await asyncio.sleep(2)

    print()

    # Configuration
    print("=" * 70)
    print("TEST CONFIGURATION")
    print("=" * 70)
    print()

    symbols = ["AAPL"]  # High volume, reliable data
    duration = 15  # minutes
    verbose = True  # Enable detailed logging

    print(f"Symbols: {', '.join(symbols)}")
    print(f"Duration: {duration} minutes")
    print(f"Verbose logging: {'enabled' if verbose else 'disabled'}")
    print()
    print("Starting test automatically in 3 seconds...")
    await asyncio.sleep(3)
    print()

    # Run test
    tester = BarAccuracyTester(
        api_key=api_key,
        symbols=symbols,
        verbose=verbose
    )

    await tester.run_test(duration_minutes=duration)

    # Generate diagnostic report
    print()
    print("=" * 70)
    print("DIAGNOSTIC REPORT")
    print("=" * 70)
    print()
    print("This report can help troubleshoot:")
    print("  - Websocket connection issues")
    print("  - Bar aggregation problems")
    print("  - Timezone handling bugs")
    print("  - Data accuracy discrepancies")
    print()

    if tester.websocket_events:
        print(f"WebSocket events: {len(tester.websocket_events)}")
        for event in tester.websocket_events:
            print(f"  - {event['time'].strftime('%H:%M:%S')}: {event['event']}")
    else:
        print("WebSocket: No disconnection events (good!)")

    print()
    print("Test completed successfully!")
    print()

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
