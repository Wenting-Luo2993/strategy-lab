"""
Unit tests for validation components.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from vibe.common.validation.rules.base import ValidationRule, ValidationResult
from vibe.common.validation.rules.trend_alignment import TrendAlignmentRule
from vibe.common.validation.rules.volume_confirmation import VolumeConfirmationRule
from vibe.common.validation.mtf_validator import MTFValidator


class MockValidationRule(ValidationRule):
    """Mock validation rule for testing."""

    def __init__(self, score=100.0, passed=True, weight=1.0):
        super().__init__(weight)
        self.score = score
        self.passed = passed

    @property
    def name(self) -> str:
        return "mock_rule"

    def validate(self, signal, symbol, timestamp, mtf_data, **kwargs):
        return ValidationResult(
            passed=self.passed,
            score=self.score,
            rule_name=self.name,
            reason="Mock validation",
            weight=self.weight,
        )


class TestValidationRule:
    """Tests for ValidationRule base class."""

    def test_cannot_instantiate_abstract_base(self):
        """ValidationRule cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ValidationRule()

    def test_validation_result_creation(self):
        """Test ValidationResult creation."""
        result = ValidationResult(
            passed=True,
            score=85.0,
            rule_name="test_rule",
            reason="Test reason",
        )

        assert result.passed
        assert result.score == 85.0
        assert result.rule_name == "test_rule"

    def test_validation_result_score_bounds(self):
        """Test ValidationResult validates score bounds."""
        with pytest.raises(ValueError):
            ValidationResult(score=150.0)  # > 100

        with pytest.raises(ValueError):
            ValidationResult(score=-10.0)  # < 0


