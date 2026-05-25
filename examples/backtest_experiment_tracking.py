"""
Example: Backtester Integration with Research Journal

Shows how to automatically track backtest experiments.
"""

from datetime import datetime
from pathlib import Path
from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.integration.experiment_tracker import wrap_backtest_engine
from vibe.research_journal.registry import ResearchRegistry
from vibe.common.ruleset.models import StrategyRuleSet
import yaml


def example_backtest_with_experiment_tracking():
    """Example: Run backtest and auto-track results in Research Journal."""
    
    # 1. Setup Research Journal
    registry = ResearchRegistry()
    
    # 2. Create hypothesis
    hyp = registry.create_hypothesis(
        title="Test ORB strategy on QQQ with 5-minute breaks",
        rationale="ORB shows edge in trending markets with volume confirmation",
        tags=["orb", "volume-based", "5min"]
    )
    print(f"✓ Created hypothesis {hyp.id}")
    
    # 3. Create experiment (before running backtest)
    exp = registry.create_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.4.2",
        parameters={
            "orb_duration_minutes": 5,
            "take_profit_atr_multiplier": 2.0,
            "stop_loss_atr_multiplier": 1.0
        },
        dataset_config={
            "symbols": ["QQQ"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31"
        },
        hypothesis_id=hyp.id,
        tags=["backtest", "production"]
    )
    print(f"✓ Created experiment {exp.id}")
    print(f"  Git commit: {exp.execution_metadata.git_commit[:8]}...")
    print(f"  Python: {exp.execution_metadata.python_version}")
    
    # 4. Load strategy configuration
    ruleset_path = Path("vibe/rulesets/orb_production.yaml")
    with open(ruleset_path) as f:
        ruleset_dict = yaml.safe_load(f)
    ruleset = StrategyRuleSet(**ruleset_dict)
    
    # 5. Setup backtester
    engine = BacktestEngine(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=100_000,
        slippage_ticks=2
    )
    
    # 6. Wrap with experiment tracking
    tracked_engine = wrap_backtest_engine(engine, registry)
    
    # 7. Run backtest (with experiment tracking)
    print("\n⏳ Running backtest...")
    result = tracked_engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31),
        experiment_id=exp.id  # This triggers experiment completion
    )
    
    # 8. Results automatically saved to research journal
    completed = registry.get_experiment(exp.id)
    print(f"\n✓ Backtest complete!")
    print(f"  Results:")
    print(f"    - Trades: {result.num_trades}")
    print(f"    - Win Rate: {result.win_rate:.1%}")
    print(f"    - Sharpe: {result.sharpe:.2f}")
    print(f"    - Expectancy: {result.expectancy:.3f}R")
    print(f"  Experiment status: {completed.status.value}")
    
    # 9. Add research note with observations
    note = registry.add_research_note(
        content=f"""
Backtest Results Summary:
- Strategy showed {result.win_rate:.1%} win rate over 1 year
- Sharpe ratio of {result.sharpe:.2f} indicates good risk-adjusted returns
- {result.num_trades} total trades across dataset
- Expectancy: {result.expectancy:.3f}R per trade

Observations:
- ORB edge strongest in trending markets (as expected)
- Volume confirmation filter reduced false signals by 20%
- Ready for out-of-sample validation on 2025 data
        """,
        related_experiment_id=exp.id,
        tags=["observation", "backtest_complete"]
    )
    print(f"\n✓ Added research note {note.id}")
    
    # 10. Discover results
    print("\n📊 Querying all completed experiments:")
    from vibe.research_journal.query import ExperimentQuery
    from vibe.research_journal.models import ExperimentStatus
    
    query = ExperimentQuery(registry)
    completed_exps = query.by_status(ExperimentStatus.COMPLETED).execute()
    print(f"  Found {len(completed_exps)} completed experiments")
    
    return result


if __name__ == "__main__":
    example_backtest_with_experiment_tracking()
