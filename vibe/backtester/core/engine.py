import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillSimulator, FillResult
from vibe.backtester.core.portfolio import PortfolioManager
from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.simulator import ExecutionSimulator
from vibe.backtester.core.execution.pending_queue import PendingOrderQueue
from vibe.backtester.core.execution.models import Order, Fill
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


def _compute_adv(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    Pre-compute Average Daily Volume (ADV) efficiently.
    
    Computes once before event loop (O(n)) instead of per-bar (O(n×20)).
    
    Args:
        df: DataFrame with volume column and daily index
        window: Rolling window size (default 20 days)
        
    Returns:
        Series indexed by date with ADV values
    """
    daily_volumes = df["volume"].resample("1D").sum()
    adv = daily_volumes.rolling(window=window).mean()
    return adv


class BacktestEngine:
    """
    Event-driven backtester. Iterates a sorted bar index from Parquet data,
    drives SimulatedClock, calls ORBStrategy via RuleSetRunner, manages
    portfolio, and returns a BacktestResult.
    
    Supports pluggable execution models via ExecutionConfig for realistic
    fills with volume constraints, dynamic slippage, and market impact.
    """

    def __init__(
        self,
        ruleset: StrategyRuleSet,
        data_dir: Path,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 2,
        execution_config: Optional[ExecutionConfig] = None,
    ) -> None:
        self.ruleset = ruleset
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
        self.execution_config = execution_config
        self.pending_orders: list[Order] = []

    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        precomputed_features: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """
        Run backtest simulation.
        
        Args:
            symbol: Trading symbol
            start_date: Backtest start date
            end_date: Backtest end date
            precomputed_features: Optional pre-computed indicators (ATR, ADX, etc.)
                                 If provided, skips indicator computation for performance.
                                 Index must match the resampled bar timestamps.
        
        Returns:
            BacktestResult with trades, metrics, and equity curve
        """
        # 1. Load and resample data
        loader = ParquetLoader(self.data_dir, [symbol])
        df_1m = asyncio.run(
            loader.get_bars(symbol, start_time=start_date, end_time=end_date)
        )
        interval = self.ruleset.instruments.timeframe  # e.g. "5m"
        pd_interval = interval.replace("m", "min")
        df = _resample(df_1m, pd_interval)
        
        # Use pre-computed features if provided, otherwise compute ATR on-the-fly
        if precomputed_features is not None:
            # Merge pre-computed features (indexed by timestamp)
            # Only use features that align with our df index
            aligned_features = precomputed_features.loc[df.index.intersection(precomputed_features.index)]
            df = df.join(aligned_features, how="left")
        else:
            # Backward compatibility: compute ATR if not provided
            df = _add_atr(df)
        
        # ORBCalculator requires a 'timestamp' column (not just the DatetimeIndex)
        df["timestamp"] = df.index

        # 2. Pre-compute ADV (Average Daily Volume) before event loop
        # This is O(n) one-time computation, not O(n×20) per-bar
        adv_series = _compute_adv(df)
        
        # 3. Determine execution config
        if self.execution_config is None:
            # Backward compatibility: use legacy config based on slippage_ticks
            execution_config = ExecutionConfig.legacy(slippage_ticks=self.slippage_ticks)
        else:
            execution_config = self.execution_config
        
        # 4. Init components
        clock = SimulatedClock()
        
        # Use ExecutionSimulator if config provided, otherwise keep FillSimulator for legacy
        if self.execution_config is not None:
            execution_sim = ExecutionSimulator(config=execution_config)
        else:
            # Backward compatibility: use old FillSimulator
            execution_sim = None
        
        fill_sim = FillSimulator(slippage_ticks=self.slippage_ticks)  # Fallback
        
        trailing_stop_config = None
        if self.ruleset.exit.trailing_stop is not None:
            trailing_stop_config = self.ruleset.exit.trailing_stop.model_dump()

        portfolio = PortfolioManager(
            self.initial_capital,
            trailing_stop_config=trailing_stop_config,
        )
        runner = RuleSetRunner(self.ruleset)
        
        # Reset pending orders for new backtest
        self.pending_orders = []

        # 5. Event loop with bar_index counter
        prev_date = None
        bar_index = 0  # Track bar index for latency support
        
        for ts, row in df.iterrows():
            clock.set_time(ts.to_pydatetime())
            current_date = ts.date()

            if current_date != prev_date:
                # Reset bar index at start of new day
                bar_index = 0
                runner.reset_daily_state(symbol)
                prev_date = current_date
            else:
                bar_index += 1

            bar = Bar(
                timestamp=ts.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            current_bars = {symbol: bar}

            # Check exits before entry (stop/EOD); sync runner state for closed positions
            open_before = set(portfolio.positions.keys())
            portfolio.check_exits(current_bars, clock)
            for closed_sym in open_before - set(portfolio.positions.keys()):
                runner.close_position(closed_sym)

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

                    # Entry at stop-market trigger price (OR_high+$0.01 / OR_low-$0.01)
                    # plus configurable slippage ticks for market impact.
                    _TICK = 0.01
                    slippage = self.slippage_ticks * _TICK
                    orb_high = metadata.get("orb_high")
                    orb_low  = metadata.get("orb_low")
                    if signal_value == 1 and orb_high is not None:
                        entry_price = orb_high + _TICK + slippage
                    elif signal_value == -1 and orb_low is not None:
                        entry_price = orb_low - _TICK - slippage
                    else:
                        entry_price = bar.close

                    quantity = self._position_size(
                        capital=portfolio.cash,
                        entry_price=entry_price,
                        stop_price=stop_price,
                    )
                    if quantity > 0:
                        # Create Order with signal_bar_index for latency tracking
                        order = Order(
                            id=f"{symbol}_{ts.timestamp()}",
                            symbol=symbol,
                            side=side,
                            size=quantity,
                            order_type="market",
                            limit_price=None,
                            timestamp=ts.to_pydatetime(),
                            signal_bar_index=bar_index,
                            price_override=entry_price,
                        )
                        
                        # Execute order (use ExecutionSimulator if available)
                        if execution_sim is not None:
                            # Get current ADV for realistic impact calculation
                            current_adv = adv_series.get(current_date)
                            fill = execution_sim.execute_market_order(
                                order=order,
                                bar=bar,
                                adv=current_adv,
                            )
                            # Convert Fill to FillResult format for portfolio compatibility
                            if fill is not None:
                                fill_result = FillResult(
                                    symbol=fill.symbol,
                                    side=fill.side,
                                    filled_qty=fill.qty,
                                    avg_price=fill.price,
                                    commission=0.0,
                                )
                        else:
                            # Backward compatibility: use FillSimulator
                            fill_result = fill_sim.execute(symbol, side, quantity, bar,
                                                           price_override=entry_price)
                        
                        if fill_result is not None:
                            take_profit = metadata.get("take_profit")
                            portfolio.open_position(fill_result, stop_price=stop_price, take_profit=take_profit, timestamp=ts.to_pydatetime())
                            runner.track_position(
                                symbol=symbol, side=side,
                                entry_price=fill_result.avg_price,
                                take_profit=take_profit,
                                stop_loss=stop_price,
                                timestamp=ts.to_pydatetime(),
                            )
                            
                            # Remove filled order from pending
                            self.pending_orders = [o for o in self.pending_orders if o.id != order.id]

            portfolio.update_equity(current_bars, ts.to_pydatetime())

        # 6. Analyze results
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
