import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.fill_simulator import FillSimulator, FillResult
from vibe.backtester.core.portfolio import PortfolioManager, parse_hhmm
from vibe.backtester.core.execution_realism import (
    EntryFillPolicy,
    ExecutionRealismConfig,
    EXECUTION_MODEL_VERSION,
)
from vibe.backtester.core.reconciliation import reconcile_portfolio
from vibe.backtester.core.resting_stop import (
    SessionMinutes,
    session_time,
    split_sessions,
)
from vibe.backtester.core.integrity import build_engine_integrity_report
from vibe.common.risk.position_sizer import PositionSizer
from vibe.common.risk.portfolio_sizing import (
    CAPPED_BY_EQUITY_HEADROOM,
    CAPPED_BY_SYMBOL_EXPOSURE,
    size_portfolio_entry,
)
from vibe.common.indicators.daily_atr import add_daily_atr, daily_atr_column
from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.simulator import ExecutionSimulator
from vibe.backtester.core.execution.pending_queue import PendingOrderQueue
from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.slippage import FixedTickSlippage
from vibe.backtester.core.execution.volume import UnlimitedVolume
from vibe.backtester.core.execution.impact import NoImpact
from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.backtester.runner import RuleSetRunner
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.bar import Bar
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.common.ruleset.support import (
    FIRST_CANDLE_DIRECTION,
    UnsupportedRulesetSettingsError,
    require_resting_stop_compatible,
)


def _next_session_bar(
    df: pd.DataFrame, idx: int, session_date: date
) -> Optional[Bar]:
    """The next bar, shaped so that filling against it fills at its open.

    E6 ``AT_NEXT_BAR_OPEN`` fills here. ``close`` is deliberately set to the
    next bar's *open*: every fill path prices a market order off ``close``, so
    this makes both the legacy simulator and the ExecutionConfig simulator fill
    at the open without either needing to know about the policy. High, low, and
    volume stay real so slippage, volume caps, and clamping still see the true
    bar.

    Returns None when there is no next bar, or when the next row belongs to
    another session. That guard matters: the row after the last bar of a day is
    the next morning's open, and filling there would hand the entry an
    overnight gap it could never have traded -- reintroducing exactly the
    free-option bug this policy exists to remove.
    """
    if idx + 1 >= len(df):
        return None
    next_ts = df.index[idx + 1]
    if next_ts.date() != session_date:
        return None
    next_row = df.iloc[idx + 1]
    next_open = float(next_row["open"])
    return Bar(
        timestamp=next_ts.to_pydatetime(),
        open=next_open,
        high=float(next_row["high"]),
        low=float(next_row["low"]),
        close=next_open,
        volume=float(next_row["volume"]),
    )


def _r_multiple_target(
    *, side: str, fill_price: float, stop_price: float, multiple: float
) -> Optional[float]:
    """Take-profit ``multiple`` initial risks beyond the entry fill.

    None when the fill is at or beyond its stop: there is no positive risk to
    scale, and the min-risk floor (when enabled) skips such entries anyway.
    """
    risk = fill_price - stop_price if side == "buy" else stop_price - fill_price
    if risk <= 0:
        return None
    return fill_price + multiple * risk if side == "buy" else fill_price - multiple * risk


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


