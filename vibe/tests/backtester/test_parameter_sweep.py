"""
Unit tests for parameter sensitivity testing framework.
"""
import pytest
from pathlib import Path

from vibe.backtester.analysis.parameter_sweep import ParameterDefinition, ParameterSweep


def test_parameter_definition():
    """Test ParameterDefinition creation."""
    param = ParameterDefinition(
        path="strategy.orb_duration_minutes",
        values=[5, 10, 15],
        name="ORB_Duration",
    )
    
    assert param.path == "strategy.orb_duration_minutes"
    assert param.values == [5, 10, 15]
    assert param.name == "ORB_Duration"


def test_parameter_definition_auto_name():
    """Test automatic name extraction from path."""
    param = ParameterDefinition(
        path="exit.take_profit.multiplier",
        values=[1.5, 2.0],
    )
    
    assert param.name == "multiplier"
    assert param.base_value == 1.5  # Defaults to first value


def test_parameter_definition_with_base():
    """Test parameter definition with explicit base value."""
    param = ParameterDefinition(
        path="strategy.orb_duration_minutes",
        values=[5, 10, 15],
        base_value=10,
        name="ORB_Duration",
    )
    
    assert param.base_value == 10
    assert param.name == "ORB_Duration"


def test_set_nested_value():
    """Test setting values in nested dictionaries."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("strategy.orb_duration_minutes", [5]),
        ],
    )
    
    config = {}
    sweep._set_nested_value(config, "strategy.orb_duration_minutes", 10)
    
    assert config == {"strategy": {"orb_duration_minutes": 10}}


def test_set_deeply_nested_value():
    """Test setting deeply nested values."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("exit.take_profit.multiplier", [2.0]),
        ],
    )
    
    config = {"exit": {"eod": True}}
    sweep._set_nested_value(config, "exit.take_profit.multiplier", 3.0)
    
    assert config == {
        "exit": {
            "eod": True,
            "take_profit": {"multiplier": 3.0},
        }
    }


def test_generate_combinations():
    """Test Cartesian product generation."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("param1", [1, 2], name="P1"),
            ParameterDefinition("param2", [10, 20], name="P2"),
        ],
    )
    
    combinations = sweep._generate_combinations()
    
    assert len(combinations) == 4
    assert {"P1": 1, "P2": 10} in combinations
    assert {"P1": 1, "P2": 20} in combinations
    assert {"P1": 2, "P2": 10} in combinations
    assert {"P1": 2, "P2": 20} in combinations


def test_generate_combinations_one_at_a_time():
    """Test one-at-a-time combination generation."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("param1", [1, 2, 3], base_value=2, name="P1"),
            ParameterDefinition("param2", [10, 20, 30], base_value=20, name="P2"),
        ],
        sweep_mode="one_at_a_time",
    )
    
    combinations = sweep._generate_combinations()
    
    # Should have: base + (3-1) + (3-1) = 1 + 2 + 2 = 5 tests
    # (Skip base values for each parameter to avoid duplicates)
    assert len(combinations) == 5
    
    # First should be base
    assert combinations[0] == {"P1": 2, "P2": 20}
    
    # Then variations of P1 (keeping P2 at base)
    assert {"P1": 1, "P2": 20} in combinations
    assert {"P1": 3, "P2": 20} in combinations
    
    # Then variations of P2 (keeping P1 at base)
    assert {"P1": 2, "P2": 10} in combinations
    assert {"P1": 2, "P2": 30} in combinations


def test_generate_combinations_grid():
    """Test grid (Cartesian product) combination generation."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("param1", [1, 2], base_value=1, name="P1"),
            ParameterDefinition("param2", [10, 20], base_value=10, name="P2"),
        ],
        sweep_mode="grid",
    )
    
    combinations = sweep._generate_combinations()
    
    # 2 * 2 = 4 combinations
    assert len(combinations) == 4
    assert {"P1": 1, "P2": 10} in combinations
    assert {"P1": 1, "P2": 20} in combinations
    assert {"P1": 2, "P2": 10} in combinations
    assert {"P1": 2, "P2": 20} in combinations


def test_generate_combinations():
    """Test combinations with three parameters (backward compat test)."""
    sweep = ParameterSweep(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        parameters=[
            ParameterDefinition("param1", [1, 2, 3], name="P1"),
            ParameterDefinition("param2", [10, 20], name="P2"),
            ParameterDefinition("param3", [100], name="P3"),
        ],
        sweep_mode="grid",
    )
    
    combinations = sweep._generate_combinations()
    
    # 3 * 2 * 1 = 6 combinations
    assert len(combinations) == 6
    
    # All should have P3=100
    assert all(c["P3"] == 100 for c in combinations)
    
    # Check a few specific combinations
    assert {"P1": 1, "P2": 10, "P3": 100} in combinations
    assert {"P1": 3, "P2": 20, "P3": 100} in combinations


# ---------------------------------------------------------------------------
# Inert sweep axes
#
# A parameter read only behind a disabled feature flag scores identically at
# every value. Reporting that as "no sensitivity" reads as evidence the knob
# does not matter, when in fact it was never consulted.
# ---------------------------------------------------------------------------

import yaml as _yaml

from vibe.backtester.analysis.parameter_sweep import ParameterDefinition
from vibe.backtester.analysis.sensitivity_runner import (
    drop_inert_parameters,
    get_base_ruleset_for_strategy,
    get_parameters_for_strategy,
)


def _ruleset(tmp_path, confirmation):
    path = tmp_path / "rs.yaml"
    path.write_text(
        _yaml.safe_dump(
            {"trade_filter": {"volume_confirmation": confirmation, "volume_threshold": 1.5}}
        ),
        encoding="utf-8",
    )
    return path


def _gated():
    return ParameterDefinition(
        path="trade_filter.volume_threshold",
        values=[1.0, 2.0],
        name="Volume_Threshold",
        requires="trade_filter.volume_confirmation",
    )


def _ungated():
    return ParameterDefinition(
        path="strategy.orb_duration_minutes", values=[5, 15], name="ORB_Duration"
    )


def test_gated_axis_dropped_when_flag_disabled(tmp_path):
    live = drop_inert_parameters([_gated(), _ungated()], _ruleset(tmp_path, False))
    assert [p.name for p in live] == ["ORB_Duration"]


def test_gated_axis_kept_when_flag_enabled(tmp_path):
    """The inverse direction, so a filter that drops everything cannot pass."""
    live = drop_inert_parameters([_gated(), _ungated()], _ruleset(tmp_path, True))
    assert [p.name for p in live] == ["Volume_Threshold", "ORB_Duration"]


def test_missing_gate_path_treated_as_disabled(tmp_path):
    path = tmp_path / "rs.yaml"
    path.write_text(_yaml.safe_dump({"strategy": {}}), encoding="utf-8")
    assert drop_inert_parameters([_gated()], path) == []


def test_volume_threshold_is_gated_in_the_shipped_orb_definitions():
    """Guards the tagging itself, not just the filter that reads it."""
    for mode in ("quick", "full"):
        by_name = {p.name: p for p in get_parameters_for_strategy("orb", mode)}
        assert by_name["Volume_Threshold"].requires == "trade_filter.volume_confirmation"


def test_shipped_orb_sweep_drops_volume_threshold_today():
    """orb_production.yaml disables volume confirmation, so the axis is inert."""
    base = get_base_ruleset_for_strategy("orb")
    live = drop_inert_parameters(get_parameters_for_strategy("orb", "quick"), base)
    assert "Volume_Threshold" not in {p.name for p in live}
    assert "EOD_Time" in {p.name for p in live}
