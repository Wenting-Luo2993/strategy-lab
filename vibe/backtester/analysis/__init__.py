"""Backtester analysis module."""

# Lazy imports to avoid dependency issues during development
__all__ = [
    "BacktestResult",
    "MetricsSummary",
    "ParameterDefinition",
    "ParameterSweep",
    "SweepResult",
    "PerformanceAnalyzer",
    "run_sensitivity_analysis",
]


def __getattr__(name):
    """Lazy import of analysis components."""
    if name == "BacktestResult" or name == "MetricsSummary":
        from vibe.backtester.analysis.metrics import BacktestResult, MetricsSummary
        return BacktestResult if name == "BacktestResult" else MetricsSummary
    elif name in ("ParameterDefinition", "ParameterSweep", "SweepResult"):
        from vibe.backtester.analysis.parameter_sweep import (
            ParameterDefinition,
            ParameterSweep,
            SweepResult,
        )
        if name == "ParameterDefinition":
            return ParameterDefinition
        elif name == "ParameterSweep":
            return ParameterSweep
        else:
            return SweepResult
    elif name == "PerformanceAnalyzer":
        from vibe.backtester.analysis.performance import PerformanceAnalyzer
        return PerformanceAnalyzer
    elif name == "run_sensitivity_analysis":
        from vibe.backtester.analysis.sensitivity_runner import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
