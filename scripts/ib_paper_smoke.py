"""Interactive Brokers paper-account smoke test.

This script proves the P0 end-to-end IB path:
1. Connect to TWS or IB Gateway paper account.
2. Request market data.
3. Optionally submit a small order.
4. Wait for fill event and record execution metrics.

Example:
    python scripts/ib_paper_smoke.py --symbols QQQ,GOOGL,AMZN,TSLA
    python scripts/ib_paper_smoke.py --order-symbol QQQ --quantity 1 --submit-order
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

MARKET_DATA_TYPES = {
    "live": 1,
    "frozen": 2,
    "delayed": 3,
    "delayed_frozen": 4,
}


def parse_symbols(raw_symbols: str) -> list[str]:
    """Parse a comma-separated symbol list."""
    symbols = [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]
    if not symbols:
        raise ValueError("At least one symbol is required")
    return symbols


def optional_float(raw_value: str | None) -> float | None:
    """Parse an optional float environment value."""
    if raw_value in (None, ""):
        return None
    return float(raw_value)


def parse_market_data_type(raw_value: str) -> int:
    """Parse IB market data type from a name or integer."""
    normalized = raw_value.strip().lower().replace("-", "_")
    if normalized in MARKET_DATA_TYPES:
        return MARKET_DATA_TYPES[normalized]
    parsed = int(normalized)
    if parsed not in set(MARKET_DATA_TYPES.values()):
        raise ValueError("market data type must be one of: live, frozen, delayed, delayed_frozen, 1, 2, 3, 4")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IB paper trading smoke test")
    parser.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "4002")))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IB_CLIENT_ID", "91")))
    parser.add_argument("--account-id", default=os.getenv("IB_ACCOUNT_ID"))
    parser.add_argument("--market-data-type", default=os.getenv("IB_MARKET_DATA_TYPE", "live"), help="live, frozen, delayed, delayed_frozen, or 1-4")
    parser.add_argument("--symbol", dest="legacy_symbol", default=None, help="Single symbol alias for --symbols")
    parser.add_argument("--symbols", default=os.getenv("IB_SMOKE_SYMBOLS", os.getenv("IB_SMOKE_SYMBOL", "QQQ,GOOGL,AMZN,TSLA")))
    parser.add_argument("--order-symbol", default=os.getenv("IB_SMOKE_ORDER_SYMBOL"))
    parser.add_argument("--quantity", type=float, default=float(os.getenv("IB_SMOKE_QUANTITY", "1")))
    parser.add_argument("--side", choices=["buy", "sell"], default=os.getenv("IB_SMOKE_SIDE", "buy"))
    parser.add_argument("--order-type", choices=["market", "limit"], default=os.getenv("IB_SMOKE_ORDER_TYPE", "market"))
    parser.add_argument("--limit-price", type=float, default=optional_float(os.getenv("IB_SMOKE_LIMIT_PRICE")))
    parser.add_argument("--submit-order", action="store_true", help="Actually submit an order to the paper account")
    parser.add_argument("--fill-timeout", type=float, default=60.0)
    parser.add_argument("--cancel-on-timeout", action="store_true", help="Cancel the submitted paper order if fill wait times out")
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
    raw_symbols = args.legacy_symbol or args.symbols
    symbols = parse_symbols(raw_symbols)
    order_symbol = (args.order_symbol or symbols[0]).upper()
    if order_symbol not in symbols:
        symbols.insert(0, order_symbol)

    broker = InteractiveBrokersAPI(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account_id=args.account_id,
        market_data_type=parse_market_data_type(args.market_data_type),
        readonly=not args.submit_order,
    )
    metrics = build_metrics_recorder(args)

    try:
        await broker.connect()
        account = await broker.get_account_info()
        logger.info("Account: id=%s net_liq=%s cash=%s buying_power=%s", account.account_id, account.net_liquidation, account.cash, account.buying_power)

        quotes = {}
        for symbol in symbols:
            quote = await broker.get_market_data(symbol)
            quotes[symbol] = quote
            logger.info("Market data: %s bid=%s ask=%s last=%s market_price=%s", quote.symbol, quote.bid, quote.ask, quote.last, quote.market_price)

        if not args.submit_order:
            logger.info("Readonly smoke complete. Re-run with --submit-order to place a paper-market order.")
            return 0

        if args.order_type == "limit" and args.limit_price is None:
            raise ValueError("--limit-price is required when --order-type limit")

        quote = quotes[order_symbol]

        order = BrokerOrder(
            symbol=order_symbol,
            side=args.side,
            quantity=args.quantity,
            order_type=args.order_type,
            expected_price=quote.market_price,
            limit_price=args.limit_price,
            strategy_order_id="ib-paper-smoke",
        )
        broker_order_id = await broker.submit_order(order)
        logger.info("Submitted order: broker_order_id=%s", broker_order_id)

        try:
            fill = await broker.wait_for_fill(broker_order_id, timeout_seconds=args.fill_timeout)
        except TimeoutError:
            status = await broker.get_order_status(broker_order_id)
            logger.warning("Timed out waiting for full fill. Latest order status: %s", status)
            if args.cancel_on_timeout:
                await broker.cancel_order(broker_order_id)
                logger.warning("Cancelled paper order after timeout: broker_order_id=%s", broker_order_id)
                return 0
            return 2

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
