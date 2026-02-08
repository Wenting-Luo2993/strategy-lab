"""
Abstract base class for validation rules.

Defines interface for pluggable validation rules used by MTF validator
to filter trading signals based on multi-timeframe confluence.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation rule check."""

    passed: bool = field(default=False)
    score: float = field(default=0.0)  # Score from 0.0 to 100.0
    rule_name: str = field(default="")
    reason: str = field(default="")
    details: Dict[str, Any] = field(default_factory=dict)
    weight: float = field(default=1.0)

    def __post_init__(self):
        """Validate score range."""
        if not 0.0 <= self.score <= 100.0:
            raise ValueError(f"Score must be between 0.0 and 100.0, got {self.score}")


class ValidationRule(ABC):
    """
    Abstract base class for all validation rules.

    Validation rules check trading signals against multi-timeframe
    confluence criteria and return a score indicating signal quality.
    """

    def __init__(self, weight: float = 1.0):
        """
        Initialize validation rule.

        Args:
            weight: Weight of this rule in the overall validation score
        """
        self.weight = weight
        self.logger = logging.getLogger(f"vibe.validation.{self.__class__.__name__}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Rule name for logging and reporting."""
        pass

    @abstractmethod
    def validate(
        self,
        signal: int,
        symbol: str,
        timestamp: Any,
        mtf_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> ValidationResult:
        """
        Validate a trading signal.

        Args:
            signal: Trading signal (1 for long, -1 for short, 0 for neutral)
            symbol: Trading symbol
            timestamp: Timestamp of signal
            mtf_data: Multi-timeframe data {timeframe: DataFrame}
            **kwargs: Additional arguments specific to rule

        Returns:
            ValidationResult with passed status and score
        """
        pass

    def _get_latest_value(
        self,
        df: pd.DataFrame,
        column: str,
        default: Optional[float] = None,
    ) -> Optional[float]:
        """
        Get latest value from DataFrame column.

        Args:
            df: DataFrame
            column: Column name
            default: Default value if not found

        Returns:
            Latest value or default
        """
        if df.empty or column not in df.columns:
            return default

        value = df[column].iloc[-1]
        return value if pd.notna(value) else default

    def _get_latest_values(
        self,
        df: pd.DataFrame,
        column: str,
        count: int = 5,
    ) -> list:
        """
        Get latest N values from DataFrame column.

        Args:
            df: DataFrame
            column: Column name
            count: Number of recent values to retrieve

        Returns:
            List of values (empty if column not found)
        """
        if df.empty or column not in df.columns:
            return []

        return df[column].tail(count).dropna().tolist()
