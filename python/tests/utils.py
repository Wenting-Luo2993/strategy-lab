"""Common testing utilities for strategy-lab.

Contains mock RiskManagement implementations and market data builders.
"""
from __future__ import annotations
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional
import pandas as pd

from src.risk_management.base import RiskManagement
from src.config.columns import TradeColumns
from src.utils.workspace import resolve_workspace_path
from paths import get_scenarios_root

class DummyRiskConfig(SimpleNamespace):
    def __init__(self, risk_per_trade=0.01, max_position_size_percent=1.0, position_allocation_cap_percent=0.25):
        super().__init__(
            risk_per_trade=risk_per_trade,
            max_position_size_percent=max_position_size_percent,
            position_allocation_cap_percent=position_allocation_cap_percent,
            trailing_stop=None,
            stop_loss_type="pct",
            take_profit_type="pct",
        )


class MockRiskManager(RiskManagement):
    """Mock risk manager for deterministic test behavior.

    Parameters
    ----------
    stop_loss_offset : float
        Distance from entry for stop loss (positive). Applied directionally.
    take_profit_offset : float
        Distance from entry for take profit (positive). Applied directionally.
    trailing : bool
        If True, provides trailing stop data that can be activated.
    position_size : float
        Fixed position size returned for calculate_position_size.
    """
    def __init__(self, config=None, stop_loss_offset=1.0, take_profit_offset=2.0, trailing: bool = False, position_size: float = 10.0):
        if config is None:
            config = DummyRiskConfig()
        super().__init__(config)
        self.stop_loss_offset = stop_loss_offset
        self.take_profit_offset = take_profit_offset
        self.trailing = trailing
        self._position_size = position_size

    def apply(self, signal: pd.Series, data: pd.DataFrame):
        entry = signal["entry_price"]
        direction = signal["signal"]
        stop_loss = entry - direction * self.stop_loss_offset
        take_profit = entry + direction * self.take_profit_offset
        trailing_data = (
            {
                "enabled": self.trailing,
                "entry_price": entry,
                "direction": direction,
                "initial_stop": stop_loss,
                "current_stop": stop_loss,
                "highest_profit_r": 0.0,
                "trailing_active": False,
            }
            if self.trailing
            else {"enabled": False}
        )
        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop_data": trailing_data,
        }

    def calculate_position_size(self, account_balance: float, entry_price: float, stop_loss: float) -> float:
        return self._position_size

    def update_trailing_stop(self, position: dict, current_price: float):
        ts_data = position.get(TradeColumns.TRAILING_STOP_DATA.value)
        if not ts_data or not ts_data.get("enabled"):
            return position
        entry = ts_data["entry_price"]
        direction = ts_data["direction"]
        profit = (current_price - entry) if direction == 1 else (entry - current_price)
        if profit > 1.0:
            if direction == 1:
                new_stop = max(position["stop_loss"], entry + 0.5)
            else:
                new_stop = min(position["stop_loss"], entry - 0.5)
            position["stop_loss"] = new_stop
            ts_data["current_stop"] = new_stop
            ts_data["trailing_active"] = True
        position[TradeColumns.TRAILING_STOP_DATA.value] = ts_data
        return position


def make_market_data_one_day(
    start_day: datetime = datetime(2024, 10, 1),
    bars: int = 78,  # one trading day of 5-min bars from 09:30 to 16:00 (6.5h * 12 = 78)
    seed: Optional[int] = 42,
    base_price: float = 100.0,
    max_pct_move: float = 0.02,
    rsi_sequence: Optional[List[float]] = None,
) -> pd.DataFrame:
    """Generate one day of synthetic 5-minute OHLCV data.

    Each bar direction is random (+1 uptick, -1 downtick). Price changes are
    bounded by `max_pct_move`. High/Low are expanded off open/close depending
    on direction to simulate realistic ranges.

    Parameters
    ----------
    seed : int
        RNG seed for reproducibility.
    max_pct_move : float
        Maximum percent move for components (< 2% default).
    rsi_sequence : list[float] | None
        Optional RSI values; if None a flat 50 series is used.
    """
    rng = random.Random(seed)
    start_ts = start_day.replace(hour=9, minute=30, second=0, microsecond=0)
    rows = []
    if rsi_sequence is None:
        rsi_sequence = [50.0] * bars
    price = base_price
    for i in range(bars):
        ts = start_ts + timedelta(minutes=5 * i)
        direction = rng.choice([-1, 1])
        # percent move for open->close
        pct_move_body = rng.uniform(0, max_pct_move) * direction
        open_p = price
        close_p = open_p * (1 + pct_move_body)
        body_high = max(open_p, close_p)
        body_low = min(open_p, close_p)
        # Wicks: small additional random ranges
        pct_wick_high = rng.uniform(0, max_pct_move / 2.0)
        pct_wick_low = rng.uniform(0, max_pct_move / 2.0)
        if direction == 1:
            high = body_high * (1 + pct_wick_high)
            low = body_low * (1 - pct_wick_low)
        else:
            high = body_high * (1 + pct_wick_high / 2.0)
            low = body_low * (1 - pct_wick_low * 1.2)
        volume = rng.randint(500, 5000)
        rsi = rsi_sequence[i]
        rows.append((ts, open_p, high, low, close_p, volume, rsi))
        price = close_p  # next bar starts at previous close
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "RSI_14"]).set_index("timestamp")
    return df


