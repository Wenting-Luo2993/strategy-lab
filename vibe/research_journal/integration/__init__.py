"""
Integration Adapters for Research Journal (Stage 8)

Connects the Research Journal Framework with the Backtester and other systems.
Provides high-level functions to create and manage experiments from backtest results.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.models import ExperimentStatus
from vibe.common.models.trade import Trade


class BacktestResultAdapter:
    """Adapter for converting backtest results to Research Journal experiments.
    
    Provides high-level API to:
    - Create experiments from backtest trade results
    - Compute metrics from trades
    - Track parameter variations through lineage
    - Complete experiments with results
    
    Usage:
        adapter = BacktestResultAdapter(registry)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id="HYP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={...},
            dataset_config={...},
            trades=[...]  # List of Trade objects
        )
    """
    
    def __init__(self, registry: ResearchRegistry):
        """Initialize adapter with registry.
        
        Args:
            registry: ResearchRegistry instance
        """
        self.registry = registry
    
    def create_experiment_from_trades(
        self,
        hypothesis_id: str,
        strategy_name: str,
        strategy_version: str,
        parameters: Dict[str, Any],
        dataset_config: Dict[str, Any],
        trades: List[Trade],
        parent_experiment_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        """Create experiment from backtest trades.
        
        Args:
            hypothesis_id: Hypothesis ID (HYP-NNN)
            strategy_name: Name of strategy
            strategy_version: Version of strategy
            parameters: Strategy parameters
            dataset_config: Dataset configuration
            trades: List of Trade objects from backtest
            parent_experiment_id: Optional parent experiment for lineage
            tags: Optional list of tags
            
        Returns:
            Experiment with results_summary computed from trades
        """
        # Create experiment
        exp = self.registry.create_experiment(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parameters=parameters,
            dataset_config=dataset_config,
            hypothesis_id=hypothesis_id,
            parent_experiment_id=parent_experiment_id,
            tags=tags or []
        )
        
        # Compute metrics from trades
        metrics = self._compute_metrics_from_trades(trades)
        
        # Update experiment with results (but don't mark complete yet)
        # This allows for subsequent modifications before marking complete
        exp.results_summary = metrics
        
        return exp
    
    def complete_experiment(
        self,
        experiment_id: str,
        trades: List[Trade],
        conclusion: str
    ):
        """Complete experiment with backtest results.
        
        Args:
            experiment_id: Experiment ID (EXP-NNN)
            trades: List of Trade objects from backtest
            conclusion: Human-readable conclusion
            
        Returns:
            Completed Experiment (immutable)
        """
        # Compute metrics
        metrics = self._compute_metrics_from_trades(trades)
        
        # Complete experiment
        completed_exp = self.registry.complete_experiment(
            experiment_id=experiment_id,
            results=metrics,
            conclusion=conclusion
        )
        
        return completed_exp
    
    @staticmethod
    def _compute_metrics_from_trades(trades: List[Trade]) -> Dict[str, Any]:
        """Compute backtest metrics from trades.
        
        Args:
            trades: List of Trade objects
            
        Returns:
            Dictionary of computed metrics
        """
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "profit_factor": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
            }
        
        total_trades = len(trades)
        winning_trades = 0
        losing_trades = 0
        total_pnl = 0.0
        win_pnl = 0.0
        loss_pnl = 0.0
        largest_win = 0.0
        largest_loss = 0.0
        
        for trade in trades:
            if trade.pnl is not None:
                total_pnl += trade.pnl
                
                if trade.pnl > 0:
                    winning_trades += 1
                    win_pnl += trade.pnl
                    largest_win = max(largest_win, trade.pnl)
                elif trade.pnl < 0:
                    losing_trades += 1
                    loss_pnl += abs(trade.pnl)
                    largest_loss = max(largest_loss, abs(trade.pnl))
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        average_win = win_pnl / winning_trades if winning_trades > 0 else 0.0
        average_loss = loss_pnl / losing_trades if losing_trades > 0 else 0.0
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else (1.0 if win_pnl > 0 else 0.0)
        
        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "average_win": average_win,
            "average_loss": average_loss,
            "profit_factor": profit_factor,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
        }
