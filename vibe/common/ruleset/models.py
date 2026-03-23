"""Pydantic models for strategy ruleset configuration."""

from typing import Optional, List, Literal, Union, Annotated
from pydantic import BaseModel, Field, model_validator, ConfigDict


# ============================================================================
# Helper Functions
# ============================================================================

def _validate_atr_params(multiplier: float, period: int, cls_name: str) -> None:
    """Validate ATR parameters (multiplier and period).

    Args:
        multiplier: ATR multiplier value
        period: ATR period value
        cls_name: Class name for error messages

    Raises:
        ValueError: If multiplier or period is not positive
    """
    if multiplier <= 0:
        raise ValueError(f"{cls_name}: multiplier must be positive, got {multiplier}")
    if period <= 0:
        raise ValueError(f"{cls_name}: period must be positive, got {period}")


# ============================================================================
# Take Profit Configurations
# ============================================================================

class OrbRangeMultipleTakeProfit(BaseModel):
    """Take profit based on ORB range multiple."""

    method: Literal["orb_range_multiple"] = "orb_range_multiple"
    multiplier: float = Field(default=2.0, description="TP = ORB_Range * multiplier")

    @model_validator(mode="after")
    def multiplier_positive(self) -> "OrbRangeMultipleTakeProfit":
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")
        return self


class ATRMultipleTakeProfit(BaseModel):
    """Take profit based on ATR multiple from entry."""

    method: Literal["atr_multiple"] = "atr_multiple"
    multiplier: float = Field(default=2.0, description="TP = Entry + (ATR * multiplier)")
    period: int = Field(default=14, description="ATR period")

    @model_validator(mode="after")
    def validate_multiplier_and_period(self) -> "ATRMultipleTakeProfit":
        _validate_atr_params(self.multiplier, self.period, "ATRMultipleTakeProfit")
        return self


class FixedPctTakeProfit(BaseModel):
    """Take profit based on fixed percentage from entry."""

    method: Literal["fixed_pct"] = "fixed_pct"
    value: float = Field(description="Percentage, e.g., 0.02 = 2%")

    @model_validator(mode="after")
    def value_positive(self) -> "FixedPctTakeProfit":
        if self.value <= 0:
            raise ValueError("value must be positive")
        return self


TakeProfitConfig = Annotated[
    Union[OrbRangeMultipleTakeProfit, ATRMultipleTakeProfit, FixedPctTakeProfit],
    Field(discriminator="method"),
]


# ============================================================================
# Stop Loss Configurations
# ============================================================================

class OrbLevelStopLoss(BaseModel):
    """Stop loss at ORB level (high for short, low for long)."""

    method: Literal["orb_level"] = "orb_level"


class ATRMultipleStopLoss(BaseModel):
    """Stop loss based on ATR multiple from entry."""

    method: Literal["atr_multiple"] = "atr_multiple"
    multiplier: float = Field(default=1.5, description="SL = Entry - (ATR * multiplier)")
    period: int = Field(default=14, description="ATR period")

    @model_validator(mode="after")
    def validate_multiplier_and_period(self) -> "ATRMultipleStopLoss":
        _validate_atr_params(self.multiplier, self.period, "ATRMultipleStopLoss")
        return self


class FixedPctStopLoss(BaseModel):
    """Stop loss based on fixed percentage from entry."""

    method: Literal["fixed_pct"] = "fixed_pct"
    value: float = Field(description="Percentage, e.g., 0.02 = 2%")

    @model_validator(mode="after")
    def value_positive(self) -> "FixedPctStopLoss":
        if self.value <= 0:
            raise ValueError("value must be positive")
        return self


StopLossConfig = Annotated[
    Union[OrbLevelStopLoss, ATRMultipleStopLoss, FixedPctStopLoss],
    Field(discriminator="method"),
]


# ============================================================================
# Trailing Stop Configurations
# ============================================================================

class RStep(BaseModel):
    """One step in a stepped R-multiple trailing stop."""

    at: float = Field(description="Trigger: unrealized P&L reaches this R multiple")
    move_stop_to: float = Field(description="Action: move stop to lock in this R multiple")

    @model_validator(mode="after")
    def stop_must_be_below_trigger(self) -> "RStep":
        if self.move_stop_to >= self.at:
            raise ValueError(
                f"move_stop_to ({self.move_stop_to}R) must be less than at ({self.at}R)"
            )
        return self


