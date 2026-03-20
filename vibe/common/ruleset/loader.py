"""Loader for strategy rulesets from YAML files."""

import logging
import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import ValidationError

from vibe.common.ruleset.models import StrategyRuleSet

logger = logging.getLogger(__name__)


class RuleSetLoader:
    """Load and manage strategy rulesets from YAML files."""

    # Base directory for all rulesets (can be overridden via RULESET_DIR env var)
    RULESETS_DIR = Path(
        os.environ.get("RULESET_DIR", Path(__file__).parent.parent.parent / "rulesets")
    )

    @classmethod
    def from_name(cls, name: str) -> StrategyRuleSet:
        """Load a ruleset by name.

        Args:
            name: Ruleset name (without .yaml extension)

        Returns:
            StrategyRuleSet instance

        Raises:
            FileNotFoundError: If ruleset file not found
            ValueError: If ruleset is invalid
        """
        ruleset_path = cls.RULESETS_DIR / f"{name}.yaml"

        if not ruleset_path.exists():
            raise FileNotFoundError(
                f"Ruleset '{name}' not found at {ruleset_path}\n"
                f"Available rulesets directory: {cls.RULESETS_DIR}"
            )

        return cls.from_yaml(ruleset_path)

    @classmethod
    def from_yaml(cls, path: Path) -> StrategyRuleSet:
        """Load a ruleset from a YAML file.

        Args:
            path: Path to YAML file

        Returns:
            StrategyRuleSet instance

        Raises:
            FileNotFoundError: If file not found
            ValueError: If YAML is invalid
            ValidationError: If validation fails
        """
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)

            if data is None:
                raise ValueError("YAML file is empty")

            # Validate and parse with Pydantic
            ruleset = StrategyRuleSet(**data)

            logger.info(
                f"Loaded ruleset: {ruleset.name} (v{ruleset.version}) from {path}"
            )
            return ruleset

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {path}: {e}")
        except ValidationError:
            # Propagate ValidationError as-is to preserve field-level details
            raise
        except FileNotFoundError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to load ruleset from {path}: {e}")

    @classmethod
    def from_yaml_str(cls, yaml_content: str) -> StrategyRuleSet:
        """Load a ruleset from YAML string (useful for testing).

        Args:
            yaml_content: YAML content as string

        Returns:
            StrategyRuleSet instance

        Raises:
            ValueError: If YAML is invalid
            ValidationError: If validation fails
        """
        try:
            data = yaml.safe_load(yaml_content)
            if data is None:
                raise ValueError("YAML content is empty")
            ruleset = StrategyRuleSet(**data)
            return ruleset
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}")
        except ValidationError:
            # Propagate ValidationError as-is to preserve field-level details
            raise
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to parse ruleset: {e}")

    @classmethod
    def list_available(cls) -> list[str]:
        """List all available rulesets.

        Returns:
            List of ruleset names (without .yaml extension)
        """
        if not cls.RULESETS_DIR.exists():
            return []

        return sorted(
            [f.stem for f in cls.RULESETS_DIR.glob("*.yaml") if f.is_file()]
        )
