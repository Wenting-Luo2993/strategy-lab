"""Simple direct test of orchestrator trade recording"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.data.trade_store import TradeStore
from datetime import datetime

# Test direct recording
store = TradeStore('data/trades.db')

# Simulate what orchestrator does
order = {
    "ticker": "AAPL",
    "side": "buy",
    "qty": 100,
    "timestamp": pd.Timestamp.now()
}

response = {
    "status": "filled",
    "filled_qty": 100,
    "avg_fill_price": 150.25,
    "commission": 1.0,
    "order_id": "TEST_001"
}

# Convert timestamp
ts = order["timestamp"]
if isinstance(ts, pd.Timestamp):
    timestamp_dt = ts.to_pydatetime()
else:
    timestamp_dt = ts

print(f"Recording trade: {order['side']} {response['filled_qty']} {order['ticker']} @ ${response['avg_fill_price']}")
print(f"Timestamp: {timestamp_dt}")

# Record
trade_id = store.record_trade(
    symbol=order["ticker"],
    side=order["side"].upper(),
    quantity=float(response["filled_qty"]),
    price=float(response["avg_fill_price"]),
    strategy="ORBStrategy",
    pnl=0.0,
    metadata={
        'run_id': 'test',
        'order_id': response["order_id"],
        'commission': float(response["commission"]),
        'order_status': response["status"]
    },
    timestamp=timestamp_dt
)

print(f"✓ Trade recorded with ID: {trade_id}")

# Verify
trades = store.get_recent_trades(limit=1)
if trades:
    t = trades[0]
    print(f"\n✓ Trade found in DB:")
    print(f"  Symbol: {t['symbol']}, Side: {t['side']}, Qty: {t['quantity']}, Price: ${t['price']:.2f}")
else:
    print("✗ No trades found!")

store.close()
