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
class RiskConfig:
    stop_loss_type: str
    stop_loss_value: float
    take_profit_type: str
    take_profit_value: float
    risk_per_trade: float = 0.01  # Default to 1% if not specified

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
        "eod_exit": [True]  # config["exit"]["eod_exit"]
    }

    # Create all parameter combos
    all_combinations = list(itertools.product(*param_grid.values()))

    configs = []
    for combo in all_combinations:
        params = dict(zip(param_grid.keys(), combo))
        config = StrategyConfig(
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
                risk_per_trade=params["risk_per_trade"]
            ),
            eod_exit=params["eod_exit"]
        )
        configs.append(config)

    logger.info(f"Generated {len(configs)} parameter configurations.")
    return configs
