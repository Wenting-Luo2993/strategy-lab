"""Unit tests for strategy ruleset Pydantic models."""

import pytest
from pydantic import ValidationError

from vibe.common.ruleset.models import (
    StrategyRuleSet,
    InstrumentConfig,
    ORBStrategyParams,
    PositionSizeConfig,
    ExitConfig,
    TradeFilterConfig,
    MTFValidationConfig,
    OrbRangeMultipleTakeProfit,
    ATRMultipleTakeProfit,
    FixedPctTakeProfit,
    OrbLevelStopLoss,
    ATRMultipleStopLoss,
    FixedPctStopLoss,
    ATRTrailingStop,
    InitialRiskPctTrailingStop,
    FixedDollarTrailingStop,
    SteppedRMultipleTrailingStop,
    RStep,
)


class TestInstrumentConfig:
    """Tests for InstrumentConfig."""

    def test_valid_config(self):
        """Test valid instrument config."""
        config = InstrumentConfig(symbols=["AAPL", "GOOGL"], timeframe="5m")
        assert config.symbols == ["AAPL", "GOOGL"]
        assert config.timeframe == "5m"

    def test_default_values(self):
        """Test default values."""
        config = InstrumentConfig()
        assert config.symbols == ["QQQ", "SPY"]
        assert config.timeframe == "5m"

    def test_empty_symbols_rejected(self):
        """Test that empty symbols list is rejected."""
        with pytest.raises(ValidationError):
            InstrumentConfig(symbols=[])


class TestORBStrategyParams:
    """Tests for ORB strategy parameters."""

    def test_valid_orb_config(self):
        """Test valid ORB config."""
        config = ORBStrategyParams(
            orb_start_time="09:30",
            orb_duration_minutes=5,
            entry_cutoff_time="15:00",
        )
        assert config.type == "orb"
        assert config.orb_start_time == "09:30"
        assert config.orb_duration_minutes == 5

    def test_default_values(self):
        """Test default values."""
        config = ORBStrategyParams()
        assert config.type == "orb"
        assert config.orb_start_time == "09:30"
        assert config.orb_duration_minutes == 5
        assert config.one_trade_per_day is True

    def test_invalid_start_time_format(self):
        """Test that invalid time format is rejected."""
        with pytest.raises(ValidationError):
            ORBStrategyParams(orb_start_time="9:30")  # Should be HH:MM

    def test_invalid_hour(self):
        """Test that invalid hour is rejected."""
        with pytest.raises(ValidationError):
            ORBStrategyParams(orb_start_time="25:00")

    def test_invalid_minute(self):
        """Test that invalid minute is rejected."""
        with pytest.raises(ValidationError):
            ORBStrategyParams(orb_start_time="09:60")


class TestTakeProfitConfigs:
    """Tests for take profit configurations."""

    def test_orb_range_multiple(self):
        """Test ORB range multiple take profit."""
        config = OrbRangeMultipleTakeProfit(multiplier=2.0)
        assert config.method == "orb_range_multiple"
        assert config.multiplier == 2.0

    def test_atr_multiple(self):
        """Test ATR multiple take profit."""
        config = ATRMultipleTakeProfit(multiplier=2.0, period=14)
        assert config.method == "atr_multiple"
        assert config.multiplier == 2.0
        assert config.period == 14

    def test_fixed_pct(self):
        """Test fixed percentage take profit."""
        config = FixedPctTakeProfit(value=0.05)
        assert config.method == "fixed_pct"
        assert config.value == 0.05

    def test_negative_multiplier_rejected(self):
        """Test that negative multiplier is rejected."""
        with pytest.raises(ValidationError):
            OrbRangeMultipleTakeProfit(multiplier=-1.0)

    def test_zero_multiplier_rejected(self):
        """Test that zero multiplier is rejected."""
        with pytest.raises(ValidationError):
            OrbRangeMultipleTakeProfit(multiplier=0)


