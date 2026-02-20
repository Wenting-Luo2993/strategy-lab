"""
Debug script for real-time bar aggregation.

Tests:
1. Timezone handling (are bars aligned to EST properly?)
2. Trade capture rate (are we getting all trades?)
3. OHLCV accuracy (compare with TradingView/yfinance)
4. Volume completeness (Finnhub free tier limitations)

Run this during market hours to compare with live TradingView data.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add vibe to path
sys.path.insert(0, str(Path(__file__).parent))

from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
from vibe.trading_bot.data.aggregator import BarAggregator
import pytz


class BarDebugger:
    """Debugger for real-time bar aggregation."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        self.aggregators = {}
        self.trade_log = []
        self.completed_bars = []
        self.trade_count = 0
        self.start_time = None

    async def run(self, symbols: list, duration_seconds: int = 300):
        """
        Run debug session.

        Args:
            symbols: List of symbols to track
            duration_seconds: How long to run (default: 5 minutes)
        """
        print("=" * 60)
        print("REAL-TIME BAR AGGREGATION DEBUGGER")
        print("=" * 60)
        print(f"\nSymbols: {', '.join(symbols)}")
        print(f"Duration: {duration_seconds}s ({duration_seconds/60:.1f} minutes)")
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("\nWaiting for trades...")
        print("-" * 60)

        self.start_time = datetime.now()

        # Create websocket client
        self.client = FinnhubWebSocketClient(api_key=self.api_key)

        # Create aggregators for each symbol
        for symbol in symbols:
            aggregator = BarAggregator(
                bar_interval="5m",
                timezone="US/Eastern"
            )
            # Set up bar completion callback
            aggregator.on_bar_complete(
                lambda bar_dict, sym=symbol: self._on_bar_complete(sym, bar_dict)
            )
            self.aggregators[symbol] = aggregator

        # Set up trade callback
        self.client.on_trade(self._on_trade)

        try:
            # Connect
            await self.client.connect()

            # Subscribe to symbols
            for symbol in symbols:
                await self.client.subscribe(symbol)

            # Run for specified duration
            await asyncio.sleep(duration_seconds)

            # Disconnect
            await self.client.disconnect()

            # Print summary
            self._print_summary(symbols)

        except Exception as e:
            print(f"\n\nERROR: {e}")
            import traceback
            traceback.print_exc()

    async def _on_trade(self, trade: dict):
        """Handle incoming trade."""
        self.trade_count += 1

        # Log trade details
        symbol = trade.get("symbol")
        price = trade.get("price")
        size = trade.get("size")
        timestamp = trade.get("timestamp")

        # Print first 10 trades
        if self.trade_count <= 10:
            print(f"  Trade #{self.trade_count}: {symbol} @ ${price:.2f} "
                  f"(size: {size}) at {timestamp.strftime('%H:%M:%S.%f')[:-3]}")

            # Check timezone
            if timestamp.tzinfo:
                tz_str = timestamp.strftime('%Z')
                offset = timestamp.strftime('%z')
                print(f"    Timezone: {tz_str} (offset: {offset})")
            else:
                print(f"    WARNING: Naive timestamp (no timezone)!")

        # Log trade
        self.trade_log.append({
            "symbol": symbol,
            "price": price,
            "size": size,
            "timestamp": timestamp,
        })

        # Add to aggregator
        aggregator = self.aggregators.get(symbol)
        if aggregator:
            aggregator.add_trade(timestamp=timestamp, price=price, size=size)

    def _on_bar_complete(self, symbol: str, bar_dict: dict):
        """Handle completed bar."""
        self.completed_bars.append({
            "symbol": symbol,
            **bar_dict
        })

        timestamp = bar_dict["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        # Convert to EST for display
        est = pytz.timezone("US/Eastern")
        if timestamp.tzinfo:
            timestamp_est = timestamp.astimezone(est)
        else:
            timestamp_est = est.localize(timestamp)

        print(f"\n[BAR COMPLETE] {symbol}")
        print(f"  Timestamp: {timestamp_est.strftime('%Y-%m-%d %H:%M:%S %Z')} (offset: {timestamp_est.strftime('%z')})")
        print(f"  OHLCV: O={bar_dict['open']:.2f} H={bar_dict['high']:.2f} "
              f"L={bar_dict['low']:.2f} C={bar_dict['close']:.2f} V={bar_dict['volume']:.0f}")
        print(f"  Trades: {bar_dict.get('trade_count', 0)}")

    def _print_summary(self, symbols: list):
        """Print debug summary."""
        elapsed = (datetime.now() - self.start_time).total_seconds()

        print("\n\n" + "=" * 60)
        print("DEBUG SUMMARY")
        print("=" * 60)

        print(f"\nDuration: {elapsed:.1f}s")
        print(f"Total trades received: {self.trade_count}")
        print(f"Trade rate: {self.trade_count / elapsed:.1f} trades/second")

        # Per-symbol breakdown
        print(f"\nPer-Symbol Breakdown:")
        for symbol in symbols:
            symbol_trades = [t for t in self.trade_log if t["symbol"] == symbol]
            symbol_volume = sum(t["size"] for t in symbol_trades)
            symbol_bars = [b for b in self.completed_bars if b["symbol"] == symbol]

            print(f"\n  {symbol}:")
            print(f"    Trades: {len(symbol_trades)}")
            print(f"    Total volume: {symbol_volume:,.0f}")
            print(f"    Bars completed: {len(symbol_bars)}")

            if symbol_bars:
                print(f"    Bar details:")
                for bar in symbol_bars:
                    ts = bar["timestamp"]
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts)
                    est = pytz.timezone("US/Eastern")
                    if ts.tzinfo:
                        ts_est = ts.astimezone(est)
                    else:
                        ts_est = est.localize(ts)

                    print(f"      {ts_est.strftime('%H:%M')}: "
                          f"O={bar['open']:.2f} H={bar['high']:.2f} "
                          f"L={bar['low']:.2f} C={bar['close']:.2f} "
                          f"V={bar['volume']:,.0f} ({bar.get('trade_count', 0)} trades)")

        # Timezone validation
        print(f"\n\nTimezone Validation:")
        if self.trade_log:
            first_trade = self.trade_log[0]
            last_trade = self.trade_log[-1]

            print(f"  First trade: {first_trade['timestamp']}")
            print(f"  Last trade: {last_trade['timestamp']}")

            if first_trade['timestamp'].tzinfo:
                print(f"  OK Trades are timezone-aware")
            else:
                print(f"  ERROR Trades are naive (no timezone)")

        if self.completed_bars:
            first_bar = self.completed_bars[0]
            ts = first_bar["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)

            if ts.tzinfo:
                print(f"  OK Bars are timezone-aware")
                print(f"  Bar timezone: {ts.strftime('%Z')} (offset: {ts.strftime('%z')})")
            else:
                print(f"  ERROR Bars are naive (no timezone)")

        # Compare with TradingView
        print(f"\n\n" + "=" * 60)
        print("NEXT STEPS:")
        print("=" * 60)
        print("1. Compare bar timestamps with TradingView")
        print("   - Our bars should align with TradingView's 5-minute bars")
        print("   - Example: Our 15:45:00-05:00 bar = TradingView's 3:45 PM EST bar")
        print()
        print("2. Compare OHLCV values")
        print("   - Open/High/Low/Close should be very close (±0.01)")
        print("   - Volume discrepancy indicates:")
        print("     * Finnhub free tier provides sampled trades (not all)")
        print("     * This is expected for free tier")
        print("     * Paid tier would provide 100% of trades")
        print()
        print("3. Timestamp validation")
        print("   - Bars should be in EST (UTC-5 or UTC-4)")
        print("   - Bar times should align to :00, :05, :10, etc.")
        print()
        print("=" * 60)


async def main():
    """Run debugger."""
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
        return 1

    # Check if market is open
    from vibe.trading_bot.core.market_schedulers import create_scheduler
    scheduler = create_scheduler(market_type="stocks", exchange="NYSE")

    if not scheduler.is_market_open():
        print("=" * 60)
        print("WARNING: Market is currently closed")
        print("=" * 60)
        next_open = scheduler.next_market_open()
        print(f"\nNext market open: {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print("\nThis script should be run during market hours (9:30 AM - 4:00 PM EST)")
        print("for meaningful results.")
        print()
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return 0

    # Run debugger
    debugger = BarDebugger(api_key=api_key)
    await debugger.run(
        symbols=["AAPL", "GOOGL", "MSFT"],
        duration_seconds=300  # 5 minutes
    )

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nDebugger interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
