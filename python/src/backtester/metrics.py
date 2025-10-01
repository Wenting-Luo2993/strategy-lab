
import numpy as np
from typing import List, Dict, Any

from ..config.columns import TradeColumns
from enum import Enum

class MetricsColumns(Enum):
    WIN_RATE = "win_rate"
    EXPECTANCY = "expectancy"
    MAX_DRAWDOWN = "max_drawdown"
    SHARPE_RATIO = "sharpe_ratio"
    PROFIT_FACTOR = "profit_factor"
    AVERAGE_TRADE_RETURN = "average_trade_return"
    AVERAGE_HOLDING_TIME = "average_holding_time"
    NUM_TRADES = "num_trades"

def win_rate(trades: List[Dict[str, Any]]) -> float:
    wins = [t for t in trades if t[TradeColumns.PNL.value] > 0]
    total = len(trades)
    return len(wins) / total if total > 0 else 0.0

def expectancy(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    win_trades = [t[TradeColumns.PNL.value] for t in trades if t[TradeColumns.PNL.value] > 0]
    loss_trades = [t[TradeColumns.PNL.value] for t in trades if t[TradeColumns.PNL.value] < 0]
    win_rate_val = win_rate(trades)
    avg_win = np.mean(win_trades) if win_trades else 0.0
    avg_loss = np.mean(loss_trades) if loss_trades else 0.0
    return win_rate_val * avg_win + (1 - win_rate_val) * avg_loss

def max_drawdown(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    equity_curve = np.cumsum([t[TradeColumns.PNL.value] for t in trades])
    peak = np.maximum.accumulate(equity_curve)
    drawdown = equity_curve - peak
    return float(np.min(drawdown))

def sharpe_ratio(trades: List[Dict[str, Any]], risk_free_rate: float = 0.0) -> float:
    if not trades:
        return 0.0
    returns = [t[TradeColumns.PNL.value] for t in trades]
    if len(returns) < 2:
        return 0.0
    excess_returns = np.array(returns) - risk_free_rate
    return float(np.mean(excess_returns) / np.std(excess_returns)) if np.std(excess_returns) != 0 else 0.0

def profit_factor(trades: List[Dict[str, Any]]) -> float:
    gross_profit = sum(t[TradeColumns.PNL.value] for t in trades if t[TradeColumns.PNL.value] > 0)
    gross_loss = abs(sum(t[TradeColumns.PNL.value] for t in trades if t[TradeColumns.PNL.value] < 0))
    return gross_profit / gross_loss if gross_loss > 0 else float('inf')

def average_trade_return(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    returns = [t[TradeColumns.PNL.value] / t[TradeColumns.ENTRY_PRICE.value] for t in trades if t[TradeColumns.ENTRY_PRICE.value] != 0]
    return float(np.mean(returns)) if returns else 0.0

def average_holding_time(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    holding_times = [
        (t[TradeColumns.EXIT_TIME.value] - t[TradeColumns.ENTRY_TIME.value]).total_seconds()
        for t in trades if TradeColumns.EXIT_TIME.value in t and TradeColumns.ENTRY_TIME.value in t
    ]
    return float(np.mean(holding_times)) if holding_times else 0.0

def summarize_metrics(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        MetricsColumns.WIN_RATE.value: win_rate(trades),
        MetricsColumns.EXPECTANCY.value: expectancy(trades),
        MetricsColumns.MAX_DRAWDOWN.value: max_drawdown(trades),
        MetricsColumns.SHARPE_RATIO.value: sharpe_ratio(trades),
        MetricsColumns.PROFIT_FACTOR.value: profit_factor(trades),
        MetricsColumns.AVERAGE_TRADE_RETURN.value: average_trade_return(trades),
        MetricsColumns.AVERAGE_HOLDING_TIME.value: average_holding_time(trades),
        MetricsColumns.NUM_TRADES.value: len(trades)
    }