class TestTrendAlignmentRule:
    """Tests for TrendAlignmentRule."""

    def setup_method(self):
        """Setup test fixtures."""
        self.rule = TrendAlignmentRule(ema_period=20, required_alignment=2)

    def _create_htf_data(self, uptrend=True):
        """Create HTF data with trend."""
        timestamps = [datetime(2024, 1, 15, 9, 0) + timedelta(hours=i) for i in range(10)]

        if uptrend:
            closes = [100.0 + i * 1.0 for i in range(10)]
        else:
            closes = [100.0 - i * 1.0 for i in range(10)]

        opens = [c - 0.2 for c in closes]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        volumes = [1000000] * 10

        # Add EMA
        ema_col = f"EMA_{self.rule.ema_period}"
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            ema_col: [avg for avg in (closes[i] for i in range(10))],  # Simplified
        })

        # Set EMA to be aligned with trend
        if uptrend:
            df[ema_col] = df["close"] - 2.0  # Price above EMA (uptrend)
        else:
            df[ema_col] = df["close"] + 2.0  # Price below EMA (downtrend)

        return df

    def test_long_signal_uptrend_pass(self):
        """Test long signal passes when HTF aligned to uptrend."""
        mtf_data = {
            "15m": self._create_htf_data(uptrend=True),
            "1h": self._create_htf_data(uptrend=True),
        }

        result = self.rule.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert result.passed
        assert result.score == 100.0

    def test_long_signal_downtrend_fail(self):
        """Test long signal fails when HTF in downtrend."""
        mtf_data = {
            "15m": self._create_htf_data(uptrend=True),
            "1h": self._create_htf_data(uptrend=False),
        }

        result = self.rule.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert not result.passed
        assert result.score == 50.0  # Partial alignment

    def test_short_signal_downtrend_pass(self):
        """Test short signal passes when HTF aligned to downtrend."""
        mtf_data = {
            "15m": self._create_htf_data(uptrend=False),
            "1h": self._create_htf_data(uptrend=False),
        }

        result = self.rule.validate(
            signal=-1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert result.passed
        assert result.score == 100.0

    def test_neutral_signal_always_pass(self):
        """Test neutral signals always pass."""
        mtf_data = {}

        result = self.rule.validate(
            signal=0,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert result.passed
        assert result.score == 100.0

    def test_missing_htf_data(self):
        """Test handling missing HTF data."""
        result = self.rule.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert not result.passed


class TestVolumeConfirmationRule:
    """Tests for VolumeConfirmationRule."""

    def setup_method(self):
        """Setup test fixtures."""
        self.rule = VolumeConfirmationRule(volume_threshold=1.5, lookback_bars=20)

    def _create_volume_data(self, volume_ratio=1.5):
        """Create test data with specific volume ratio."""
        timestamps = [datetime(2024, 1, 15, 9, 0) + timedelta(minutes=5 * i) for i in range(25)]

        closes = [100.0 + np.random.randn() * 0.5 for _ in range(25)]
        opens = [c + np.random.randn() * 0.2 for c in closes]
        highs = [max(o, c) + 0.2 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 0.2 for o, c in zip(opens, closes)]

        # Create volume with high current volume
        avg_volume = 1000000
        volumes = [avg_volume] * 24
        volumes.append(int(avg_volume * volume_ratio))  # Current bar

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        return df

    def test_high_volume_pass(self):
        """Test validation passes with high volume."""
        df = self._create_volume_data(volume_ratio=2.0)

        mtf_data = {"5m": df}

        result = self.rule.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert result.passed
        assert result.score > 80.0

    def test_low_volume_fail(self):
        """Test validation fails with low volume."""
        df = self._create_volume_data(volume_ratio=0.8)

        mtf_data = {"5m": df}

        result = self.rule.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert not result.passed
        assert result.score < 50.0

    def test_neutral_signal_always_pass(self):
        """Test neutral signals always pass."""
        mtf_data = {}

        result = self.rule.validate(
            signal=0,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        assert result.passed


class TestMTFValidator:
    """Tests for MTFValidator orchestrator."""

    def setup_method(self):
        """Setup test fixtures."""
        self.validator = MTFValidator(min_score=60.0)

    def test_all_rules_pass(self):
        """Test when all rules pass."""
        rules = [
            MockValidationRule(score=100, passed=True, weight=0.4),
            MockValidationRule(score=100, passed=True, weight=0.3),
            MockValidationRule(score=100, passed=True, weight=0.3),
        ]

        validator = MTFValidator(rules=rules, min_score=60)

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert passed
        assert score == 100.0
        assert len(results) == 3

    def test_weighted_score_calculation(self):
        """Test weighted score calculation."""
        rules = [
            MockValidationRule(score=100, passed=True, weight=0.5),
            MockValidationRule(score=50, passed=False, weight=0.5),
        ]

        validator = MTFValidator(rules=rules, min_score=60)

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        expected_score = 75.0  # (100 * 0.5 + 50 * 0.5) / 1.0
        assert abs(score - expected_score) < 0.01

    def test_fails_below_threshold(self):
        """Test signal fails when below threshold."""
        rules = [
            MockValidationRule(score=40, passed=False, weight=1.0),
        ]

        validator = MTFValidator(rules=rules, min_score=60)

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert not passed
        assert score == 40.0

    def test_neutral_signal_always_pass(self):
        """Test neutral signals always pass."""
        validator = MTFValidator(rules=[], min_score=60)

        passed, score, results = validator.validate(
            signal=0,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert passed
        assert score == 100.0

    def test_add_rule(self):
        """Test adding rules dynamically."""
        validator = MTFValidator()
        assert len(validator.rules) == 0

        rule = MockValidationRule()
        validator.add_rule(rule)

        assert len(validator.rules) == 1

    def test_set_min_score(self):
        """Test updating minimum score."""
        validator = MTFValidator(min_score=60)

        passed, score, _ = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert passed  # No rules, score = 100

        # Test invalid score raises error
        with pytest.raises(ValueError):
            validator.set_min_score(150)  # > 100

        with pytest.raises(ValueError):
            validator.set_min_score(-10)  # < 0

    def test_get_results_summary(self):
        """Test getting validation results summary."""
        rules = [
            MockValidationRule(score=100, passed=True, weight=0.5),
            MockValidationRule(score=50, passed=False, weight=0.5),
        ]

        validator = MTFValidator(rules=rules)

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        summary = validator.get_rule_results_summary(results)

        assert summary["total_rules"] == 2
        assert summary["passed_rules"] == 1
        assert summary["failed_rules"] == 1
        assert abs(summary["weighted_score"] - 75.0) < 0.01

    def test_clear_rules(self):
        """Test clearing all rules."""
        validator = MTFValidator()
        validator.add_rule(MockValidationRule())
        validator.add_rule(MockValidationRule())

        assert len(validator.rules) == 2

        validator.clear_rules()
        assert len(validator.rules) == 0

    def test_rule_error_handling(self):
        """Test handling errors in rules."""

        class ErrorRule(ValidationRule):
            @property
            def name(self) -> str:
                return "error_rule"

            def validate(self, signal, symbol, timestamp, mtf_data, **kwargs):
                raise ValueError("Test error")

        validator = MTFValidator(rules=[ErrorRule()])

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data={},
        )

        assert not passed
        assert score == 0.0
        assert len(results) == 1
        assert "error" in results[0].details


class TestMTFValidatorIntegration:
    """Integration tests for MTF validator with real rules."""

    def test_orb_with_mtf_validation_pass(self):
        """Test ORB strategy with MTF validation."""
        # Create realistic HTF data
        timestamps = [datetime(2024, 1, 15, 9, 0) + timedelta(hours=i) for i in range(10)]

        # Uptrend data
        closes = [100.0 + i * 1.0 for i in range(10)]
        opens = [c - 0.2 for c in closes]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        volumes = [1000000 * (1.5 + i * 0.1) for i in range(10)]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "EMA_20": [c - 2.0 for c in closes],  # Price above EMA (uptrend)
        })

        mtf_data = {
            "15m": df,
            "1h": df,
        }

        # Create validator with real rules
        rules = [
            TrendAlignmentRule(ema_period=20, required_alignment=2, weight=0.4),
            VolumeConfirmationRule(volume_threshold=1.5, lookback_bars=10, weight=0.3),
        ]

        validator = MTFValidator(rules=rules, min_score=60)

        passed, score, results = validator.validate(
            signal=1,
            symbol="AAPL",
            timestamp=datetime.now(),
            mtf_data=mtf_data,
        )

        # Should pass with good conditions
        assert passed
        assert score >= 60.0
