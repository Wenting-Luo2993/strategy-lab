"""
E2E Integration Test: Paper Trading Flow

Tests the complete signal → execution → position tracking → exit monitoring chain
using a live Finnhub WebSocket connection and a simple AlwaysFireStrategy.

AlwaysFireStrategy fires LONG on the first bar it sees, with:
  - Stop loss: entry - $1.00 (tight, to force an exit quickly)
  - Take profit: entry + $2.00

This isolates whether the trading components work end-to-end without waiting
for a real ORB breakout.

Usage:
  $env:FINNHUB_API_KEY="your_key"
  $env:DISCORD_WEBHOOK_URL="your_webhook"   # optional
  python test_e2e_paper_trading.py

Prerequisites:
  - Oracle Cloud container must be stopped (Finnhub free tier = 1 connection)
  - Must be run during US market hours for live bars
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import pytz
import pandas as pd

# Project root on path
sys.path.insert(0, str(Path(__file__).parent))

from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager, OrderRetryPolicy
from vibe.trading_bot.execution.trade_executor import TradeExecutor
from vibe.common.risk import PositionSizer
from vibe.common.strategies.base import StrategyBase, StrategyConfig, ExitSignal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("e2e_test")

SYMBOL = "QQQ"
INITIAL_CAPITAL = 50_000.0
STOP_DISTANCE = 1.00   # $1.00 below entry
TP_DISTANCE = 2.00     # $2.00 above entry
EOD_OFFSET_MINUTES = 45  # EOD exit fires 45 min after test start
MARKET_TZ = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Simple test strategy: fires LONG once, immediately, on first bar
# ---------------------------------------------------------------------------

class AlwaysFireStrategy(StrategyBase):
    """
    Fires LONG on the first bar where no position is open.
    Used only for E2E testing — not a real trading strategy.
    """

    def __init__(self):
        super().__init__(StrategyConfig(name="AlwaysFire"))
        self._fired = False

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(0, index=df.index)

    def generate_signal_incremental(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:

        if self.has_position(symbol):
            return 0, {"reason": "position_open"}

        if self._fired:
            return 0, {"reason": "already_fired"}

        price = float(current_bar.get("close", 0))
        if price <= 0:
            return 0, {"reason": "invalid_price"}

        stop = round(price - STOP_DISTANCE, 2)
        tp   = round(price + TP_DISTANCE, 2)

        self._fired = True
        logger.info(f"[AlwaysFireStrategy] Firing LONG @ ${price:.2f}  SL=${stop:.2f}  TP=${tp:.2f}")

        return 1, {
            "signal": "always_fire_long",
            "current_price": price,
            "stop_loss": stop,
            "take_profit": tp,
            "orb_high": price,
            "orb_low": stop,
            "orb_range": price - stop,
        }


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

class E2EPaperTradingTest:

    def __init__(self, api_key: str, discord_webhook: Optional[str] = None):
        self.api_key = api_key
        self.discord_webhook = discord_webhook

        # Components
        self.exchange = MockExchange(initial_capital=INITIAL_CAPITAL)
        policy = OrderRetryPolicy(max_retries=2, base_delay_seconds=1.0)
        order_manager = OrderManager(exchange=self.exchange, retry_policy=policy)
        sizer = PositionSizer(risk_pct=0.01)
        self.executor = TradeExecutor(
            exchange=self.exchange,
            order_manager=order_manager,
            position_sizer=sizer,
        )
        self.strategy = AlwaysFireStrategy()

        # EOD time = 45 min after test start (guarantees EOD exit fires)
        from datetime import timedelta
        start_et = datetime.now(MARKET_TZ)
        eod_dt = start_et + timedelta(minutes=EOD_OFFSET_MINUTES)
        self._eod_time = eod_dt.strftime("%H:%M")
        logger.info(f"EOD exit time set to {self._eod_time} ET ({EOD_OFFSET_MINUTES} min from now)")

        # Bar tracking
        self._latest_bar: Optional[Dict] = None
        self._bar_count = 0
        self._bar_ready = asyncio.Event()

        # Results
        self.results = {
            "bars_received": 0,
            "signal_fired": False,
            "trade_executed": False,
            "position_tracked": False,
            "exit_triggered": False,
            "exit_reason": None,
            "entry_price": None,
            "exit_price": None,
            "pnl": None,
        }

    def _on_bar_complete(self, bar: dict) -> None:
        """Called by BarAggregator when a 5-min bar is complete."""
        self._latest_bar = bar
        self._bar_count += 1
        ts = bar.get("timestamp", "?")
        logger.info(
            f"[BAR #{self._bar_count}] {SYMBOL} | "
            f"O={bar.get('open',0):.2f} H={bar.get('high',0):.2f} "
            f"L={bar.get('low',0):.2f} C={bar.get('close',0):.2f} "
            f"V={bar.get('volume',0)} | ts={ts}"
        )
        self._bar_ready.set()

    async def _run_trading_cycle(self, bar: dict) -> None:
        """Mirror of orchestrator._trading_cycle: signal → execute → monitor."""

        # Minimal df_context (strategy doesn't need ATR for AlwaysFireStrategy)
        df_context = pd.DataFrame([bar])

        # 1. Generate signal
        signal_value, metadata = self.strategy.generate_signal_incremental(
            symbol=SYMBOL,
            current_bar=bar,
            df_context=df_context,
        )
        logger.info(f"[CYCLE] signal={signal_value}  reason={metadata.get('reason') or metadata.get('signal')}")

        # 2. Execute if signal
        if signal_value != 0:
            self.results["signal_fired"] = True
            entry_price = metadata["current_price"]
            stop_price  = metadata["stop_loss"]

            result = await self.executor.execute_signal(
                symbol=SYMBOL,
                signal=signal_value,
                entry_price=entry_price,
                stop_price=stop_price,
                take_profit=metadata.get("take_profit"),
                strategy_name="AlwaysFire",
            )

            if result.success:
                self.results["trade_executed"] = True
                self.results["entry_price"] = entry_price
                logger.info(
                    f"[TRADE] Executed {int(result.position_size)} shares @ ${entry_price:.2f}  "
                    f"order_id={result.order_id}"
                )

                # Wire into strategy position tracking
                self.strategy.track_position(
                    symbol=SYMBOL,
                    side="buy",
                    entry_price=entry_price,
                    take_profit=metadata.get("take_profit"),
                    stop_loss=stop_price,
                    timestamp=datetime.now(MARKET_TZ),
                )
                self.results["position_tracked"] = self.strategy.has_position(SYMBOL)
                logger.info(f"[POSITION] strategy.has_position(QQQ) = {self.results['position_tracked']}")

                # Send Discord ORDER_SENT if configured
                await self._send_discord_order_sent(result, entry_price, stop_price, metadata)
            else:
                logger.error(f"[TRADE FAILED] {result.reason}")

        # 3. Monitor open positions
        await self._monitor_positions(bar)

    async def _monitor_positions(self, bar: dict) -> None:
        """Check if open position hit stop, TP, or EOD."""
        if not self.strategy.has_position(SYMBOL):
            return

        current_price = float(bar.get("close", 0))
        now_et = datetime.now(MARKET_TZ)
        bar_time_str = now_et.strftime("%H:%M")

        pos = self.strategy.get_position(SYMBOL)
        exit_signal = self.strategy.check_exit_conditions(
            symbol=SYMBOL,
            current_price=current_price,
            current_time=bar_time_str,
            market_close=self._eod_time,
        )

        if exit_signal is None:
            entry = pos["entry_price"]
            pnl_pct = (current_price - entry) / entry * 100
            logger.info(
                f"[MONITOR] {SYMBOL} LONG @ ${entry:.2f} | "
                f"Current=${current_price:.2f} | P&L={pnl_pct:+.2f}% | "
                f"SL=${pos['stop_loss']:.2f} | TP=${pos.get('take_profit', 0):.2f}"
            )
            return

        # Exit triggered
        logger.info(f"[EXIT] {exit_signal.exit_type.upper()} triggered — {exit_signal.reason}")
        close_result = await self.executor._close_position(SYMBOL)

        if close_result.success:
            entry_price = pos["entry_price"]
            quantity = abs(int(close_result.position_size)) if close_result.position_size else 0
            pnl = (current_price - entry_price) * quantity
            pnl_pct = (current_price - entry_price) / entry_price * 100

            self.strategy.close_position(SYMBOL)

            self.results["exit_triggered"] = True
            self.results["exit_reason"] = exit_signal.exit_type
            self.results["exit_price"] = current_price
            self.results["pnl"] = pnl

            logger.info(
                f"[CLOSED] {SYMBOL} @ ${current_price:.2f} | "
                f"Entry=${entry_price:.2f} | {quantity} shares | "
                f"P&L=${pnl:+.2f} ({pnl_pct:+.2f}%)"
            )

            await self._send_discord_order_filled(close_result, entry_price, current_price, pnl, pnl_pct, exit_signal.exit_type)
        else:
            logger.error(f"[CLOSE FAILED] {close_result.reason}")

    async def _send_discord_order_sent(self, result, entry_price, stop_price, metadata):
        if not self.discord_webhook:
            return
        try:
            from vibe.trading_bot.notifications.payloads import OrderNotificationPayload
            from vibe.trading_bot.notifications.helper import discord_notification_context
            payload = OrderNotificationPayload(
                event_type="ORDER_SENT",
                timestamp=datetime.now(MARKET_TZ),
                order_id=result.order_id or "test",
                symbol=SYMBOL,
                side="buy",
                order_type="market",
                quantity=result.position_size,
                strategy_name="AlwaysFire (E2E Test)",
                signal_reason=metadata.get("signal"),
                order_price=entry_price,
                exchange="PAPER",
            )
            async with discord_notification_context(self.discord_webhook) as notifier:
                await notifier.send_order_event(payload)
            logger.info("[DISCORD] ORDER_SENT sent")
        except Exception as e:
            logger.error(f"[DISCORD] Failed: {e}")

    async def _send_discord_order_filled(self, result, entry_price, exit_price, pnl, pnl_pct, reason):
        if not self.discord_webhook:
            return
        try:
            from vibe.trading_bot.notifications.payloads import OrderNotificationPayload
            from vibe.trading_bot.notifications.helper import discord_notification_context
            quantity = abs(int(result.position_size)) if result.position_size else 0
            payload = OrderNotificationPayload(
                event_type="ORDER_FILLED",
                timestamp=datetime.now(MARKET_TZ),
                order_id=result.order_id or "test",
                symbol=SYMBOL,
                side="sell",
                order_type="market",
                quantity=quantity,
                strategy_name="AlwaysFire (E2E Test)",
                signal_reason=reason,
                fill_price=exit_price,
                filled_quantity=quantity,
                realized_pnl=pnl,
                realized_pnl_pct=pnl_pct,
                order_price=exit_price,
                exchange="PAPER",
            )
            async with discord_notification_context(self.discord_webhook) as notifier:
                await notifier.send_order_event(payload)
            logger.info("[DISCORD] ORDER_FILLED sent")
        except Exception as e:
            logger.error(f"[DISCORD] Failed: {e}")

    async def run(self) -> None:
        """Main test loop: connect Finnhub, wait for bars, run trading cycles."""
        from vibe.trading_bot.data.providers.finnhub import FinnhubWebSocketClient
        from vibe.trading_bot.data.aggregator import BarAggregator

        logger.info("=" * 60)
        logger.info("E2E PAPER TRADING TEST")
        logger.info(f"Symbol: {SYMBOL}  Capital: ${INITIAL_CAPITAL:,.0f}")
        logger.info(f"Strategy: AlwaysFireStrategy (fires LONG on first bar)")
        logger.info(f"Stop: ${STOP_DISTANCE:.2f} below entry  TP: ${TP_DISTANCE:.2f} above entry")
        logger.info("=" * 60)

        # Set up bar aggregator
        aggregator = BarAggregator(bar_interval="5m")
        aggregator.on_bar_complete(self._on_bar_complete)

        # Connect Finnhub
        client = FinnhubWebSocketClient(api_key=self.api_key)

        async def on_trade(trade_data: dict) -> None:
            price = trade_data.get("price", 0)
            size  = trade_data.get("size", 0)
            ts_dt = trade_data.get("timestamp")  # already a datetime (UTC)
            if price and price > 0 and size and size > 0:
                if ts_dt is None:
                    ts_dt = datetime.now(MARKET_TZ)
                aggregator.add_trade(price=price, size=size, timestamp=ts_dt)

        client.on_trade(on_trade)

        logger.info("Connecting to Finnhub WebSocket...")
        await client.connect()
        await client.subscribe(SYMBOL)
        logger.info(f"Subscribed to {SYMBOL}. Waiting for bars...")

        try:
            max_bars = 20  # run for up to 20 bars (~100 min), EOD fires at 45 min
            bars_processed = 0

            while bars_processed < max_bars:
                # Wait for next bar (timeout after 10 min)
                try:
                    await asyncio.wait_for(self._bar_ready.wait(), timeout=600)
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for bar — no trades in 10 min?")
                    break

                self._bar_ready.clear()
                bar = self._latest_bar
                bars_processed += 1
                self.results["bars_received"] = bars_processed

                await self._run_trading_cycle(bar)

                # Stop once we've seen entry AND exit
                if self.results["exit_triggered"]:
                    logger.info("[TEST] Exit confirmed — stopping early")
                    break

        finally:
            await client.disconnect()

        self._print_results()

    def _print_results(self) -> None:
        r = self.results
        logger.info("")
        logger.info("=" * 60)
        logger.info("TEST RESULTS")
        logger.info("=" * 60)

        checks = [
            ("Bars received",      r["bars_received"] > 0,        str(r["bars_received"])),
            ("Signal fired",       r["signal_fired"],              "YES" if r["signal_fired"] else "NO"),
            ("Trade executed",     r["trade_executed"],            "YES" if r["trade_executed"] else "NO"),
            ("Position tracked",   r["position_tracked"],          "YES" if r["position_tracked"] else "NO"),
            ("Exit triggered",     r["exit_triggered"],            f"{r['exit_reason']}" if r["exit_triggered"] else "NO"),
            ("P&L",                r["pnl"] is not None,           f"${r['pnl']:+.2f}" if r["pnl"] is not None else "N/A"),
        ]

        all_passed = True
        for label, passed, value in checks:
            status = "[PASS]" if passed else "[FAIL]"
            if not passed:
                all_passed = False
            logger.info(f"  {status}  {label:<22} {value}")

        logger.info("")
        if all_passed:
            logger.info("ALL CHECKS PASSED - E2E flow is working")
        else:
            logger.info("SOME CHECKS FAILED - review logs above")
        logger.info("=" * 60)


async def main():
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        logger.error("Set FINNHUB_API_KEY environment variable")
        sys.exit(1)

    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_webhook:
        logger.info("Discord notifications enabled")
    else:
        logger.info("Discord notifications disabled (set DISCORD_WEBHOOK_URL to enable)")

    test = E2EPaperTradingTest(api_key=api_key, discord_webhook=discord_webhook)
    await test.run()


if __name__ == "__main__":
    asyncio.run(main())
