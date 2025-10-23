# src/backtester/__init__.py
from .engine import BacktestEngine
from .data_fetcher import fetch_backtest_data
from .benchmark import (
    generate_buy_hold_benchmark,
    generate_multi_ticker_benchmarks,
    fetch_and_generate_benchmarks,
)

__all__ = [
    "BacktestEngine",
    "fetch_backtest_data",
    "generate_buy_hold_benchmark",
    "generate_multi_ticker_benchmarks",
    "fetch_and_generate_benchmarks",
]