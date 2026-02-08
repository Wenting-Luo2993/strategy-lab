"""
Trend Alignment Validation Rule.

Checks if higher timeframe trends are aligned with the trading signal.
Long signals pass when price > EMA on both 15m and 1h.
Short signals pass when price < EMA on both 15m and 1h.
"""

import logging
from typing import Dict, Any, Optional
import pandas as pd

from vibe.common.validation.rules.base import ValidationRule, ValidationResult

logger = logging.getLogger(__name__)


class TrendAlignmentRule(ValidationRule):
    """
    Validates that higher timeframe trends align with signal direction.

    Checks if price is above/below EMA on 15m and 1h timeframes.
    """

    def __init__(
        self,
        ema_period: int = 20,
        required_alignment: int = 2,
        weight: float = 0.4,
    ):
        """
        Initialize Trend Alignment Rule.

        Args:
            ema_period: Period for EMA calculation
            required_alignment: Number of timeframes that must align
            weight: Weight in overall validation score
        """
        super().__init__(weight)
        self.ema_period = ema_period
        self.required_alignment = required_alignment

    @property
    def name(self) -> str:
        """Rule name."""
        return "trend_alignment"

    def validate(
        self,
        signal: int,
        symbol: str,
        timestamp: Any,
        mtf_data: Dict[str, pd.DataFrame],
        **kwargs,
    ) -> ValidationResult:
        """
        Validate trend alignment across timeframes.

        Args:
            signal: Trading signal (1 long, -1 short)
            symbol: Trading symbol
            timestamp: Signal timestamp
            mtf_data: {timeframe: DataFrame} with OHLCV and indicators
            **kwargs: Additional arguments

        Returns:
            ValidationResult with alignment score
        """
        if signal == 0:
            return ValidationResult(
                passed=True,
                score=100.0,
                rule_name=self.name,
                reason="Neutral signal - no alignment check needed",
            )

        # Check available timeframes
        timeframes = ["15m", "1h"]
        available_tfs = [tf for tf in timeframes if tf in mtf_data]

        if not available_tfs:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="No HTF data available",
                details={"available_timeframes": list(mtf_data.keys())},
            )

        # Get EMA column name
        ema_col = f"EMA_{self.ema_period}"

        alignments = []
        details = {}

        for tf in available_tfs:
            df = mtf_data[tf]

            if df.empty:
                details[tf] = "empty_dataframe"
                continue

            # Get latest price and EMA
            price = self._get_latest_value(df, "close")
            ema = self._get_latest_value(df, ema_col)

            if price is None or ema is None:
                details[tf] = f"missing_data: price={price}, ema={ema}"
                continue

            # Check alignment
            if signal == 1:  # Long
                aligned = price > ema
            else:  # Short
                aligned = price < ema

            alignments.append(aligned)
            details[tf] = {
                "price": price,
                "ema": ema,
                "aligned": aligned,
            }

        # Calculate score
        if not alignments:
            return ValidationResult(
                passed=False,
                score=0.0,
                rule_name=self.name,
                reason="Could not check any timeframes",
                details=details,
            )

        alignment_count = sum(alignments)
        score = (alignment_count / len(alignments)) * 100.0
        passed = alignment_count >= self.required_alignment

        reason = f"{alignment_count}/{len(alignments)} timeframes aligned for {'long' if signal == 1 else 'short'}"

        return ValidationResult(
            passed=passed,
            score=score,
            rule_name=self.name,
            reason=reason,
            details=details,
            weight=self.weight,
        )
