"""
Composite scoring functions for strategy optimization.

Provides multi-metric scoring with tail risk adjustments to capture convex
payoff structures (e.g., trend-following strategies with extreme tail dependence).
"""
import numpy as np
from typing import List, Dict, Any
from vibe.backtester.analysis.metrics import BacktestResult


def calculate_tail_ratio(r_multiples: List[float], percentile: float = 0.95) -> float:
    """
    Calculate tail ratio: (95th percentile return) / abs(5th percentile return).
    
    Higher values indicate positive skew (fat right tail).
    
    Args:
        r_multiples: List of R-multiple returns
        percentile: Upper percentile for tail measurement (default 0.95)
    
    Returns:
        Tail ratio (0 if insufficient data)
    """
    if len(r_multiples) < 10:  # Need minimum sample size
        return 0.0
    
    r_array = np.array(r_multiples)
    upper_tail = np.percentile(r_array, percentile * 100)
    lower_tail = abs(np.percentile(r_array, (1 - percentile) * 100))
    
    if lower_tail == 0:
        return 0.0
    
    return upper_tail / lower_tail


def composite_score(
    result: BacktestResult,
    weights: Dict[str, float] = None,
    min_trades: int = 30,
) -> float:
    """
    Calculate composite optimization score.
    
    Default formula (optimized for convex strategies):
        0.30 * sharpe_ratio
      + 0.20 * expectancy_r
      + 0.10 * tail_ratio
      + 0.20 * win_rate
      + 0.20 * profit_factor_normalized
    
    Args:
        result: BacktestResult from backtest
        weights: Optional custom weights for each component
        min_trades: Minimum trades required (penalize low sample size)
    
    Returns:
        Composite score (higher = better)
    """
    # Default weights (can be overridden)
    if weights is None:
        weights = {
            "sharpe": 0.30,
            "expectancy_r": 0.20,
            "tail_ratio": 0.10,
            "win_rate": 0.20,
            "profit_factor": 0.20,
        }
    
    metrics = result.overall
    equity = result.equity
    
    # Penalize strategies with too few trades
    if metrics.n_trades < min_trades:
        sample_penalty = metrics.n_trades / min_trades
    else:
        sample_penalty = 1.0
    
    # Component 1: Sharpe ratio (normalized to 0-1)
    # Assume Sharpe > 2.0 is excellent, cap at that
    sharpe_normalized = min(equity.sharpe_ratio / 2.0, 1.0)
    
    # Component 2: Expectancy in R-multiples
    # Assume expectancy > 0.5R is excellent, cap at 1.0
    expectancy_normalized = min(metrics.expectancy_r / 0.5, 1.0)
    
    # Component 3: Tail ratio (convexity measure)
    tail_ratio = calculate_tail_ratio(metrics.r_multiples)
    tail_normalized = min(tail_ratio / 3.0, 1.0)  # Ratio > 3 is excellent
    
    # Component 4: Win rate
    win_rate_normalized = metrics.win_rate
    
    # Component 5: Profit factor (normalized)
    # Calculate profit factor from trades
    wins = [t.pnl for t in result.trades if t.pnl > 0]
    losses = [t.pnl for t in result.trades if t.pnl < 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = 0.0
    
    # Normalize profit factor (assume > 2.0 is excellent)
    profit_factor_normalized = min(profit_factor / 2.0, 1.0)
    
    # Calculate weighted score
    score = (
        weights["sharpe"] * sharpe_normalized +
        weights["expectancy_r"] * expectancy_normalized +
        weights["tail_ratio"] * tail_normalized +
        weights["win_rate"] * win_rate_normalized +
        weights["profit_factor"] * profit_factor_normalized
    )
    
    # Apply sample size penalty
    score *= sample_penalty
    
    return score


def rank_results(
    results: List[BacktestResult],
    weights: Dict[str, float] = None,
    min_trades: int = 30,
) -> List[Dict[str, Any]]:
    """
    Rank a list of backtest results by composite score.
    
    Args:
        results: List of BacktestResult objects
        weights: Optional custom weights for scoring
        min_trades: Minimum trades threshold
    
    Returns:
        List of dicts with result + score, sorted descending by score
    """
    scored_results = []
    
    for result in results:
        score = composite_score(result, weights=weights, min_trades=min_trades)
        scored_results.append({
            "result": result,
            "score": score,
            "sharpe": result.equity.sharpe_ratio,
            "expectancy_r": result.overall.expectancy_r,
            "tail_ratio": calculate_tail_ratio(result.overall.r_multiples),
            "win_rate": result.overall.win_rate,
            "n_trades": result.overall.n_trades,
            "total_pnl": result.overall.total_pnl,
        })
    
    # Sort by score descending
    scored_results.sort(key=lambda x: x["score"], reverse=True)
    
    return scored_results


def score_breakdown(result: BacktestResult, weights: Dict[str, float] = None) -> Dict[str, float]:
    """
    Get detailed breakdown of composite score components.
    
    Useful for understanding which metrics are driving the score.
    
    Args:
        result: BacktestResult to analyze
        weights: Optional custom weights
    
    Returns:
        Dictionary with component scores and total
    """
    if weights is None:
        weights = {
            "sharpe": 0.30,
            "expectancy_r": 0.20,
            "tail_ratio": 0.10,
            "win_rate": 0.20,
            "profit_factor": 0.20,
        }
    
    metrics = result.overall
    equity = result.equity
    
    # Calculate normalized components
    sharpe_normalized = min(equity.sharpe_ratio / 2.0, 1.0)
    expectancy_normalized = min(metrics.expectancy_r / 0.5, 1.0)
    tail_ratio = calculate_tail_ratio(metrics.r_multiples)
    tail_normalized = min(tail_ratio / 3.0, 1.0)
    win_rate_normalized = metrics.win_rate
    
    wins = [t.pnl for t in result.trades if t.pnl > 0]
    losses = [t.pnl for t in result.trades if t.pnl < 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    profit_factor_normalized = min(profit_factor / 2.0, 1.0)
    
    return {
        "sharpe_raw": equity.sharpe_ratio,
        "sharpe_normalized": sharpe_normalized,
        "sharpe_contribution": weights["sharpe"] * sharpe_normalized,
        
        "expectancy_r_raw": metrics.expectancy_r,
        "expectancy_normalized": expectancy_normalized,
        "expectancy_contribution": weights["expectancy_r"] * expectancy_normalized,
        
        "tail_ratio_raw": tail_ratio,
        "tail_normalized": tail_normalized,
        "tail_contribution": weights["tail_ratio"] * tail_normalized,
        
        "win_rate_raw": metrics.win_rate,
        "win_rate_normalized": win_rate_normalized,
        "win_rate_contribution": weights["win_rate"] * win_rate_normalized,
        
        "profit_factor_raw": profit_factor,
        "profit_factor_normalized": profit_factor_normalized,
        "profit_factor_contribution": weights["profit_factor"] * profit_factor_normalized,
        
        "total_score": composite_score(result, weights=weights),
    }
