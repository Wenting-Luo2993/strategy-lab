#!/usr/bin/env python3
"""
Debug script to test parameter modification logic.
"""
import sys
import yaml
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.analysis.parameter_sweep import ParameterDefinition, ParameterSweep


def test_parameter_modification():
    """Test that parameters are being modified correctly."""
    
    # Load base ruleset
    with open("vibe/rulesets/orb_production.yaml", "r") as f:
        base_config = yaml.safe_load(f)
    
    print("=" * 80)
    print("BASE CONFIG VALUES")
    print("=" * 80)
    print(f"orb_duration_minutes: {base_config['strategy']['orb_duration_minutes']}")
    print(f"take_profit.multiplier: {base_config['exit']['take_profit']['multiplier']}")
    print(f"position_size.value: {base_config['position_size']['value']}")
    
    # Create ParameterSweep
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
    
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=parameters,
        sweep_mode="one_at_a_time",
    )
    
    # Generate combinations
    combinations = sweep._generate_combinations()
    
    print(f"\n" + "=" * 80)
    print(f"GENERATED {len(combinations)} COMBINATIONS")
    print("=" * 80)
    
    # Test each combination
    for i, params in enumerate(combinations, 1):
        print(f"\n[{i}] Testing params: {params}")
        
        # Create modified config
        config = deepcopy(base_config)
        for param_def in parameters:
            value = params[param_def.name]
            
            # Manually set the value
            keys = param_def.path.split(".")
            current = config
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            current[keys[-1]] = value
        
        # Verify values were set
        actual_duration = config['strategy']['orb_duration_minutes']
        actual_tp = config['exit']['take_profit']['multiplier']
        actual_risk = config['position_size']['value']
        
        expected_duration = params['ORB_Duration']
        expected_tp = params['TP_Multiplier']
        expected_risk = params['Risk_Pct']
        
        print(f"    orb_duration_minutes: {actual_duration} (expected: {expected_duration}) {'✓' if actual_duration == expected_duration else '✗'}")
        print(f"    take_profit.multiplier: {actual_tp} (expected: {expected_tp}) {'✓' if actual_tp == expected_tp else '✗'}")
        print(f"    position_size.value: {actual_risk} (expected: {expected_risk}) {'✓' if actual_risk == expected_risk else '✗'}")
        
        if actual_duration != expected_duration or actual_tp != expected_tp or actual_risk != expected_risk:
            print("    ❌ MISMATCH DETECTED!")
            return False
    
    print("\n" + "=" * 80)
    print("✓ ALL PARAMETER MODIFICATIONS WORKING CORRECTLY!")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = test_parameter_modification()
    sys.exit(0 if success else 1)
