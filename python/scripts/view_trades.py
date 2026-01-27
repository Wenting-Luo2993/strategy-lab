"""
Database Viewer - View trades stored in SQLite database

Quick utility to inspect the trades database from command line.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date
from src.data.trade_store import TradeStore
import argparse


def view_recent(store, limit=10):
    """View most recent trades."""
    trades = store.get_recent_trades(limit=limit)

    if not trades:
        print("No trades found.")
        return

    print(f"\n📊 Recent {len(trades)} Trades:")
    print("-" * 80)
    print(f"{'ID':<5} {'Time':<20} {'Symbol':<8} {'Side':<6} {'Qty':<8} {'Price':<10} {'P&L':<10}")
    print("-" * 80)

    for trade in trades:
        timestamp = trade['timestamp'][:19] if trade['timestamp'] else ''
        pnl_str = f"${trade['pnl']:.2f}" if trade['pnl'] is not None else 'N/A'

        print(f"{trade['id']:<5} {timestamp:<20} {trade['symbol']:<8} {trade['side']:<6} "
              f"{trade['quantity']:<8.0f} ${trade['price']:<9.2f} {pnl_str:<10}")


def view_summary(store, target_date=None):
    """View daily summary."""
    if target_date is None:
        target_date = date.today()

    summary = store.get_daily_summary(target_date)

    print(f"\n📈 Daily Summary - {target_date}")
    print("-" * 80)
    print(f"Total Trades:     {summary['total_trades']}")
    print(f"Total P&L:        ${summary['total_pnl']:.2f}")
    print(f"Gross Profit:     ${summary['gross_profit']:.2f}")
    print(f"Gross Loss:       ${summary['gross_loss']:.2f}")
    print(f"Win Rate:         {summary['win_rate']:.1f}%")
    print(f"Total Volume:     {summary['total_volume']:.0f} shares")

    if summary['symbols_traded']:
        print(f"Symbols Traded:   {', '.join(summary['symbols_traded'])}")

    if summary['best_trade']:
        best = summary['best_trade']
        print(f"\nBest Trade:       {best['symbol']} - ${best['pnl']:.2f}")

    if summary['worst_trade']:
        worst = summary['worst_trade']
        print(f"Worst Trade:      {worst['symbol']} - ${worst['pnl']:.2f}")


def view_by_symbol(store, symbol, limit=20):
    """View trades for specific symbol."""
    trades = store.get_trades_by_symbol(symbol, limit=limit)

    if not trades:
        print(f"No trades found for {symbol}.")
        return

    print(f"\n📊 {len(trades)} Trades for {symbol}:")
    print("-" * 80)
    print(f"{'Time':<20} {'Side':<6} {'Qty':<8} {'Price':<10} {'Strategy':<12} {'P&L':<10}")
    print("-" * 80)

    total_pnl = 0
    for trade in trades:
        timestamp = trade['timestamp'][:19] if trade['timestamp'] else ''
        strategy = trade['strategy'] or 'N/A'
        pnl_str = f"${trade['pnl']:.2f}" if trade['pnl'] is not None else 'N/A'
        if trade['pnl']:
            total_pnl += trade['pnl']

        print(f"{timestamp:<20} {trade['side']:<6} {trade['quantity']:<8.0f} "
              f"${trade['price']:<9.2f} {strategy:<12} {pnl_str:<10}")

    print("-" * 80)
    print(f"Total P&L for {symbol}: ${total_pnl:.2f}")


def view_stats(store):
    """View overall statistics."""
    total_pnl = store.get_total_pnl()
    all_trades = store.get_recent_trades(limit=100000)  # Get all

    print("\n📊 Overall Statistics:")
    print("-" * 80)
    print(f"Total Trades:     {len(all_trades)}")
    print(f"Total P&L:        ${total_pnl:.2f}")

    if all_trades:
        symbols = set(t['symbol'] for t in all_trades)
        strategies = set(t['strategy'] for t in all_trades if t['strategy'])

        print(f"Unique Symbols:   {len(symbols)}")
        print(f"Strategies Used:  {len(strategies)}")

        if symbols:
            print(f"Symbols:          {', '.join(sorted(symbols))}")
        if strategies:
            print(f"Strategies:       {', '.join(sorted(strategies))}")


def main():
    parser = argparse.ArgumentParser(description='View trades database')
    parser.add_argument('--db', default='data/trades.db', help='Database path')
    parser.add_argument('--recent', type=int, metavar='N', help='Show N recent trades')
    parser.add_argument('--summary', action='store_true', help='Show daily summary')
    parser.add_argument('--symbol', metavar='SYM', help='Show trades for symbol')
    parser.add_argument('--stats', action='store_true', help='Show overall stats')
    parser.add_argument('--all', action='store_true', help='Show everything')

    args = parser.parse_args()

    # Initialize store
    store = TradeStore(args.db)

    # If no specific option, show recent
    if not any([args.recent, args.summary, args.symbol, args.stats, args.all]):
        args.recent = 10

    # Show requested views
    if args.all or args.stats:
        view_stats(store)

    if args.all or args.summary:
        view_summary(store)

    if args.all or args.recent:
        limit = args.recent if args.recent else 10
        view_recent(store, limit=limit)

    if args.symbol:
        view_by_symbol(store, args.symbol.upper())

    store.close()
    print()


if __name__ == '__main__':
    main()