class TestStopLossConfigs:
    """Tests for stop loss configurations."""

    def test_orb_level(self):
        """Test ORB level stop loss."""
        config = OrbLevelStopLoss()
        assert config.method == "orb_level"

    def test_atr_multiple(self):
        """Test ATR multiple stop loss."""
        config = ATRMultipleStopLoss(multiplier=1.5, period=14)
        assert config.method == "atr_multiple"
        assert config.multiplier == 1.5

    def test_fixed_pct(self):
        """Test fixed percentage stop loss."""
        config = FixedPctStopLoss(value=0.02)
        assert config.method == "fixed_pct"
        assert config.value == 0.02

    def test_negative_value_rejected(self):
        """Test that negative value is rejected."""
        with pytest.raises(ValidationError):
            FixedPctStopLoss(value=-0.02)


class TestRStep:
    """Tests for R-step in trailing stop."""

    def test_valid_step(self):
        """Test valid R-step."""
        step = RStep(at=2.0, move_stop_to=1.0)
        assert step.at == 2.0
        assert step.move_stop_to == 1.0

    def test_stop_must_be_below_trigger(self):
        """Test that stop must be below trigger."""
        with pytest.raises(ValidationError):
            RStep(at=2.0, move_stop_to=2.5)

    def test_stop_equal_to_trigger_rejected(self):
        """Test that stop equal to trigger is rejected."""
        with pytest.raises(ValidationError):
            RStep(at=2.0, move_stop_to=2.0)


class TestTrailingStopConfigs:
    """Tests for trailing stop configurations."""

    def test_atr_trailing_stop(self):
        """Test ATR trailing stop."""
        config = ATRTrailingStop(multiplier=2.0, period=14)
        assert config.method == "atr"
        assert config.multiplier == 2.0

    def test_initial_risk_pct_trailing_stop(self):
        """Test initial risk percentage trailing stop."""
        config = InitialRiskPctTrailingStop(value=0.5)
        assert config.method == "initial_risk_pct"
        assert config.value == 0.5

    def test_fixed_dollar_trailing_stop(self):
        """Test fixed dollar trailing stop."""
        config = FixedDollarTrailingStop(value=50.0)
        assert config.method == "fixed_dollar"
        assert config.value == 50.0

    def test_stepped_r_multiple(self):
        """Test stepped R-multiple trailing stop."""
        config = SteppedRMultipleTrailingStop(
            steps=[
                RStep(at=2.0, move_stop_to=1.0),
                RStep(at=3.0, move_stop_to=2.0),
            ]
        )
        assert config.method == "stepped_r_multiple"
        assert len(config.steps) == 2

    def test_stepped_non_ascending_rejected(self):
        """Test that non-ascending steps are rejected."""
        with pytest.raises(ValidationError):
            SteppedRMultipleTrailingStop(
                steps=[
                    RStep(at=3.0, move_stop_to=2.0),
                    RStep(at=2.0, move_stop_to=1.0),  # Out of order
                ]
            )

    def test_stepped_empty_steps_rejected(self):
        """Test that empty steps list is rejected."""
        with pytest.raises(ValidationError):
            SteppedRMultipleTrailingStop(steps=[])

    def test_initial_risk_pct_value_out_of_bounds(self):
        """Test that value outside 0-1 is rejected."""
        with pytest.raises(ValidationError):
            InitialRiskPctTrailingStop(value=1.5)

    def test_initial_risk_pct_zero_rejected(self):
        """Test that zero value is rejected."""
        with pytest.raises(ValidationError):
            InitialRiskPctTrailingStop(value=0)