class ATRTrailingStop(BaseModel):
    """Trailing stop based on ATR multiple."""

    method: Literal["atr"] = "atr"
    multiplier: float = Field(default=2.0, description="Trail by N × ATR")
    period: int = Field(default=14, description="ATR period")

    @model_validator(mode="after")
    def validate_multiplier_and_period(self) -> "ATRTrailingStop":
        _validate_atr_params(self.multiplier, self.period, "ATRTrailingStop")
        return self


class InitialRiskPctTrailingStop(BaseModel):
    """Trailing stop as percentage of initial risk distance."""

    method: Literal["initial_risk_pct"] = "initial_risk_pct"
    value: float = Field(
        description="Trail = initial_stop_distance * value (e.g., 0.5 = 50%)"
    )

    @model_validator(mode="after")
    def validate_value_range(self) -> "InitialRiskPctTrailingStop":
        if not (0 < self.value < 1):
            raise ValueError("value must be between 0 and 1")
        return self


class FixedDollarTrailingStop(BaseModel):
    """Trailing stop as fixed dollar amount."""

    method: Literal["fixed_dollar"] = "fixed_dollar"
    value: float = Field(description="Trail by fixed dollar amount")

    @model_validator(mode="after")
    def value_positive(self) -> "FixedDollarTrailingStop":
        if self.value <= 0:
            raise ValueError("value must be positive")
        return self


class SteppedRMultipleTrailingStop(BaseModel):
    """Trailing stop with stepped R-multiple rules."""

    method: Literal["stepped_r_multiple"] = "stepped_r_multiple"
    steps: List[RStep] = Field(description="Ordered list of profit-locking steps")

    @model_validator(mode="after")
    def steps_must_be_ascending(self) -> "SteppedRMultipleTrailingStop":
        if not self.steps:
            raise ValueError("steps list cannot be empty")
        ats = [s.at for s in self.steps]
        if ats != sorted(ats):
            raise ValueError("steps must be listed in ascending order of 'at'")
        return self


TrailingStopConfig = Annotated[
    Union[
        ATRTrailingStop,
        InitialRiskPctTrailingStop,
        FixedDollarTrailingStop,
        SteppedRMultipleTrailingStop,
    ],
    Field(discriminator="method"),
]


# ============================================================================
# Strategy Configurations
# ============================================================================

class ORBStrategyParams(BaseModel):
    """ORB (Opening Range Breakout) strategy parameters."""

    type: Literal["orb"] = "orb"
    orb_start_time: str = Field(default="09:30", description="ORB window start time (HH:MM)")
    orb_duration_minutes: int = Field(default=5, description="ORB window duration in minutes")
    orb_body_pct_filter: float = Field(
        default=0.0,
        description="Minimum body percentage for valid breakout candle (0.0 = no filter)",
    )
    entry_cutoff_time: str = Field(default="15:00", description="No entries after this time")
    entry_mode: str = Field(
        default="no_validation_simple",
        description="Entry validation mode",
    )
    one_trade_per_day: bool = Field(default=True, description="Max one trade per day")
    cancel_other_side: bool = Field(
        default=True,
        description="Cancel opposite order if one side triggers",
    )
    allow_reentry: bool = Field(default=False, description="Allow re-entry after exit")

    @model_validator(mode="after")
    def validate_times(self) -> "ORBStrategyParams":
        # Basic time format validation (HH:MM with zero-padding)
        for time_str in [self.orb_start_time, self.entry_cutoff_time]:
            try:
                parts = time_str.split(":")
                if len(parts) != 2:
                    raise ValueError()
                # Check that hour and minute parts have correct length (2 chars each)
                if len(parts[0]) != 2 or len(parts[1]) != 2:
                    raise ValueError()
                hour, minute = int(parts[0]), int(parts[1])
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError()
            except (ValueError, IndexError):
                raise ValueError(f"Invalid time format: {time_str} (expected HH:MM)")
        return self


StrategyParams = Annotated[
    Union[ORBStrategyParams],
    Field(discriminator="type"),
]


# ============================================================================
# Position Sizing
# ============================================================================

class PositionSizeConfig(BaseModel):
    """Position sizing configuration."""

    method: Literal["max_loss_pct", "fixed_dollar", "fixed_shares"] = Field(
        default="max_loss_pct",
        description="Position sizing method",
    )
    value: float = Field(description="Value (pct for max_loss_pct, amount/shares for others)")
    max_shares: Optional[int] = Field(
        default=None,
        description="Maximum shares cap (optional)",
    )

    @model_validator(mode="after")
    def validate_value(self) -> "PositionSizeConfig":
        if self.value <= 0:
            raise ValueError("value must be positive")
        return self


# ============================================================================
# Exit Configuration
# ============================================================================

