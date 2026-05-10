#!/usr/bin/env python3
"""
Quick demo of parameter sweep combination generation.

This demonstrates the core logic without requiring dependencies.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def demo_one_at_a_time():
    """Demo one-at-a-time sweep logic."""
    print("=" * 80)
    print("ONE-AT-A-TIME SWEEP MODE")
    print("=" * 80)
    
    # Define parameters
    params = [
        {"name": "ORB_Duration", "values": [5, 10, 15], "base": 5},
        {"name": "TP_Multiplier", "values": [1.5, 2.0, 3.0], "base": 2.0},
        {"name": "Risk_Pct", "values": [0.005, 0.01, 0.02], "base": 0.01},
    ]
    
    print("\nParameters:")
    for p in params:
        print(f"  {p['name']}: {p['values']} (base={p['base']})")
    
    # Generate combinations
    combinations = []
    
    # Base combination
    base_combo = {p["name"]: p["base"] for p in params}
    combinations.append(base_combo)
    
    # Vary each parameter
    for param in params:
        for value in param["values"]:
            if value == param["base"]:
                continue  # Skip base value
            
            combo = base_combo.copy()
            combo[param["name"]] = value
            combinations.append(combo)
    
    print(f"\nTotal combinations: {len(combinations)}")
    print("\nAll tests:")
    for i, combo in enumerate(combinations, 1):
        marker = " ← BASE" if i == 1 else ""
        print(f"  {i}. {combo}{marker}")
    
    print(f"\n✓ Efficiency: {len(combinations)} tests instead of {3*3*3} (grid)")


def demo_grid_search():
    """Demo grid search logic."""
    print("\n" + "=" * 80)
    print("GRID SEARCH MODE")
    print("=" * 80)
    
    # Simpler example for grid
    params = [
        {"name": "ORB_Duration", "values": [5, 10]},
        {"name": "TP_Multiplier", "values": [2.0, 3.0]},
    ]
    
    print("\nParameters:")
    for p in params:
        print(f"  {p['name']}: {p['values']}")
    
    # Generate Cartesian product
    import itertools
    
    param_names = [p["name"] for p in params]
    value_lists = [p["values"] for p in params]
    
    combinations = []
    for values in itertools.product(*value_lists):
        combo = dict(zip(param_names, values))
        combinations.append(combo)
    
    print(f"\nTotal combinations: {len(combinations)} (2 × 2 = 4)")
    print("\nAll tests:")
    for i, combo in enumerate(combinations, 1):
        print(f"  {i}. {combo}")
    
    print("\n✓ Tests all combinations!")


def main():
    print("\n" + "=" * 80)
    print("PARAMETER SWEEP - COMBINATION GENERATION DEMO")
    print("=" * 80)
    
    demo_one_at_a_time()
    demo_grid_search()
    
    print("\n" + "=" * 80)
    print("✓ DEMO COMPLETE")
    print("=" * 80)
    print("\nTo run actual backtests:")
    print("  python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --mode quick")
    print()


if __name__ == "__main__":
    main()
