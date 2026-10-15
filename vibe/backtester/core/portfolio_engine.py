"""Shared-capital, multi-symbol portfolio simulation.

The portfolio layer is strategy-agnostic: it consumes the same runner contract
as the single-symbol engine and treats strategy metadata as opaque execution
instructions. Capital contention is resolved only from declared symbol
priority and signal timestamps. Prices, returns, and later bars are not
available to the ranking policy.
"""

from __future__ import annotations

import asyncio
import heapq
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

from vibe.backtester.analysis.metrics import (
    METRIC_CALCULATION_VERSION,
    PortfolioBacktestResult,
)
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.clock import SimulatedClock
from vibe.backtester.core.engine import BacktestEngine, _add_atr, _resample
from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.impact import NoImpact
from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.simulator import ExecutionSimulator
from vibe.backtester.core.execution.slippage import FixedTickSlippage
from vibe.backtester.core.execution.volume import UnlimitedVolume
from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    EntryFillPolicy,
    ExecutionRealismConfig,
)
from vibe.backtester.core.fill_simulator import FillResult, FillSimulator
from vibe.backtester.core.portfolio import PortfolioManager, parse_hhmm
from vibe.backtester.core.reconciliation import reconcile_portfolio
from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.backtester.runner import RuleSetRunner
from vibe.common.models.bar import Bar
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.research_pipeline.hashing import hash_object

_ET = ZoneInfo("America/New_York")
PORTFOLIO_SIMULATION_VERSION = 1


class SignalRunner(Protocol):
    """Minimal strategy adapter consumed by the portfolio engine."""

    def generate_signal(
        self,
        symbol: str,
        current_bar: dict[str, Any],
        df_context: pd.DataFrame,
    ) -> tuple[int, dict[str, Any]]: ...

    def entry_stop_trigger(
        self,
        signal: int,
        metadata: dict[str, Any],
    ) -> Optional[float]: ...

    def track_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        take_profit: Optional[float],
        stop_loss: float,
        timestamp: datetime,
    ) -> None: ...

    def close_position(self, symbol: str) -> None: ...

    def reset_daily_state(self, symbol: str) -> None: ...


class IncompleteSessionPolicy(str, Enum):
    """How an incomplete exchange session is handled."""

    FAIL = "fail"
    LIQUIDATE_LAST_OBSERVED = "liquidate_last_observed"


@dataclass(frozen=True)
class PortfolioSimulationConfig:
    """Declared capital-contention and concentration policy."""

    max_symbol_exposure_pct: float
    max_open_positions: Optional[int] = None
    symbol_priority: tuple[str, ...] = ()
    incomplete_session_policy: IncompleteSessionPolicy = (
        IncompleteSessionPolicy.FAIL
    )

    def __post_init__(self) -> None:
        if not 0 < self.max_symbol_exposure_pct <= 1:
            raise ValueError("max_symbol_exposure_pct must be in (0, 1]")
        if self.max_open_positions is not None and self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive when provided")
        if len(set(self.symbol_priority)) != len(self.symbol_priority):
            raise ValueError("symbol_priority cannot contain duplicates")
        if not isinstance(
            self.incomplete_session_policy, IncompleteSessionPolicy
        ):
            raise TypeError(
                "incomplete_session_policy must be an "
                "IncompleteSessionPolicy"
            )

    def identity(self) -> dict[str, object]:
        return {
            "portfolio_simulation_version": PORTFOLIO_SIMULATION_VERSION,
            "ranking_rule": "declared_symbol_priority_then_lexical",
            "max_symbol_exposure_pct": self.max_symbol_exposure_pct,
            "max_open_positions": self.max_open_positions,
            "symbol_priority": list(self.symbol_priority),
            "incomplete_session_policy": self.incomplete_session_policy.value,
        }


@dataclass(frozen=True)
class RankedSignal:
    """The complete input visible to the contention ranking rule."""

    symbol: str
    signal_time: datetime
    side: str