class ExitConfig(BaseModel):
    """Exit conditions for trades."""

    eod: bool = Field(default=True, description="Enable end-of-day exit")
    eod_time: str = Field(default="15:55", description="EOD exit time (HH:MM)")
    take_profit: Optional[TakeProfitConfig] = Field(
        default=None,
        description="Take profit configuration",
    )
    stop_loss: StopLossConfig = Field(
        default_factory=OrbLevelStopLoss,
        description="Stop loss configuration",
    )
    trailing_stop: Optional[TrailingStopConfig] = Field(
        default=None,
        description="Trailing stop configuration",
    )

    @model_validator(mode="after")
    def at_least_one_exit_required(self) -> "ExitConfig":
        """Require at least one exit condition (always satisfied since stop_loss is required)."""
        # stop_loss is now required with a default value (OrbLevelStopLoss),
        # so there will always be at least one exit condition.
        # Keep this validator for documentation and future-proofing.
        has_exit = (
            self.eod
            or self.stop_loss is not None
            or self.take_profit is not None
            or self.trailing_stop is not None
        )
        if not has_exit:
            raise ValueError(
                "ExitConfig must define at least one exit condition "
                "(eod, stop_loss, take_profit, or trailing_stop)"
            )
        return self

    @model_validator(mode="after")
    def validate_eod_time(self) -> "ExitConfig":
        """Validate EOD time format (HH:MM with zero-padding)."""
        if self.eod_time is not None:
            try:
                parts = self.eod_time.split(":")
                if len(parts) != 2:
                    raise ValueError()
                # Check that hour and minute parts have correct length (2 chars each)
                if len(parts[0]) != 2 or len(parts[1]) != 2:
                    raise ValueError()
                hour, minute = int(parts[0]), int(parts[1])
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError()
            except (ValueError, IndexError):
                raise ValueError(f"Invalid eod_time format: {self.eod_time} (expected HH:MM)")
        return self


# ============================================================================
# Trade Filters
# ============================================================================

class TradeFilterConfig(BaseModel):
    """Trade entry filters."""

    vix_max: Optional[float] = Field(
        default=None,
        description="Maximum VIX level for trading (optional)",
    )
    volume_confirmation: bool = Field(
        default=False,
        description="Require above-average volume confirmation",
    )
    volume_threshold: float = Field(
        default=1.5,
        description="Volume must be > average * threshold",
    )


# ============================================================================
# Multi-Timeframe Validation
# ============================================================================

class MTFValidationConfig(BaseModel):
    """Multi-timeframe validation configuration."""

    enabled: bool = Field(default=False, description="Enable MTF validation")
    timeframe: str = Field(default="30m", description="Higher timeframe for validation")
    condition: str = Field(
        default="trend_aligned",
        description="Validation condition (trend_aligned, etc.)",
    )


# ============================================================================
# Instrument Configuration
# ============================================================================

class InstrumentConfig(BaseModel):
    """Instrument/symbol configuration."""

    symbols: List[str] = Field(
        default=["QQQ", "SPY"],
        description="Trading symbols",
    )
    timeframe: str = Field(default="5m", description="Bar timeframe (e.g., 5m, 1m, 15m)")

    @model_validator(mode="after")
    def symbols_not_empty(self) -> "InstrumentConfig":
        if not self.symbols:
            raise ValueError("symbols list cannot be empty")
        return self


# ============================================================================
# Main Strategy Ruleset
# ============================================================================

class StrategyRuleSet(BaseModel):
    """Complete strategy ruleset with all trading configuration."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Ruleset name")
    version: str = Field(default="1.0", description="Ruleset version")
    description: Optional[str] = Field(default=None, description="Ruleset description")

    instruments: InstrumentConfig = Field(
        default_factory=InstrumentConfig,
        description="Instrument configuration",
    )
    strategy: StrategyParams = Field(description="Strategy parameters")
    position_size: PositionSizeConfig = Field(
        description="Position sizing configuration (required)"
    )
    exit: ExitConfig = Field(
        default_factory=ExitConfig,
        description="Exit configuration",
    )
    trade_filter: TradeFilterConfig = Field(
        default_factory=TradeFilterConfig,
        description="Trade filter configuration",
    )
    mtf_validation: MTFValidationConfig = Field(
        default_factory=MTFValidationConfig,
        description="Multi-timeframe validation configuration",
    )

    @model_validator(mode="after")
    def validate_all(self) -> "StrategyRuleSet":
        """Cross-field validation."""
        # Add any cross-field checks here
        return self