def _add_take_profit_features(df: pd.DataFrame, ruleset: StrategyRuleSet) -> pd.DataFrame:
    """Add the daily ATR column an ``atr_multiple`` take-profit reads at signal time."""
    take_profit = ruleset.exit.take_profit
    if getattr(take_profit, "method", None) != "atr_multiple":
        return df
    period = int(take_profit.period)
    if daily_atr_column(period) in df.columns:
        return df
    return add_daily_atr(df, period)


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
        data_dir: Path | str | None = None,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 2,
        execution_config: Optional[ExecutionConfig] = None,
        execution_realism: Optional[ExecutionRealismConfig] = None,
    ) -> None:
        self.ruleset = ruleset
        # None defers to vibe.backtester.data.paths, which honours
        # BACKTEST__DATA_DIR and falls back to the main worktree's shared copy.
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
        self.execution_config = execution_config
        # None means legacy semantics, so existing runs stay bit-identical.
        # Pass ExecutionRealismConfig.realistic() to opt into honest exits.
        self.execution_realism = execution_realism or ExecutionRealismConfig.legacy()
        self.pending_orders: list[Order] = []
        # Counts of orders reduced by each cap, reported in execution_diagnostics.
        self.sizing_caps: dict[str, int] = {}
        # Entry orders reduced by buying power at sizing and/or fill, by id.
        self.buying_power_capped_orders: set[str] = set()
        self.entries_skipped_min_risk = 0
        self.entries_skipped_ambiguous_trigger = 0
        self.entries_skipped_no_1m_data = 0
        self._last_sizing_cap: str | None = None
        if self.execution_realism.entry_fill_policy is EntryFillPolicy.RESTING_STOP:
            # A resting stop cannot see the completed breakout bar, so any
            # filter that reads it would be lookahead. Refuse up front.
            require_resting_stop_compatible(ruleset)
            if execution_config is not None and execution_config.latency_bars != 0:
                raise ValueError(
                    "EntryFillPolicy.RESTING_STOP fills on the triggering minute; "
                    f"execution_config.latency_bars={execution_config.latency_bars} "
                    "would defer it to a later bar"
                )
        config = ruleset.position_size
        entry_order = getattr(ruleset.strategy, "entry_order", None)
        if (
            entry_order is not None
            and entry_order.type == "resting_stop"
            and self.execution_realism.entry_fill_policy
            is not EntryFillPolicy.RESTING_STOP
        ):
            raise UnsupportedRulesetSettingsError(
                f"Ruleset {ruleset.name!r} declares entry_order.type='resting_stop'; "
                "backtest it with EntryFillPolicy.RESTING_STOP "
                "(ExecutionRealismConfig.realistic_resting_stop())"
            )
        if (
            ruleset.strategy.entry_mode == FIRST_CANDLE_DIRECTION
            and self.execution_realism.entry_fill_policy
            is EntryFillPolicy.AT_STOP_TRIGGER
        ):
            # AT_STOP_TRIGGER prices the fill at the ORB breakout level, which
            # for a first-candle entry is the stop side or a level never traded.
            raise UnsupportedRulesetSettingsError(
                f"Ruleset {ruleset.name!r}: entry_mode='first_candle_direction' is a "
                "market entry after the opening candle closes; "
                "EntryFillPolicy.AT_STOP_TRIGGER would price it at a breakout level. "
                "Use AT_NEXT_BAR_OPEN (or AT_SIGNAL_BAR_CLOSE)"
            )
        self._position_sizer = PositionSizer(
            risk_pct=config.value,
            max_position_size=getattr(config, "max_shares", None),
            max_position_pct=getattr(config, "max_position_pct", None),
        )
        # Account-level rules from the ruleset's ``portfolio:`` block. None
        # keeps every historical code path, and therefore every result, intact.
        self.portfolio_rules = getattr(ruleset, "portfolio", None)
        self.daily_loss_halts = 0

    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        precomputed_features: Optional[pd.DataFrame] = None,
        *,
        graded_start: Optional[date] = None,
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
            graded_start: First **graded** session. Sessions before it are warmup:
                their bars prime indicators but generate no orders, and they are
                excluded from the equity curve and therefore from every metric
                denominator. ``None`` grades the whole range.

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
        df = _add_take_profit_features(df, self.ruleset)
        
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

        use_realistic_execution = self.execution_config is not None

        def _use_orb_price_override() -> bool:
            # Preserve legacy instant-fill behavior by default.
            if not use_realistic_execution:
                return True
            # Explicitly allow legacy-like configs to preserve old behavior.
            return (
                isinstance(execution_config.slippage_model, FixedTickSlippage)
                and isinstance(execution_config.volume_model, UnlimitedVolume)
                and isinstance(execution_config.impact_model, NoImpact)
            )
        
        # 4. Init components
        clock = SimulatedClock()
        
        # Use ExecutionSimulator only when user explicitly opts in with execution_config.
        if use_realistic_execution:
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
            execution_realism=self.execution_realism,
            eod_cutoff=parse_hhmm(self.ruleset.exit.eod_time),
            eod_enabled=self.ruleset.exit.eod,
        )
        # The signal gate and the fill price are two halves of one execution
        # model. Rejecting a retraced wick while still filling at the ORB level
        # books the breakout price without the exposure that earned it, so the
        # two are wired from the same setting.
        entry_at_signal_close = (
            self.execution_realism.entry_fill_policy
            is EntryFillPolicy.AT_SIGNAL_BAR_CLOSE
        )
        runner = RuleSetRunner(
            self.ruleset, reject_retraced_wick=entry_at_signal_close
        )
        
        # Reset pending orders for new backtest
        pending_queue = PendingOrderQueue()
        pending_order_meta: dict[str, dict[str, float | None]] = {}
        self.pending_orders = []
        self.sizing_caps = {}
        self.buying_power_capped_orders = set()
        self.entries_skipped_min_risk = 0
        self.entries_skipped_ambiguous_trigger = 0
        self.entries_skipped_no_1m_data = 0
        self.resting_stop_triggers = 0
        self.resting_stop_gap_fills = 0
        self.resting_stop_entry_bar_exits = 0

        # RESTING_STOP simulates the entry order on the source 1-minute bars.
        resting_stop = (
            self.execution_realism.entry_fill_policy is EntryFillPolicy.RESTING_STOP
        )
        minute_sessions = split_sessions(df_1m) if resting_stop else {}
        bar_span = pd.Timedelta(pd_interval)
        resting_done: set[date] = set()

        # 5. Event loop with bar_index counter
        prev_date = None
        bar_index = 0  # Track bar index for latency support
        warmup_sessions_seen: set[date] = set()
        # ``portfolio:`` block only: equity at each session's first bar (E0)
        # and sessions halted by the daily loss limit.
        session_start_equity = self.initial_capital
        halted_sessions: set[date] = set()
        self.daily_loss_halts = 0

        # The last bar of each session, so an open position is never carried
        # overnight. This is a correctness backstop, not a strategy rule: the
        # EOD exit above is configurable and may legitimately be disabled or
        # set to the closing bell, and an intraday position that outlives its
        # session would block every later entry and leave its P&L unrealized
        # -- producing a run with zero trades and a moving equity curve.
        session_last_ts = {
            session: group_index[-1]
            for session, group_index in df.groupby(df.index.date).groups.items()
        }

        for i, (ts, row) in enumerate(df.iterrows()):
            clock.set_time(ts.to_pydatetime())
            current_date = ts.date()

            # Warmup bars prime indicators and nothing else. Suppressing orders
            # is not enough on its own: a warmup trade would move cash, and
            # position sizing is a function of cash, so it would silently
            # change every graded trade that followed. The graded window has to
            # begin from the same capital regardless of how much warmup was
            # supplied, or folds are not comparable -- which is the entire
            # point of the increment.
            is_warmup = graded_start is not None and current_date < graded_start
            if is_warmup:
                warmup_sessions_seen.add(current_date)
            if current_date != prev_date:
                # Reset bar index at start of new day
                bar_index = 0
                runner.reset_daily_state(symbol)
                # Expire any unfilled prior-day orders at EOD boundary.
                pending_queue = PendingOrderQueue()
                pending_order_meta.clear()
                self.pending_orders = []
                prev_date = current_date
                # Positions never survive a session, so equity at the first
                # bar is the account's cash: the live warm-up snapshot.
                session_start_equity = portfolio.equity_basis()
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

            if (
                self.portfolio_rules is not None
                and current_date not in halted_sessions
                and self._daily_loss_limit_breached(
                    portfolio, current_bars, session_start_equity
                )
            ):
                halted_sessions.add(current_date)
                self.daily_loss_halts += 1
                for halted_sym in list(portfolio.positions):
                    portfolio._close_at_bar_close(
                        halted_sym, current_bars[halted_sym], clock,
                        reason="DAILY_LOSS_LIMIT",
                    )
                    runner.close_position(halted_sym)
                pending_queue = PendingOrderQueue()
                pending_order_meta.clear()
                self.pending_orders = []

            # Generate entry signal only if no open position and no pending order.
            has_pending_symbol_order = any(o.symbol == symbol for o in self.pending_orders)
            resting_entry = None
            resting_post_fill: tuple[SessionMinutes, int] | None = None
            if (
                not is_warmup
                and symbol not in portfolio.positions
                and not has_pending_symbol_order
                and current_date not in halted_sessions
            ):
                if resting_stop:
                    # The strategy's completed-bar signal is not consulted: a
                    # resting order fills on the triggering minute, before
                    # that bar has completed.
                    resting_entry = self._resting_stop_entry(
                        symbol=symbol, ts=ts, i=i, df=df,
                        sessions=minute_sessions, done=resting_done,
                        runner=runner, bar_span=bar_span,
                    )
                    signal_value, metadata = 0, {}
                else:
                    current_bar_dict = row.to_dict()
                    current_bar_dict["timestamp"] = ts.to_pydatetime()

                    signal_value, metadata = runner.generate_signal(
                        symbol, current_bar_dict, df.loc[:ts]
                    )

                if resting_entry is not None:
                    side, stop_price, rs_take_profit, rs_minutes, rs_trigger = (
                        resting_entry
                    )
                    signal_value = 1 if side == "buy" else -1
                    metadata = {"take_profit": rs_take_profit}
                    _TICK = 0.01
                    slippage = self.slippage_ticks * _TICK
                    pin_to_trigger = False
                    stop_trigger = None
                    # close = the trigger fill price, so the fill paths price
                    # the order there (plus slippage) on the real minute bar.
                    entry_bar = rs_minutes.bar(
                        rs_trigger.index, close=rs_trigger.base_price
                    )
                elif signal_value in (1, -1):
                    side = "buy" if signal_value == 1 else "sell"
                    stop_price = metadata.get("stop_loss", bar.close * 0.99)

                    # E6: what price this entry can actually be had at.
                    # AT_STOP_TRIGGER models a resting stop-market order at
                    # OR_high+$0.01 / OR_low-$0.01. AT_SIGNAL_BAR_CLOSE models
                    # an instant reaction to the completed bar. AT_NEXT_BAR_OPEN
                    # models what live really does: evaluate the completed bar,
                    # submit a market order, fill at the next obtainable price.
                    _TICK = 0.01
                    slippage = self.slippage_ticks * _TICK
                    orb_high = metadata.get("orb_high")
                    orb_low  = metadata.get("orb_low")

                    policy = self.execution_realism.entry_fill_policy

                    stop_trigger: float | None = None
                    if signal_value == 1 and orb_high is not None:
                        stop_trigger = orb_high + _TICK + slippage
                    elif signal_value == -1 and orb_low is not None:
                        stop_trigger = orb_low - _TICK - slippage

                    pin_to_trigger = (
                        policy is EntryFillPolicy.AT_STOP_TRIGGER
                        and stop_trigger is not None
                    )

                    entry_bar = bar
                    if policy is EntryFillPolicy.AT_NEXT_BAR_OPEN:
                        next_bar = _next_session_bar(df, i, current_date)
                        if next_bar is None:
                            # No bar left in this session to fill against, so
                            # this signal simply does not become a trade.
                            signal_value = 0
                        else:
                            entry_bar = next_bar

                if signal_value in (1, -1):
                    if pin_to_trigger:
                        entry_price = stop_trigger
                    else:
                        # Sized on the price the fill simulator will produce,
                        # so the sizing decision and the fill agree.
                        # ``entry_bar.close`` is this bar's close under
                        # AT_SIGNAL_BAR_CLOSE and the next bar's open under
                        # AT_NEXT_BAR_OPEN.
                        entry_price = (
                            entry_bar.close + slippage
                            if side == "buy"
                            else entry_bar.close - slippage
                        )

                    if self.portfolio_rules is not None:
                        quantity = self._portfolio_position_size(
                            portfolio=portfolio,
                            entry_price=entry_price,
                            stop_price=stop_price,
                            session_start_equity=session_start_equity,
                            current_equity=self._mark_to_market_equity(
                                portfolio, current_bars
                            ),
                        )
                    else:
                        quantity = self._position_size(
                            capital=portfolio.cash,
                            entry_price=entry_price,
                            stop_price=stop_price,
                            buying_power=portfolio.available_buying_power(entry_price),
                        )
                    if quantity > 0:
                        # Create Order with signal_bar_index for latency tracking
                        order_price_override = (
                            entry_price
                            if pin_to_trigger and _use_orb_price_override()
                            else None
                        )
                        order = Order(
                            id=f"{symbol}_{ts.timestamp()}",
                            symbol=symbol,
                            side=side,
                            size=quantity,
                            order_type="market",
                            limit_price=None,
                            timestamp=ts.to_pydatetime(),
                            signal_bar_index=bar_index,
                            price_override=order_price_override,
                        )

                        pending_queue.add(order)
                        pending_order_meta[order.id] = {
                            "stop_loss": stop_price,
                            "take_profit": metadata.get("take_profit"),
                            "fill_bar": entry_bar,
                            "buying_power_capped": (
                                self._last_sizing_cap == "buying_power"
                            ),
                        }
                        if resting_entry is not None:
                            pending_order_meta[order.id]["entry_time"] = (
                                rs_minutes.index[rs_trigger.index]
                            )
                            resting_post_fill = (rs_minutes, rs_trigger.index)

            # Execute all orders eligible for this bar based on configured latency.
            eligible_orders = pending_queue.get_eligible_orders(
                current_bar_index=bar_index,
                latency_bars=execution_config.latency_bars,
            )

            for order in eligible_orders:
                fill_result = None

                # Under AT_NEXT_BAR_OPEN the order fills against the bar after
                # the signal, not the signal bar. Default to the current bar so
                # every other policy is unaffected.
                fill_bar = pending_order_meta.get(order.id, {}).get("fill_bar", bar)

                if execution_sim is not None:
                    # Align daily ADV lookup key with ADV series index dtype/timezone.
                    if adv_series.index.tz is not None:
                        adv_key = pd.Timestamp(ts).tz_convert(adv_series.index.tz).normalize()
                    else:
                        adv_key = pd.Timestamp(ts).tz_localize(None).normalize()

                    current_adv = adv_series.get(adv_key)
                    if pd.isna(current_adv):
                        current_adv = None

                    fill = execution_sim.execute_order(
                        order=order,
                        bar=fill_bar,
                        adv=current_adv,
                    )
                    if fill is not None:
                        fill_result = FillResult(
                            symbol=fill.symbol,
                            side=fill.side,
                            filled_qty=fill.qty,
                            avg_price=fill.price,
                            commission=0.0,
                        )
                else:
                    # Legacy default path: instant market fills with ORB entry override.
                    if order.order_type != "market":
                        continue
                    fill_result = fill_sim.execute(
                        order.symbol,
                        order.side,
                        order.size,
                        fill_bar,
                        price_override=order.price_override,
                    )

                if fill_result is None or fill_result.filled_qty <= 0:
                    continue

                order_meta = pending_order_meta.get(order.id, {})
                stop_price = float(order_meta.get("stop_loss", bar.close * 0.99))
                take_profit = order_meta.get("take_profit")

                if (
                    order.symbol not in portfolio.positions
                    and self.execution_realism.entry_risk_too_small(
                        side=order.side,
                        fill_price=fill_result.avg_price,
                        stop_price=stop_price,
                    )
                ):
                    self.entries_skipped_min_risk += 1
                    pending_queue.mark_filled(order.id)
                    pending_order_meta.pop(order.id, None)
                    continue

                fill_result, buying_power_fill_capped = (
                    self._clamp_fill_to_buying_power(fill_result, portfolio)
                )
                if fill_result.filled_qty <= 0:
                    pending_queue.mark_filled(order.id)
                    pending_order_meta.pop(order.id, None)
                    continue

                if buying_power_fill_capped or order_meta.get("buying_power_capped"):
                    # A set, because an order can be capped at sizing and
                    # again at fill, and a partial fill re-enters this loop
                    # under the same id. Each capped entry counts once.
                    self.buying_power_capped_orders.add(order.id)

                if order.symbol not in portfolio.positions:
                    entry_time = order_meta.get("entry_time", ts).to_pydatetime()
                    r_multiple = getattr(runner, "take_profit_r_multiple", None)
                    if r_multiple is not None:
                        take_profit = _r_multiple_target(
                            side=order.side,
                            fill_price=fill_result.avg_price,
                            stop_price=stop_price,
                            multiple=r_multiple,
                        )
                    portfolio.open_position(
                        fill_result,
                        stop_price=stop_price,
                        take_profit=take_profit,
                        timestamp=entry_time,
                    )
                    runner.track_position(
                        symbol=order.symbol,
                        side=order.side,
                        entry_price=fill_result.avg_price,
                        take_profit=take_profit,
                        stop_loss=stop_price,
                        timestamp=entry_time,
                    )
                else:
                    # Partial fills can accumulate into an existing position.
                    portfolio.add_to_position(fill_result, timestamp=ts.to_pydatetime())

                pending_queue.mark_filled(order.id)

                if (
                    fill_result.filled_qty < order.size
                    and not buying_power_fill_capped
                ):
                    remainder = order.remaining(fill_result.filled_qty)
                    pending_queue.add(remainder)
                else:
                    pending_order_meta.pop(order.id, None)

            # Keep exposed state synchronized for tests/diagnostics.
            self.pending_orders = [entry.order for entry in pending_queue._orders]

            # RESTING_STOP: the position exists from the fill minute, so its
            # exits are checked on the remaining minutes of this bar. The 5-min
            # check for this bar already ran (before entry) and the next one
            # starts at the next bar, so nothing before the fill is evaluated.
            if resting_post_fill is not None and symbol in portfolio.positions:
                rs_minutes, fill_idx = resting_post_fill
                _, bar_hi = rs_minutes.span(ts, ts + bar_span)
                for j in range(fill_idx + 1, bar_hi):
                    if symbol not in portfolio.positions:
                        break
                    clock.set_time(rs_minutes.index[j].to_pydatetime())
                    portfolio.check_exits({symbol: rs_minutes.bar(j)}, clock)
                    if symbol not in portfolio.positions:
                        runner.close_position(symbol)
                        self.resting_stop_entry_bar_exits += 1
                clock.set_time(ts.to_pydatetime())

            # Never carry an intraday position overnight. Runs before this
            # relied on the EOD exit alone, which was safe only while its
            # cutoff was hardcoded to 15:55; a ruleset setting eod_time to the
            # closing bell, or disabling exit.eod, left the position open.
            if ts == session_last_ts.get(current_date):
                open_before_close = set(portfolio.positions.keys())
                portfolio.close_open_positions_at_session_end(current_bars, clock)
                for closed_sym in open_before_close - set(portfolio.positions.keys()):
                    runner.close_position(closed_sym)

            # Warmup sessions are absent from the equity curve, which is what
            # keeps them out of every metric denominator: PerformanceAnalyzer
            # derives n_sessions by resampling this curve. Excluding them here
            # is therefore the single edit that scopes all session-based
            # metrics, rather than each metric needing its own exclusion.
            if not is_warmup:
                portfolio.update_equity(current_bars, ts.to_pydatetime())

        # 6. Analyze results
        reconciliation = reconcile_portfolio(portfolio)
        # The reported window is the *graded* one. A segment whose start_date
        # still pointed at the warmup load boundary would misreport its own
        # span, and any fold comparison built on it would be off by the warmup
        # length.
        reported_start = (
            datetime.combine(graded_start, datetime.min.time(), tzinfo=start_date.tzinfo)
            if graded_start is not None
            else start_date
        )
        result = PerformanceAnalyzer.analyze(
            trades=portfolio.trade_history,
            equity_curve=portfolio.equity_curve,
            initial_capital=self.initial_capital,
            symbol=symbol,
            start_date=reported_start,
            end_date=end_date,
            ruleset_name=self.ruleset.name,
            ruleset_version=self.ruleset.version,
            execution_diagnostics={
                "ambiguous_exit_bars": float(portfolio.ambiguous_exit_bars),
                "gap_through_exits": float(portfolio.gap_through_exits),
                "exit_slippage_events": float(portfolio.exit_slippage_events),
                "exit_slippage_cost": float(portfolio.exit_slippage_cost),
                "min_cash": float(portfolio.min_cash),
                "max_gross_exposure_ratio": float(portfolio.max_gross_exposure_ratio),
                "max_funded_entry_leverage": float(
                    portfolio.max_funded_entry_leverage
                ),
                "total_costs": float(portfolio.total_costs),
                "execution_model_version": float(EXECUTION_MODEL_VERSION),
                "orders_capped_by_buying_power": float(
                    len(self.buying_power_capped_orders)
                ),
                "entries_skipped_min_risk": float(self.entries_skipped_min_risk),
                "orders_capped_by_declared_limits": float(
                    self.sizing_caps.get("max_position_size", 0)
                    + self.sizing_caps.get("max_position_pct", 0)
                ),
                # Reported rather than raised. A reconciliation failure means
                # the result is arithmetically wrong, but P6 owns the decision
                # to block a run -- the engine's job is to make the fact
                # visible on every run, including legacy ones.
                "accounting_failures": float(len(reconciliation.failures)),
                "accounting_checks_applicable": float(
                    sum(1 for f in reconciliation.findings if f.applicable)
                ),
                # Published so a reviewer can confirm from the result alone
                # that warmup was actually supplied and actually excluded,
                # rather than trusting the caller's intent.
                "warmup_sessions_excluded": float(len(warmup_sessions_seen)),
                **(self._resting_stop_diagnostics() if resting_stop else {}),
                **(
                    self._portfolio_rule_diagnostics()
                    if self.portfolio_rules is not None
                    else {}
                ),
            },
        )
        result.engine_integrity_report = build_engine_integrity_report(
            result,
            reconciliation,
            max_gross_leverage=self.execution_realism.max_gross_leverage,
            enforce_buying_power=self.execution_realism.enforce_buying_power,
            open_position_count=len(portfolio.positions),
            pending_order_count=len(self.pending_orders),
        )
        return result

    def _resting_stop_diagnostics(self) -> dict[str, float]:
        """Diagnostics published only under ``EntryFillPolicy.RESTING_STOP``.

        Kept out of every other policy's result so their diagnostics (and the
        golden snapshots that pin them) are unchanged.
        """
        body_pct = getattr(self.ruleset.strategy, "orb_body_pct_filter", None)
        return {
            "entries_skipped_ambiguous_trigger": float(
                self.entries_skipped_ambiguous_trigger
            ),
            "entries_skipped_no_1m_data": float(self.entries_skipped_no_1m_data),
            "resting_stop_triggers": float(self.resting_stop_triggers),
            "resting_stop_gap_fills": float(self.resting_stop_gap_fills),
            "resting_stop_entry_bar_exits": float(self.resting_stop_entry_bar_exits),
            # The ruleset's completed-bar body filter cannot apply to a
            # resting order; reported so the omission is visible.
            "orb_body_pct_filter_ignored": float(body_pct or 0.0),
        }

    def _portfolio_rule_diagnostics(self) -> dict[str, float]:
        """Diagnostics published only when the ruleset has a ``portfolio:`` block."""
        return {
            "orders_capped_by_symbol_exposure": float(
                self.sizing_caps.get(CAPPED_BY_SYMBOL_EXPOSURE, 0)
            ),
            "orders_capped_by_equity_headroom": float(
                self.sizing_caps.get(CAPPED_BY_EQUITY_HEADROOM, 0)
            ),
            "daily_loss_limit_halts": float(self.daily_loss_halts),
        }

    @staticmethod
    def _mark_to_market_equity(
        portfolio: PortfolioManager, current_bars: dict[str, Bar]
    ) -> float:
        """Cash plus open positions at the latest observed close (live NLV)."""
        equity = portfolio.cash
        for symbol, position in portfolio.positions.items():
            bar = current_bars.get(symbol)
            price = bar.close if bar is not None else position.entry_price
            signed = position.quantity if position.side == "buy" else -position.quantity
            equity += signed * price
        return equity

    def _daily_loss_limit_breached(
        self,
        portfolio: PortfolioManager,
        current_bars: dict[str, Bar],
        session_start_equity: float,
    ) -> bool:
        limit_pct = self.portfolio_rules.daily_loss_limit_pct
        if limit_pct is None or session_start_equity <= 0:
            return False
        pnl = self._mark_to_market_equity(portfolio, current_bars) - session_start_equity
        return pnl <= -limit_pct * session_start_equity

    def _portfolio_position_size(
        self,
        *,
        portfolio: PortfolioManager,
        entry_price: float,
        stop_price: float,
        session_start_equity: float,
        current_equity: float,
        reserved_pending_notional: float = 0.0,
    ) -> int:
        """Size with the shared rule live trading uses (``portfolio:`` block).

        There is no broker here, so the live-only AvailableFunds clamp is
        absent; the realism config's buying-power clamp still applies at fill.
        """
        rules = self.portfolio_rules
        self._last_sizing_cap = None
        if abs(entry_price - stop_price) <= 0:
            return 0
        sizing_equity = (
            session_start_equity
            if rules.sizing_equity_basis == "session_start"
            else current_equity
        )
        decision = size_portfolio_entry(
            entry_price=entry_price,
            stop_price=stop_price,
            sizing_equity=sizing_equity,
            risk_pct=self.ruleset.position_size.value,
            max_symbol_exposure_pct=rules.max_symbol_exposure_pct,
            max_gross_leverage=rules.max_gross_leverage,
            current_equity=current_equity,
            gross_open_notional=portfolio.committed_notional(),
            reserved_pending_notional=reserved_pending_notional,
            max_shares=self.ruleset.position_size.max_shares,
        )
        if decision.capped_by is not None:
            self._last_sizing_cap = decision.capped_by
            self.sizing_caps[decision.capped_by] = (
                self.sizing_caps.get(decision.capped_by, 0) + 1
            )
        return decision.quantity

    def _resting_stop_entry(
        self,
        *,
        symbol: str,
        ts: pd.Timestamp,
        i: int,
        df: pd.DataFrame,
        sessions: dict,
        done: set,
        runner: RuleSetRunner,
        bar_span: pd.Timedelta,
    ):
        """Simulate the session's resting OR stops over the minutes of bar ``ts``.

        Returns ``(side, stop, take_profit, minutes, trigger)`` when a resting
        order fills inside ``[ts, ts + bar_span)``, else None. Uses only what
        a resting order knows when it fills: the opening range, the clock, the
        open-known daily ATR (for the target), and the triggering minute.
        Once a session triggers (filled, skipped or ambiguous) it is done:
        one entry attempt per day.
        """
        session = ts.date()
        if session in done:
            return None

        strategy_cfg = self.ruleset.strategy
        minutes = sessions.get(session)
        if minutes is None or not minutes.minute_resolution:
            # Never fall back to the coarse bars: a stop's fill minute and
            # gap-open price cannot be recovered from them.
            done.add(session)
            self.entries_skipped_no_1m_data += 1
            return None

        tz = ts.tz
        or_start = session_time(session, strategy_cfg.orb_start_time, tz)
        or_end = or_start + pd.Timedelta(minutes=strategy_cfg.orb_duration_minutes)
        cutoff = session_time(session, strategy_cfg.entry_cutoff_time, tz)
        if ts >= cutoff:
            done.add(session)
            return None
        window_start = max(ts, or_end)
        window_end = min(ts + bar_span, cutoff)
        if window_end <= window_start:
            return None

        opening_range = minutes.opening_range(or_start, or_end)
        if opening_range is None:
            done.add(session)
            return None
        or_high, or_low = opening_range

        direction = getattr(strategy_cfg, "direction_mode", "symmetric")
        long_level = or_high if direction != "short_only" else None
        short_level = or_low if direction != "long_only" else None

        lo, hi = minutes.span(window_start, window_end)
        trigger = minutes.first_trigger(
            lo, hi,
            long_level=long_level, short_level=short_level,
            long_stop=or_low, short_stop=or_high,
        )
        if trigger is None:
            return None
        done.add(session)
        self.resting_stop_triggers += 1
        if trigger.ambiguous:
            self.entries_skipped_ambiguous_trigger += 1
            return None

        side = trigger.side
        level = long_level if side == "buy" else short_level
        if trigger.base_price != level:
            self.resting_stop_gap_fills += 1
        stop = or_low if side == "buy" else or_high
        take_profit = runner.resting_stop_take_profit(
            orb_high=or_high,
            orb_low=or_low,
            side=side,
            df_context=df.iloc[: i + 1],
            symbol=symbol,
            trading_date=session,
        )
        return side, stop, take_profit, minutes, trigger

    def _clamp_fill_to_buying_power(
        self,
        fill: FillResult,
        portfolio: PortfolioManager,
    ) -> tuple[FillResult, bool]:
        """Reconcile funded size against the actual simulated fill price.

        Position sizing runs before dynamic slippage and impact are known. A
        size affordable at the next bar's open can therefore become a few
        dollars too large at the final execution price. The portfolio assertion
        remains the backstop; this final gate performs the expected clamp and
        cancels the unfunded remainder instead of retrying it on later bars.
        """
        available = portfolio.available_buying_power(fill.avg_price)
        if available is None:
            return fill, False

        max_qty = max(0, int(available / fill.avg_price))
        if fill.filled_qty <= max_qty:
            return fill, False

        self.sizing_caps["buying_power_fill"] = (
            self.sizing_caps.get("buying_power_fill", 0) + 1
        )
        return FillResult(
            symbol=fill.symbol,
            side=fill.side,
            filled_qty=float(max_qty),
            avg_price=fill.avg_price,
            commission=fill.commission,
        ), True

    def _position_size(
        self,
        capital: float,
        entry_price: float,
        stop_price: float,
        buying_power: float | None = None,
    ) -> int:
        """Size a position with the same sizer live trading uses.

        Routing through ``PositionSizer`` keeps declared ruleset caps
        (``max_shares``, ``max_position_pct``) honoured identically in
        simulation and live, which the previous inline formula ignored
        outright.

        ``buying_power`` is None under legacy semantics, where sizing stays
        unbounded and bit-identical to prior results. The legacy path also
        keeps its one-share floor and its tolerance of non-positive capital,
        both of which are wrong but load-bearing for historical comparisons.
        """
        risk_pct = self.ruleset.position_size.value
        self._last_sizing_cap = None
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0

        if buying_power is None:
            risk_dollars = capital * risk_pct
            size = max(1, int(risk_dollars / stop_distance))
            return self._apply_declared_caps(size, capital, entry_price)

        if capital <= 0:
            # Funded sizing cannot express "trade anyway on negative equity".
            return 0

        result = self._position_sizer.calculate(
            entry_price=entry_price,
            stop_price=stop_price,
            account_value=capital,
            buying_power=buying_power,
        )
        if result.was_capped:
            self._last_sizing_cap = result.capped_by
            self.sizing_caps[result.capped_by] = (
                self.sizing_caps.get(result.capped_by, 0) + 1
            )
        return int(result.size)

    def _apply_declared_caps(
        self, size: int, capital: float, entry_price: float
    ) -> int:
        """Honour max_shares / max_position_pct even under legacy semantics.

        These are explicit ruleset declarations, so ignoring them was a defect
        rather than a legacy behaviour worth preserving. No ruleset in the repo
        currently sets either, so this changes no existing result.
        """
        config = self.ruleset.position_size
        max_shares = getattr(config, "max_shares", None)
        max_position_pct = getattr(config, "max_position_pct", None)

        if max_shares is not None and size > max_shares:
            size = int(max_shares)
            self.sizing_caps["max_position_size"] = (
                self.sizing_caps.get("max_position_size", 0) + 1
            )
        if max_position_pct is not None and entry_price > 0 and capital > 0:
            max_size = int((capital * max_position_pct) / entry_price)
            if size > max_size:
                size = max_size
                self.sizing_caps["max_position_pct"] = (
                    self.sizing_caps.get("max_position_pct", 0) + 1
                )
        return max(0, size)
