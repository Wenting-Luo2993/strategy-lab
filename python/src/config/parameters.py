import itertools
import os
import yaml

from dataclasses import dataclass

from src.utils.logger import get_logger

@dataclass
class OrbConfig:
    timeframe: str
    start_time: str
    body_breakout_percentage: float

@dataclass
class TrailingStopConfig:
    """Configuration for trailing stop system"""
    enabled: bool = False  # Whether to use trailing stops
    # Dictionary mapping profit thresholds (in ATR) to trailing stop distances (in ATR)
    # e.g., {2.0: 0.5, 2.5: 1.0, 3.0: 2.0, 4.0: 3.0, 5.0: 4.0}
    levels: dict = None
    # Dynamic trailing mode - when True, uses rules instead of discrete levels
    dynamic_mode: bool = False
    # Base trailing amount (in R units) for dynamic mode
    base_trail_r: float = 0.5
    # List of [profit_threshold_r, trail_amount_r] breakpoints for dynamic trailing
    # e.g., [[2.0, 1.0], [5.0, 2.0]] means:
    # - Below 2R profit: trail by base_trail_r (0.5R)
    # - From 2R to 5R profit: trail by 1R
    # - Above 5R profit: trail by 2R
    breakpoints: list = None

@dataclass
class RiskConfig:
    stop_loss_type: str
    stop_loss_value: float
    take_profit_type: str
    take_profit_value: float
    risk_per_trade: float = 0.01  # Default to 1% if not specified
    position_allocation_cap_percent: float = 0.25  # Max % of current balance allocatable to a single position
    trailing_stop: TrailingStopConfig = None  # Trailing stop configuration
    # Opening Range (OR) based initial stop percentage (0-1). When provided, overrides
    # basic ORBStrategy.initial_stop_value logic. For a long position the initial stop is:
    #   OR_Low + initial_stop_orb_pct * (OR_High - OR_Low)
    # For a short position:
    #   OR_High - initial_stop_orb_pct * (OR_High - OR_Low)
    # If None, strategy falls back to using OR_Low for long / OR_High for short.
    initial_stop_orb_pct: float | None = None

@dataclass
class StrategyConfig:
    orb_config: OrbConfig
    entry_volume_filter: float
    risk: RiskConfig
    eod_exit: bool

logger = get_logger("ConfigLoader")

def load_strategy_parameters() -> list[StrategyConfig]:

    config_path = os.path.join(os.path.dirname(__file__), "../config/backtest_parameters.yaml")
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Pre-process nested parameters
    take_profit_combos = []
    for tp_config in config["exit"]["take_profit"]:
        for value in tp_config["values"]:
            take_profit_combos.append({"type": tp_config["type"], "value": value})

    stop_loss_combos = []
    for sl_config in config["risk"]["stop_loss"]:
        for value in sl_config["values"]:
            stop_loss_combos.append({"type": sl_config["type"], "value": value})

    # Flatten parameters for grid search
    param_grid = {
        "timeframe": config["orb"]["timeframe"],
        "start_time": config["orb"]["start_time"],
        "body_breakout_percentage": config["orb"]["body_breakout_percentage"],
        "volume_filter": config["entry"]["volume_filter"],
        "trend_filter": config["entry"]["trend_filter"],
        "stop_loss": stop_loss_combos,
        "take_profit": take_profit_combos,
        "risk_per_trade": config["risk"]["risk_per_trade"],
        "eod_exit": [True],  # config["exit"]["eod_exit"]
        "trailing_stop": [config["trailing_stop"]]
    }

    # Create all parameter combos
    all_combinations = list(itertools.product(*param_grid.values()))

    configs = []
    for combo in all_combinations:
        params = dict(zip(param_grid.keys(), combo))
        strategy_config = StrategyConfig(
            orb_config=OrbConfig(
                timeframe=params["timeframe"],
                start_time=params["start_time"],
                body_breakout_percentage=params["body_breakout_percentage"]
            ),
            entry_volume_filter=params["volume_filter"],
            risk=RiskConfig(
                stop_loss_type=params["stop_loss"]["type"],
                stop_loss_value=params["stop_loss"]["value"],
                take_profit_type=params["take_profit"]["type"],
                take_profit_value=params["take_profit"]["value"],
                risk_per_trade=params["risk_per_trade"],
                # Initialize trailing stop with configuration from params
                trailing_stop=TrailingStopConfig(
                    enabled=params["trailing_stop"]["enabled"],
                    dynamic_mode=params["trailing_stop"]["dynamic_mode"],
                    base_trail_r=params["trailing_stop"]["base_trail_r"],
                    breakpoints=params["trailing_stop"]["breakpoints"],
                    levels=params["trailing_stop"]["levels"]
                )
            ),
            eod_exit=params["eod_exit"]
        )
        configs.append(strategy_config)

    logger.info(f"Generated {len(configs)} parameter configurations.")
    logger.info(f"Sample first config: {str(configs[0])}")
    return configs