def build_three_market_data(seed: int = 101) -> dict:
    """Return three small slices of market data representing bull, bear, sideways regimes.

    Returns a dict mapping regime label to DataFrame (first 15 bars for brevity).
    Regime determination relies on RSI values: >=60 bull, <=40 bear, else sideways.
    """
    bull = make_market_data_one_day(seed=seed, rsi_sequence=[65] + [55]*77).iloc[:15]
    bear = make_market_data_one_day(seed=seed + 1, rsi_sequence=[35] + [45]*77).iloc[:15]
    side = make_market_data_one_day(seed=seed + 2, rsi_sequence=[50]*78).iloc[:15]
    # Attach tickers for identification
    bull.attrs['ticker'] = 'BULL_TICK'
    bear.attrs['ticker'] = 'BEAR_TICK'
    side.attrs['ticker'] = 'SIDE_TICK'
    return {'bull': bull, 'bear': bear, 'side': side}


def strategy_config_to_dict(cfg):
    """Convert a StrategyConfig instance (nested dataclasses) into a plain dict.

    This supports stable hashing via `hash_config` and reuse across snapshot tests.
    Safe when `trailing_stop` is None.
    """
    return {
        "orb_config": {
            "timeframe": cfg.orb_config.timeframe,
            "start_time": cfg.orb_config.start_time,
            "body_breakout_percentage": cfg.orb_config.body_breakout_percentage,
            "initial_stop_orb_pct": cfg.orb_config.initial_stop_orb_pct,
        },
        "entry_volume_filter": cfg.entry_volume_filter,
        "risk": {
            "stop_loss_type": cfg.risk.stop_loss_type,
            "stop_loss_value": cfg.risk.stop_loss_value,
            "take_profit_type": cfg.risk.take_profit_type,
            "take_profit_value": cfg.risk.take_profit_value,
            "risk_per_trade": cfg.risk.risk_per_trade,
            "position_allocation_cap_percent": cfg.risk.position_allocation_cap_percent,
            "trailing_stop": (
                {
                    "enabled": cfg.risk.trailing_stop.enabled,
                    "dynamic_mode": cfg.risk.trailing_stop.dynamic_mode,
                    "base_trail_r": cfg.risk.trailing_stop.base_trail_r,
                    "breakpoints": cfg.risk.trailing_stop.breakpoints,
                    "levels": cfg.risk.trailing_stop.levels,
                }
                if getattr(cfg.risk, "trailing_stop", None)
                else None
            ),
        },
        "eod_exit": cfg.eod_exit,
    }


def load_fixture_df(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
    timeframe: Optional[str] = None,
    root_candidates: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Load deterministic fixture market data for a ticker & date range.

    Uses the shared scenarios root (SCENARIOS_REL_DIR) resolved via
    `get_scenarios_root()` unless explicit root candidates are provided. Each
    candidate is resolved with `resolve_workspace_path` to ensure portability.

    Directory naming convention matches extraction script:
      Single day: YYYY-MM-DD
      Date range: YYYY-MM-DD_YYYY-MM-DD

    Args:
        ticker: Symbol (e.g. 'AAPL').
        start_date: Inclusive start date.
        end_date: Optional inclusive end date for multi-day fixtures.
        timeframe: Optional substring filter (e.g. '5m').
        root_candidates: Optional list of alternative scenario roots.

    Returns:
        DataFrame with datetime index or empty DataFrame if not found/error.
    """
    from paths import get_fixture_name
    expected_dir_name = get_fixture_name(start_date, end_date)

    if root_candidates is None:
        roots = [get_scenarios_root()]
    else:
        roots = [resolve_workspace_path(r) for r in root_candidates]

    for base in roots:
        if not base.exists():
            continue
        candidate_dir = base / expected_dir_name
        if not (candidate_dir.exists() and candidate_dir.is_dir()):
            continue
        fixture_dir = candidate_dir
        # Look for ticker-specific data file
        patterns = [f"{ticker}.parquet", f"{ticker}.csv", f"{ticker}_*.parquet", f"{ticker}_*.csv"]
        data_files: List[Path] = []
        for pat in patterns:
            data_files.extend(fixture_dir.glob(pat))
        if timeframe:
            data_files = [f for f in data_files if timeframe in f.name]
        data_files.sort(key=lambda p: (p.suffix != ".parquet", p.name))
        if not data_files:
            continue
        path = data_files[0]
        try:
            if path.suffix == ".parquet":
                df = pd.read_parquet(path)
            else:
                df = pd.read_csv(path, parse_dates=True, index_col=0)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, errors="coerce")
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()