class TestPositionSizeConfig:
    """Tests for position sizing."""

    def test_max_loss_pct(self):
        """Test max loss percentage method."""
        config = PositionSizeConfig(method="max_loss_pct", value=0.01)
        assert config.method == "max_loss_pct"
        assert config.value == 0.01

    def test_with_max_shares_cap(self):
        """Test with max shares cap."""
        config = PositionSizeConfig(method="max_loss_pct", value=0.01, max_shares=500)
        assert config.max_shares == 500

    def test_negative_value_rejected(self):
        """Test that negative value is rejected."""
        with pytest.raises(ValidationError):
            PositionSizeConfig(method="max_loss_pct", value=-0.01)

    def test_zero_value_rejected(self):
        """Test that zero value is rejected."""
        with pytest.raises(ValidationError):
            PositionSizeConfig(method="max_loss_pct", value=0)


class TestExitConfig:
    """Tests for exit configuration."""

    def test_valid_exit_with_eod(self):
        """Test valid exit config with EOD."""
        config = ExitConfig(eod=True)
        assert config.eod is True
        assert config.eod_time == "15:55"

    def test_valid_exit_with_take_profit(self):
        """Test valid exit config with take profit."""
        config = ExitConfig(
            eod=False,
            take_profit=OrbRangeMultipleTakeProfit(multiplier=2.0),
        )
        assert config.take_profit is not None

    def test_at_least_one_exit_required(self):
        """Test that at least one exit is required."""
        with pytest.raises(ValidationError):
            ExitConfig(
                eod=False,
                take_profit=None,
                stop_loss=None,
                trailing_stop=None,
            )

    def test_valid_with_all_exits(self):
        """Test valid config with all exits."""
        config = ExitConfig(
            eod=True,
            eod_time="15:55",
            take_profit=OrbRangeMultipleTakeProfit(),
            stop_loss=OrbLevelStopLoss(),
            trailing_stop=ATRTrailingStop(),
        )
        assert config.eod is True
        assert config.take_profit is not None
        assert config.stop_loss is not None
        assert config.trailing_stop is not None

    def test_invalid_eod_time_format(self):
        """Test that invalid eod_time format is rejected."""
        with pytest.raises(ValidationError):
            ExitConfig(eod=True, eod_time="15-55")  # Should be HH:MM

    def test_default_eod_config(self):
        """Test default EOD config."""
        config = ExitConfig()
        assert config.eod is True
        assert config.eod_time == "15:55"


class TestTradeFilterConfig:
    """Tests for trade filter configuration."""

    def test_valid_filter(self):
        """Test valid filter config."""
        config = TradeFilterConfig(vix_max=30, volume_confirmation=True)
        assert config.vix_max == 30
        assert config.volume_confirmation is True

    def test_optional_vix(self):
        """Test that VIX is optional."""
        config = TradeFilterConfig(vix_max=None)
        assert config.vix_max is None

    def test_defaults(self):
        """Test default values."""
        config = TradeFilterConfig()
        assert config.vix_max is None
        assert config.volume_confirmation is False
        assert config.volume_threshold == 1.5


class TestMTFValidationConfig:
    """Tests for multi-timeframe validation."""

    def test_disabled_by_default(self):
        """Test that MTF validation is disabled by default."""
        config = MTFValidationConfig()
        assert config.enabled is False

    def test_enabled_config(self):
        """Test enabled MTF validation."""
        config = MTFValidationConfig(enabled=True, timeframe="30m", condition="trend_aligned")
        assert config.enabled is True
        assert config.timeframe == "30m"


