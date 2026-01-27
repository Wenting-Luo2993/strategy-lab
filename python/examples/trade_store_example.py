"""
Example usage of TradeStore

This script demonstrates how to use the TradeStore module
to record and query trade executions.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, date
from src.data.trade_store import TradeStore

def main():
    # Initialize the trade store
    print("Initializing TradeStore...")
    store = TradeStore('data/trades.db')

    # Example 1: Record a simple trade
    print("\n1. Recording a simple trade...")
    trade_id = store.record_trade(
        symbol='AAPL',
        side='BUY',
        quantity=100,
        price=150.25
    )
    print(f"   ✓ Trade recorded with ID: {trade_id}")

    # Example 2: Record a trade with all fields
    print("\n2. Recording a trade with metadata...")
    trade_id = store.record_trade(
        symbol='TSLA',
        side='SELL',
        quantity=50,
        price=200.75,
        strategy='ORB',
        pnl=125.50,
        metadata={
            'signal': 'breakout',
            'stop_loss': 198.50,
            'take_profit': 205.00,
            'entry_time': '09:35:00'
        }
    )
    print(f"   ✓ Trade recorded with ID: {trade_id}")

    # Example 3: Record a few more trades for demonstration
    print("\n3. Recording additional trades...")
    trades_data = [
        ('AAPL', 'SELL', 100, 151.50, 'ORB', 125.00),
        ('MSFT', 'BUY', 200, 300.00, 'MOMENTUM', None),
        ('GOOGL', 'BUY', 30, 100.50, 'ORB', -25.00),
    ]

    for symbol, side, qty, price, strategy, pnl in trades_data:
        store.record_trade(symbol, side, qty, price, strategy, pnl)
    print(f"   ✓ {len(trades_data)} trades recorded")

    # Example 4: Get recent trades
    print("\n4. Retrieving recent trades...")
    recent = store.get_recent_trades(limit=5)
    for trade in recent:
        print(f"   - {trade['side']} {trade['quantity']} {trade['symbol']} @ ${trade['price']:.2f}")

    # Example 5: Get today's summary
    print("\n5. Daily summary:")
    summary = store.get_daily_summary(date.today())
    print(f"   Total Trades: {summary['total_trades']}")
    print(f"   Total P&L: ${summary['total_pnl']:.2f}")
    print(f"   Win Rate: {summary['win_rate']:.1f}%")
    print(f"   Symbols Traded: {', '.join(summary['symbols_traded'])}")

    if summary['best_trade']:
        best = summary['best_trade']
        print(f"   Best Trade: {best['symbol']} (${best['pnl']:.2f})")

    if summary['worst_trade']:
        worst = summary['worst_trade']
        print(f"   Worst Trade: {worst['symbol']} (${worst['pnl']:.2f})")

    # Example 6: Query trades by symbol
    print("\n6. Trades for AAPL:")
    aapl_trades = store.get_trades_by_symbol('AAPL')
    for trade in aapl_trades:
        print(f"   - {trade['side']} {trade['quantity']} @ ${trade['price']:.2f} (P&L: ${trade['pnl'] or 0:.2f})")

    # Example 7: Query trades by strategy
    print("\n7. Trades for ORB strategy:")
    orb_trades = store.get_trades_by_strategy('ORB')
    print(f"   {len(orb_trades)} trades using ORB strategy")

    # Example 8: Get total P&L
    print("\n8. Overall Statistics:")
    total_pnl = store.get_total_pnl()
    print(f"   Total P&L (all time): ${total_pnl:.2f}")

    # Clean up
    store.close()
    print("\n✓ TradeStore demo complete!")

if __name__ == '__main__':
    main()
