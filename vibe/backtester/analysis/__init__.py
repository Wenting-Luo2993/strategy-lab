"""Backtester analysis module."""

# Lazy imports to avoid dependency issues during development
__all__ = [
    "BacktestResult",
    "PerformanceAnalyzer",
]


def __getattr__(name):
    """Lazy import of analysis components."""
    if name == "BacktestResult":
        from vibe.backtester.analysis.metrics import BacktestResult
        return BacktestResult
    if name == "PerformanceAnalyzer":
        from vibe.backtester.analysis.performance import PerformanceAnalyzer
        return PerformanceAnalyzer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
