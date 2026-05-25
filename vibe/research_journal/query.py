"""
Query API for Research Journal (Stage 6)

Provides chainable, composable queries for experiments and hypotheses.
Supports filtering by tag, status, parameter, result quality, and date range.
"""

from typing import List, Optional, Dict, Any, Callable
from datetime import datetime, timezone
from vibe.research_journal.models import (
    Experiment,
    Hypothesis,
    ExperimentStatus,
    HypothesisStatus,
)
from vibe.research_journal.registry import ResearchRegistry


class ExperimentQuery:
    """Chainable query builder for experiments.
    
    Usage:
        query = ExperimentQuery(registry)
        results = (query
                   .by_tag("validation")
                   .by_status(ExperimentStatus.COMPLETED)
                   .by_result_quality("sharpe_ratio", 1.0, 2.0)
                   .execute())
    """
    
    def __init__(self, registry: ResearchRegistry):
        """Initialize query builder.
        
        Args:
            registry: ResearchRegistry instance to query
        """
        self.registry = registry
        self._filters: List[Callable[[Experiment], bool]] = []
    
    def by_tag(self, tag: str) -> "ExperimentQuery":
        """Filter experiments by tag (case-insensitive).
        
        Args:
            tag: Tag to filter by
            
        Returns:
            Self for chaining
        """
        tag_lower = tag.lower()
        
        def filter_fn(exp: Experiment) -> bool:
            return any(t.lower() == tag_lower for t in exp.tags)
        
        self._filters.append(filter_fn)
        return self
    
    def by_status(self, status: ExperimentStatus) -> "ExperimentQuery":
        """Filter experiments by status.
        
        Args:
            status: ExperimentStatus to filter by
            
        Returns:
            Self for chaining
        """
        def filter_fn(exp: Experiment) -> bool:
            return exp.status == status
        
        self._filters.append(filter_fn)
        return self
    
    def by_hypothesis(self, hypothesis_id: str) -> "ExperimentQuery":
        """Filter experiments by hypothesis.
        
        Args:
            hypothesis_id: Hypothesis ID (HYP-NNN format)
            
        Returns:
            Self for chaining
        """
        def filter_fn(exp: Experiment) -> bool:
            return exp.hypothesis_id == hypothesis_id
        
        self._filters.append(filter_fn)
        return self
    
    def by_parameter(self, param_path: str, value: Any) -> "ExperimentQuery":
        """Filter experiments by parameter value.
        
        Supports nested paths like 'strategy.atr_filter' or simple keys like 'orb_minutes'.
        
        Args:
            param_path: Parameter path (dot-separated for nested access)
            value: Value to match
            
        Returns:
            Self for chaining
        """
        def filter_fn(exp: Experiment) -> bool:
            # Support nested paths like "strategy.atr_filter"
            parts = param_path.split(".")
            current = exp.parameters
            
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return False
            
            return current == value
        
        self._filters.append(filter_fn)
        return self
    
    def by_date_range(
        self, start_date: datetime, end_date: datetime
    ) -> "ExperimentQuery":
        """Filter experiments by creation date range.
        
        Args:
            start_date: Start of date range (inclusive)
            end_date: End of date range (inclusive)
            
        Returns:
            Self for chaining
        """
        def filter_fn(exp: Experiment) -> bool:
            return start_date <= exp.created_at <= end_date
        
        self._filters.append(filter_fn)
        return self
    
    def by_result_quality(
        self, metric: str, min_value: float, max_value: float
    ) -> "ExperimentQuery":
        """Filter experiments by result metric range.
        
        Only matches completed experiments with results_summary.
        
        Args:
            metric: Result metric key (e.g., 'sharpe_ratio', 'expectancy_r')
            min_value: Minimum value (inclusive)
            max_value: Maximum value (inclusive)
            
        Returns:
            Self for chaining
        """
        def filter_fn(exp: Experiment) -> bool:
            if exp.results_summary is None:
                return False
            
            if metric not in exp.results_summary:
                return False
            
            value = exp.results_summary[metric]
            return min_value <= value <= max_value
        
        self._filters.append(filter_fn)
        return self
    
    def execute(self) -> List[Experiment]:
        """Execute query and return matching experiments.
        
        Returns:
            List of experiments matching all filter criteria (intersection)
        """
        all_experiments = self.registry.list_experiments()
        
        # Apply all filters (AND logic - must match all)
        for filter_fn in self._filters:
            all_experiments = [exp for exp in all_experiments if filter_fn(exp)]
        
        return all_experiments
    
    @staticmethod
    def combine(
        registry: ResearchRegistry, *queries: "ExperimentQuery"
    ) -> List[Experiment]:
        """Combine multiple queries with intersection (AND logic).
        
        Returns experiments that match all queries.
        
        Args:
            registry: ResearchRegistry instance
            *queries: ExperimentQuery instances to combine
            
        Returns:
            List of experiments matching all queries (intersection)
        """
        if not queries:
            return []
        
        # Get results from first query
        results = queries[0].execute()
        result_ids = {exp.id for exp in results}
        
        # Intersect with remaining queries
        for query in queries[1:]:
            query_results = query.execute()
            query_ids = {exp.id for exp in query_results}
            result_ids &= query_ids  # Intersection
        
        # Rebuild list maintaining order from first query
        return [exp for exp in results if exp.id in result_ids]


class HypothesisQuery:
    """Chainable query builder for hypotheses.
    
    Usage:
        query = HypothesisQuery(registry)
        results = (query
                   .by_tag("orb")
                   .by_status(HypothesisStatus.ACTIVE)
                   .execute())
    """
    
    def __init__(self, registry: ResearchRegistry):
        """Initialize query builder.
        
        Args:
            registry: ResearchRegistry instance to query
        """
        self.registry = registry
        self._filters: List[Callable[[Hypothesis], bool]] = []
    
    def by_tag(self, tag: str) -> "HypothesisQuery":
        """Filter hypotheses by tag (case-insensitive).
        
        Args:
            tag: Tag to filter by
            
        Returns:
            Self for chaining
        """
        tag_lower = tag.lower()
        
        def filter_fn(hyp: Hypothesis) -> bool:
            return any(t.lower() == tag_lower for t in hyp.tags)
        
        self._filters.append(filter_fn)
        return self
    
    def by_status(self, status: HypothesisStatus) -> "HypothesisQuery":
        """Filter hypotheses by status.
        
        Args:
            status: HypothesisStatus to filter by
            
        Returns:
            Self for chaining
        """
        def filter_fn(hyp: Hypothesis) -> bool:
            return hyp.status == status
        
        self._filters.append(filter_fn)
        return self
    
    def execute(self) -> List[Hypothesis]:
        """Execute query and return matching hypotheses.
        
        Returns:
            List of hypotheses matching all filter criteria (intersection)
        """
        # Load all hypotheses from disk
        research_root = self.registry.research_root
        hypothesis_dir = research_root / "hypotheses"
        
        if not hypothesis_dir.exists():
            return []
        
        all_hypotheses = []
        for hyp_file in hypothesis_dir.glob("HYP-*.yaml"):
            hyp_id = hyp_file.stem
            try:
                hyp = self.registry.get_hypothesis(hyp_id)
                all_hypotheses.append(hyp)
            except Exception:
                # Skip files that can't be loaded
                continue
        
        # Apply all filters (AND logic)
        for filter_fn in self._filters:
            all_hypotheses = [hyp for hyp in all_hypotheses if filter_fn(hyp)]
        
        return all_hypotheses
