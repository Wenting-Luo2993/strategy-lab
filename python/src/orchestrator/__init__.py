"""
Orchestrator module for trading system simulation.

This module provides orchestrators for backtesting and paper trading simulation.
"""

from .backtest_orchestrator import BackTestOrchestrator
from .dark_trading_orchestrator import DarkTradingOrchestrator

__all__ = [
    "DarkTradingOrchestrator",
    "BackTestOrchestrator"
]