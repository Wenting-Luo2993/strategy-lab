"""Factory utilities for building reusable StrategyConfig instances.

These helpers centralize construction of strategy configurations so example scripts,
verification harnesses, backtests, and orchestrators can all share consistent
parameter defaults. Feel free to extend with new strategies or risk profiles.

Primary export today: build_orb_atr_strategy_config()
"""
from __future__ import annotations

from typing import List, Optional

from .parameters import (
    StrategyConfig,
    OrbConfig,
    RiskConfig,
    TrailingStopConfig,
)

# --- ORB + ATR Risk Strategy Builders ---------------------------------------------------------

def build_orb_atr_strategy_config(
    timeframe: str = "5",
    start_time: str = "09:30",
    body_breakout_percentage: float = 0.5,
    stop_loss_atr: float = 1.5,
    take_profit_atr: float = 3.0,
    risk_per_trade: float = 0.01,
    position_cap_percent: float = 0.25,
    trailing_enabled: bool = True,
    trailing_dynamic: bool = True,
    base_trail_r: float = 0.5,
    trailing_breakpoints: Optional[List[List[float]]] = None,
    trailing_levels: Optional[dict[float, float]] = None,
    eod_exit: bool = True,
    entry_volume_filter: float = 0.0,
    initial_stop_orb_pct: float | None = None,
) -> StrategyConfig:
    """Construct a StrategyConfig for an Opening Range Breakout using ATR-based
    stop & take profit along with optional dynamic trailing stops.

    Args:
        timeframe: ORB range duration in minutes (string form matching existing config usage).
        start_time: Session start used for ORB window (e.g., "09:30").
        body_breakout_percentage: Minimum body breakout % for validating breakout candles.
        stop_loss_atr: ATR multiple for initial stop.
        take_profit_atr: ATR multiple for profit target.
        risk_per_trade: Fraction of equity to risk per trade.
        position_cap_percent: Max allocation cap as fraction of equity.
        trailing_enabled: Enable trailing stop logic.
        trailing_dynamic: Use dynamic R-based breakpoints if True; else static trailing.
        base_trail_r: Base R multiple for initial trail distance.
        trailing_breakpoints: List of [unrealized_r, new_trail_r] adjustments.
        trailing_levels: Mapping unrealized_r -> locked_in_r for dynamic levels.
        eod_exit: Force end-of-day exit logic.
        entry_volume_filter: Placeholder for volume filter threshold; 0 disables.

    Returns:
        StrategyConfig ready to pass into ORBStrategy.
    """
    if trailing_breakpoints is None:
        trailing_breakpoints = [[2.0, 1.0], [3.0, 1.5], [5.0, 2.0]]
    if trailing_levels is None:
        trailing_levels = {2.0: 0.5, 3.0: 1.0, 4.0: 2.0}

    orb_cfg = OrbConfig(
        timeframe=timeframe,
        start_time=start_time,
        body_breakout_percentage=body_breakout_percentage,
        initial_stop_orb_pct=initial_stop_orb_pct,
    )

    trailing_cfg = TrailingStopConfig(
        enabled=trailing_enabled,
        dynamic_mode=trailing_dynamic,
        base_trail_r=base_trail_r,
        breakpoints=trailing_breakpoints,
        levels=trailing_levels,
    )

    risk_cfg = RiskConfig(
        stop_loss_type="atr",
        stop_loss_value=stop_loss_atr,
        take_profit_type="atr",
        take_profit_value=take_profit_atr,
        risk_per_trade=risk_per_trade,
        position_allocation_cap_percent=position_cap_percent,
        trailing_stop=trailing_cfg,
    )

    return StrategyConfig(
        orb_config=orb_cfg,
        entry_volume_filter=entry_volume_filter,
        risk=risk_cfg,
        eod_exit=eod_exit,
    )


def build_default_orb_strategy_config() -> StrategyConfig:
    """Return a sane default ORB + ATR strategy config (thin wrapper).

    Mirrors the example used in `example_dark_replay.py`.
    """
    return build_orb_atr_strategy_config(initial_stop_orb_pct=0.25)


def build_orb_atr_strategy_config_with_or_stop(
    initial_stop_orb_pct: float = 0.25,
    **kwargs,
) -> StrategyConfig:
    """Explicit builder that requires an OR-based initial stop percentage.

    This convenience wrapper passes through remaining keyword args to
    build_orb_atr_strategy_config while enforcing a supplied initial_stop_orb_pct.
    """
    return build_orb_atr_strategy_config(initial_stop_orb_pct=initial_stop_orb_pct, **kwargs)

__all__ = [
    "build_orb_atr_strategy_config",
    "build_default_orb_strategy_config",
    "build_orb_atr_strategy_config_with_or_stop",
]
