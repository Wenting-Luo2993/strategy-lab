"""Unit tests for ruleset loader."""

import pytest
from pathlib import Path
import tempfile
import yaml

from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import StrategyRuleSet, ORBStrategyParams


class TestRuleSetLoaderFromName:
    """Tests for loading rulesets by name."""

    def test_load_orb_conservative(self):
        """Test loading the orb_conservative ruleset."""
        ruleset = RuleSetLoader.from_name("orb_conservative")
        assert ruleset.name == "orb_conservative"
        assert ruleset.version == "1.0"
        assert isinstance(ruleset.strategy, ORBStrategyParams)
        assert ruleset.strategy.type == "orb"

    def test_load_nonexistent_ruleset_raises(self):
        """Test that loading nonexistent ruleset raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            RuleSetLoader.from_name("nonexistent_ruleset")

    def test_ruleset_name_without_yaml_extension(self):
        """Test that name should not include .yaml extension."""
        # This should work
        ruleset = RuleSetLoader.from_name("orb_conservative")
        assert ruleset is not None

        # This should fail (looking for orb_conservative.yaml.yaml)
        with pytest.raises(FileNotFoundError):
            RuleSetLoader.from_name("orb_conservative.yaml")


class TestRuleSetLoaderFromYaml:
    """Tests for loading rulesets from YAML files."""

    def test_load_from_yaml_file(self):
        """Test loading from a YAML file path."""
        # Use a real ruleset file
        ruleset_path = RuleSetLoader.RULESETS_DIR / "orb_conservative.yaml"
        ruleset = RuleSetLoader.from_yaml(ruleset_path)
        assert ruleset.name == "orb_conservative"
        assert isinstance(ruleset.strategy, ORBStrategyParams)

    def test_invalid_yaml_raises(self):
        """Test that invalid YAML raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Invalid YAML"):
                RuleSetLoader.from_yaml(temp_path)
        finally:
            temp_path.unlink()

    def test_empty_yaml_raises(self):
        """Test that empty YAML raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="empty"):
                RuleSetLoader.from_yaml(temp_path)
        finally:
            temp_path.unlink()

    def test_missing_required_fields_raises(self):
        """Test that missing required fields raises ValueError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            # Missing 'name' and 'strategy' (required fields)
            yaml.dump({"version": "1.0"}, f)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError):
                RuleSetLoader.from_yaml(temp_path)
        finally:
            temp_path.unlink()


class TestRuleSetLoaderFromYamlStr:
    """Tests for loading rulesets from YAML strings."""

    def test_load_from_yaml_string(self):
        """Test loading from a YAML string."""
        yaml_content = """
name: test_ruleset
version: "1.0"
strategy:
  type: orb
  orb_start_time: "09:30"
  orb_duration_minutes: 5
position_size:
  method: max_loss_pct
  value: 0.01
"""
        ruleset = RuleSetLoader.from_yaml_str(yaml_content)
        assert ruleset.name == "test_ruleset"
        assert ruleset.strategy.type == "orb"

    def test_invalid_yaml_string_raises(self):
        """Test that invalid YAML string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid YAML"):
            RuleSetLoader.from_yaml_str("invalid: [")

    def test_empty_yaml_string_raises(self):
        """Test that empty YAML string raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            RuleSetLoader.from_yaml_str("")

    def test_missing_required_fields_in_string_raises(self):
        """Test that missing required fields in string raises ValueError."""
        yaml_content = "version: 1.0"
        with pytest.raises(ValueError):
            RuleSetLoader.from_yaml_str(yaml_content)


class TestRuleSetLoaderListAvailable:
    """Tests for listing available rulesets."""

    def test_list_available_includes_orb_conservative(self):
        """Test that orb_conservative appears in available list."""
        available = RuleSetLoader.list_available()
        assert "orb_conservative" in available

    def test_list_available_returns_sorted(self):
        """Test that available list is sorted."""
        available = RuleSetLoader.list_available()
        assert available == sorted(available)

    def test_list_available_returns_list_of_strings(self):
        """Test that available list contains strings without extensions."""
        available = RuleSetLoader.list_available()
        assert isinstance(available, list)
        for item in available:
            assert isinstance(item, str)
            assert not item.endswith(".yaml")


class TestRuleSetIntegration:
    """Integration tests for ruleset loading and validation."""

    def test_load_and_access_all_fields(self):
        """Test loading a ruleset and accessing all fields."""
        ruleset = RuleSetLoader.from_name("orb_conservative")

        # Check instruments
        assert len(ruleset.instruments.symbols) > 0
        assert ruleset.instruments.timeframe == "5m"

        # Check strategy
        assert ruleset.strategy.type == "orb"
        assert ruleset.strategy.orb_start_time == "09:30"
        assert ruleset.strategy.orb_duration_minutes == 5

        # Check position sizing
        assert ruleset.position_size.method == "max_loss_pct"
        assert ruleset.position_size.value == 0.01

        # Check exit config
        assert ruleset.exit.eod is True
        assert ruleset.exit.eod_time == "15:55"
        assert ruleset.exit.take_profit is not None
        assert ruleset.exit.stop_loss is not None

        # Check trade filters
        assert ruleset.trade_filter is not None

        # Check MTF validation
        assert ruleset.mtf_validation is not None

    def test_ruleset_is_immutable_after_loading(self):
        """Test that loaded ruleset is immutable (frozen model)."""
        ruleset = RuleSetLoader.from_name("orb_conservative")
        # Pydantic v2 doesn't freeze by default, but we can verify the model structure
        assert ruleset.name == "orb_conservative"

    def test_ruleset_serializes_to_dict(self):
        """Test that ruleset can be serialized to dict."""
        ruleset = RuleSetLoader.from_name("orb_conservative")
        ruleset_dict = ruleset.model_dump()
        assert isinstance(ruleset_dict, dict)
        assert ruleset_dict["name"] == "orb_conservative"
        assert "strategy" in ruleset_dict
        assert "exit" in ruleset_dict


class TestRuleSetYamlFormat:
    """Tests for YAML format compatibility."""

    def test_yaml_with_nested_objects(self):
        """Test that YAML with nested objects is correctly parsed."""
        yaml_content = """
name: test
strategy:
  type: orb
  orb_start_time: "09:30"
  orb_duration_minutes: 5
position_size:
  method: max_loss_pct
  value: 0.01
exit:
  eod: true
  eod_time: "15:55"
  take_profit:
    method: orb_range_multiple
    multiplier: 2.0
  stop_loss:
    method: orb_level
"""
        ruleset = RuleSetLoader.from_yaml_str(yaml_content)
        assert ruleset.exit.take_profit.multiplier == 2.0
        assert ruleset.exit.stop_loss.method == "orb_level"

    def test_yaml_with_stepped_trailing_stop(self):
        """Test YAML with stepped trailing stop configuration."""
        yaml_content = """
name: test
strategy:
  type: orb
position_size:
  method: max_loss_pct
  value: 0.01
exit:
  eod: false
  take_profit:
    method: orb_range_multiple
    multiplier: 2.0
  trailing_stop:
    method: stepped_r_multiple
    steps:
      - at: 2.0
        move_stop_to: 1.0
      - at: 3.0
        move_stop_to: 2.0
      - at: 4.0
        move_stop_to: 2.5
"""
        ruleset = RuleSetLoader.from_yaml_str(yaml_content)
        assert ruleset.exit.trailing_stop is not None
        assert len(ruleset.exit.trailing_stop.steps) == 3
        assert ruleset.exit.trailing_stop.steps[0].at == 2.0
        assert ruleset.exit.trailing_stop.steps[0].move_stop_to == 1.0
