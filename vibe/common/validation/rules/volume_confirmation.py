"""
Volume Confirmation Validation Rule.

Checks if trading volume confirms the breakout signal.
Volume must be above average and ideally increasing on higher timeframes.
"""

import logging
from typing import Dict, Any, Optional
import pandas as pd

from vibe.common.validation.rules.base import ValidationRule, ValidationResult

logger = logging.getLogger(__name__)


class VolumeConfirmationRule(ValidationRule):
    """
    Validates that volume confirms the trading signal.

    Checks if current bar volume is above average and volume trend is rising.
    """

    def __init__(
        self,
        volume_threshold: float = 1.5,
        lookback_bars: int = 20,
        check_trend: bool = True,
        weight: float = 0.3,
    ):
        """
        Initialize Volume Confirmation Rule.

        Args:
            volume_threshold: Current volume must be > average * threshold
            lookback_bars: Number of bars for calculating average volume
            check_trend: Whether to check if volume is increasing
            weight: Weight in overall validation score
        """
        super().__init__(weight)
        self.volume_threshold = volume_threshold
        self.lookback_bars = lookback_bars
        self.check_trend = check_trend

    @property
    def name(self) -> str:
        """Rule name."""
        return "volume_confirmation"

    def validate(
        self,
        signal: int,
        symbol: str,
        timestamp: Any,
        mtf_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> ValidationResult:
        """
        Validate volume confirmation.

        Args:
            signal: Trading signal (1 long, -1 short)
            symbol: Trading symbol
            timestamp: Signal timestamp
            mtf_data: {timeframe: DataFrame} with OHLCV
            **kwargs: Additional arguments

        Returns:
            ValidationResult with volume score
        """
        if signal == 0:
            return ValidationResult(
                passed=True,
                score=100.0,
                rule_name=self.name,
                reason="Neutral signal - no volume check needed",
            )

        # Use 5m data for volume check if available, otherwise use first available
        check_tf = "5m" if "5m" in mtf_data else next(iter(mtf_data.keys()), None)

        if check_tf is None or check_tf not in mtf_data:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="No data available for volume check",
            )

        df = mtf_data[check_tf]

        if df.empty or len(df) < 2:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="Insufficient data for volume check",
            )

        # Get current and average volume
        current_volume = self._get_latest_value(df, "volume")

        if current_volume is None or current_volume == 0:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="Invalid current volume",
            )

        # Calculate average volume from lookback period
        lookback_count = min(self.lookback_bars, len(df) - 1)
        avg_volume = df["volume"].iloc[-lookback_count:].mean()

        if avg_volume == 0:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="Average volume is zero",
            )

        # Calculate volume ratio
        volume_ratio = current_volume / avg_volume

        # Check if volume is above threshold
        volume_confirmed = volume_ratio >= self.volume_threshold

        details = {
            "current_volume": current_volume,
            "average_volume": avg_volume,
            "volume_ratio": volume_ratio,
            "volume_confirmed": volume_confirmed,
        }

        # Check volume trend if enabled
        volume_trend_score = 100.0
        if self.check_trend and len(df) >= 3:
            recent_volumes = df["volume"].iloc[-3:].values
            is_rising = recent_volumes[-1] >= recent_volumes[-2] and recent_volumes[-2] >= recent_volumes[-3]
            details["volume_trend_rising"] = is_rising

            if not is_rising:
                volume_trend_score = 50.0  # Partial score for non-rising volume

        # Calculate score
        if volume_confirmed:
            # Scale score based on how far above threshold
            score = min(100.0, volume_ratio / self.volume_threshold * volume_trend_score)
        else:
            # Partial score if close to threshold
            score = (volume_ratio / self.volume_threshold) * 50.0

        score = min(100.0, score)

        return ValidationResult(
            passed=volume_confirmed,
            score=score,
            rule_name=self.name,
            reason=f"Volume ratio {volume_ratio:.2f}x (threshold {self.volume_threshold}x)",
            details=details,
            weight=self.weight,
        )
