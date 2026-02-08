"""
MTF Validator Orchestrator.

Runs all validation rules on trading signals and aggregates results
into weighted validation score for signal filtering.
"""

import logging
from typing import Dict, List, Tuple, Any, Optional
import pandas as pd

from vibe.common.validation.rules.base import ValidationRule, ValidationResult

logger = logging.getLogger(__name__)


class MTFValidator:
    """
    Orchestrates multiple validation rules for signal filtering.

    Runs all rules, collects results, and calculates weighted score.
    """

    def __init__(
        self,
        rules: Optional[List[ValidationRule]] = None,
        min_score: float = 60.0,
    ):
        """
        Initialize MTF Validator.

        Args:
            rules: List of ValidationRule instances to apply
            min_score: Minimum weighted score for signal to pass
        """
        self.rules = rules or []
        self.min_score = min_score
        self.logger = logging.getLogger("vibe.validation.mtf_validator")

    def add_rule(self, rule: ValidationRule) -> None:
        """Add a validation rule."""
        self.rules.append(rule)

    def validate(
        self,
        signal: int,
        symbol: str,
        timestamp: Any,
        mtf_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> Tuple[bool, float, List[ValidationResult]]:
        """
        Validate a trading signal against all rules.

        Args:
            signal: Trading signal (1 long, -1 short, 0 neutral)
            symbol: Trading symbol
            timestamp: Signal timestamp
            mtf_data: {timeframe: DataFrame} with OHLCV and indicators
            **kwargs: Additional arguments for rules

        Returns:
            Tuple of (passed, weighted_score, results)
            - passed: Boolean indicating if signal passes validation
            - weighted_score: Weighted validation score (0-100)
            - results: List of ValidationResult from each rule
        """
        if signal == 0:
            # Neutral signals always pass
            return True, 100.0, []

        # Run all rules
        results = []
        for rule in self.rules:
            try:
                result = rule.validate(
                    signal=signal,
                    symbol=symbol,
                    timestamp=timestamp,
                    mtf_data=mtf_data,
                    **kwargs,
                )
                results.append(result)
            except Exception as e:
                self.logger.error(f"Error in rule {rule.name}: {e}")
                # Create failing result on error
                results.append(
                    ValidationResult(
                        passed=False,
                        score=0.0,
                        rule_name=rule.name,
                        reason=f"Rule error: {str(e)}",
                        details={"error": str(e)},
                        weight=rule.weight,
                    )
                )

        # Calculate weighted score
        if not results:
            # No rules to validate against
            return True, 100.0, []

        weighted_score = self._calculate_weighted_score(results)
        passed = weighted_score >= self.min_score

        self.logger.debug(
            f"{symbol} signal {signal}: weighted_score={weighted_score:.1f} "
            f"(threshold={self.min_score}), passed={passed}"
        )

        return passed, weighted_score, results

    def _calculate_weighted_score(self, results: List[ValidationResult]) -> float:
        """
        Calculate weighted validation score from individual rule results.

        Args:
            results: List of ValidationResult from rules

        Returns:
            Weighted score (0-100)
        """
        if not results:
            return 100.0

        # Calculate total weight
        total_weight = sum(result.weight for result in results)

        if total_weight == 0:
            # Equal weighting if all weights are 0
            return sum(result.score for result in results) / len(results)

        # Calculate weighted sum
        weighted_sum = sum(result.score * result.weight for result in results)
        weighted_score = weighted_sum / total_weight

        return weighted_score

    def get_rule_results_summary(self, results: List[ValidationResult]) -> Dict[str, Any]:
        """
        Get summary of validation rule results.

        Args:
            results: List of ValidationResult

        Returns:
            Dict with summary information
        """
        if not results:
            return {"total_rules": 0, "passed_rules": 0, "rules": []}

        passed = sum(1 for r in results if r.passed)

        summary = {
            "total_rules": len(results),
            "passed_rules": passed,
            "failed_rules": len(results) - passed,
            "weighted_score": self._calculate_weighted_score(results),
            "rules": [
                {
                    "name": r.rule_name,
                    "passed": r.passed,
                    "score": r.score,
                    "weight": r.weight,
                    "reason": r.reason,
                    "details": r.details,
                }
                for r in results
            ],
        }

        return summary

    def set_min_score(self, min_score: float) -> None:
        """
        Update minimum passing score.

        Args:
            min_score: New minimum score (0-100)
        """
        if not 0.0 <= min_score <= 100.0:
            raise ValueError(f"min_score must be between 0 and 100, got {min_score}")
        self.min_score = min_score

    def clear_rules(self) -> None:
        """Clear all validation rules."""
        self.rules.clear()
