"""
Backtester Integration with Research Journal (Stage 8+)

Provides optional experiment tracking for:
- BacktestEngine: Auto-create experiments from backtest runs
- ParameterSweep: Track optimization iterations with lineage

These modules are purely additive - existing code works unchanged.
Enable by passing optional `registry` or `experiment_id` parameters.
"""

from .experiment_tracker import (
    BacktestExperimentTracker,
    wrap_backtest_engine,
)
from .sweep_tracker import (
    ParameterSweepExperimentTracker,
    SweepResultExperimentLinker,
)

__all__ = [
    "BacktestExperimentTracker",
    "wrap_backtest_engine",
    "ParameterSweepExperimentTracker",
    "SweepResultExperimentLinker",
]