class CausalSignalRanker:
    """Deterministic ranking with no market-data or outcome input surface."""

    def __init__(self, declared_priority: tuple[str, ...] = ()) -> None:
        self._priority = {
            symbol: index for index, symbol in enumerate(declared_priority)
        }

    def rank(self, signals: list[RankedSignal]) -> list[RankedSignal]:
        fallback = len(self._priority)
        return sorted(
            signals,
            key=lambda signal: (
                self._priority.get(signal.symbol, fallback),
                signal.symbol,
                signal.signal_time,
                signal.side,
            ),
        )


@dataclass
class _PendingSignal:
    ranked: RankedSignal
    stop_price: float
    take_profit: Optional[float]
    eligible_bar_index: int
    fill_at_open: bool
    price_override: Optional[float] = None
    remaining_quantity: Optional[float] = None


def merge_symbol_events(
    frames: Mapping[str, pd.DataFrame],
) -> Iterator[tuple[pd.Timestamp, tuple[str, ...]]]:
    """Yield a stable k-way merge without materializing the union timeline."""
    heap: list[tuple[pd.Timestamp, str, Iterator[pd.Timestamp]]] = []
    for symbol in sorted(frames):
        frame = frames[symbol]
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise TypeError(f"{symbol} frame must use a DatetimeIndex")
        if frame.index.tz is None:
            raise ValueError(f"{symbol} frame index must be timezone-aware")
        if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
            raise ValueError(f"{symbol} frame index must be sorted and unique")
        iterator = iter(frame.index)
        first = next(iterator, None)
        if first is not None:
            heapq.heappush(heap, (first, symbol, iterator))

    while heap:
        timestamp = heap[0][0]
        symbols: list[str] = []
        while heap and heap[0][0] == timestamp:
            _, symbol, iterator = heapq.heappop(heap)
            symbols.append(symbol)
            following = next(iterator, None)
            if following is not None:
                heapq.heappush(heap, (following, symbol, iterator))
        yield timestamp, tuple(symbols)


def _compute_causal_adv(df: pd.DataFrame, window: int) -> pd.Series:
    """Trailing ADV from completed prior sessions only."""
    session_index = pd.DatetimeIndex(
        [
            datetime.combine(
                _exchange_date(timestamp), datetime.min.time(), tzinfo=_ET
            )
            for timestamp in df.index
        ]
    )
    daily_volumes = df["volume"].groupby(session_index).sum()
    return daily_volumes.shift(1).rolling(window=window).mean()


def _exchange_date(timestamp: pd.Timestamp | datetime) -> date:
    value = (
        timestamp.to_pydatetime()
        if isinstance(timestamp, pd.Timestamp)
        else timestamp
    )
    if value.tzinfo is None:
        raise ValueError("portfolio timestamps must be timezone-aware")
    return value.astimezone(_ET).date()


