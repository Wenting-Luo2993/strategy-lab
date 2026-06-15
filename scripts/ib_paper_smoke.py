"""Interactive Brokers paper-account smoke test.

This script proves the P0 end-to-end IB path:
1. Connect to TWS or IB Gateway paper account.
2. Request market data.
3. Optionally submit a small order.
4. Wait for fill event and record execution metrics.

Example:
    python scripts/ib_paper_smoke.py --symbol AAPL --quantity 1 --submit-order
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vibe.trading_bot.brokers.base import BrokerOrder
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.storage.metrics_store import MetricsStore
from vibe.trading_bot.storage.operational_metrics import (
    OperationalMetricsRecorder,
    SupabaseRestMetricsSink,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IB paper trading smoke test")
    parser.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "7497")))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IB_CLIENT_ID", "91")))
    parser.add_argument("--account-id", default=os.getenv("IB_ACCOUNT_ID"))
    parser.add_argument("--symbol", default=os.getenv("IB_SMOKE_SYMBOL", "AAPL"))
    parser.add_argument("--quantity", type=float, default=float(os.getenv("IB_SMOKE_QUANTITY", "1")))
    parser.add_argument("--side", choices=["buy", "sell"], default=os.getenv("IB_SMOKE_SIDE", "buy"))
    parser.add_argument("--submit-order", action="store_true", help="Actually submit a market order to the paper account")
    parser.add_argument("--fill-timeout", type=float, default=60.0)
    parser.add_argument("--metrics-db", default=os.getenv("OPERATIONAL_METRICS_DB", "./data/local/operational_metrics.db"))
    parser.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL"))
    parser.add_argument("--supabase-anon-key", default=os.getenv("SUPABASE_ANON_KEY"))
    parser.add_argument("--supabase-table", default=os.getenv("SUPABASE_OPERATIONAL_METRICS_TABLE", "operational_metrics"))
    return parser.parse_args()


def build_metrics_recorder(args: argparse.Namespace) -> OperationalMetricsRecorder:
    local_store = MetricsStore(args.metrics_db)
    remote_sink = None
    if args.supabase_url and args.supabase_anon_key:
        remote_sink = SupabaseRestMetricsSink(
            url=args.supabase_url,
            anon_key=args.supabase_anon_key,
            table_name=args.supabase_table,
        )
    return OperationalMetricsRecorder(local_store=local_store, remote_sink=remote_sink)


async def main() -> int:
    args = parse_args()
    broker = InteractiveBrokersAPI(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account_id=args.account_id,
        readonly=not args.submit_order,
    )
    metrics = build_metrics_recorder(args)

    try:
        await broker.connect()
        account = await broker.get_account_info()
        logger.info("Account: id=%s net_liq=%s cash=%s buying_power=%s", account.account_id, account.net_liquidation, account.cash, account.buying_power)

        quote = await broker.get_market_data(args.symbol)
        logger.info("Market data: %s bid=%s ask=%s last=%s market_price=%s", quote.symbol, quote.bid, quote.ask, quote.last, quote.market_price)

        if not args.submit_order:
            logger.info("Readonly smoke complete. Re-run with --submit-order to place a paper-market order.")
            return 0

        order = BrokerOrder(
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            order_type="market",
            expected_price=quote.market_price,
            strategy_order_id="ib-paper-smoke",
        )
        broker_order_id = await broker.submit_order(order)
        logger.info("Submitted order: broker_order_id=%s", broker_order_id)

        fill = await broker.wait_for_fill(broker_order_id, timeout_seconds=args.fill_timeout)
        await metrics.record_fill_event(fill)
        logger.info(
            "Fill event: order_id=%s qty=%s expected=%s actual=%s slippage=%s slippage_bps=%s latency_ms=%.2f",
            fill.broker_order_id,
            fill.quantity,
            fill.expected_price,
            fill.avg_fill_price,
            fill.slippage,
            fill.slippage_bps,
            fill.latency_ms,
        )
        return 0
    finally:
        await broker.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
