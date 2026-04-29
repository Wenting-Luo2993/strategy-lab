import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillSimulator
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.backtester.runner import RuleSetRunner
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.bar import Bar
from vibe.common.ruleset.models import StrategyRuleSet


def _resample(df: pd.DataFrame, interval: str = "5min") -> pd.DataFrame:
    return df.resample(interval, closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()


def _add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR_{period} column using Wilder's smoothing (alpha = 1/period)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df = df.copy()
    df[f"ATR_{period}"] = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return df


class BacktestEngine:
    """
    Event-driven backtester. Iterates a sorted bar index from Parquet data,
    drives SimulatedClock, calls ORBStrategy via RuleSetRunner, manages
    portfolio, and returns a BacktestResult.
    """

    def __init__(
        self,
        ruleset: StrategyRuleSet,
        data_dir: Path,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
    ) -> None:
        self.ruleset = ruleset
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks

    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> BacktestResult:
        # 1. Load and resample data
        loader = ParquetLoader(self.data_dir, [symbol])
        df_1m = asyncio.run(
            loader.get_bars(symbol, start_time=start_date, end_time=end_date)
        )
        interval = self.ruleset.instruments.timeframe  # e.g. "5m"
        pd_interval = interval.replace("m", "min")
        df = _resample(df_1m, pd_interval)
        df = _add_atr(df)

        # 2. Init components
        clock = SimulatedClock()
        fill_sim = FillSimulator(slippage_ticks=self.slippage_ticks)
        portfolio = PortfolioManager(self.initial_capital)
        runner = RuleSetRunner(self.ruleset)

        # 3. Event loop
        prev_date = None
        for ts, row in df.iterrows():
            clock.set_time(ts.to_pydatetime())
            current_date = ts.date()

            if current_date != prev_date:
                runner.reset_daily_state(symbol)
                prev_date = current_date

            bar = Bar(
                timestamp=ts.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            current_bars = {symbol: bar}

            # Check exits before entry (stop/EOD)
            portfolio.check_exits(current_bars, clock)

            # Generate entry signal only if no open position
            if symbol not in portfolio.positions:
                current_bar_dict = row.to_dict()
                current_bar_dict["timestamp"] = ts.to_pydatetime()

                signal_value, metadata = runner.generate_signal(
                    symbol, current_bar_dict, df.loc[:ts]
                )

                if signal_value in (1, -1):
                    side = "buy" if signal_value == 1 else "sell"
                    stop_price = metadata.get("stop_loss", bar.close * 0.99)
                    quantity = self._position_size(
                        capital=portfolio.cash,
                        entry_price=bar.close,
                        stop_price=stop_price,
                    )
                    if quantity > 0:
                        fill = fill_sim.execute(symbol, side, quantity, bar)
                        portfolio.open_position(fill, stop_price=stop_price, timestamp=ts.to_pydatetime())
                        runner.track_position(
                            symbol=symbol, side=side,
                            entry_price=fill.avg_price,
                            take_profit=metadata.get("take_profit"),
                            stop_loss=stop_price,
                            timestamp=ts.to_pydatetime(),
                        )

            portfolio.update_equity(current_bars, ts.to_pydatetime())

        # 4. Analyze results
        return PerformanceAnalyzer.analyze(
            trades=portfolio.trade_history,
            equity_curve=portfolio.equity_curve,
            initial_capital=self.initial_capital,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            ruleset_name=self.ruleset.name,
            ruleset_version=self.ruleset.version,
        )

    def _position_size(
        self, capital: float, entry_price: float, stop_price: float
    ) -> int:
        """Risk position_size.value% of capital per trade based on stop distance."""
        risk_pct = self.ruleset.position_size.value
        risk_dollars = capital * risk_pct
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0
        return max(1, int(risk_dollars / stop_distance))
