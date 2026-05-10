#!/usr/bin/env python3
"""
Example: How to use the parameter sensitivity framework.

This demonstrates the framework usage without requiring actual backtest data.
"""
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.analysis import ParameterDefinition, ParameterSweep


def example_basic_usage():
    """Basic usage example."""
    print("=" * 80)
    print("EXAMPLE 1: One-at-a-Time Sweep (Quick Mode)")
    print("=" * 80)
    
    # Define parameters to test
    parameters = [
        ParameterDefinition(
            path="strategy.orb_duration_minutes",
            values=[5, 10, 15],
            base_value=5,
            name="ORB_Duration",
        ),
        ParameterDefinition(
            path="exit.take_profit.multiplier",
            values=[1.5, 2.0, 3.0],
            base_value=2.0,
            name="TP_Multiplier",
        ),
        ParameterDefinition(
            path="position_size.value",
            values=[0.005, 0.01, 0.02],
            base_value=0.01,
            name="Risk_Pct",
        ),
    ]
    
    # Create sweep instance (one-at-a-time mode)
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        initial_capital=10_000.0,
        slippage_ticks=5,
        sweep_mode="one_at_a_time",
    )
    
    # Show what combinations will be tested
    combinations = sweep._generate_combinations()
    
    print(f"\nBase ruleset: {sweep.base_ruleset_path}")
    print(f"Parameters: {[p.name for p in parameters]}")
    print(f"Sweep mode: {sweep.sweep_mode}")
    print(f"Total combinations: {len(combinations)}")
    print(f"\nAll combinations:")
    for i, combo in enumerate(combinations, 1):
        is_base = i == 1
        marker = " (BASE)" if is_base else ""
        print(f"  {i}. {combo}{marker}")
    
    print("\n✓ One-at-a-time mode: varies one parameter at a time!")


def example_grid_search():
    """Example: Grid search mode."""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Grid Search (Full Mode)")
    print("=" * 80)
    
    # Same parameters but fewer values for demonstration
    parameters = [
        ParameterDefinition(
            path="strategy.orb_duration_minutes",
            values=[5, 10],
            base_value=5,
            name="ORB_Duration",
        ),
        ParameterDefinition(
            path="exit.take_profit.multiplier",
            values=[2.0, 3.0],
            base_value=2.0,
            name="TP_Multiplier",
        ),
    ]
    
    # Create sweep with grid mode
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        sweep_mode="grid",
    )
    
    combinations = sweep._generate_combinations()
    
    print(f"\nParameters: {[p.name for p in parameters]}")
    print(f"Sweep mode: {sweep.sweep_mode}")
    print(f"Total combinations: {len(combinations)} (2 × 2 = 4)")
    print(f"\nAll combinations:")
    for i, combo in enumerate(combinations, 1):
        print(f"  {i}. {combo}")
    
    print("\n✓ Grid mode: tests all combinations!")


def example_comparison():
    """Compare sweep modes."""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Comparison of Sweep Modes")
    print("=" * 80)
    
    parameters = [
        ParameterDefinition("param1", [1, 2, 3], base_value=2, name="P1"),
        ParameterDefinition("param2", [10, 20, 30], base_value=20, name="P2"),
        ParameterDefinition("param3", [100, 200], base_value=100, name="P3"),
    ]
    
    # One-at-a-time
    sweep_oat = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        sweep_mode="one_at_a_time",
    )
    combos_oat = sweep_oat._generate_combinations()
    
    # Grid
    sweep_grid = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        sweep_mode="grid",
    )
    combos_grid = sweep_grid._generate_combinations()
    
    print(f"\nWith 3 parameters:")
    print(f"  P1: {len(parameters[0].values)} values")
    print(f"  P2: {len(parameters[1].values)} values")
    print(f"  P3: {len(parameters[2].values)} values")
    print(f"\nOne-at-a-time: {len(combos_oat)} tests")
    print(f"  (base + (3-1) + (3-1) + (2-1) = 1 + 2 + 2 + 1 = 6)")
    print(f"\nGrid search: {len(combos_grid)} tests")
    print(f"  (3 × 3 × 2 = 18)")
    print(f"\nEfficiency: {len(combos_grid) / len(combos_oat):.1f}x more tests in grid mode")
    print("\n✓ Use one-at-a-time for quick parameter exploration!")


def example_nested_parameters():
    """Example with deeply nested parameters."""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Nested Parameter Paths")
    print("=" * 80)
    
    # Test nested parameter modification
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("strategy.orb_duration_minutes", [10]),
        ],
    )
    
    # Show how nested paths work
    test_cases = [
        ("strategy.orb_duration_minutes", 15),
        ("exit.take_profit.multiplier", 2.5),
        ("position_size.value", 0.015),
        ("trade_filter.volume_threshold", 1.8),
    ]
    
    print("\nDemonstrating nested path modification:")
    for path, value in test_cases:
        config = {}
        sweep._set_nested_value(config, path, value)
        print(f"\n  Path: {path}")
        print(f"  Value: {value}")
        print(f"  Result: {config}")


def example_custom_strategy():
    """Example: Adding parameters for a custom strategy."""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Custom Strategy Parameters")
    print("=" * 80)
    
    # Example: Mean reversion strategy parameters
    parameters = [
        ParameterDefinition(
            path="strategy.lookback_periods",
            values=[10, 20, 30, 50],
            name="Lookback",
        ),
        ParameterDefinition(
            path="strategy.entry_std_threshold",
            values=[1.5, 2.0, 2.5],
            name="Entry_StdDev",
        ),
        ParameterDefinition(
            path="strategy.exit_std_threshold",
            values=[0.5, 1.0],
            name="Exit_StdDev",
        ),
    ]
    
    print("\nCustom Strategy: Mean Reversion")
    print(f"Parameters: {[p.name for p in parameters]}")
    
    total = 1
    for p in parameters:
        total *= len(p.values)
        print(f"  - {p.name}: {len(p.values)} values -> {p.values}")
    
    print(f"\nTotal combinations: {total}")
    print("\n✓ Custom strategy parameters defined!")


def main():
    """Run all examples."""
    print("\n" + "=" * 80)
    print("PARAMETER SENSITIVITY FRAMEWORK - USAGE EXAMPLES")
    print("=" * 80)
    
    try:
        example_basic_usage()
        example_grid_search()
        example_comparison()
        example_nested_parameters()
        example_custom_strategy()
        
        print("\n" + "=" * 80)
        print("ALL EXAMPLES COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        print("\nTo run actual backtests with parameter sweep:")
        print("  # One-at-a-time (quick)")
        print("  python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --mode quick")
        print("\n  # Grid search (comprehensive)")
        print("  python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --mode full")
        print()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