class TestStrategyRuleSet:
    """Tests for complete strategy ruleset."""

    def test_minimal_ruleset(self):
        """Test minimal valid ruleset."""
        ruleset = StrategyRuleSet(
            name="test_ruleset",
            strategy=ORBStrategyParams(),
            position_size=PositionSizeConfig(method="max_loss_pct", value=0.01),
        )
        assert ruleset.name == "test_ruleset"
        assert ruleset.version == "1.0"
        assert ruleset.strategy.type == "orb"

    def test_complete_ruleset(self):
        """Test complete ruleset with all fields."""
        ruleset = StrategyRuleSet(
            name="complete",
            version="1.2",
            description="Complete test ruleset",
            instruments=InstrumentConfig(symbols=["AAPL", "GOOGL"], timeframe="5m"),
            strategy=ORBStrategyParams(
                orb_start_time="09:30",
                orb_duration_minutes=5,
            ),
            position_size=PositionSizeConfig(method="max_loss_pct", value=0.01),
            exit=ExitConfig(
                eod=True,
                take_profit=OrbRangeMultipleTakeProfit(multiplier=2.0),
                stop_loss=OrbLevelStopLoss(),
            ),
            trade_filter=TradeFilterConfig(vix_max=30),
            mtf_validation=MTFValidationConfig(enabled=True),
        )
        assert ruleset.name == "complete"
        assert ruleset.version == "1.2"
        assert len(ruleset.instruments.symbols) == 2

    def test_default_values(self):
        """Test default values in ruleset."""
        ruleset = StrategyRuleSet(
            name="defaults",
            strategy=ORBStrategyParams(),
            position_size=PositionSizeConfig(method="max_loss_pct", value=0.01),
        )
        assert ruleset.instruments.symbols == ["QQQ", "SPY"]
        assert ruleset.position_size.method == "max_loss_pct"
        assert ruleset.exit.eod is True
        assert ruleset.mtf_validation.enabled is False


class TestDiscriminatedUnions:
    """Tests for discriminated union type handling."""

    def test_strategy_type_discriminator(self):
        """Test that strategy type is correctly discriminated."""
        data = {
            "name": "test",
            "strategy": {
                "type": "orb",
                "orb_start_time": "09:30",
                "orb_duration_minutes": 5,
            },
            "position_size": {
                "method": "max_loss_pct",
                "value": 0.01,
            },
        }
        ruleset = StrategyRuleSet(**data)
        assert isinstance(ruleset.strategy, ORBStrategyParams)
        assert ruleset.strategy.type == "orb"

    def test_take_profit_method_discriminator(self):
        """Test that take profit method is correctly discriminated."""
        data = {
            "name": "test",
            "strategy": ORBStrategyParams(),
            "position_size": {
                "method": "max_loss_pct",
                "value": 0.01,
            },
            "exit": {
                "eod": False,
                "take_profit": {
                    "method": "fixed_pct",
                    "value": 0.05,
                },
            },
        }
        ruleset = StrategyRuleSet(**data)
        assert isinstance(ruleset.exit.take_profit, FixedPctTakeProfit)
        assert ruleset.exit.take_profit.value == 0.05

    def test_stop_loss_method_discriminator(self):
        """Test that stop loss method is correctly discriminated."""
        data = {
            "name": "test",
            "strategy": ORBStrategyParams(),
            "position_size": {
                "method": "max_loss_pct",
                "value": 0.01,
            },
            "exit": {
                "eod": False,
                "stop_loss": {
                    "method": "atr_multiple",
                    "multiplier": 1.5,
                    "period": 14,
                },
            },
        }
        ruleset = StrategyRuleSet(**data)
        assert isinstance(ruleset.exit.stop_loss, ATRMultipleStopLoss)
        assert ruleset.exit.stop_loss.multiplier == 1.5

    def test_trailing_stop_method_discriminator(self):
        """Test that trailing stop method is correctly discriminated."""
        data = {
            "name": "test",
            "strategy": ORBStrategyParams(),
            "position_size": {
                "method": "max_loss_pct",
                "value": 0.01,
            },
            "exit": {
                "eod": False,
                "trailing_stop": {
                    "method": "stepped_r_multiple",
                    "steps": [
                        {"at": 2.0, "move_stop_to": 1.0},
                        {"at": 3.0, "move_stop_to": 2.0},
                    ],
                },
                "take_profit": OrbRangeMultipleTakeProfit(),
            },
        }
        ruleset = StrategyRuleSet(**data)
        assert isinstance(ruleset.exit.trailing_stop, SteppedRMultipleTrailingStop)
        assert len(ruleset.exit.trailing_stop.steps) == 2
