"""
Example: Parameter Sweep Integration with Research Journal

Shows how to automatically track optimization iterations with lineage.
"""

from datetime import datetime
from pathlib import Path
from vibe.backtester.analysis.parameter_sweep import ParameterSweep, ParameterDefinition
from vibe.backtester.integration.sweep_tracker import (
    ParameterSweepExperimentTracker,
    SweepResultExperimentLinker
)
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.query import ExperimentQuery
from vibe.research_journal.models import ExperimentStatus


def example_parameter_sweep_with_lineage():
    """Example: Run parameter sweep with experiment lineage tracking."""
    
    # 1. Setup Research Journal
    registry = ResearchRegistry()
    
    # 2. Create hypothesis
    hyp = registry.create_hypothesis(
        title="Optimize ORB parameters for QQQ",
        rationale="Find best ORB duration and take-profit ratio combination",
        tags=["orb", "optimization", "parameter_tuning"]
    )
    print(f"✓ Created hypothesis {hyp.id}")
    
    # 3. Setup parameter sweep with experiment tracking
    tracker = ParameterSweepExperimentTracker(
        registry=registry,
        hypothesis_id=hyp.id
    )
    
    # 4. Create parent experiment for sweep
    parent_exp_id = tracker.create_parent_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.4.2",
        base_parameters={
            "orb_duration_minutes": 5,
            "take_profit_atr_multiplier": 2.0
        },
        dataset_config={
            "symbols": ["QQQ"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31"
        },
        tags=["sweep_2026", "optimization"]
    )
    print(f"✓ Created parent experiment {parent_exp_id}")
    
    # 5. Define parameters to sweep
    parameters = [
        ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15]),
        ParameterDefinition("exit.take_profit.multiplier", [1.5, 2.0, 2.5]),
    ]
    
    # 6. Run parameter sweep
    sweep = ParameterSweep(
        base_ruleset_path=Path("vibe/rulesets/orb_production.yaml"),
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        initial_capital=100_000,
        slippage_ticks=2
    )
    
    print("\n⏳ Running parameter sweep (9 variations)...")
    results = sweep.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 12, 31)
    )
    
    # 7. For each result, create child experiment
    print("\n📊 Creating child experiments for each variation...")
    for idx, row in results.iterrows():
        # Extract parameters for this variation
        var_num = idx + 1
        
        # Create child experiment
        var_exp_id = tracker.create_variation_experiment(
            variation_number=var_num,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            variation_parameters={
                "orb_duration_minutes": row['orb_duration_minutes'],
                "take_profit_atr_multiplier": row['take_profit_multiplier']
            },
            dataset_config={
                "symbols": ["QQQ"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31"
            }
        )
        
        # Complete with metrics
        tracker.complete_variation_experiment(
            experiment_id=var_exp_id,
            trades=row.get('trades', []),
            metrics={
                "sharpe_ratio": row['sharpe'],
                "expectancy_r": row['expectancy'],
                "total_pnl": row['pnl'],
                "win_rate": row['win_rate'],
                "num_trades": row['num_trades']
            },
            rank=row['rank'],
            conclusion=f"ORB {row['orb_duration_minutes']}m, TP {row['take_profit_multiplier']}"
        )
        
        print(f"  ✓ Variation {var_num}: ORB {row['orb_duration_minutes']}m, "
              f"TP {row['take_profit_multiplier']} → Rank #{row['rank']}")
    
    # 8. Link sweep results
    linker = SweepResultExperimentLinker(registry)
    linker.link_sweep_results(parent_exp_id, results)
    print(f"\n✓ Linked all sweep results")
    
    # 9. View lineage
    print("\n🔗 Experiment Lineage:")
    lineage = registry.get_lineage_graph()
    
    # Parent
    parent_exp = registry.get_experiment(parent_exp_id)
    print(f"  Parent: {parent_exp_id}")
    print(f"    Status: {parent_exp.status.value}")
    print(f"    Parameters: {parent_exp.parameters}")
    
    # Children
    children = lineage.get_children(parent_exp_id)
    print(f"\n  Children ({len(children)} variations):")
    for child_id in children[:3]:  # Show first 3
        child = registry.get_experiment(child_id)
        metrics = child.results_summary or {}
        print(f"    - {child_id}: Sharpe={metrics.get('sharpe_ratio', 'N/A'):.2f}, "
              f"Expectancy={metrics.get('expectancy_r', 'N/A'):.3f}R")
    
    # 10. Query best result
    print("\n🏆 Finding best result:")
    best_query = ExperimentQuery(registry)
    best_exps = (best_query
                 .by_hypothesis(hyp.id)
                 .by_status(ExperimentStatus.COMPLETED)
                 .execute())
    
    if best_exps:
        # Find with highest Sharpe
        best = max(best_exps, 
                   key=lambda e: e.results_summary.get('sharpe_ratio', 0) 
                   if e.results_summary else 0)
        
        print(f"  Best variation: {best.id}")
        print(f"    ORB Duration: {best.parameters['orb_duration_minutes']}m")
        print(f"    Take Profit: {best.parameters['take_profit_atr_multiplier']}")
        print(f"    Sharpe: {best.results_summary['sharpe_ratio']:.2f}")
        print(f"    Expectancy: {best.results_summary['expectancy_r']:.3f}R")
    
    return results


if __name__ == "__main__":
    example_parameter_sweep_with_lineage()
