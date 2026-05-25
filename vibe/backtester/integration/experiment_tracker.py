"""
Backtester Integration Extension for Research Journal (Stage 8+)

Provides optional experiment tracking for BacktestEngine.
When enabled, automatically creates and completes experiments from backtest runs.

Usage:
    engine = BacktestEngine(ruleset, data_dir)
    result = engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31),
        experiment_id="EXP-001"  # Optional: link to research journal
    )
"""

from typing import Optional, List
from datetime import datetime
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.integration.backtest_adapter import BacktestResultAdapter
from vibe.common.models.trade import Trade


class BacktestExperimentTracker:
    """Adapter to track backtest runs in Research Journal.
    
    Provides optional integration between BacktestEngine and ResearchRegistry.
    No changes to existing backtest code - purely additive.
    """
    
    def __init__(self, registry: Optional[ResearchRegistry] = None):
        """Initialize tracker.
        
        Args:
            registry: Optional ResearchRegistry instance.
                     If None, tracking is disabled.
        """
        self.registry = registry
        self.adapter = BacktestResultAdapter(registry) if registry else None
        self.enabled = registry is not None
    
    def track_backtest_result(
        self,
        backtest_result: BacktestResult,
        experiment_id: str,
        strategy_name: str,
        strategy_version: str,
        parameters: dict,
        dataset_config: dict,
        conclusion: str = "Backtest completed"
    ) -> Optional[str]:
        """Track backtest result as completed experiment.
        
        Args:
            backtest_result: BacktestResult from engine.run()
            experiment_id: Experiment ID to complete (EXP-NNN)
            strategy_name: Strategy name
            strategy_version: Strategy version
            parameters: Strategy parameters
            dataset_config: Dataset configuration
            conclusion: Human-readable conclusion
            
        Returns:
            Experiment ID if tracking enabled, None otherwise
        """
        if not self.enabled:
            return None
        
        # Extract trades from backtest result
        trades = backtest_result.trades if hasattr(backtest_result, 'trades') else []
        
        # Complete experiment
        completed = self.adapter.complete_experiment(
            experiment_id=experiment_id,
            trades=trades,
            conclusion=conclusion
        )
        
        return completed.id
    
    def can_track(self) -> bool:
        """Check if experiment tracking is enabled."""
        return self.enabled


def wrap_backtest_engine(engine, registry: Optional[ResearchRegistry] = None):
    """Decorator factory to add experiment tracking to BacktestEngine.
    
    Usage:
        engine = BacktestEngine(ruleset, data_dir)
        tracked_engine = wrap_backtest_engine(engine, registry)
        
        result = tracked_engine.run(
            symbol="QQQ",
            start_date=...,
            end_date=...,
            experiment_id="EXP-001"  # New optional parameter
        )
    """
    tracker = BacktestExperimentTracker(registry)
    
    original_run = engine.run
    
    def wrapped_run(
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        precomputed_features=None,
        experiment_id: Optional[str] = None
    ):
        # Run backtest normally
        result = original_run(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            precomputed_features=precomputed_features
        )
        
        # If experiment_id provided and tracking enabled, complete experiment
        if experiment_id and tracker.can_track():
            tracker.track_backtest_result(
                backtest_result=result,
                experiment_id=experiment_id,
                strategy_name=engine.ruleset.strategy_name if hasattr(engine.ruleset, 'strategy_name') else "Unknown",
                strategy_version=getattr(engine.ruleset, 'version', '1.0'),
                parameters=getattr(engine.ruleset, 'parameters', {}),
                dataset_config={
                    'symbol': symbol,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                conclusion=f"Backtest completed: {result.expectancy:.3f}R expectancy"
            )
        
        return result
    
    engine.run = wrapped_run
    engine._experiment_tracker = tracker
    
    return engine
