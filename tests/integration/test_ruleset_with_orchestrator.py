"""Integration tests for ruleset with orchestrator."""

import pytest
from unittest.mock import Mock, patch

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.common.ruleset import RuleSetLoader
from vibe.common.ruleset.models import ORBStrategyParams


class TestRulesetWithOrchestrator:
    """Integration tests for ruleset loading in orchestrator."""

    def test_orchestrator_loads_ruleset_from_config(self):
        """Test that orchestrator loads ruleset from active_ruleset in config."""
        config = AppSettings(active_ruleset="orb_conservative")

        # Create orchestrator (will load ruleset)
        orchestrator = TradingOrchestrator(config=config)

        # Verify ruleset was loaded
        assert orchestrator.ruleset is not None
        assert orchestrator.ruleset.name == "orb_conservative"
        assert orchestrator.ruleset.version == "1.0"

    def test_orchestrator_accepts_injected_ruleset(self):
        """Test that orchestrator can accept an injected ruleset."""
        config = AppSettings()
        ruleset = RuleSetLoader.from_name("orb_conservative")

        orchestrator = TradingOrchestrator(config=config, ruleset=ruleset)

        assert orchestrator.ruleset == ruleset
        assert orchestrator.ruleset.name == "orb_conservative"

    def test_orchestrator_logs_ruleset_info(self, caplog):
        """Test that orchestrator logs ruleset information on init."""
        config = AppSettings(active_ruleset="orb_conservative")

        with caplog.at_level("INFO"):
            orchestrator = TradingOrchestrator(config=config)

        log_output = caplog.text
        assert "orb_conservative" in log_output
        assert "v1.0" in log_output

    def test_orchestrator_fails_with_missing_ruleset(self):
        """Test that orchestrator raises error if ruleset doesn't exist."""
        config = AppSettings(active_ruleset="nonexistent_ruleset")

        with pytest.raises(FileNotFoundError):
            TradingOrchestrator(config=config)

    def test_orchestrator_ruleset_has_instruments(self):
        """Test that orchestrator ruleset includes instruments config."""
        config = AppSettings(active_ruleset="orb_conservative")
        orchestrator = TradingOrchestrator(config=config)

        assert orchestrator.ruleset.instruments is not None
        assert len(orchestrator.ruleset.instruments.symbols) > 0
        assert orchestrator.ruleset.instruments.timeframe == "5m"

    def test_orchestrator_ruleset_has_strategy(self):
        """Test that orchestrator ruleset includes strategy config."""
        config = AppSettings(active_ruleset="orb_conservative")
        orchestrator = TradingOrchestrator(config=config)

        assert orchestrator.ruleset.strategy is not None
        assert isinstance(orchestrator.ruleset.strategy, ORBStrategyParams)
        assert orchestrator.ruleset.strategy.type == "orb"

    def test_orchestrator_ruleset_has_exit_config(self):
        """Test that orchestrator ruleset includes exit configuration."""
        config = AppSettings(active_ruleset="orb_conservative")
        orchestrator = TradingOrchestrator(config=config)

        assert orchestrator.ruleset.exit is not None
        assert orchestrator.ruleset.exit.eod is True
        assert orchestrator.ruleset.exit.take_profit is not None
        assert orchestrator.ruleset.exit.stop_loss is not None

    def test_orchestrator_ruleset_has_position_sizing(self):
        """Test that orchestrator ruleset includes position sizing config."""
        config = AppSettings(active_ruleset="orb_conservative")
        orchestrator = TradingOrchestrator(config=config)

        assert orchestrator.ruleset.position_size is not None
        assert orchestrator.ruleset.position_size.method == "max_loss_pct"
        assert orchestrator.ruleset.position_size.value == 0.01

    def test_orchestrator_preserves_config_separation(self):
        """Test that ruleset and AppSettings are properly separated."""
        config = AppSettings(
            active_ruleset="orb_conservative",
            environment="production",
            log_level="DEBUG",
        )
        orchestrator = TradingOrchestrator(config=config)

        # AppSettings should still have its original values
        assert orchestrator.config.environment == "production"
        assert orchestrator.config.log_level == "DEBUG"

        # Ruleset should have its own values
        assert orchestrator.ruleset.name == "orb_conservative"
        assert orchestrator.ruleset.instruments.symbols != orchestrator.config.trading.symbols


class TestRulesetLoaderAvailability:
    """Tests for checking ruleset availability."""

    def test_orb_conservative_exists(self):
        """Test that orb_conservative ruleset exists and can be loaded."""
        available = RuleSetLoader.list_available()
        assert "orb_conservative" in available

    def test_can_load_any_available_ruleset(self):
        """Test that any listed ruleset can be loaded."""
        available = RuleSetLoader.list_available()

        for ruleset_name in available:
            ruleset = RuleSetLoader.from_name(ruleset_name)
            assert ruleset is not None
            assert ruleset.name == ruleset_name
