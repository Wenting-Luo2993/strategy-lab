"""
Parameter Sweep Integration Extension for Research Journal (Stage 8+)

Provides optional experiment tracking for ParameterSweep.
Creates experiments for each parameter variation with lineage tracking.

Usage:
    sweep = ParameterSweep(base_ruleset, data_dir, parameters)
    
    # Optional: enable research journal tracking
    sweep.enable_experiment_tracking(
        registry=registry,
        hypothesis_id="HYP-001"
    )
    
    results = sweep.run(symbol="QQQ", start_date=..., end_date=...)
    
    # Parent experiment created for sweep
    # Child experiment created for each parameter combination
"""

from typing import Optional, List, Dict, Any
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.integration.backtest_adapter import BacktestResultAdapter
from vibe.research_journal.models import ExperimentStatus


class ParameterSweepExperimentTracker:
    """Tracks parameter sweep iterations as linked experiments.
    
    Features:
    - Creates parent experiment for sweep run
    - Creates child experiments for each parameter variation
    - Links children to parent via lineage
    - Tracks parameter changes across iterations
    """
    
    def __init__(
        self,
        registry: Optional[ResearchRegistry] = None,
        hypothesis_id: Optional[str] = None,
    ):
        """Initialize tracker.
        
        Args:
            registry: Optional ResearchRegistry instance
            hypothesis_id: Optional hypothesis to link experiments to
        """
        self.registry = registry
        self.hypothesis_id = hypothesis_id
        self.adapter = BacktestResultAdapter(registry) if registry else None
        self.enabled = registry is not None
        self.parent_experiment_id = None
    
    def create_parent_experiment(
        self,
        strategy_name: str,
        strategy_version: str,
        base_parameters: Dict[str, Any],
        dataset_config: Dict[str, Any],
        tags: Optional[List[str]] = None
    ) -> Optional[str]:
        """Create parent experiment for sweep run.
        
        Args:
            strategy_name: Strategy name
            strategy_version: Strategy version
            base_parameters: Base parameters (before sweep)
            dataset_config: Dataset configuration
            tags: Optional tags
            
        Returns:
            Parent experiment ID if tracking enabled
        """
        if not self.enabled:
            return None
        
        parent = self.registry.create_experiment(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parameters=base_parameters,
            dataset_config=dataset_config,
            hypothesis_id=self.hypothesis_id,
            tags=tags or ["parameter_sweep"]
        )
        
        self.parent_experiment_id = parent.id
        return parent.id
    
    def create_variation_experiment(
        self,
        variation_number: int,
        strategy_name: str,
        strategy_version: str,
        variation_parameters: Dict[str, Any],
        dataset_config: Dict[str, Any],
        tags: Optional[List[str]] = None
    ) -> Optional[str]:
        """Create child experiment for parameter variation.
        
        Args:
            variation_number: Sequence number (1, 2, 3, ...)
            strategy_name: Strategy name
            strategy_version: Strategy version
            variation_parameters: Parameters for this variation
            dataset_config: Dataset configuration
            tags: Optional tags
            
        Returns:
            Experiment ID for this variation
        """
        if not self.enabled:
            return None
        
        child = self.registry.create_experiment(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parameters=variation_parameters,
            dataset_config=dataset_config,
            hypothesis_id=self.hypothesis_id,
            parent_experiment_id=self.parent_experiment_id,
            tags=tags or [f"sweep_variation_{variation_number}"]
        )
        
        return child.id
    
    def complete_variation_experiment(
        self,
        experiment_id: str,
        trades: List,
        metrics: Dict[str, Any],
        rank: int,
        conclusion: str
    ) -> Optional[str]:
        """Complete variation experiment with results.
        
        Args:
            experiment_id: Variation experiment ID
            trades: List of Trade objects
            metrics: Computed metrics
            rank: Rank in sweep results (1=best, 2=second, etc.)
            conclusion: Human-readable conclusion
            
        Returns:
            Completed experiment ID
        """
        if not self.enabled:
            return None
        
        # Complete experiment
        completed = self.registry.complete_experiment(
            experiment_id=experiment_id,
            results=metrics,
            conclusion=f"Rank #{rank}: {conclusion}"
        )
        
        return completed.id
    
    def can_track(self) -> bool:
        """Check if experiment tracking is enabled."""
        return self.enabled


class SweepResultExperimentLinker:
    """Links completed sweep results to experiments."""
    
    def __init__(self, registry: Optional[ResearchRegistry] = None):
        """Initialize linker.
        
        Args:
            registry: Optional ResearchRegistry instance
        """
        self.registry = registry
        self.enabled = registry is not None
    
    def link_sweep_results(
        self,
        parent_experiment_id: str,
        sweep_results_df,
        best_rank: int = 1
    ):
        """Link sweep results to experiments.
        
        Args:
            parent_experiment_id: Parent experiment ID
            sweep_results_df: Results DataFrame with metrics
            best_rank: Rank of best result to highlight
        """
        if not self.enabled:
            return
        
        # Find best result row
        best_result = sweep_results_df.iloc[best_rank - 1]
        
        # Add research note to parent
        conclusion = f"Best result: {best_result['score']:.3f} score"
        
        self.registry.add_research_note(
            content=f"Parameter sweep completed with {len(sweep_results_df)} variations. {conclusion}",
            related_experiment_id=parent_experiment_id,
            tags=["sweep_summary"]
        )


# Example integration code (for documentation)
"""
def run_sweep_with_experiment_tracking():
    '''Example: Running parameter sweep with Research Journal tracking'''
    
    # Setup
    registry = ResearchRegistry()
    sweep = ParameterSweep(base_ruleset, data_dir, parameters)
    
    # Enable tracking
    tracker = ParameterSweepExperimentTracker(
        registry=registry,
        hypothesis_id="HYP-001"
    )
    
    # Create parent experiment
    parent_exp_id = tracker.create_parent_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.4.2",
        base_parameters={"orb_minutes": 5},
        dataset_config={"symbols": ["QQQ"]},
        tags=["optimization_sweep"]
    )
    
    # Run sweep
    results_df = sweep.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31)
    )
    
    # For each variation result, create child experiment
    for idx, row in results_df.iterrows():
        # Create variation experiment
        var_exp_id = tracker.create_variation_experiment(
            variation_number=idx + 1,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            variation_parameters=row['parameters'],
            dataset_config={"symbols": ["QQQ"]},
            tags=[f"variation_{idx+1}"]
        )
        
        # Complete with results
        tracker.complete_variation_experiment(
            experiment_id=var_exp_id,
            trades=row['trades'],
            metrics={
                'score': row['score'],
                'sharpe': row['sharpe'],
                'expectancy': row['expectancy']
            },
            rank=row['rank'],
            conclusion=f"Parameter variation {idx+1}"
        )
    
    # Link all results
    linker = SweepResultExperimentLinker(registry)
    linker.link_sweep_results(
        parent_experiment_id=parent_exp_id,
        sweep_results_df=results_df
    )
    
    return results_df
"""