class PortfolioBacktestEngine(BacktestEngine):
    """Run one strategy over a merged timeline and one finite capital pool."""

    def __init__(
        self,
        ruleset: StrategyRuleSet,
        portfolio_config: PortfolioSimulationConfig,
        data_dir: Path | str | None = None,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 2,
        execution_config: Optional[ExecutionConfig] = None,
        execution_realism: Optional[ExecutionRealismConfig] = None,
        runner_factory: Optional[
            Callable[[StrategyRuleSet, bool], SignalRunner]
        ] = None,
    ) -> None:
        super().__init__(
            ruleset=ruleset,
            data_dir=data_dir,
            initial_capital=initial_capital,
            slippage_ticks=slippage_ticks,
            execution_config=execution_config,
            execution_realism=execution_realism,
        )
        if not self.execution_realism.enforce_buying_power:
            raise ValueError(
                "Portfolio simulation requires buying-power enforcement; pass "
                "ExecutionRealismConfig.realistic()"
            )
        self.portfolio_config = portfolio_config
        self._runner_factory = runner_factory
        self.ranker = CausalSignalRanker(portfolio_config.symbol_priority)

    def run(
        self,
        symbols: list[str] | tuple[str, ...],
        start_date: datetime,
        end_date: datetime,
        *,
        graded_start: Optional[date] = None,
    ) -> PortfolioBacktestResult:
        ordered_symbols = self._validate_symbols(symbols)
        frames: dict[str, pd.DataFrame] = {}
        for symbol in ordered_symbols:
            loader = ParquetLoader(self.data_dir, [symbol])
            raw = asyncio.run(
                loader.get_bars(
                    symbol, start_time=start_date, end_time=end_date
                )
            )
            interval = self.ruleset.instruments.timeframe.replace("m", "min")
            frames[symbol] = _add_atr(_resample(raw, interval))
        return self.run_frames(
            frames,
            start_date=start_date,
            end_date=end_date,
            graded_start=graded_start,
        )

    def run_frames(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        start_date: datetime,
        end_date: datetime,
        graded_start: Optional[date] = None,
    ) -> PortfolioBacktestResult:
        """Run already-resampled symbol frames through the full portfolio path."""
        symbols = self._validate_symbols(tuple(frames))
        prepared = self._prepare_frames(frames, symbols)
        timeline = merge_symbol_events(prepared)
        if not any(not frame.empty for frame in prepared.values()):
            raise ValueError("portfolio simulation requires at least one bar")

        execution_config = self.execution_config or ExecutionConfig.legacy(
            slippage_ticks=self.slippage_ticks
        )
        execution_sim = (
            ExecutionSimulator(config=execution_config)
            if self.execution_config is not None
            else None
        )
        fill_sim = FillSimulator(slippage_ticks=self.slippage_ticks)
        use_price_override = (
            execution_sim is None
            or (
                isinstance(execution_config.slippage_model, FixedTickSlippage)
                and isinstance(execution_config.volume_model, UnlimitedVolume)
                and isinstance(execution_config.impact_model, NoImpact)
            )
        )

        trailing = (
            self.ruleset.exit.trailing_stop.model_dump()
            if self.ruleset.exit.trailing_stop is not None
            else None
        )
        portfolio = PortfolioManager(
            self.initial_capital,
            trailing_stop_config=trailing,
            execution_realism=self.execution_realism,
            eod_cutoff=parse_hhmm(self.ruleset.exit.eod_time),
            eod_enabled=self.ruleset.exit.eod,
        )
        runner = self._make_runner()
        clock = SimulatedClock()
        adv = {
            symbol: _compute_causal_adv(
                prepared[symbol], execution_config.adv_window
            )
            for symbol in symbols
        }
        bar_indices = {symbol: -1 for symbol in symbols}
        previous_sessions: dict[str, date] = {}
        pending: dict[str, _PendingSignal] = {}
        last_bars: dict[str, Bar] = {}
        warmup_sessions: set[date] = set()
        session_ends, incomplete_sessions = self._session_end_timestamps(
            prepared
        )
        diagnostics = {
            "signals_ranked": 0,
            "signals_rejected_max_positions": 0,
            "signals_rejected_no_buying_power": 0,
            "orders_capped_by_concentration": 0,
            "incomplete_symbol_sessions": incomplete_sessions,
        }
        self.sizing_caps = {}

        for timestamp, event_symbols in timeline:
            now = timestamp.to_pydatetime()
            clock.set_time(now)
            event_bars: dict[str, Bar] = {}

            for symbol in event_symbols:
                bar_indices[symbol] += 1
                session = _exchange_date(timestamp)
                if previous_sessions.get(symbol) != session:
                    runner.reset_daily_state(symbol)
                    pending.pop(symbol, None)
                    previous_sessions[symbol] = session
                row = prepared[symbol].loc[timestamp]
                bar = Bar(
                    timestamp=now,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                event_bars[symbol] = bar
                last_bars[symbol] = bar

            # Orders declared for this bar's open are allocated before any use
            # of this bar's high/low. An intrabar stop may free capital later,
            # but it cannot retroactively fund an earlier open.
            self._execute_ready(
                fill_at_open=True,
                pending=pending,
                timestamp=timestamp,
                event_bars=event_bars,
                bar_indices=bar_indices,
                adv=adv,
                portfolio=portfolio,
                runner=runner,
                execution_sim=execution_sim,
                fill_sim=fill_sim,
                use_price_override=use_price_override,
                diagnostics=diagnostics,
            )

            open_before = set(portfolio.positions)
            portfolio.check_exits(event_bars, clock)
            for symbol in open_before - set(portfolio.positions):
                runner.close_position(symbol)
                pending.pop(symbol, None)

            for symbol in event_symbols:
                session = _exchange_date(timestamp)
                is_warmup = graded_start is not None and session < graded_start
                if is_warmup:
                    warmup_sessions.add(session)
                    continue
                # There is no same-session bar on which a newly emitted signal
                # can be acted upon. Refusing it also prevents a close-policy
                # entry from being opened after this session's EOD sweep.
                if timestamp == session_ends[symbol][session]:
                    continue
                if symbol in portfolio.positions or symbol in pending:
                    continue
                row = prepared[symbol].loc[timestamp]
                current = row.to_dict()
                current["timestamp"] = now
                signal, metadata = runner.generate_signal(
                    symbol, current, prepared[symbol].loc[:timestamp]
                )
                if signal not in (1, -1):
                    continue
                side = "buy" if signal == 1 else "sell"
                policy = self.execution_realism.entry_fill_policy
                wait_bars = execution_config.latency_bars
                if policy is EntryFillPolicy.AT_NEXT_BAR_OPEN:
                    wait_bars = max(1, wait_bars)
                pending[symbol] = _PendingSignal(
                    ranked=RankedSignal(
                        symbol=symbol, signal_time=now, side=side
                    ),
                    stop_price=float(
                        metadata.get(
                            "stop_loss",
                            event_bars[symbol].close * 0.99,
                        )
                    ),
                    take_profit=metadata.get("take_profit"),
                    eligible_bar_index=bar_indices[symbol] + wait_bars,
                    fill_at_open=policy is EntryFillPolicy.AT_NEXT_BAR_OPEN,
                    price_override=self._entry_price_override(
                        runner, signal, metadata
                    ),
                )

            self._execute_ready(
                fill_at_open=False,
                pending=pending,
                timestamp=timestamp,
                event_bars=event_bars,
                bar_indices=bar_indices,
                adv=adv,
                portfolio=portfolio,
                runner=runner,
                execution_sim=execution_sim,
                fill_sim=fill_sim,
                use_price_override=use_price_override,
                diagnostics=diagnostics,
            )

            before_session_close = set(portfolio.positions)
            self._close_at_session_end(
                portfolio, event_bars, session_ends, timestamp, clock
            )
            for symbol in before_session_close - set(portfolio.positions):
                runner.close_position(symbol)
                pending.pop(symbol, None)

            # A signal waiting past its session is cancelled, never carried
            # across an overnight gap.
            for symbol, item in list(pending.items()):
                if (
                    symbol in event_bars
                    and timestamp == session_ends[symbol][_exchange_date(timestamp)]
                ):
                    pending.pop(symbol)

            if graded_start is None or _exchange_date(timestamp) >= graded_start:
                portfolio.update_equity(last_bars, now)

        reconciliation = reconcile_portfolio(portfolio)
        reported_start = (
            datetime.combine(
                graded_start, datetime.min.time(), tzinfo=start_date.tzinfo
            )
            if graded_start is not None
            else start_date
        )
        evidence = {
            **{key: float(value) for key, value in diagnostics.items()},
            "ambiguous_exit_bars": float(portfolio.ambiguous_exit_bars),
            "gap_through_exits": float(portfolio.gap_through_exits),
            "exit_slippage_events": float(portfolio.exit_slippage_events),
            "exit_slippage_cost": float(portfolio.exit_slippage_cost),
            "min_cash": float(portfolio.min_cash),
            "max_gross_exposure_ratio": float(
                portfolio.max_gross_exposure_ratio
            ),
            "max_funded_entry_leverage": float(
                portfolio.max_funded_entry_leverage
            ),
            "max_single_symbol_concentration": float(
                getattr(portfolio, "max_single_symbol_concentration", 0.0)
            ),
            "max_simultaneous_positions": float(
                getattr(portfolio, "max_simultaneous_positions", 0)
            ),
            "total_costs": float(portfolio.total_costs),
            "execution_model_version": float(EXECUTION_MODEL_VERSION),
            "accounting_failures": float(len(reconciliation.failures)),
            "accounting_checks_applicable": float(
                sum(
                    1
                    for finding in reconciliation.findings
                    if finding.applicable
                )
            ),
            "warmup_sessions_excluded": float(len(warmup_sessions)),
        }
        aggregate = PerformanceAnalyzer.analyze(
            trades=portfolio.trade_history,
            equity_curve=portfolio.equity_curve,
            initial_capital=self.initial_capital,
            symbol="__PORTFOLIO__",
            start_date=reported_start,
            end_date=end_date,
            ruleset_name=self.ruleset.name,
            ruleset_version=self.ruleset.version,
            execution_diagnostics=evidence,
        )
        identity = {
            **self.portfolio_config.identity(),
            "symbols": list(symbols),
            "initial_capital": self.initial_capital,
            "execution": self.execution_realism.identity(),
            "execution_config": self._execution_config_identity(
                execution_config
            ),
            "ruleset_name": self.ruleset.name,
            "ruleset_version": self.ruleset.version,
            "ruleset_content_sha256": hash_object(
                self.ruleset.model_dump(mode="python")
            ),
            "metric_calculation_version": METRIC_CALCULATION_VERSION,
        }
        input_data_sha256 = self._input_data_sha256(prepared)
        validation_payload = {
            **identity,
            "input_data_sha256": input_data_sha256,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "graded_start": (
                graded_start.isoformat() if graded_start is not None else None
            ),
        }
        validation_identity = hash_object(validation_payload)
        return PortfolioBacktestResult(
            aggregate=aggregate,
            by_symbol={
                symbol: PerformanceAnalyzer._calc_convexity(
                    [
                        trade
                        for trade in portfolio.trade_history
                        if trade.symbol == symbol
                    ]
                )
                for symbol in symbols
            },
            symbols=symbols,
            portfolio_identity=identity,
            validation_identity=validation_identity,
        )

    def _execute_ready(
        self,
        *,
        fill_at_open: bool,
        pending: dict[str, _PendingSignal],
        timestamp: pd.Timestamp,
        event_bars: Mapping[str, Bar],
        bar_indices: Mapping[str, int],
        adv: Mapping[str, pd.Series],
        portfolio: PortfolioManager,
        runner: SignalRunner,
        execution_sim: Optional[ExecutionSimulator],
        fill_sim: FillSimulator,
        use_price_override: bool,
        diagnostics: dict[str, int],
    ) -> None:
        ready = [
            item.ranked
            for symbol, item in pending.items()
            if item.fill_at_open is fill_at_open
            and symbol in event_bars
            and bar_indices[symbol] >= item.eligible_bar_index
            and _exchange_date(item.ranked.signal_time)
            == _exchange_date(timestamp)
        ]
        diagnostics["signals_ranked"] += len(ready)
        for ranked in self.ranker.rank(ready):
            item = pending.get(ranked.symbol)
            if item is None:
                continue
            completed = self._execute_pending(
                item=item,
                timestamp=timestamp,
                bar=event_bars[ranked.symbol],
                bar_index=bar_indices[ranked.symbol],
                adv=adv[ranked.symbol],
                portfolio=portfolio,
                runner=runner,
                execution_sim=execution_sim,
                fill_sim=fill_sim,
                use_price_override=use_price_override,
                diagnostics=diagnostics,
            )
            if completed:
                pending.pop(ranked.symbol, None)

    def _execute_pending(
        self,
        *,
        item: _PendingSignal,
        timestamp: pd.Timestamp,
        bar: Bar,
        bar_index: int,
        adv: pd.Series,
        portfolio: PortfolioManager,
        runner: SignalRunner,
        execution_sim: Optional[ExecutionSimulator],
        fill_sim: FillSimulator,
        use_price_override: bool,
        diagnostics: dict[str, int],
    ) -> bool:
        symbol = item.ranked.symbol
        if (
            symbol not in portfolio.positions
            and
            self.portfolio_config.max_open_positions is not None
            and len(portfolio.positions)
            >= self.portfolio_config.max_open_positions
        ):
            diagnostics["signals_rejected_max_positions"] += 1
            return True

        fill_bar = bar
        if item.fill_at_open:
            fill_bar = Bar(
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.open,
                volume=bar.volume,
            )
        slippage = self.slippage_ticks * 0.01
        entry_price = (
            fill_bar.close + slippage
            if item.ranked.side == "buy"
            else fill_bar.close - slippage
        )
        if item.price_override is not None and use_price_override:
            entry_price = item.price_override
        buying_power = portfolio.available_buying_power(entry_price) or 0.0
        concentration_limit = (
            portfolio.equity_basis()
            * self.portfolio_config.max_symbol_exposure_pct
        )
        existing_notional = (
            abs(portfolio.positions[symbol].quantity)
            * portfolio.positions[symbol].entry_price
            if symbol in portfolio.positions
            else 0.0
        )
        concentration_headroom = max(
            0.0, concentration_limit - existing_notional
        )
        available = min(buying_power, concentration_headroom)
        if available <= 0:
            diagnostics["signals_rejected_no_buying_power"] += 1
            return True

        quantity = (
            int(item.remaining_quantity)
            if item.remaining_quantity is not None
            else self._position_size(
                capital=portfolio.equity_basis(),
                entry_price=entry_price,
                stop_price=item.stop_price,
                buying_power=available,
            )
        )
        if quantity <= 0:
            diagnostics["signals_rejected_no_buying_power"] += 1
            return True
        requested_notional = quantity * entry_price
        if concentration_headroom + 1e-6 < buying_power and (
            requested_notional >= concentration_headroom - entry_price
        ):
            diagnostics["orders_capped_by_concentration"] += 1

        order = Order(
            id=(
                f"portfolio_{symbol}_"
                f"{item.ranked.signal_time.timestamp()}_{bar_index}"
            ),
            symbol=symbol,
            side=item.ranked.side,
            size=quantity,
            order_type="market",
            limit_price=None,
            timestamp=item.ranked.signal_time,
            signal_bar_index=bar_index,
            price_override=(
                item.price_override if use_price_override else None
            ),
        )
        fill_result: Optional[FillResult] = None
        if execution_sim is not None:
            adv_key = pd.Timestamp(timestamp)
            if adv.index.tz is not None:
                adv_key = adv_key.tz_convert(adv.index.tz).normalize()
            else:
                adv_key = adv_key.tz_localize(None).normalize()
            current_adv = adv.get(adv_key)
            simulated = execution_sim.execute_order(
                order=order,
                bar=fill_bar,
                adv=None if pd.isna(current_adv) else current_adv,
            )
            if simulated is not None:
                fill_result = FillResult(
                    symbol=simulated.symbol,
                    side=simulated.side,
                    filled_qty=simulated.qty,
                    avg_price=simulated.price,
                )
        else:
            fill_result = fill_sim.execute(
                symbol,
                item.ranked.side,
                quantity,
                fill_bar,
                price_override=order.price_override,
            )
        if fill_result is None or fill_result.filled_qty <= 0:
            return False

        fill_result, buying_power_capped = self._clamp_fill_to_buying_power(
            fill_result, portfolio
        )
        max_concentration_qty = int(
            concentration_headroom / fill_result.avg_price
        )
        concentration_capped = fill_result.filled_qty > max_concentration_qty
        if concentration_capped:
            diagnostics["orders_capped_by_concentration"] += 1
            fill_result = FillResult(
                symbol=fill_result.symbol,
                side=fill_result.side,
                filled_qty=float(max_concentration_qty),
                avg_price=fill_result.avg_price,
            )
        if fill_result.filled_qty <= 0:
            return True

        if symbol in portfolio.positions:
            portfolio.add_to_position(
                fill_result, timestamp=timestamp.to_pydatetime()
            )
        else:
            portfolio.open_position(
                fill_result,
                stop_price=item.stop_price,
                take_profit=item.take_profit,
                timestamp=timestamp.to_pydatetime(),
            )
            runner.track_position(
                symbol=symbol,
                side=item.ranked.side,
                entry_price=fill_result.avg_price,
                take_profit=item.take_profit,
                stop_loss=item.stop_price,
                timestamp=timestamp.to_pydatetime(),
            )
        if (
            fill_result.filled_qty < quantity
            and not buying_power_capped
            and not concentration_capped
        ):
            item.remaining_quantity = quantity - fill_result.filled_qty
            item.eligible_bar_index = bar_index + 1
            return False
        return True

    def _entry_price_override(
        self,
        runner: SignalRunner,
        signal: int,
        metadata: dict[str, Any],
    ) -> Optional[float]:
        if (
            self.execution_realism.entry_fill_policy
            is not EntryFillPolicy.AT_STOP_TRIGGER
        ):
            return None
        trigger = runner.entry_stop_trigger(signal, metadata)
        if trigger is None:
            return None
        slippage = self.slippage_ticks * 0.01
        return trigger + slippage if signal == 1 else trigger - slippage

    def _make_runner(self) -> SignalRunner:
        reject_retraced = (
            self.execution_realism.entry_fill_policy
            is EntryFillPolicy.AT_SIGNAL_BAR_CLOSE
        )
        if self._runner_factory is not None:
            return self._runner_factory(self.ruleset, reject_retraced)
        return RuleSetRunner(
            self.ruleset, reject_retraced_wick=reject_retraced
        )

    def _prepare_frames(
        self,
        frames: Mapping[str, pd.DataFrame],
        symbols: tuple[str, ...],
    ) -> dict[str, pd.DataFrame]:
        required = {"open", "high", "low", "close", "volume"}
        prepared: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = frames[symbol].copy().sort_index()
            missing = required - set(frame.columns)
            if missing:
                raise ValueError(
                    f"{symbol} frame missing columns: {sorted(missing)}"
                )
            if "ATR_14" not in frame:
                frame = _add_atr(frame)
            frame["timestamp"] = frame.index
            prepared[symbol] = frame
        return prepared

    @staticmethod
    def _validate_symbols(
        symbols: list[str] | tuple[str, ...],
    ) -> tuple[str, ...]:
        ordered = tuple(sorted(symbols))
        if not ordered:
            raise ValueError("portfolio simulation requires at least one symbol")
        if len(set(ordered)) != len(ordered):
            raise ValueError("portfolio symbols must be unique")
        return ordered

    def _session_end_timestamps(
        self,
        frames: Mapping[str, pd.DataFrame],
    ) -> tuple[dict[str, dict[date, pd.Timestamp]], int]:
        all_timestamps = [
            timestamp
            for frame in frames.values()
            for timestamp in frame.index
        ]
        start = min(_exchange_date(timestamp) for timestamp in all_timestamps)
        end = max(_exchange_date(timestamp) for timestamp in all_timestamps)
        schedule = mcal.get_calendar("XNYS").schedule(
            start_date=start.isoformat(), end_date=end.isoformat()
        )
        interval = pd.Timedelta(
            self.ruleset.instruments.timeframe.replace("m", "min")
        )
        expected = {
            session.date(): (
                pd.Timestamp(row["market_open"]),
                pd.Timestamp(row["market_close"]) - interval,
            )
            for session, row in schedule.iterrows()
        }
        session_ends: dict[str, dict[date, pd.Timestamp]] = {}
        incomplete: list[str] = []

        for symbol, frame in frames.items():
            grouped = dict(_group_session_timestamps(frame))
            unexpected = sorted(set(grouped) - set(expected))
            if unexpected:
                raise ValueError(
                    f"{symbol} contains non-XNYS sessions: {unexpected}"
                )
            session_ends[symbol] = {}
            for session, (expected_open, expected_last_bar) in expected.items():
                timestamps = grouped.get(session)
                if not timestamps:
                    incomplete.append(f"{symbol}:{session}:missing_session")
                    continue
                observed_open = min(timestamps)
                observed_last = max(timestamps)
                expected_timestamps = list(
                    pd.date_range(
                        start=expected_open,
                        end=expected_last_bar,
                        freq=interval,
                    )
                )
                if timestamps != expected_timestamps:
                    incomplete.append(
                        f"{symbol}:{session}:expected_"
                        f"{expected_open.isoformat()}.."
                        f"{expected_last_bar.isoformat()}_observed_"
                        f"{observed_open.isoformat()}.."
                        f"{observed_last.isoformat()}"
                    )
                session_ends[symbol][session] = observed_last

        if (
            incomplete
            and self.portfolio_config.incomplete_session_policy
            is IncompleteSessionPolicy.FAIL
        ):
            preview = ", ".join(incomplete[:5])
            suffix = "" if len(incomplete) <= 5 else ", ..."
            raise ValueError(
                "Incomplete exchange-session data: "
                f"{preview}{suffix}. Set incomplete_session_policy="
                "'liquidate_last_observed' only when that fallback is "
                "deliberately acceptable."
            )
        return session_ends, len(incomplete)

    @staticmethod
    def _input_data_sha256(
        frames: Mapping[str, pd.DataFrame],
    ) -> str:
        digest = hashlib.sha256()
        for symbol in sorted(frames):
            digest.update(symbol.encode())
            columns = sorted(frames[symbol].columns)
            hashed = pd.util.hash_pandas_object(
                frames[symbol][columns], index=True
            )
            digest.update(hashed.to_numpy().tobytes())
        return digest.hexdigest()

    @staticmethod
    def _execution_config_identity(
        config: ExecutionConfig,
    ) -> dict[str, object]:
        def model_identity(model: object) -> dict[str, object]:
            return {
                "type": type(model).__name__,
                "parameters": {
                    key: value
                    for key, value in sorted(vars(model).items())
                },
            }

        return {
            "slippage": model_identity(config.slippage_model),
            "volume": model_identity(config.volume_model),
            "impact": model_identity(config.impact_model),
            "latency_bars": config.latency_bars,
            "adv_window": config.adv_window,
        }

    def _close_at_session_end(
        self,
        portfolio: PortfolioManager,
        event_bars: Mapping[str, Bar],
        session_ends: Mapping[str, Mapping[date, pd.Timestamp]],
        timestamp: pd.Timestamp,
        clock: SimulatedClock,
    ) -> None:
        for symbol in list(portfolio.positions):
            if symbol not in event_bars:
                continue
            session = _exchange_date(timestamp)
            if timestamp != session_ends[symbol][session]:
                continue
            position = portfolio.positions[symbol]
            bar = event_bars[symbol]
            side = "sell" if position.side == "buy" else "buy"
            price = portfolio._apply_exit_slippage(
                bar.close,
                reason="EOD",
                side=side,
                quantity=position.quantity,
                bar=bar,
            )
            portfolio.close_position(
                FillResult(
                    symbol=symbol,
                    side=side,
                    filled_qty=position.quantity,
                    avg_price=price,
                ),
                exit_reason="EOD",
                timestamp=clock.now(),
            )


def _group_session_timestamps(
    frame: pd.DataFrame,
) -> list[tuple[date, list[pd.Timestamp]]]:
    grouped: dict[date, list[pd.Timestamp]] = {}
    for timestamp in frame.index:
        grouped.setdefault(_exchange_date(timestamp), []).append(timestamp)
    return list(grouped.items())
